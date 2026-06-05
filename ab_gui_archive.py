#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
ab_gui_archive.py - Tab Archiviazione (zip in _Archivio/)

Lista tutte le app registrate in _Config/*/build.json e per ciascuna mostra
versione, ultimo zip esistente e stato di allineamento (Allineato / Da
ri-archiviare / Errore). Permette di creare in batch gli zip di:
- solo le app obsolete (zip mancante o piu' vecchio delle sorgenti)
- selezione manuale
- tutte le app

Include anche un pulsante di diagnostica disallineamenti che apre una
finestra con il report prodotto da diagnose_config_misalignments().

Caratteristiche:
- Riusa colori e costanti stato da ab_gui_batch.py per coerenza visuale
- Archiviazione in thread separato, non blocca la GUI
- Refresh popola la tree senza verificare (verifica = on-demand)
"""

import subprocess
import threading
import tkinter as tk
from tkinter import ttk, messagebox
from pathlib import Path

from ab_config import (
    discover_build_configs,
    load_build_json,
)
from ab_archive import (
    archive_root,
    collect_archive_files,
    find_latest_archive,
    check_needs_archive,
    create_archive_zip,
    diagnose_config_misalignments,
)
from ab_log import (
    LOG_DIR,
    write_archive_session_log,
)
from ab_gui_batch import (
    COLOR_OK, COLOR_REBUILD, COLOR_MISSING, COLOR_ERROR,
)


class ArchiveTab(ttk.Frame):
    """
    Tab che gestisce l'archiviazione automatica delle app in _Archivio/.
    Usa la stessa "lista file" di BatchTab (script + local_modules da build.json)
    e aggiunge: file .md ricorsivi nella cartella app + tutto _Config/<app>/.
    Crea zip nominati come <NomeApp>_v<ver>_<YYYYMMDD>.zip.
    """

    def __init__(self, parent, app):
        super().__init__(parent)
        self.app = app
        self._configs: list[dict] = []
        self._json_paths: list[Path] = []
        self._archive_running = False
        # Buffer del log di sessione (popolato durante il batch e scritto a
        # fine sessione in ~/Documents/log/AppBuilder_archive_<ts>.log).
        # Reset all'inizio di ogni nuova sessione di archiviazione.
        self._session_log_buffer: list[str] = []
        self._session_included: dict[str, list[Path]] = {}
        self._session_excluded: dict[str, list[tuple[Path, str]]] = {}
        self._create_widgets()

    def _create_widgets(self):
        info_frame = ttk.Frame(self)
        info_frame.pack(fill="x", pady=(0, 6))

        ttk.Label(info_frame, text="Archivio in:", foreground="gray").pack(side="left")
        self._archive_root_label = ttk.Label(info_frame, text="", foreground="#555")
        self._archive_root_label.pack(side="left", padx=6)

        # Treeview con la lista delle app
        tree_frame = ttk.Frame(self)
        tree_frame.pack(fill="both", expand=False, pady=(0, 8))

        cols = ("app_name", "version", "last_zip", "status")
        self.tree = ttk.Treeview(tree_frame, columns=cols, show="headings",
                                 height=10, selectmode="extended")
        self.tree.heading("app_name", text="App")
        self.tree.heading("version", text="Versione")
        self.tree.heading("last_zip", text="Ultimo zip")
        self.tree.heading("status", text="Stato")
        self.tree.column("app_name", width=200, minwidth=120)
        self.tree.column("version", width=80, minwidth=60, anchor="center")
        self.tree.column("last_zip", width=240, minwidth=150)
        self.tree.column("status", width=260, minwidth=160)

        vsb = ttk.Scrollbar(tree_frame, orient="vertical", command=self.tree.yview)
        self.tree.configure(yscrollcommand=vsb.set)
        self.tree.pack(side="left", fill="both", expand=True)
        vsb.pack(side="right", fill="y")

        # Tag colori (riuso quelli di BatchTab)
        self.tree.tag_configure("ok", foreground=COLOR_OK)
        self.tree.tag_configure("rebuild", foreground=COLOR_REBUILD)
        self.tree.tag_configure("missing", foreground=COLOR_MISSING)
        self.tree.tag_configure("error", foreground=COLOR_ERROR)

        # Bottoni — disposizione per fase del workflow:
        #
        # Riga 1 (ESPLORAZIONE): operazioni che NON modificano nulla e
        #   servono a capire lo stato delle app.
        #     Aggiorna lista | Verifica tutto | Verifica disallineamenti
        #
        # Riga 2 (ESECUZIONE): il flusso operativo dell'archiviazione,
        #   dalla prova alla consultazione del risultato.
        #     Dry-run | Archivia obsolete - selezionate - tutte | Visualizza ZIP
        #
        # I separatori segnano i passaggi di sotto-fase (prova -> esegui ->
        # controlla), non distinzioni categoriche.
        explore_frame = ttk.Frame(self)
        explore_frame.pack(fill="x", pady=(0, 4))

        ttk.Button(explore_frame, text="↻ Aggiorna lista",
                   command=self.refresh).pack(side="left")

        ttk.Separator(explore_frame, orient="vertical").pack(side="left", fill="y", padx=10)

        ttk.Button(explore_frame, text="✓ Verifica tutto",
                   command=self.verify_all).pack(side="left")

        ttk.Separator(explore_frame, orient="vertical").pack(side="left", fill="y", padx=10)

        ttk.Button(explore_frame, text="🔍 Verifica disallineamenti",
                   command=self.show_misalignments).pack(side="left")

        # Riga 2: esecuzione
        exec_frame = ttk.Frame(self)
        exec_frame.pack(fill="x", pady=(0, 8))

        ttk.Button(exec_frame, text="🔬 Dry-run",
                   command=self.dry_run_selected).pack(side="left")

        ttk.Separator(exec_frame, orient="vertical").pack(side="left", fill="y", padx=10)

        self.btn_arch_obsolete = ttk.Button(
            exec_frame, text="↺ Archivia obsolete",
            command=self.archive_obsolete)
        self.btn_arch_obsolete.pack(side="left")

        self.btn_arch_selected = ttk.Button(
            exec_frame, text="▶ Archivia selezionate",
            command=self.archive_selected)
        self.btn_arch_selected.pack(side="left", padx=(8, 0))

        self.btn_arch_all = ttk.Button(
            exec_frame, text="▶▶ Archivia tutte",
            command=self.archive_all)
        self.btn_arch_all.pack(side="left", padx=(8, 0))

        ttk.Separator(exec_frame, orient="vertical").pack(side="left", fill="y", padx=10)

        ttk.Button(exec_frame, text="🗂 Visualizza ZIP",
                   command=self.view_zip_selected).pack(side="left")

        # Progress bar
        prog_frame = ttk.Frame(self)
        prog_frame.pack(fill="x", pady=(0, 4))
        self.arch_status_label = ttk.Label(prog_frame, text="")
        self.arch_status_label.pack(side="left")
        self.arch_progress = ttk.Progressbar(prog_frame, mode="determinate", length=300)
        self.arch_progress.pack(side="left", padx=12)

        # Log — header con label + bottoni accessori
        log_header = ttk.Frame(self)
        log_header.pack(fill="x", pady=(2, 0))
        ttk.Label(log_header, text="Log archiviazione:").pack(side="left")
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

    # -----------------------------------------------------------------------
    # Utility log e UI
    # -----------------------------------------------------------------------

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
        """Svuota il log archiviazione."""
        self.log.delete("1.0", "end")

    def open_log_folder(self):
        """Apre ~/Documents/log/ nel Finder (la cartella viene creata se
        non esiste, cosi' il click funziona sempre anche prima del primo
        batch di archiviazione)."""
        try:
            LOG_DIR.mkdir(parents=True, exist_ok=True)
            subprocess.run(["open", str(LOG_DIR)], check=False)
        except Exception as e:
            messagebox.showwarning(
                "Apri cartella log",
                f"Impossibile aprire {LOG_DIR}:\n{e}")

    def _config_path(self) -> Path:
        return Path(self.app._config_var.get())

    def _base_path(self) -> Path:
        return Path(self.app._base_var.get())

    # -----------------------------------------------------------------------
    # Caricamento e verifica
    # -----------------------------------------------------------------------

    def refresh(self):
        """Carica/ricarica le configurazioni da _Config/*/build.json."""
        config_path = self._config_path()
        arch = archive_root(config_path)
        self._archive_root_label.config(text=str(arch))

        # Pulisci treeview
        for item in self.tree.get_children():
            self.tree.delete(item)

        self._configs = []
        self._json_paths = []

        json_paths = discover_build_configs(config_path)
        if not json_paths:
            self.log_write(f"[i] Nessun build.json trovato in {config_path}\n")
            return

        for jp in json_paths:
            cfg = load_build_json(jp)
            if cfg is None:
                continue
            self._configs.append(cfg)
            self._json_paths.append(jp)

        self._populate_tree()
        self.log_write(f"[i] Caricate {len(self._configs)} configurazioni\n")

    def _populate_tree(self):
        """Popola la treeview senza verificare lo stato (verifica = on-demand)."""
        config_path = self._config_path()
        for cfg in self._configs:
            app_name = cfg.get("app_name", "?")
            version = cfg.get("version_detected") or "—"
            latest = find_latest_archive(config_path, app_name)
            if latest:
                zip_info = f"{latest.name}"
            else:
                zip_info = "—"
            self.tree.insert("", "end",
                             values=(app_name, version, zip_info, "(non verificato)"))

    def verify_all(self):
        """Verifica lo stato di archiviazione di tutte le app."""
        config_path = self._config_path()
        # Reset righe
        for item in self.tree.get_children():
            self.tree.delete(item)

        n_obsolete = 0
        n_ok = 0
        n_error = 0

        for cfg in self._configs:
            app_name = cfg.get("app_name", "?")
            version = cfg.get("version_detected") or "—"

            needs, reason, _ = check_needs_archive(cfg, config_path)
            latest = find_latest_archive(config_path, app_name)
            zip_info = latest.name if latest else "—"

            if "mancanti" in reason.lower():
                tag = "error"
                n_error += 1
            elif needs:
                tag = "rebuild"
                n_obsolete += 1
            else:
                tag = "ok"
                n_ok += 1

            self.tree.insert("", "end",
                             values=(app_name, version, zip_info, reason),
                             tags=(tag,))

        msg = (f"[i] Verifica completata: {n_ok} allineate, "
               f"{n_obsolete} obsolete, {n_error} con errori\n")
        self.log_write(msg)
        self.arch_status_label.config(
            text=f"Allineate: {n_ok}  Obsolete: {n_obsolete}  Errori: {n_error}")

    # -----------------------------------------------------------------------
    # Archiviazione
    # -----------------------------------------------------------------------

    def _selected_configs(self) -> list[dict]:
        """Restituisce le cfg selezionate nella tree."""
        selected_iids = self.tree.selection()
        if not selected_iids:
            return []
        # Map app_name -> cfg per lookup
        idx_map = {self.tree.item(iid)["values"][0]: cfg
                   for iid, cfg in zip(self.tree.get_children(), self._configs)}
        result = []
        for iid in selected_iids:
            vals = self.tree.item(iid)["values"]
            if vals:
                name = vals[0]
                if name in idx_map:
                    result.append(idx_map[name])
        return result

    def _obsolete_configs(self) -> list[dict]:
        """Restituisce le cfg che necessitano archiviazione."""
        config_path = self._config_path()
        result = []
        for cfg in self._configs:
            needs, _, info = check_needs_archive(cfg, config_path)
            if needs and not info.get("missing"):
                result.append(cfg)
        return result

    def archive_obsolete(self):
        cfgs = self._obsolete_configs()
        if not cfgs:
            messagebox.showinfo("Archiviazione",
                                "Nessuna app obsoleta da archiviare.")
            return
        self._run_archive_batch(cfgs, "obsolete")

    def archive_selected(self):
        cfgs = self._selected_configs()
        if not cfgs:
            messagebox.showinfo("Archiviazione",
                                "Nessuna app selezionata.")
            return
        self._run_archive_batch(cfgs, "selezionate")

    def archive_all(self):
        if not self._configs:
            messagebox.showinfo("Archiviazione", "Nessuna app caricata.")
            return
        if not messagebox.askyesno(
                "Archivia tutte",
                f"Confermi l'archiviazione di TUTTE le {len(self._configs)} app?"):
            return
        self._run_archive_batch(self._configs, "tutte")

    def _run_archive_batch(self, cfgs: list[dict], label: str):
        """Esegue l'archiviazione in un thread separato."""
        if self._archive_running:
            messagebox.showwarning("Archiviazione",
                                   "Archiviazione gia' in corso.")
            return

        self._archive_running = True
        self._set_buttons_state(False)
        self.arch_progress.config(maximum=len(cfgs), value=0)

        # Reset stato di sessione per il log su file. Lo facciamo qui invece
        # che in __init__ o clear_log per fare in modo che il log su file
        # contenga SOLO l'attivita' di questa sessione (e non residui da
        # batch precedenti rimasti nel buffer).
        self._session_log_buffer = []
        self._session_included = {}
        self._session_excluded = {}

        self.log_write(f"\n=== Archiviazione {label}: {len(cfgs)} app ===\n")

        def worker():
            config_path = self._config_path()
            n_ok = 0
            n_fail = 0
            for i, cfg in enumerate(cfgs, 1):
                app_name = cfg.get("app_name", "?")
                self.log_write(f"\n[{i}/{len(cfgs)}] {app_name}\n")
                self.arch_status_label.config(text=f"Archivio: {app_name}...")

                # Pre-raccolta per il log: cosa entrera' nello zip e cosa
                # verra' filtrato. Non e' una doppia scansione costosa,
                # collect_archive_files e' la stessa che fa anche
                # create_archive_zip subito dopo (cache filesystem amica).
                try:
                    files_info = collect_archive_files(cfg, config_path)
                    self._session_included[app_name] = list(files_info.get("app_files", []))
                    self._session_excluded[app_name] = list(files_info.get("excluded", []))
                except Exception:
                    # se la pre-raccolta fallisce, andiamo avanti senza dettaglio
                    pass

                success, msg = create_archive_zip(cfg, config_path, self.log_write)
                if success:
                    self.log_write(f"   ✓ {msg}\n")
                    n_ok += 1
                else:
                    self.log_write(f"   ✗ {msg}\n")
                    n_fail += 1

                self.arch_progress.config(value=i)
                self.app.update_idletasks()

            self.log_write(f"\n=== Completato: {n_ok} OK, {n_fail} errori ===\n")

            # Scrittura log su file. Tutto questo blocco e' protetto da un
            # try/except diagnostico perche' tocca anche widget Tk (status
            # label, log_write) da un thread non-UI e qualunque eccezione
            # silenziosa farebbe morire il thread senza segnali. Se c'e' un
            # problema, lo vediamo nella GUI E sul terminale di lancio.
            try:
                log_path = write_archive_session_log(
                    log_buffer=self._session_log_buffer,
                    summary={"n_ok": n_ok, "n_fail": n_fail, "label": label},
                    included_per_app=self._session_included,
                    excluded_per_app=self._session_excluded,
                )
                if log_path is not None:
                    self.log_write(f"📄 Log sessione salvato: {log_path}\n")
                else:
                    self.log_write("⚠ Impossibile salvare il log su file "
                                   "(vedi traceback nel terminale di lancio)\n")
            except Exception as e:
                import traceback
                self.log_write(f"\n⚠ Errore inatteso nella scrittura log: "
                               f"{type(e).__name__}: {e}\n")
                self.log_write(traceback.format_exc() + "\n")

            self.arch_status_label.config(
                text=f"Completato: {n_ok} OK, {n_fail} errori")
            self._archive_running = False
            self._set_buttons_state(True)
            # Aggiorna la lista per mostrare i nuovi zip
            self.app.after(0, self.verify_all)

        threading.Thread(target=worker, daemon=True).start()

    def _set_buttons_state(self, enabled: bool):
        state = "normal" if enabled else "disabled"
        self.btn_arch_obsolete.config(state=state)
        self.btn_arch_selected.config(state=state)
        self.btn_arch_all.config(state=state)

    # -----------------------------------------------------------------------
    # Diagnostica disallineamenti
    # -----------------------------------------------------------------------

    def show_misalignments(self):
        """Apre una finestra con il report dei disallineamenti."""
        base = self._base_path()
        cfg_path = self._config_path()
        report = diagnose_config_misalignments(base, cfg_path)

        win = tk.Toplevel(self.app)
        win.title("Disallineamenti app ↔ _Config")
        win.geometry("860x640")
        win.minsize(640, 400)  # garantisce minimo utile
        # ESC chiude la finestra (coerente con tutte le altre Toplevel del tab)
        win.bind("<Escape>", lambda e: win.destroy())

        # Top: descrizione
        header = ttk.Frame(win, padding=10)
        header.pack(fill="x")
        ttk.Label(header,
                  text="Anomalie rilevate tra cartelle app e cartelle _Config:",
                  font=("", 12, "bold")).pack(anchor="w")
        ttk.Label(header, foreground="#666",
                  text=f"App in: {base}\nConfig in: {cfg_path}").pack(anchor="w", pady=(4, 0))
        ttk.Label(header, foreground="#444", wraplength=720,
                  text=("Criterio: una cartella app si considera collegata a "
                        "_Config/<X>/ se ha lo stesso nome, oppure se i suoi "
                        "script contengono APP_NAME = \"X\", oppure se il campo "
                        "\"script\" del build.json di X punta a un file dentro "
                        "la cartella app."),
                  font=("", 10, "italic")).pack(anchor="w", pady=(4, 0))

        # Bottone chiudi (PACKATO PER PRIMO in basso, cosi' resta sempre visibile
        # anche se il body sopra cresce molto)
        btn_frame = ttk.Frame(win, padding=(10, 6, 10, 10))
        btn_frame.pack(side="bottom", fill="x")
        ttk.Button(btn_frame, text="Chiudi", command=win.destroy).pack(side="right")

        # Body con testo (riempie lo spazio rimanente sopra al bottone)
        body = ttk.Frame(win, padding=(10, 0, 10, 6))
        body.pack(fill="both", expand=True)

        txt = tk.Text(body, wrap="word", font=("Menlo", 11))
        scroll = ttk.Scrollbar(body, orient="vertical", command=txt.yview)
        txt.configure(yscrollcommand=scroll.set)
        scroll.pack(side="right", fill="y")
        txt.pack(side="left", fill="both", expand=True)

        # Tag per evidenziazione
        txt.tag_configure("section", font=("", 12, "bold"), foreground="#003366",
                          spacing1=8, spacing3=4)
        txt.tag_configure("ok", foreground=COLOR_OK)
        txt.tag_configure("warn", foreground=COLOR_REBUILD)
        txt.tag_configure("err", foreground=COLOR_ERROR)
        txt.tag_configure("suggestion", foreground="#555", font=("Menlo", 10, "italic"))

        total = (len(report["missing_config"])
                 + len(report["orphan_config"])
                 + len(report["app_name_mismatch"]))
        n_matched = len(report.get("matched", []))

        if total == 0:
            txt.insert("end", "✓ Nessun disallineamento rilevato.\n", "ok")
            txt.insert("end", f"\n{n_matched} cartelle app correttamente "
                              "collegate alle relative _Config.\n")
        else:
            txt.insert("end",
                       f"⚠ {total} anomalie rilevate  "
                       f"({n_matched} collegamenti validi)\n\n", "warn")

        # Sezione 1: cartelle app senza _Config
        if report["missing_config"]:
            txt.insert("end", "Cartelle app senza _Config/<NomeApp>/ corrispondente:\n",
                       "section")
            for app_dir, suggested in report["missing_config"]:
                txt.insert("end", f"  • {app_dir}\n", "err")
                if suggested:
                    txt.insert("end",
                               f"      → forse intendi: _Config/{suggested}/  "
                               f"(rinomina la cartella app o quella _Config)\n",
                               "suggestion")
                else:
                    txt.insert("end",
                               f"      → nessun suggerimento (crea _Config/{app_dir}/)\n",
                               "suggestion")
            txt.insert("end", "\n")

        # Sezione 2: _Config orfane
        if report["orphan_config"]:
            txt.insert("end", "Cartelle _Config orfane (nessuna app corrispondente):\n",
                       "section")
            for cfg_dir, suggested in report["orphan_config"]:
                txt.insert("end", f"  • _Config/{cfg_dir}/\n", "err")
                if suggested:
                    txt.insert("end",
                               f"      → forse appartiene a: {suggested}/  "
                               f"(rinomina _Config o la cartella app)\n",
                               "suggestion")
                else:
                    txt.insert("end",
                               f"      → nessun suggerimento (app obsoleta da rimuovere?)\n",
                               "suggestion")
            txt.insert("end", "\n")

        # Sezione 3: app_name mismatch
        if report["app_name_mismatch"]:
            txt.insert("end",
                       "build.json con app_name diverso dal nome cartella _Config:\n",
                       "section")
            for cfg_dir, app_name in report["app_name_mismatch"]:
                txt.insert("end", f"  • _Config/{cfg_dir}/build.json\n", "err")
                txt.insert("end",
                           f"      app_name nel JSON: \"{app_name}\"  ≠  "
                           f"cartella: \"{cfg_dir}\"\n",
                           "suggestion")
            txt.insert("end", "\n")

        # Sezione 4: collegamenti riconosciuti (informativa)
        matched = report.get("matched", [])
        if matched:
            txt.insert("end",
                       f"Collegamenti riconosciuti ({len(matched)}):\n",
                       "section")
            for app_dir_name, cfg_dir, method in sorted(matched):
                if method == "nome_diretto":
                    txt.insert("end",
                               f"  ✓ {app_dir_name}/  ↔  _Config/{cfg_dir}/\n",
                               "ok")
                else:
                    # Collegamento via APP_NAME
                    txt.insert("end",
                               f"  ✓ {app_dir_name}/  ↔  _Config/{cfg_dir}/   "
                               f"(via {method})\n",
                               "ok")
            txt.insert("end", "\n")

        txt.config(state="disabled")

    # -----------------------------------------------------------------------
    # Dry-run: anteprima di cosa entrera' nello zip senza creare niente
    # -----------------------------------------------------------------------

    def dry_run_selected(self):
        """
        Apre una finestra che mostra, per le app selezionate, cosa entrerebbe
        nello zip se si lanciasse l'archiviazione adesso. Non crea nulla:
        e' una simulazione che usa la stessa funzione collect_archive_files()
        usata dal vero create_archive_zip().

        Output diviso per sezioni:
          - File dalla cartella app (Inclusi + Esclusi dai filtri copy/bak)
          - File dalla configurazione (_Config/<app>/)
        cosi' e' subito chiaro da quale fonte arriva ciascun file.

        Se nessuna app e' selezionata, mostra tutte quelle in lista.
        """
        sel = self.tree.selection()
        if sel:
            # Uso l'helper _selected_configs() che mappa iid -> cfg via app_name.
            # Gli iid del Treeview sono auto-generati ("I001", "I002", ...)
            # NON convertibili con int(), quindi non posso indicizzare _configs
            # direttamente. _selected_configs gestisce gia' correttamente questo.
            cfgs_to_show = self._selected_configs()
            if not cfgs_to_show:
                messagebox.showinfo("Dry-run",
                                    "Selezione non valida (nessuna app risolta).")
                return
        else:
            if not self._configs:
                messagebox.showinfo("Dry-run", "Nessuna app caricata.")
                return
            if not messagebox.askyesno(
                    "Dry-run",
                    f"Nessuna app selezionata.\n\n"
                    f"Vuoi fare il dry-run di TUTTE le {len(self._configs)} app?"):
                return
            cfgs_to_show = self._configs

        config_path = self._config_path()

        win = tk.Toplevel(self.app)
        win.title(f"Dry-run archiviazione ({len(cfgs_to_show)} app)")
        win.geometry("900x640")
        win.minsize(640, 400)
        win.bind("<Escape>", lambda e: win.destroy())

        # Header
        header = ttk.Frame(win, padding=10)
        header.pack(fill="x")
        ttk.Label(header,
                  text="🔬 Dry-run: simulazione archiviazione",
                  font=("", 13, "bold")).pack(anchor="w")
        ttk.Label(header, foreground="#666", wraplength=820,
                  text=("Questa e' una SIMULAZIONE: non viene creato alcuno "
                        "zip. Mostra cosa entrerebbe e cosa verrebbe scartato "
                        "dai filtri se si lanciasse l'archiviazione adesso.")
                  ).pack(anchor="w", pady=(4, 0))

        # Bottone chiudi in basso (sempre visibile)
        btn_frame = ttk.Frame(win, padding=(10, 6, 10, 10))
        btn_frame.pack(side="bottom", fill="x")
        ttk.Button(btn_frame, text="Chiudi (ESC)",
                   command=win.destroy).pack(side="right")

        # Body con testo scrollabile
        body = ttk.Frame(win, padding=(10, 0, 10, 6))
        body.pack(fill="both", expand=True)

        txt = tk.Text(body, wrap="none", font=("Menlo", 11), padx=10, pady=10)
        vsb = ttk.Scrollbar(body, orient="vertical",   command=txt.yview)
        hsb = ttk.Scrollbar(body, orient="horizontal", command=txt.xview)
        txt.configure(yscrollcommand=vsb.set, xscrollcommand=hsb.set)
        vsb.pack(side="right",  fill="y")
        hsb.pack(side="bottom", fill="x")
        txt.pack(fill="both", expand=True)

        # Tag stile
        txt.tag_configure("app_header",
                          font=("", 13, "bold"), foreground="#003366",
                          spacing1=10, spacing3=4)
        txt.tag_configure("section_app",
                          font=("Menlo", 11, "bold"), foreground="#444",
                          spacing1=6, spacing3=2)
        txt.tag_configure("section_cfg",
                          font=("Menlo", 11, "bold"), foreground="#444",
                          spacing1=6, spacing3=2)
        txt.tag_configure("section_excl",
                          font=("Menlo", 11, "bold"), foreground="#8B6914",
                          spacing1=4, spacing3=2)
        txt.tag_configure("ok", foreground=COLOR_OK)
        txt.tag_configure("missing", foreground=COLOR_ERROR)
        txt.tag_configure("excluded", foreground="#A0522D")
        txt.tag_configure("excluded_detail",
                          foreground="#B8860B", font=("Menlo", 10))
        txt.tag_configure("muted",
                          foreground="#888", font=("Menlo", 10))
        txt.tag_configure("total", font=("", 10, "bold"), foreground="#222")

        # Totali globali per il riepilogo finale
        grand_inc = 0
        grand_exc = 0
        grand_missing_apps = 0

        for cfg in cfgs_to_show:
            app_name = cfg.get("app_name", "?")
            txt.insert("end", f"\n{'='*70}\n", "muted")
            txt.insert("end", f"  {app_name}\n", "app_header")
            txt.insert("end", f"{'='*70}\n", "muted")

            try:
                files_info = collect_archive_files(cfg, config_path)
            except Exception as e:
                txt.insert("end", f"  ⚠ Errore: {e}\n", "missing")
                grand_missing_apps += 1
                continue

            # Se file mancanti critici (script non trovato), evidenzia subito
            missing = files_info.get("missing", [])
            if missing:
                txt.insert("end", f"  ⚠ File critici mancanti:\n", "missing")
                for m in missing:
                    txt.insert("end", f"      {m}\n", "missing")
                grand_missing_apps += 1
                # Continua comunque la visualizzazione di cio' che si e'
                # riusciti a raccogliere (potrebbe essere parziale)

            app_root = files_info.get("app_root")
            app_files = files_info.get("app_files", [])
            cfg_files = files_info.get("config_files", [])
            cfg_root = files_info.get("config_root")
            excluded = files_info.get("excluded", [])

            # Sezione 1: file dalla cartella app
            if app_root:
                rel_app_root = self._relpath_friendly(app_root)
                txt.insert("end",
                           f"\n  📁 Cartella app   ({rel_app_root})\n",
                           "section_app")
                if app_files:
                    txt.insert("end",
                               f"     Inclusi ({len(app_files)}):\n",
                               "muted")
                    for f in app_files:
                        try:
                            rel = f.relative_to(app_root)
                        except Exception:
                            rel = f.name
                        txt.insert("end", f"        ✓ {rel}\n", "ok")
                else:
                    txt.insert("end",
                               "     (nessun file rilevato)\n", "muted")

                # Esclusi dai filtri (relativi alla cartella app)
                if excluded:
                    txt.insert("end",
                               f"     Esclusi dai filtri ({len(excluded)}):\n",
                               "section_excl")
                    for f, motivo in excluded:
                        try:
                            rel = f.relative_to(app_root)
                        except Exception:
                            rel = f.name
                        txt.insert("end", f"        ⊘ {rel}\n", "excluded")
                        txt.insert("end",
                                   f"            motivo: {motivo}\n",
                                   "excluded_detail")

            # Sezione 2: file da _Config/<app>/
            if cfg_root:
                rel_cfg_root = self._relpath_friendly(cfg_root)
                txt.insert("end",
                           f"\n  📁 Configurazione   ({rel_cfg_root})\n",
                           "section_cfg")
                if cfg_files:
                    txt.insert("end",
                               f"     Inclusi ({len(cfg_files)}):\n",
                               "muted")
                    for f in cfg_files:
                        try:
                            rel = f.relative_to(cfg_root)
                        except Exception:
                            rel = f.name
                        txt.insert("end", f"        ✓ {rel}\n", "ok")
                else:
                    txt.insert("end",
                               "     (cartella vuota)\n", "muted")

            # Riepilogo per app
            n_inc = len(app_files) + len(cfg_files)
            n_exc = len(excluded)
            grand_inc += n_inc
            grand_exc += n_exc
            txt.insert("end",
                       f"\n  ─ Totale per {app_name}: "
                       f"{n_inc} inclusi, {n_exc} esclusi\n",
                       "total")

        # Riepilogo globale in cima al testo (lo inseriamo dopo aver scritto
        # tutto, cosi' i totali sono noti)
        summary = (
            f"\n{len(cfgs_to_show)} app simulate  •  "
            f"{grand_inc} file totali inclusi  •  "
            f"{grand_exc} esclusi dai filtri"
        )
        if grand_missing_apps:
            summary += f"  •  ⚠ {grand_missing_apps} app con file critici mancanti"
        summary += "\n"
        txt.insert("1.0", summary, "total")

        txt.config(state="disabled")

    def _relpath_friendly(self, p: Path) -> str:
        """
        Restituisce un path leggibile: se p e' dentro Python/ lo mostra
        relativo a Python/, altrimenti lo restituisce assoluto con "~"
        al posto di HOME quando possibile.
        """
        try:
            python_root = self._config_path().parent  # Python/
            return str(p.relative_to(python_root.parent))  # Documenti_IRC/Python/...
        except Exception:
            pass
        try:
            home = Path.home()
            return "~/" + str(p.relative_to(home))
        except Exception:
            return str(p)

    # -----------------------------------------------------------------------
    # Visualizza ZIP: tree del contenuto dell'ultimo zip archiviato
    # -----------------------------------------------------------------------

    def view_zip_selected(self):
        """
        Apre una finestra con il contenuto dello zip piu' recente dell'app
        selezionata (in _Archivio/, non in _Backup/). Mostra una vista ad
        albero con icone per estensione, dimensioni e date.
        """
        import zipfile

        sel = self.tree.selection()
        if not sel:
            messagebox.showinfo("Visualizza ZIP",
                                "Seleziona un'app dalla lista.")
            return
        if len(sel) > 1:
            messagebox.showinfo("Visualizza ZIP",
                                "Seleziona una sola app alla volta.")
            return

        # Usa l'helper _selected_configs() — gli iid Tk sono "I001", "I002",
        # non convertibili a int. _selected_configs li risolve via app_name.
        cfgs = self._selected_configs()
        if not cfgs:
            messagebox.showinfo("Visualizza ZIP",
                                "Selezione non valida (nessuna app risolta).")
            return
        cfg = cfgs[0]
        app_name = cfg.get("app_name", "?")
        config_path = self._config_path()

        zip_path = find_latest_archive(config_path, app_name)
        if zip_path is None or not zip_path.exists():
            messagebox.showinfo(
                "Visualizza ZIP",
                f"Nessuno zip trovato per '{app_name}' in _Archivio/.\n"
                "Archivia prima l'app.")
            return

        # Legge il contenuto dello zip
        try:
            with zipfile.ZipFile(zip_path, "r") as zf:
                entries = zf.infolist()
        except Exception as e:
            messagebox.showerror("Errore lettura ZIP", str(e))
            return

        # Finestra
        win = tk.Toplevel(self.app)
        win.title(f"Contenuto ZIP — {zip_path.name}")
        win.geometry("760x560")
        win.minsize(520, 360)
        win.bind("<Escape>", lambda e: win.destroy())

        # Header con info zip
        hdr = ttk.Frame(win, padding=(10, 8, 10, 4))
        hdr.pack(fill="x")
        try:
            zip_size_b = zip_path.stat().st_size
            if zip_size_b < 1024:
                zip_size = f"{zip_size_b} B"
            elif zip_size_b < 1024 * 1024:
                zip_size = f"{zip_size_b // 1024} KB"
            else:
                zip_size = f"{zip_size_b / (1024*1024):.1f} MB"
        except Exception:
            zip_size = "—"
        n_files = sum(1 for e in entries if not e.filename.endswith("/"))
        ttk.Label(hdr, text=zip_path.name,
                  font=("", 14, "bold")).pack(anchor="w")
        ttk.Label(hdr, text=f"{zip_size}  •  {n_files} file  •  "
                            f"{self._relpath_friendly(zip_path)}",
                  foreground="#555").pack(anchor="w")

        ttk.Separator(win, orient="horizontal").pack(fill="x", padx=10, pady=(4, 0))

        # Bottoni footer (PRIMA del body, cosi' restano sempre visibili)
        foot = ttk.Frame(win, padding=(10, 4, 10, 8))
        foot.pack(side="bottom", fill="x")
        ttk.Button(foot, text="Apri in Finder",
                   command=lambda: subprocess.run(
                       ["open", "-R", str(zip_path)], check=False)
                   ).pack(side="left")
        ttk.Button(foot, text="Chiudi (ESC)",
                   command=win.destroy).pack(side="right")

        # Frame treeview
        tf = ttk.Frame(win, padding=(10, 6, 10, 6))
        tf.pack(fill="both", expand=True)
        tf.rowconfigure(0, weight=1)
        tf.columnconfigure(0, weight=1)

        tv = ttk.Treeview(tf, columns=("size", "date"), show="tree headings")
        tv.heading("#0",   text="Nome")
        tv.heading("size", text="Dimensione")
        tv.heading("date", text="Data")
        tv.column("#0",   width=420, minwidth=200)
        tv.column("size", width=100, minwidth=70,  anchor="e")
        tv.column("date", width=140, minwidth=100, anchor="center")

        vsb = ttk.Scrollbar(tf, orient="vertical",   command=tv.yview)
        hsb = ttk.Scrollbar(tf, orient="horizontal", command=tv.xview)
        tv.configure(yscrollcommand=vsb.set, xscrollcommand=hsb.set)
        tv.grid(row=0, column=0, sticky="nsew")
        vsb.grid(row=0, column=1, sticky="ns")
        hsb.grid(row=1, column=0, sticky="ew")

        # --- Costruisce l'albero ---
        node_map: dict[str, str] = {}  # path stringa -> iid Treeview

        def _fmt_size(b: int) -> str:
            if b == 0:
                return "—"
            if b < 1024:
                return f"{b} B"
            if b < 1024 * 1024:
                return f"{b // 1024} KB"
            return f"{b / (1024*1024):.1f} MB"

        def _fmt_date(info) -> str:
            try:
                y, mo, d, h, mi, _ = info.date_time
                return f"{y}-{mo:02d}-{d:02d}  {h:02d}:{mi:02d}"
            except Exception:
                return "—"

        def _ensure_dir(parts: list[str]) -> str:
            """Assicura che esista un nodo per ogni cartella del path.
            Restituisce l'iid del nodo foglia (l'ultima cartella)."""
            iid_acc = ""
            for depth, part in enumerate(parts):
                key = "/".join(parts[: depth + 1])
                if key not in node_map:
                    parent_iid = node_map.get("/".join(parts[:depth]), "")
                    new_iid = tv.insert(
                        parent_iid, "end",
                        text=f"📁  {part}",
                        values=("", ""),
                        open=True,
                    )
                    node_map[key] = new_iid
                iid_acc = node_map[key]
            return iid_acc

        EXT_ICONS = {
            ".py":      "🐍",
            ".json":    "{}",
            ".md":      "📝",
            ".png":     "🖼",
            ".icns":    "🖼",
            ".jpg":     "🖼",
            ".jpeg":    "🖼",
            ".zip":     "📦",
            ".txt":     "📄",
            ".log":     "📄",
            ".yaml":    "⚙️",
            ".yml":     "⚙️",
            ".toml":    "⚙️",
            ".cfg":     "⚙️",
            ".ini":     "⚙️",
            ".plist":   "⚙️",
            ".command": "⚡",
            ".sh":      "⚡",
        }

        # Ordina: prima cartelle (per profondita'), poi file
        sorted_entries = sorted(
            entries, key=lambda e: (e.filename.count("/"), e.filename))

        for info in sorted_entries:
            fname = info.filename
            # Salta cartelle pure (finiscono con /)
            if fname.endswith("/"):
                parts = [p for p in fname.rstrip("/").split("/") if p]
                _ensure_dir(parts)
                continue

            parts = [p for p in fname.split("/") if p]
            if not parts:
                continue

            if len(parts) > 1:
                parent_iid = _ensure_dir(parts[:-1])
            else:
                parent_iid = ""

            file_name = parts[-1]
            ext = Path(file_name).suffix.lower()
            file_icon = EXT_ICONS.get(ext, "📄")

            tv.insert(
                parent_iid, "end",
                text=f"{file_icon}  {file_name}",
                values=(_fmt_size(info.file_size), _fmt_date(info)),
            )
