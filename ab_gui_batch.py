#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
ab_gui_batch.py - Tab Batch / Catalog

Lista tutte le app registrate in _Config/*/build.json, mostra il loro stato
(Aggiornata / Da ricostruire / Disabilitata / Script mancante / Errore),
permette di ricostruire selettivamente in batch (solo obsolete, selezione,
tutte) e di abilitare/disabilitare app dalla pipeline.

Le costanti colore/stato sono definite qui e importate dal tab Archiviazione
per coerenza visuale.

Caratteristiche:
- Verifica stato on-demand via check_needs_rebuild
- Esecuzione batch in thread separato con progress bar determinata
- Aggiornamento incrementale dei colori/stato per riga al termine di ogni build
- Aggiornamento del build.json dopo ogni rebuild (mtime, version, python_builder)
"""

import datetime
import json
import subprocess
import threading
import tkinter as tk
from tkinter import ttk, messagebox
from pathlib import Path

from ab_utils import (
    extract_version,
    find_local_imports,
    max_mtime,
)
from ab_config import (
    config_root,
    discover_build_configs,
    load_build_json,
    resolve_script_path,
    check_needs_rebuild,
)
from ab_build import execute_build
from ab_log import (
    LOG_DIR,
    write_build_session_log,
)


# Costanti colori stato (riutilizzate anche da ab_gui_archive.py)
COLOR_OK = "darkgreen"
COLOR_REBUILD = "#b8860b"   # dark goldenrod
COLOR_MISSING = "gray"
COLOR_ERROR = "red"
COLOR_DISABLED = "gray"

STATUS_OK = "✓ Aggiornata"
STATUS_REBUILD = "↺ Da ricostruire"
STATUS_MISSING = "? Script mancante"
STATUS_DISABLED = "— Disabilitata"
STATUS_ERROR = "✗ Errore"


class BatchTab(ttk.Frame):
    def __init__(self, parent, app):
        super().__init__(parent)
        self.app = app
        self._configs: list[dict] = []          # dati caricati
        self._json_paths: list[Path] = []       # percorsi json
        self._build_running = False
        # Buffer del log della sessione batch corrente e risultati per-app
        # (scritti a fine batch in ~/Documents/log/AppBuilder_build_<ts>.log)
        self._session_log_buffer: list[str] = []
        self._session_results: list[dict] = []
        self._create_widgets()

    def _create_widgets(self):
        info_frame = ttk.Frame(self)
        info_frame.pack(fill="x", pady=(0, 6))

        ttk.Label(info_frame, text="Configurazioni rilevate da:", foreground="gray").pack(side="left")
        self._config_root_label = ttk.Label(info_frame, text="", foreground="#555")
        self._config_root_label.pack(side="left", padx=6)

        # Treeview per la lista delle app
        tree_frame = ttk.Frame(self)
        tree_frame.pack(fill="both", expand=False, pady=(0, 8))

        cols = ("app_name", "script", "version", "status")
        self.tree = ttk.Treeview(tree_frame, columns=cols, show="headings", height=10,
                                 selectmode="extended")
        self.tree.heading("app_name", text="App")
        self.tree.heading("script", text="Script")
        self.tree.heading("version", text="Versione")
        self.tree.heading("status", text="Stato")
        self.tree.column("app_name", width=160, minwidth=100)
        self.tree.column("script", width=300, minwidth=150)
        self.tree.column("version", width=80, minwidth=60, anchor="center")
        self.tree.column("status", width=260, minwidth=160)

        vsb = ttk.Scrollbar(tree_frame, orient="vertical", command=self.tree.yview)
        self.tree.configure(yscrollcommand=vsb.set)
        self.tree.pack(side="left", fill="both", expand=True)
        vsb.pack(side="right", fill="y")

        # Tag colori
        self.tree.tag_configure("ok", foreground=COLOR_OK)
        self.tree.tag_configure("rebuild", foreground=COLOR_REBUILD)
        self.tree.tag_configure("missing", foreground=COLOR_MISSING)
        self.tree.tag_configure("disabled", foreground=COLOR_DISABLED)
        self.tree.tag_configure("error", foreground=COLOR_ERROR)

        # Bottoni azione
        btn_frame = ttk.Frame(self)
        btn_frame.pack(fill="x", pady=(0, 8))

        ttk.Button(btn_frame, text="↻ Aggiorna lista", command=self.refresh).pack(side="left")
        ttk.Button(btn_frame, text="✓ Verifica tutto", command=self.verify_all).pack(side="left", padx=(8, 0))

        ttk.Separator(btn_frame, orient="vertical").pack(side="left", fill="y", padx=10)

        self.btn_build_obsolete = ttk.Button(btn_frame, text="↺ Build obsolete",
                                              command=self.build_obsolete)
        self.btn_build_obsolete.pack(side="left")

        self.btn_build_selected = ttk.Button(btn_frame, text="▶ Build selezionate",
                                              command=self.build_selected)
        self.btn_build_selected.pack(side="left", padx=(8, 0))

        self.btn_build_all = ttk.Button(btn_frame, text="▶▶ Build tutte",
                                         command=self.build_all)
        self.btn_build_all.pack(side="left", padx=(8, 0))

        ttk.Separator(btn_frame, orient="vertical").pack(side="left", fill="y", padx=10)
        ttk.Button(btn_frame, text="Abilita/Disabilita", command=self.toggle_enabled).pack(side="left")

        # Progress bar batch
        prog_frame = ttk.Frame(self)
        prog_frame.pack(fill="x", pady=(0, 4))
        self.batch_status_label = ttk.Label(prog_frame, text="")
        self.batch_status_label.pack(side="left")
        self.batch_progress = ttk.Progressbar(prog_frame, mode="determinate", length=300)
        self.batch_progress.pack(side="left", padx=12)

        # Log batch — header con label + bottoni accessori
        log_header = ttk.Frame(self)
        log_header.pack(fill="x", pady=(2, 0))
        ttk.Label(log_header, text="Log batch:").pack(side="left")
        ttk.Button(log_header, text="🧹 Pulisci log",
                   command=self.clear_log).pack(side="right")
        ttk.Button(log_header, text="📂 Apri cartella log",
                   command=self.open_log_folder).pack(side="right", padx=(0, 6))

        log_frame = ttk.Frame(self)
        log_frame.pack(fill="both", expand=True)

        log_scroll_y = ttk.Scrollbar(log_frame, orient="vertical")
        log_scroll_x = ttk.Scrollbar(log_frame, orient="horizontal")
        self.log = tk.Text(
            log_frame,
            height=10,
            wrap="none",
            yscrollcommand=log_scroll_y.set,
            xscrollcommand=log_scroll_x.set,
            font=("Menlo", 11)
        )
        log_scroll_y.config(command=self.log.yview)
        log_scroll_x.config(command=self.log.xview)
        log_scroll_y.pack(side="right", fill="y")
        log_scroll_x.pack(side="bottom", fill="x")
        self.log.pack(side="left", fill="both", expand=True)

    def log_write(self, s: str):
        if threading.get_ident() != self.app._ui_thread_id:
            self.app.after(0, self.log_write, s)
            return
        self.log.insert("end", s)
        self.log.see("end")
        # Bufferizza per il log su file (verra' scritto a fine batch)
        self._session_log_buffer.append(s)
        self.app.update_idletasks()

    def clear_log(self):
        """Svuota il log batch."""
        self.log.delete("1.0", "end")

    def open_log_folder(self):
        """Apre ~/Documents/log/ nel Finder."""
        try:
            LOG_DIR.mkdir(parents=True, exist_ok=True)
            subprocess.run(["open", str(LOG_DIR)], check=False)
        except Exception as e:
            messagebox.showwarning(
                "Apri cartella log",
                f"Impossibile aprire {LOG_DIR}:\n{e}")

    def refresh(self):
        """Carica (o ricarica) tutte le configurazioni da _Config/*/build.json."""
        base_path = Path(self.app._base_var.get()).expanduser()
        config_path = Path(self.app._config_var.get()).expanduser()
        cr = config_root(config_path)
        self._config_root_label.config(text=str(cr))

        json_paths = discover_build_configs(config_path)
        self._json_paths = json_paths
        self._configs = []
        for jp in json_paths:
            cfg = load_build_json(jp)
            if cfg:
                self._configs.append(cfg)
            else:
                self._configs.append({"app_name": jp.parent.name, "_error": True})

        self._populate_tree()

    def _populate_tree(self):
        """Riempie il Treeview con i dati caricati, calcolando lo stato."""
        self.tree.delete(*self.tree.get_children())
        config_path = Path(self.app._config_var.get()).expanduser()
        install_dir = self.app.install_dir.get()

        for i, cfg in enumerate(self._configs):
            app_name = cfg.get("app_name", f"App_{i}")
            script = cfg.get("script", "?")
            version = cfg.get("version_detected") or "—"
            enabled = cfg.get("enabled", True)
            has_error = cfg.get("_error", False)

            if has_error:
                tag = "error"
                status = STATUS_ERROR
            elif not enabled:
                tag = "disabled"
                status = STATUS_DISABLED
            else:
                needs, reason = check_needs_rebuild(cfg, config_path, install_dir)
                script_path = resolve_script_path(script, config_path)
                if not script_path.exists() and "Non installata" not in reason:
                    tag = "missing"
                    status = STATUS_MISSING
                elif needs:
                    tag = "rebuild"
                    status = f"{STATUS_REBUILD} — {reason}"
                else:
                    tag = "ok"
                    status = reason

            self.tree.insert("", "end", iid=str(i),
                             values=(app_name, script, version, status),
                             tags=(tag,))

    def verify_all(self):
        """Ricalcola lo stato di tutte le app e aggiorna la tabella."""
        self.refresh()
        self.log_write(f"[{datetime.datetime.now().strftime('%H:%M:%S')}] Verifica completata: "
                       f"{len(self._configs)} configurazioni trovate.\n")

    def _items_needing_rebuild(self) -> list[int]:
        """Restituisce gli indici delle app che necessitano di rebuild."""
        config_path = Path(self.app._config_var.get()).expanduser()
        install_dir = self.app.install_dir.get()
        result = []
        for i, cfg in enumerate(self._configs):
            if not cfg.get("enabled", True) or cfg.get("_error"):
                continue
            needs, _ = check_needs_rebuild(cfg, config_path, install_dir)
            if needs:
                result.append(i)
        return result

    def build_obsolete(self):
        indices = self._items_needing_rebuild()
        if not indices:
            messagebox.showinfo("Batch", "Tutte le app risultano aggiornate. Nessun build necessario.")
            return
        names = [self._configs[i].get("app_name", "?") for i in indices]
        msg = f"Verranno ricostruite {len(indices)} app:\n\n" + "\n".join(f"  • {n}" for n in names)
        if messagebox.askyesno("Build obsolete", msg):
            self._start_batch(indices)

    def build_selected(self):
        sel = self.tree.selection()
        if not sel:
            messagebox.showinfo("Batch", "Seleziona almeno un'app dalla lista.")
            return
        indices = [int(iid) for iid in sel]
        names = [self._configs[i].get("app_name", "?") for i in indices]
        msg = f"Verranno ricostruite {len(indices)} app:\n\n" + "\n".join(f"  • {n}" for n in names)
        if messagebox.askyesno("Build selezionate", msg):
            self._start_batch(indices)

    def build_all(self):
        enabled = [i for i, cfg in enumerate(self._configs)
                   if cfg.get("enabled", True) and not cfg.get("_error")]
        if not enabled:
            messagebox.showinfo("Batch", "Nessuna app abilitata trovata.")
            return
        names = [self._configs[i].get("app_name", "?") for i in enabled]
        msg = f"Verranno ricostruite TUTTE le {len(enabled)} app abilitate:\n\n" + \
              "\n".join(f"  • {n}" for n in names)
        if messagebox.askyesno("Build tutte", msg):
            self._start_batch(enabled)

    def toggle_enabled(self):
        """Abilita/disabilita le app selezionate nel build.json."""
        sel = self.tree.selection()
        if not sel:
            messagebox.showinfo("Batch", "Seleziona almeno un'app dalla lista.")
            return
        for iid in sel:
            i = int(iid)
            cfg = self._configs[i]
            if cfg.get("_error"):
                continue
            cfg["enabled"] = not cfg.get("enabled", True)
            # Salva immediatamente il json_path corrispondente
            jp = self._json_paths[i]
            try:
                jp.write_text(json.dumps(cfg, indent=2, ensure_ascii=False), encoding='utf-8')
            except Exception as e:
                self.log_write(f"[!] Errore salvataggio {jp}: {e}\n")
        self._populate_tree()

    def _start_batch(self, indices: list[int]):
        if self._build_running:
            messagebox.showwarning("Batch", "Un batch e' gia' in esecuzione.")
            return
        self._build_running = True
        self._set_buttons_state("disabled")
        self.batch_progress["maximum"] = len(indices)
        self.batch_progress["value"] = 0

        # Reset stato di sessione per il log su file: ogni batch produce
        # un suo log indipendente in ~/Documents/log/AppBuilder_build_<ts>.log
        self._session_log_buffer = []
        self._session_results = []

        # Memorizza il numero di app per la label del log finale
        self._session_batch_label = f"{len(indices)} app"

        t = threading.Thread(target=self._batch_thread, args=(indices,), daemon=True)
        t.start()

    def _batch_thread(self, indices: list[int]):
        config_path = Path(self.app._config_var.get()).expanduser()
        python_root = config_path.parent  # es. Python/
        install_dir = self.app.install_dir.get()
        python_builder = self.app.python_builder.get()

        ok_count = 0
        fail_count = 0

        for step, i in enumerate(indices, 1):
            cfg = self._configs[i]
            app_name = cfg.get("app_name", "?")

            self.app.after(0, lambda n=app_name, s=step, tot=len(indices):
                self.batch_status_label.config(text=f"[{s}/{tot}] {n}..."))
            self.app.after(0, lambda s=step: self.batch_progress.config(value=s - 1))

            script_field = cfg.get("script", "")
            script_path = resolve_script_path(script_field, config_path)

            if not script_path.exists():
                self.log_write(f"\n[!] {app_name}: script non trovato ({script_field}), salto.\n")
                fail_count += 1
                self._session_results.append({
                    "app_name": app_name,
                    "success": False,
                    "message": f"Script non trovato: {script_field}",
                    "version": cfg.get("version_detected", ""),
                    "target": "",
                })
                self.app.after(0, lambda iid=str(i): self._set_row_tag(iid, "error",
                    f"{STATUS_ERROR} — Script non trovato"))
                continue

            # Icona: relativa a python_root, oppure assoluta
            icon_field = cfg.get("icon", "")
            icon_abs = ""
            if icon_field:
                icon_p = Path(icon_field).expanduser()
                if icon_p.is_absolute():
                    icon_abs = str(icon_p) if icon_p.exists() else ""
                else:
                    icon_p = config_path.parent / icon_field
                    icon_abs = str(icon_p) if icon_p.exists() else ""

            hidden_imports = cfg.get("hidden_imports", [])
            source_mtime = max_mtime(script_path, find_local_imports(script_path))

            try:
                target, builder_usato = execute_build(
                    script_path=script_path,
                    app_name=app_name,
                    icon=icon_abs,
                    windowed=cfg.get("windowed", True),
                    hidden_imports=hidden_imports,
                    install_dir_str=install_dir,
                    python_builder_str=cfg.get("python_builder", ""),
                    log_fn=self.log_write,
                    base_path=script_path.parent,
                    clean_after=True,
                    safe_install_fn=lambda t, n: self.app.safe_install_target(t, n, self.log_write),
                )

                # Aggiorna build.json con i nuovi dati
                # builder_usato e' il Python effettivamente usato (risolto da execute_build)
                jp = self._json_paths[i]
                cfg["source_mtime"] = source_mtime
                cfg["built_at"] = datetime.datetime.now().isoformat(timespec='seconds')
                cfg["version_detected"] = extract_version(script_path)
                cfg["python_builder"] = builder_usato
                try:
                    jp.write_text(json.dumps(cfg, indent=2, ensure_ascii=False), encoding='utf-8')
                    self._configs[i] = cfg
                except Exception as e:
                    self.log_write(f"[!] Errore aggiornamento build.json: {e}\n")

                ok_count += 1
                self._session_results.append({
                    "app_name": app_name,
                    "success": True,
                    "message": "",
                    "version": cfg.get("version_detected", ""),
                    "target": str(target),
                })
                built_at_short = cfg["built_at"][:16]
                self.app.after(0, lambda iid=str(i), ba=built_at_short:
                    self._set_row_tag(iid, "ok", f"Aggiornata (build: {ba})"))

            except Exception as e:
                fail_count += 1
                self.log_write(f"\n[X] ERRORE build {app_name}: {e}\n")
                self._session_results.append({
                    "app_name": app_name,
                    "success": False,
                    "message": f"{type(e).__name__}: {e}",
                    "version": cfg.get("version_detected", ""),
                    "target": "",
                })
                self.app.after(0, lambda iid=str(i): self._set_row_tag(iid, "error",
                    f"{STATUS_ERROR} — {str(e)[:60]}"))

        summary = f"\n{'='*60}\nBatch completato: {ok_count} ok, {fail_count} errori.\n{'='*60}\n"
        self.log_write(summary)

        # Scrittura log su file. Protetto da try/except diagnostico.
        try:
            label = getattr(self, "_session_batch_label", f"{len(indices)} app")
            log_path = write_build_session_log(
                log_buffer=self._session_log_buffer,
                summary={"n_ok": ok_count, "n_fail": fail_count, "label": label},
                per_app_results=self._session_results,
            )
            if log_path is not None:
                self.log_write(f"📄 Log batch salvato: {log_path}\n")
            else:
                self.log_write("⚠ Impossibile salvare il log su file "
                               "(vedi traceback nel terminale di lancio)\n")
        except Exception as e:
            import traceback
            self.log_write(f"\n⚠ Errore inatteso scrittura log: "
                           f"{type(e).__name__}: {e}\n")
            self.log_write(traceback.format_exc() + "\n")

        self.app.after(0, self._batch_finished, ok_count, fail_count)

    def _set_row_tag(self, iid: str, tag: str, status_text: str):
        """Aggiorna tag e colonna stato di una riga del Treeview."""
        try:
            vals = list(self.tree.item(iid, "values"))
            vals[3] = status_text
            self.tree.item(iid, values=vals, tags=(tag,))
        except Exception:
            pass

    def _batch_finished(self, ok: int, fail: int):
        self.batch_progress["value"] = self.batch_progress["maximum"]
        self.batch_status_label.config(
            text=f"Completato: {ok} ok, {fail} errori."
        )
        self._build_running = False
        self._set_buttons_state("normal")
        if fail == 0:
            messagebox.showinfo("Batch completato", f"Tutte le {ok} app sono state ricostruite con successo.")
        else:
            messagebox.showwarning("Batch completato", f"{ok} app ok, {fail} errori. Controlla il log.")

    def _set_buttons_state(self, state: str):
        self.btn_build_obsolete.config(state=state)
        self.btn_build_selected.config(state=state)
        self.btn_build_all.config(state=state)
