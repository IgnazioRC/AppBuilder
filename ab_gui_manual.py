#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
ab_gui_manual.py - Tab Manuale

Permette di selezionare uno script .py dalla cartella sorgente, configurare
nome app, icona, hidden imports, opzione windowed e clean-after, e poi
eseguire un build singolo via execute_build(). Al termine salva (o
aggiorna) il build.json corrispondente in _Config/<app_name>/.

Caratteristiche:
- Rileva automaticamente APP_NAME dallo script, con CamelCase fallback
- Pre-popola i campi caricando un eventuale build.json esistente
- Auto-detect del Python builder via wrapper o .venv locale
- Build in thread separato per non bloccare la UI
"""

import re
import subprocess
import threading
import tkinter as tk
from tkinter import ttk, filedialog, messagebox
from pathlib import Path

from path_widgets import PathVar, PathEntry

from ab_utils import (
    find_icons_dir,
    find_local_imports,
    extract_app_name,
    max_mtime,
)
from ab_config import (
    build_json_path,
    load_build_json,
    save_build_json,
)
from ab_build import execute_build
from ab_log import (
    LOG_DIR,
    write_build_log,
)


class ManualTab(ttk.Frame):
    def __init__(self, parent, app):
        super().__init__(parent)
        self.app = app

        self.icon_path = PathVar(value="")
        self.app_name = tk.StringVar(value="")
        self.windowed = tk.BooleanVar(value=True)
        self.auto_name = tk.BooleanVar(value=True)
        self.clean_after = tk.BooleanVar(value=False)
        self.hidden_imports = tk.StringVar(value="")
        self.app_name_user_edited = False
        self.selected_script = None
        self.detected_modules = []
        self.build_dir = None

        # Buffer del log della build corrente. Si resetta a ogni click su
        # "Esegui build" cosi' ogni build manuale produce un file di log
        # pulito in ~/Documents/log/AppBuilder_build_<ts>_<NomeApp>.log
        self._session_log_buffer: list[str] = []

        self._create_widgets()
        self.refresh_scripts()

    def _create_widgets(self):
        ttk.Label(self, text="Script disponibili:").pack(anchor="w")

        list_frame = ttk.Frame(self)
        list_frame.pack(fill="x", pady=(0, 10))

        list_scroll = ttk.Scrollbar(list_frame, orient="vertical")
        self.listbox = tk.Listbox(list_frame, height=6, yscrollcommand=list_scroll.set, exportselection=False)
        list_scroll.config(command=self.listbox.yview)
        self.listbox.pack(side="left", fill="x", expand=True)
        list_scroll.pack(side="right", fill="y")
        self.listbox.bind("<<ListboxSelect>>", self.on_select_script)

        self.modules_frame = ttk.LabelFrame(self, text="Moduli locali rilevati", padding=8)
        self.modules_frame.pack(fill="x", pady=(0, 10))
        self.modules_label = ttk.Label(self.modules_frame, text="Seleziona uno script per analizzare gli import...")
        self.modules_label.pack(anchor="w")

        form = ttk.LabelFrame(self, text="Opzioni build", padding=10)
        form.pack(fill="x", pady=(0, 10))

        ttk.Label(form, text="Nome App (Finder):").grid(row=0, column=0, sticky="w")
        self.app_entry = ttk.Entry(form, textvariable=self.app_name, width=40)
        self.app_entry.grid(row=0, column=1, sticky="w", padx=8)
        self.app_entry.bind("<KeyRelease>", self._on_app_name_edited)
        ttk.Checkbutton(form, text="Auto da script", variable=self.auto_name).grid(row=0, column=2, sticky="w")

        ttk.Label(form, text="Icona (.icns):").grid(row=1, column=0, sticky="w", pady=(6, 0))
        PathEntry(form, pathvar=self.icon_path).grid(row=1, column=1, sticky="we", padx=8, pady=(6, 0))
        ttk.Button(form, text="Scegli...", command=self.pick_icon).grid(row=1, column=2, sticky="w", pady=(6, 0))

        ttk.Label(form, text="Hidden imports:").grid(row=2, column=0, sticky="w", pady=(6, 0))
        ttk.Entry(form, textvariable=self.hidden_imports, width=55).grid(row=2, column=1, sticky="w", padx=8, pady=(6, 0))
        ttk.Label(form, text="(separati da virgola)", foreground="gray").grid(row=2, column=2, sticky="w", pady=(6, 0))

        checks_frame = ttk.Frame(form)
        checks_frame.grid(row=3, column=0, columnspan=3, sticky="w", pady=(10, 0))
        ttk.Checkbutton(checks_frame, text="Windowed (GUI, senza Terminale)", variable=self.windowed).pack(side="left")
        ttk.Checkbutton(checks_frame, text="Pulisci file temporanei dopo build", variable=self.clean_after).pack(side="left", padx=(20, 0))

        btns = ttk.Frame(self)
        btns.pack(fill="x", pady=(0, 10))
        self.btn_build = ttk.Button(btns, text="Esegui build", command=self.build_clicked)
        self.btn_build.pack(side="left")
        self.progress = ttk.Progressbar(btns, mode='indeterminate', length=150)
        self.progress.pack(side="left", padx=15)
        ttk.Button(btns, text="📂 Apri cartella log",
                   command=self.open_log_folder).pack(side="left", padx=(0, 6))
        ttk.Button(btns, text="🧹 Pulisci log", command=self.clear_log).pack(side="left", padx=(0, 10))
        ttk.Button(btns, text="Esci", command=self.app.destroy).pack(side="left")

        ttk.Label(self, text="Log:").pack(anchor="w")

        log_frame = ttk.Frame(self)
        log_frame.pack(fill="both", expand=True)

        log_scroll_y = ttk.Scrollbar(log_frame, orient="vertical")
        log_scroll_x = ttk.Scrollbar(log_frame, orient="horizontal")
        self.log = tk.Text(
            log_frame,
            height=12,
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
        # Bufferizza per il log su file (sara' scritto a fine build)
        self._session_log_buffer.append(s)
        self.app.update_idletasks()

    def clear_log(self):
        self.log.delete("1.0", "end")

    def open_log_folder(self):
        """Apre ~/Documents/log/ nel Finder. La cartella viene creata se
        non esiste, cosi' il click funziona sempre."""
        try:
            LOG_DIR.mkdir(parents=True, exist_ok=True)
            subprocess.run(["open", str(LOG_DIR)], check=False)
        except Exception as e:
            messagebox.showwarning(
                "Apri cartella log",
                f"Impossibile aprire {LOG_DIR}:\n{e}")

    def reset_for_new_base(self):
        self.app_name.set("")
        self.app_name_user_edited = False
        self.auto_name.set(True)
        self.icon_path.set("")
        self.hidden_imports.set("")

    def refresh_scripts(self):
        self.listbox.delete(0, "end")
        base = Path(self.app._base_var.get()).expanduser()
        if not base.exists():
            self.log_write(f"[!] Cartella non esistente: {base}\n")
            return
        scripts = sorted([p.name for p in base.glob("*.py") if p.is_file()])
        for s in scripts:
            self.listbox.insert("end", s)
        if scripts:
            self.listbox.selection_set(0)
            self.on_select_script()

    def _on_app_name_edited(self, event=None):
        self.app_name_user_edited = True
        if self.auto_name.get():
            self.auto_name.set(False)

    def on_select_script(self, event=None):
        sel = self.listbox.curselection()
        if not sel:
            return
        script_name = self.listbox.get(sel[0])
        self.selected_script = script_name

        base = Path(self.app._base_var.get()).expanduser()
        script_path = base / script_name

        if self.auto_name.get() and not self.app_name_user_edited:
            # Prima controlla se lo script dichiara APP_NAME esplicitamente
            app_name_from_script = extract_app_name(script_path)
            if app_name_from_script:
                new_name = app_name_from_script
            else:
                stem = Path(script_name).stem
                stem = re.sub(r"[_ -]main$", "", stem, flags=re.IGNORECASE)
                if stem.lower().startswith("appbuilder"):
                    new_name = "AppBuilder"
                else:
                    parts = re.split(r"[_ -]+", stem)
                    def _cap(p):
                        if not p: return ""
                        if any(c.isupper() for c in p[1:]):
                            return p[0].upper() + p[1:]
                        return p.capitalize()
                    new_name = "".join(_cap(p) for p in parts if p)
            self.app_name.set(new_name)
            self.app_name_user_edited = False

        # Prova a caricare build.json esistente per pre-popolare i campi.
        # Cerca prima con il nome UI corrente, poi con APP_NAME dallo script
        # (gestisce il caso in cui il campo UI non sia ancora aggiornato).
        config_path = Path(self.app._config_var.get()).expanduser()
        existing_cfg = load_build_json(build_json_path(config_path, self.app_name.get()))
        if not existing_cfg:
            # Fallback: prova con APP_NAME dallo script (se non già usato sopra)
            app_name_from_script = extract_app_name(script_path)
            if app_name_from_script and app_name_from_script != self.app_name.get():
                existing_cfg = load_build_json(build_json_path(config_path, app_name_from_script))
                if existing_cfg:
                    # Allinea il campo UI al nome trovato
                    self.app_name.set(app_name_from_script)
                    self.app_name_user_edited = False
        if existing_cfg:
            self._load_from_config(existing_cfg, base)

        # Auto-detect python_builder
        cand_wrapper = script_path.parent / "python_builder"
        cand_venv_py = script_path.parent / ".venv" / "bin" / "python3"

        def _has_pyinstaller(py):
            try:
                r = subprocess.run([str(py), "-c", "import PyInstaller"],
                                   capture_output=True, timeout=6)
                return r.returncode == 0
            except Exception:
                return False

        if cand_wrapper.exists() and _has_pyinstaller(cand_wrapper):
            self.app.python_builder.set(str(cand_wrapper))
        elif cand_venv_py.exists() and _has_pyinstaller(cand_venv_py):
            self.app.python_builder.set(str(cand_venv_py))

        self.detected_modules = find_local_imports(script_path)
        if self.detected_modules:
            modules_text = ", ".join(self.detected_modules)
            self.modules_label.config(
                text=f"Trovati {len(self.detected_modules)} moduli locali: {modules_text}",
                foreground="darkgreen"
            )
        else:
            self.modules_label.config(
                text="Nessun modulo locale rilevato (script autonomo)",
                foreground="gray"
            )

    def _load_from_config(self, cfg: dict, base_path: Path):
        """Pre-popola i campi dalla configurazione salvata."""
        # Icona: ricostruisce path assoluto da relativo a base_path.parent
        icon_rel = cfg.get("icon", "")
        if icon_rel:
            icon_abs = base_path.parent / icon_rel
            if icon_abs.exists():
                self.icon_path.set(str(icon_abs))

        hidden = cfg.get("hidden_imports", [])
        if hidden:
            self.hidden_imports.set(", ".join(hidden))

        if "windowed" in cfg:
            self.windowed.set(cfg["windowed"])

    def pick_icon(self):
        base = Path(self.app._base_var.get()).expanduser()
        guessed = find_icons_dir(base)
        initial_dir = str(guessed) if guessed else (
            str(self.app.icon_base_path) if self.app.icon_base_path.exists() else str(base)
        )
        f = filedialog.askopenfilename(
            title="Scegli icona (.icns o .png)",
            initialdir=initial_dir,
            filetypes=[("Icone macOS", "*.icns"), ("Immagini", "*.png"), ("Tutti i file", "*.*")]
        )
        if f:
            self.icon_path.set(f)

    def build_clicked(self):
        sel = self.listbox.curselection()
        if sel:
            script_name = self.listbox.get(sel[0])
            self.selected_script = script_name
        else:
            script_name = self.selected_script

        if not script_name:
            messagebox.showerror("Errore", "Seleziona uno script dalla lista.")
            return
        app_name = self.app_name.get().strip()
        if not app_name:
            messagebox.showerror("Errore", "Inserisci un nome per l'app.")
            return

        icon = self.icon_path.get().strip()
        if icon and not Path(icon).exists():
            messagebox.showerror("Errore", f"File icona non trovato:\n{icon}")
            return

        if self.detected_modules:
            msg = (
                f"Lo script importa {len(self.detected_modules)} moduli locali:\n"
                f"{', '.join(self.detected_modules)}\n\n"
                "PyInstaller dovrebbe includerli automaticamente.\n"
                "Procedere con il build?"
            )
            if not messagebox.askyesno("Moduli locali rilevati", msg):
                return

        # Reset del buffer log: cosi' il file di log finale contiene SOLO
        # questa build (non residui di build precedenti rimasti in memoria).
        self._session_log_buffer = []

        self.btn_build.config(state="disabled")
        self.progress.start(10)
        t = threading.Thread(target=self._build_thread, args=(script_name, app_name), daemon=True)
        t.start()

    def _build_thread(self, script_name: str, app_name: str):
        # Stato per la scrittura del log finale (popolato in success/error)
        build_success = False
        build_details: dict = {"app_name": app_name}
        try:
            base = Path(self.app._base_var.get()).expanduser()
            script_path = base / script_name
            if not script_path.exists():
                raise RuntimeError(f"Script non trovato: {script_path}")

            icon = self.icon_path.get().strip()
            hidden_list = [h.strip() for h in self.hidden_imports.get().split(",") if h.strip()]
            config_path = Path(self.app._config_var.get()).expanduser()
            _existing_cfg = load_build_json(build_json_path(config_path, app_name)) or {}
            _extra_modules = _existing_cfg.get("local_modules") or []
            source_mtime = max_mtime(script_path, find_local_imports(script_path))

            # Popola i dettagli per il log finale prima del build, cosi' anche
            # se il build fallisce abbiamo gia' qualche info nell'header.
            build_details.update({
                "script_path": str(script_path),
                "icon": icon,
                "windowed": self.windowed.get(),
                "hidden_imports": hidden_list,
            })

            target, builder_usato = execute_build(
                script_path=script_path,
                app_name=app_name,
                icon=icon,
                windowed=self.windowed.get(),
                hidden_imports=hidden_list,
                install_dir_str=self.app.install_dir.get(),
                python_builder_str=self.app.python_builder.get(),
                log_fn=self.log_write,
                base_path=base,
                clean_after=self.clean_after.get(),
                safe_install_fn=lambda t, n: self.app.safe_install_target(t, n, self.log_write),
                extra_modules=_extra_modules,
            )

            build_details["target"] = str(target)
            build_details["python_builder"] = builder_usato

            # --- Salva build.json in _Config/<app_name>/ ---
            # Usa builder_usato (risolto da execute_build) invece del campo UI,
            # cosi' il JSON riflette il Python effettivamente usato per il build.
            config_path = Path(self.app._config_var.get()).expanduser()
            json_path = save_build_json(
                config_path=config_path,
                base_path=base,
                script_path=script_path,
                app_name=app_name,
                icon=icon,
                windowed=self.windowed.get(),
                hidden_imports=hidden_list,
                install_dir=self.app.install_dir.get(),
                python_builder=builder_usato,
                source_mtime=source_mtime,
            )
            self.log_write(f"\n✓ build.json salvato in _Config/{app_name}/\n")

            # Estrai version_detected dal build.json appena salvato (per il log)
            saved_cfg = load_build_json(json_path)
            if saved_cfg:
                build_details["version_detected"] = saved_cfg.get("version_detected", "")

            build_success = True

            self.app.after(0, lambda: messagebox.showinfo(
                "Build completato",
                f"App '{app_name}' installata in:\n{target}\n\n"
                f"✓ build.json salvato in _Config/{app_name}/"
            ))

        except Exception as e:
            error_msg = str(e)
            self.log_write(f"\n[X] ERRORE: {error_msg}\n")
            self.app.after(0, lambda msg=error_msg: messagebox.showerror("Errore build", msg))
        finally:
            # Scrittura log su file. Protetto da try/except diagnostico: se
            # qualcosa va storto nella scrittura, segnaliamo a video ma non
            # blocchiamo la chiusura del build.
            try:
                log_path = write_build_log(
                    log_buffer=self._session_log_buffer,
                    app_name=app_name,
                    success=build_success,
                    details=build_details,
                )
                if log_path is not None:
                    self.log_write(f"\n📄 Log build salvato: {log_path}\n")
                else:
                    self.log_write("\n⚠ Impossibile salvare il log su file "
                                   "(vedi traceback nel terminale di lancio)\n")
            except Exception as e:
                import traceback
                self.log_write(f"\n⚠ Errore inatteso scrittura log: "
                               f"{type(e).__name__}: {e}\n")
                self.log_write(traceback.format_exc() + "\n")

            self.app.after(0, self._build_finished)

    def _build_finished(self):
        self.progress.stop()
        self.btn_build.config(state="normal")
