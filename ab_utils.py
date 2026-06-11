#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
ab_utils.py - Utility generali per AppBuilder

Funzioni pure, senza dipendenze da Tkinter o dallo stato della GUI.

Contiene:
- trova_python_builder_affidabile()  individua Python con PyInstaller
- find_icons_dir()                   cerca cartella icone vicino allo script
- run_cmd()                          esegue comando shell con log
- find_local_imports()               analisi ricorsiva degli import locali
- extract_version()                  estrae versione da uno script .py
- extract_app_name()                 estrae costante APP_NAME da uno script
- max_mtime()                        mtime massimo di script + moduli locali
"""

import ast
import os
import re
import shutil
import subprocess
import sys
from pathlib import Path
from irc_paths import SHARED_ROOT


# ---------------------------------------------------------------------------
# Python builder
# ---------------------------------------------------------------------------

def trova_python_builder_affidabile():
    """
    Trova il Python builder corretto, evitando di usare sys.executable
    quando siamo dentro un bundle PyInstaller (sys.frozen = True).
    Priorita':
    1. Variabile d'ambiente PYTHON_BUILDER (override manuale)
    2. Se NON siamo frozen: sys.executable (Python reale)
    3. Percorsi noti del venv Dropbox e altri candidati
    4. python3 / python del sistema
    """

    def has_pyinstaller(py):
        try:
            r = subprocess.run(
                [py, "-c", "import PyInstaller; print(PyInstaller.__version__)"],
                capture_output=True, text=True, timeout=6
            )
            return r.returncode == 0
        except Exception:
            return False

    env_builder = os.environ.get("PYTHON_BUILDER")
    if env_builder and os.path.isfile(env_builder) and os.access(env_builder, os.X_OK):
        return env_builder

    if not getattr(sys, "frozen", False):
        if (sys.executable
                and os.path.isfile(sys.executable)
                and os.access(sys.executable, os.X_OK)
                and has_pyinstaller(sys.executable)):
            return sys.executable

    candidates = [
        # Venv IRC canonico: ~/Python_venv/stable/ (identico su tutte le macchine IRC)
        str(Path.home() / "Python_venv/stable/bin/python3"),
        str(Path.home() / "Python_venv/stable/bin/python"),
        # Fallback: Homebrew, poi Python di sistema
        "/opt/homebrew/bin/python3",
        "/usr/local/bin/python3",
        "/usr/bin/python3",
    ]

    for c in candidates:
        if os.path.isfile(c) and os.access(c, os.X_OK) and has_pyinstaller(c):
            return c

    for name in ("python3", "python"):
        w = shutil.which(name)
        if w and os.path.isfile(w) and os.access(w, os.X_OK) and has_pyinstaller(w):
            return w

    return None


# ---------------------------------------------------------------------------
# Filesystem helpers
# ---------------------------------------------------------------------------

def find_icons_dir(start: Path) -> Path | None:
    """Trova una cartella icone vicina a 'start'."""
    candidates = [
        start / "Icons",
        start / "Icone",
        start / "icons",
        start / "icone",
        start.parent / "Icons",
        start.parent / "Icone",
        start.parent / "icons",
        start.parent / "icone",
    ]
    for c in candidates:
        if c.exists():
            return c
    return None


def run_cmd(cmd, log_fn, cwd=None):
    """Esegue un comando e invia stdout/stderr a log_fn."""
    log_fn(f"$ {cmd}\n")
    p = subprocess.Popen(
        cmd,
        cwd=cwd,
        shell=True,
        stdout=subprocess.PIPE,
        stderr=subprocess.STDOUT,
        text=True,
        bufsize=1
    )
    for line in p.stdout:
        log_fn(line)
    rc = p.wait()
    if rc != 0:
        raise RuntimeError(f"Comando fallito (exit {rc}): {cmd}")
    return rc


# ---------------------------------------------------------------------------
# Analisi sorgenti
# ---------------------------------------------------------------------------

def get_shared_dir() -> Path | None:
    """Cartella shared canonica, definita in irc_paths."""
    return SHARED_ROOT if SHARED_ROOT.exists() else None


def find_local_imports(script_path: Path, visited: set = None,
                       extra_dirs: list = None) -> list[str]:
    """
    Analizza uno script Python e trova i moduli locali importati.
    RICORSIVO: analizza anche i moduli locali trovati per trovare le loro dipendenze.
    Cerca in script_dir e in extra_dirs (es. shared/).
    Gestisce anche import della forma 'from shared.modulo import ...'
    estraendo il nome del modulo foglia (es. 'path_widgets').

    Restituisce solo i moduli rilevati staticamente via AST. I moduli dichiarati
    esplicitamente in build.json['local_modules'] vanno aggiunti dai chiamanti
    (collect_archive_files, execute_build).
    """
    if visited is None:
        visited = set()

    script_path = script_path.resolve()
    if script_path in visited:
        return []
    visited.add(script_path)

    local_modules = []
    script_dir = script_path.parent

    # Cartella shared canonica, aggiunta automaticamente alle dir di ricerca
    shared_dir = get_shared_dir()
    search_dirs = [script_dir]
    if extra_dirs:
        search_dirs += [Path(d) for d in extra_dirs]
    if shared_dir and shared_dir not in search_dirs:
        search_dirs.append(shared_dir)

    try:
        source = script_path.read_text(encoding='utf-8')
        tree = ast.parse(source)
    except Exception:
        return []

    imported_names = set()

    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            for alias in node.names:
                module_name = alias.name.split('.')[0]
                imported_names.add(module_name)
        elif isinstance(node, ast.ImportFrom):
            if node.module:
                parts = node.module.split('.')
                # Aggiunge sia il package radice che il modulo foglia
                imported_names.add(parts[0])
                if len(parts) > 1:
                    imported_names.add(parts[-1])

    for name in imported_names:
        for search_dir in search_dirs:
            local_file = search_dir / f"{name}.py"
            if local_file.exists() and local_file.resolve() != script_path:
                if name not in local_modules:
                    local_modules.append(name)
                    sub_modules = find_local_imports(local_file, visited, extra_dirs)
                    for sub in sub_modules:
                        if sub not in local_modules:
                            local_modules.append(sub)
                break  # trovato in questa dir, non cercare nelle altre

    return sorted(local_modules)


def extract_version(script_path: Path) -> str | None:
    """
    Cerca un numero di versione nello script.
    Riconosce pattern comuni come:
      __version__ = "1.4"
      # Versione: 1.4
      VERSION = "2.0"
    """
    try:
        source = script_path.read_text(encoding='utf-8', errors='ignore')
        patterns = [
            r'__version__\s*=\s*["\']([^"\']+)["\']',
            r'(?:APP_|VERSIONE_SCRIPT\s*=\s*["\']([^"\']+)["\']|)VERSION\s*=\s*["\']([^"\']+)["\']',
            r'#\s*[Vv]ersione?:?\s*([\d]+\.[\d]+(?:\.[\d]+)?)',
            r'Versione:\s*([\d]+\.[\d]+(?:\.[\d]+)?)',
        ]
        for pat in patterns:
            m = re.search(pat, source)
            if m:
                # Supporta pattern con gruppi multipli (es. APP_VERSION / VERSION)
                val = next((g for g in m.groups() if g), None)
                if val:
                    return val.strip()
    except Exception:
        pass
    return None


def extract_app_name(script_path: Path) -> str | None:
    """
    Cerca la costante APP_NAME nello script per usarla come nome cartella _Config.
    Riconosce:
      APP_NAME = "Riconciliazione Moneyspire"
      APP_NAME = 'Riconciliazione Moneyspire'
    Se presente, viene usato al posto del nome derivato dal nome file.
    """
    try:
        source = script_path.read_text(encoding='utf-8', errors='ignore')
        m = re.search(r'^APP_NAME\s*=\s*["\']([^"\']+)["\']', source, re.MULTILINE)
        if m:
            return m.group(1).strip()
    except Exception:
        pass
    return None


def max_mtime(script_path: Path, local_modules: list[str]) -> float:
    """
    Restituisce il mtime massimo tra lo script principale e tutti i moduli locali.
    """
    mtimes = []
    try:
        mtimes.append(script_path.stat().st_mtime)
    except Exception:
        pass
    script_dir = script_path.parent
    # Se esiste una cartella-pacchetto con lo stesso nome dello script,
    # scansiona ricorsivamente tutti i .py (es. pc_focus_lab/ per pc_focus_lab.py)
    package_dir = script_dir / script_path.stem
    if package_dir.is_dir():
        for py_file in package_dir.rglob("*.py"):
            try:
                mtimes.append(py_file.stat().st_mtime)
            except Exception:
                pass
    shared_dir = get_shared_dir()
    search_dirs = [script_dir]
    if shared_dir:
        search_dirs.append(shared_dir)
    for mod in local_modules:
        for search_dir in search_dirs:
            mod_path = search_dir / f"{mod}.py"
            try:
                mtimes.append(mod_path.stat().st_mtime)
            except Exception:
                pass
    return max(mtimes) if mtimes else 0.0
