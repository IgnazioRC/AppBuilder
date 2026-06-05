#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
ab_gui_main.py - Finestra principale BuilderUI

Contiene la classe BuilderUI (tk.Tk) che ospita l'intestazione comune
(cartella script, python builder, install dir, cartella _Config) e il
Notebook con i tre tab: Manuale, Batch, Archiviazione.

Le variabili Tk condivise tra tab (python_builder, install_dir, base_path,
config_path) vivono qui e vengono passate ai tab tramite il riferimento
`self.app` che ogni tab conserva.
"""

import sys
import threading
import tkinter as tk
from tkinter import ttk, filedialog
from pathlib import Path

from path_widgets import PathVar, PathEntry

from ab_utils import find_icons_dir, trova_python_builder_affidabile
from ab_gui_manual import ManualTab
from ab_gui_batch import BatchTab
from ab_gui_archive import ArchiveTab


class BuilderUI(tk.Tk):
    def __init__(self, base_path: Path, icon_path: Path, config_path: Path):
        super().__init__()
        self.title("Python App Builder (PyInstaller) v3.0")
        self.geometry("980x800")

        self.base_path = base_path
        self.icon_base_path = icon_path
        if not self.icon_base_path.exists():
            guessed = find_icons_dir(self.base_path)
            if guessed:
                self.icon_base_path = guessed

        self._ui_thread_id = threading.get_ident()

        # Variabili condivise tra tab
        builder_trovato = trova_python_builder_affidabile() or sys.executable
        self.python_builder = PathVar(value=str(builder_trovato))
        self.install_dir = PathVar(value="/Applications/Python Apps")
        self._base_var = PathVar(value=str(self.base_path))
        self._config_var = PathVar(value=str(config_path))

        self._create_widgets()

    def _create_widgets(self):
        """Crea il layout principale con intestazione comune e Notebook."""

        # === INTESTAZIONE COMUNE ===
        top = ttk.Frame(self, padding=10)
        top.pack(fill="x")

        ttk.Label(top, text="Cartella script (.py):").grid(row=0, column=0, sticky="w")
        self.base_entry = PathEntry(top, pathvar=self._base_var)
        self.base_entry.grid(row=0, column=1, sticky="we", padx=8)
        ttk.Button(top, text="Sfoglia...", command=self.pick_base).grid(row=0, column=2)

        ttk.Label(top, text="Python builder (con PyInstaller):").grid(row=1, column=0, sticky="w", pady=(6, 0))
        self.builder_entry = PathEntry(top, pathvar=self.python_builder)
        self.builder_entry.grid(row=1, column=1, sticky="we", padx=8, pady=(6, 0))
        ttk.Button(top, text="Scegli...", command=self.pick_python_builder).grid(row=1, column=2, pady=(6, 0))

        ttk.Label(top, text="Installa in:").grid(row=2, column=0, sticky="w", pady=(6, 0))
        self.install_entry = PathEntry(top, pathvar=self.install_dir)
        self.install_entry.grid(row=2, column=1, sticky="we", padx=8, pady=(6, 0))
        ttk.Button(top, text="Sfoglia...", command=self.pick_install_dir).grid(row=2, column=2, pady=(6, 0))

        ttk.Label(top, text="Cartella _Config:").grid(row=3, column=0, sticky="w", pady=(6, 0))
        self.config_entry = PathEntry(top, pathvar=self._config_var)
        self.config_entry.grid(row=3, column=1, sticky="we", padx=8, pady=(6, 0))
        ttk.Button(top, text="Sfoglia...", command=self.pick_config_dir).grid(row=3, column=2, pady=(6, 0))
        top.columnconfigure(1, weight=1)

        ttk.Separator(self, orient="horizontal").pack(fill="x", padx=10)

        # === NOTEBOOK ===
        self.notebook = ttk.Notebook(self, padding=(10, 6, 10, 10))
        self.notebook.pack(fill="both", expand=True)

        # Tab Manuale
        self.manual_tab = ManualTab(self.notebook, self)
        self.notebook.add(self.manual_tab, text="  Manuale  ")

        # Tab Batch
        self.batch_tab = BatchTab(self.notebook, self)
        self.notebook.add(self.batch_tab, text="  Batch / Catalog  ")

        # Tab Archiviazione (zip in _Archivio/)
        self.archive_tab = ArchiveTab(self.notebook, self)
        self.notebook.add(self.archive_tab, text="  Archiviazione  ")

        # Quando si cambia tab, aggiorna la lista
        self.notebook.bind("<<NotebookTabChanged>>", self._on_tab_changed)

    def _on_tab_changed(self, event=None):
        tab = self.notebook.index(self.notebook.select())
        if tab == 1:  # Batch tab
            self.batch_tab.refresh()
        elif tab == 2:  # Archive tab
            self.archive_tab.refresh()

    def pick_base(self):
        d = filedialog.askdirectory(initialdir=str(self.base_path))
        if not d:
            return
        self.base_path = Path(d)
        self._base_var.set(str(self.base_path))
        builder_trovato = trova_python_builder_affidabile() or sys.executable
        self.python_builder.set(str(builder_trovato))
        self.manual_tab.reset_for_new_base()
        self.manual_tab.refresh_scripts()

    def pick_python_builder(self):
        f = filedialog.askopenfilename(
            title="Scegli Python builder (con PyInstaller)",
            initialdir=str(Path.home()),
            filetypes=[("Tutti i file", "*.*")]
        )
        if f:
            self.python_builder.set(f)

    def pick_config_dir(self):
        initial_dir = self._config_var.get().strip() or str(Path.home())
        d = filedialog.askdirectory(title="Scegli cartella _Config", initialdir=initial_dir)
        if d:
            self._config_var.set(d)
            self.batch_tab.refresh()
            self.archive_tab.refresh()

    def pick_install_dir(self):
        initial_dir = self.install_dir.get().strip() or "/Applications"
        d = filedialog.askdirectory(title="Scegli cartella di installazione", initialdir=initial_dir)
        if d:
            self.install_dir.set(d)

    def _current_running_app_bundle(self) -> Path | None:
        try:
            if getattr(sys, "frozen", False):
                exe = Path(sys.executable).resolve()
                if exe.parent.name == "MacOS" and exe.parent.parent.name == "Contents":
                    bundle = exe.parent.parent.parent
                    if bundle.suffix == ".app":
                        return bundle
        except Exception:
            pass
        return None

    def safe_install_target(self, target: Path, app_name: str, log_fn=None) -> Path:
        current = self._current_running_app_bundle()
        try:
            if current and current.resolve() == target.resolve():
                alt = target.with_name(f"{app_name} (new).app")
                if log_fn:
                    log_fn(f"[!] Target coincide con l'app in esecuzione. Installero' in: {alt}\n")
                return alt
        except Exception:
            pass
        return target
