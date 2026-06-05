#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
ab_build.py - Engine di build PyInstaller

Funzione condivisa da ManualTab (build singola) e BatchTab (build di gruppo).
Esegue il flusso completo: validazione builder, costruzione comando PyInstaller,
gestione hidden-import e add-data, copia in cartella di installazione,
rimozione quarantena, pulizia bundle, codesign ad-hoc.

Contiene:
- execute_build()             funzione principale, ritorna (target, builder_usato)
- _convert_png_to_icns()      conversione icona PNG -> ICNS via sips/iconutil
"""

import os
import re
import shlex
import shutil
import subprocess
from pathlib import Path

from ab_utils import (
    trova_python_builder_affidabile,
    run_cmd,
    find_local_imports,
)

# log_path arriva dal modulo condiviso 'shared/path_widgets.py'
from path_widgets import log_path


def execute_build(script_path: Path, app_name: str, icon: str,
                  windowed: bool, hidden_imports: list[str],
                  install_dir_str: str, python_builder_str: str,
                  log_fn, base_path: Path,
                  clean_after: bool = False,
                  safe_install_fn=None) -> Path:
    """
    Esegue il build PyInstaller completo.
    Restituisce il path del bundle .app installato.
    Lancia RuntimeError in caso di errore.
    """
    base = script_path.parent

    # Opzione A: usa python_builder_str se esiste e ha PyInstaller,
    # altrimenti ricalcola automaticamente con trova_python_builder_affidabile().
    def _valida_builder(py_str: str) -> Path | None:
        if not py_str or not py_str.strip():
            return None
        p = Path(py_str).expanduser()
        if not p.exists() or not os.access(str(p), os.X_OK):
            return None
        try:
            subprocess.check_call(
                [str(p), "-m", "PyInstaller", "--version"],
                stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL, timeout=10
            )
            return p
        except Exception:
            return None

    builder_py = _valida_builder(python_builder_str)
    if builder_py is None:
        motivo = "non specificato" if not python_builder_str.strip() else f"non valido o senza PyInstaller: {python_builder_str}"
        log_fn(f"[i] python_builder {motivo}\n")
        log_fn("[i] Ricerca automatica Python builder affidabile...\n")
        trovato = trova_python_builder_affidabile()
        if trovato is None:
            raise RuntimeError(
                "Nessun Python con PyInstaller trovato.\n"
                "Installa PyInstaller nel venv stable:\n"
                "  pystable && pip install pyinstaller"
            )
        builder_py = Path(trovato)
        log_fn(f"[i] Uso: {builder_py}\n")
    else:
        log_fn(f"[i] Python builder: {builder_py}\n")

    # Verifica disponibilita' PyInstaller (gia' verificata sopra, questo e' per il log)
    try:
        subprocess.check_call(
            [str(builder_py), "-m", "PyInstaller", "--version"],
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
        )
    except Exception:
        raise RuntimeError(
            "PyInstaller non e' installato per il Python builder selezionato.\n"
            f"Python builder: {builder_py}\n\n"
            "Installa con:\n"
            f"  {builder_py} -m pip install --upgrade pip\n"
            f"  {builder_py} -m pip install pyinstaller\n"
        )

    build_dir = base / "_build_app"
    dist_dir = build_dir / "dist"
    work_dir = build_dir / "build"
    spec_dir = build_dir
    build_dir.mkdir(parents=True, exist_ok=True)

    # Eventuale conversione PNG -> ICNS
    icon_eff = icon
    if icon and icon.lower().endswith('.png'):
        png_path = Path(icon).expanduser()
        if png_path.exists():
            safe = re.sub(r'[^A-Za-z0-9]+', '_', app_name)
            tmp_icns = build_dir / f"icon_{safe}.icns"
            _convert_png_to_icns(png_path, tmp_icns, log_fn)
            icon_eff = str(tmp_icns)

    icon_arg = f"--icon {shlex.quote(icon_eff)}" if icon_eff else ""
    windowed_arg = "--windowed" if windowed else ""

    # Cartella shared
    shared_dir = Path.home() / "Library" / "CloudStorage" / "Dropbox" / "Documenti_IRC" / "Python" / "shared"
    config_paths_arg = f"--paths {shlex.quote(str(shared_dir))} " if shared_dir.exists() else ""

    # Rileva moduli locali (script_dir + shared/)
    _local_mods = find_local_imports(script_path)

    # Moduli trovati in shared/ devono essere --hidden-import perché spesso
    # vengono importati dentro try/except e PyInstaller li ignora
    _shared_mods = []
    if shared_dir.exists():
        for mod in _local_mods:
            if (shared_dir / f"{mod}.py").exists():
                _shared_mods.append(mod)

    # hidden_imports espliciti dall'utente + quelli auto-rilevati da shared/
    _all_hidden = list(hidden_imports)
    for mod in _shared_mods:
        if mod not in _all_hidden:
            _all_hidden.append(mod)
            log_fn(f"[hidden-import] {mod} (da shared/)\n")

    hidden_arg = ""
    for h in _all_hidden:
        hidden_arg += f' --hidden-import {shlex.quote(h)}'

    # --add-data automatico: include tutti i .py nella stessa cartella dello script
    # che NON sono importati (non rilevati da find_local_imports) ma che
    # potrebbero essere lanciati come sottoprocessi.
    # Esclude lo script principale stesso e i moduli già rilevati come import.
    add_data_arg = ""
    already_known = set(_local_mods) | {script_path.stem}
    for sibling in sorted(base.glob("*.py")):
        if sibling.resolve() == script_path.resolve():
            continue
        if sibling.stem in already_known:
            continue
        # File .py nella stessa cartella non già incluso: aggiungilo come dato
        add_data_arg += f' --add-data {shlex.quote(str(sibling) + ":.")}'
        log_fn(f"[add-data] {sibling.name}\n")

    cmd = (
        f"{shlex.quote(str(builder_py))} -m PyInstaller "
        f"--noconfirm {windowed_arg} "
        f"--name {shlex.quote(app_name)} "
        f"--distpath {shlex.quote(str(dist_dir))} "
        f"--workpath {shlex.quote(str(work_dir))} "
        f"--specpath {shlex.quote(str(spec_dir))} "
        f"--paths {shlex.quote(str(base))} "
        f"{config_paths_arg}"
        f"{icon_arg} {hidden_arg}{add_data_arg} "
        f"{shlex.quote(str(script_path))}"
    )

    log_fn("\n" + "=" * 60 + "\n")
    log_fn(f"BUILD: {app_name}\n")
    log_fn(f"Script: {log_path(script_path)}\n")
    log_fn("=" * 60 + "\n\n")

    run_cmd(cmd, log_fn, cwd=str(base))

    app_bundle = dist_dir / f"{app_name}.app"
    if not app_bundle.exists():
        raise RuntimeError(f"Bundle non trovato: {app_bundle}")

    log_fn("\n--- Rimozione quarantena ---\n")
    run_cmd(
        f'find {shlex.quote(str(app_bundle))} -exec xattr -d com.apple.quarantine {{}} \\; 2>/dev/null || true',
        log_fn
    )

    log_fn("\n--- Copia in cartella di installazione ---\n")
    install_dir = Path(install_dir_str).expanduser()
    install_dir.mkdir(parents=True, exist_ok=True)
    target = install_dir / f"{app_name}.app"
    if safe_install_fn:
        target = safe_install_fn(target, app_name)
    if target.exists():
        run_cmd(f'rm -rf {shlex.quote(str(target))}', log_fn)
    run_cmd(f'ditto {shlex.quote(str(app_bundle))} {shlex.quote(str(target))}', log_fn)

    log_fn("\n--- Pulizia bundle (dot_clean + xattr) ---\n")
    try:
        run_cmd(f'dot_clean -m {shlex.quote(str(target))} 2>/dev/null || true', log_fn)
        run_cmd(f'find {shlex.quote(str(target))} -exec xattr -c {{}} \\; 2>/dev/null || true', log_fn)
        run_cmd(f'find {shlex.quote(str(target))} -exec xattr -d com.apple.quarantine {{}} \\; 2>/dev/null || true', log_fn)
    except Exception as e:
        log_fn(f"[!] Pulizia bundle non riuscita (non bloccante): {e}\n")

    log_fn("\n--- Codesign ad-hoc ---\n")
    try:
        run_cmd(f'codesign --force --deep --sign - {shlex.quote(str(target))}', log_fn)
        run_cmd(f'codesign --verify --deep --verbose=2 {shlex.quote(str(target))}', log_fn)
        log_fn("Codesign completato.\n")
    except Exception as e:
        log_fn(f"[!] Codesign non riuscito: {e}\n")

    if clean_after and build_dir.exists():
        log_fn("\n--- Pulizia file temporanei ---\n")
        try:
            shutil.rmtree(build_dir)
            log_fn(f"Rimosso: {build_dir}\n")
        except Exception as e:
            log_fn(f"[!] Errore pulizia: {e}\n")

    log_fn("\n" + "=" * 60 + "\n")
    log_fn(f"OK - BUILD COMPLETATO: {app_name}\n")
    log_fn(f"App installata in: {target}\n")
    log_fn("=" * 60 + "\n")

    return target, str(builder_py)


def _convert_png_to_icns(png_path: Path, out_icns: Path, log_fn):
    """Converte un PNG in .icns usando sips+iconutil."""
    iconset = out_icns.with_suffix(".iconset")
    cmd = (
        f"rm -rf {shlex.quote(str(iconset))} && mkdir -p {shlex.quote(str(iconset))} && "
        f"sips -z 16 16 {shlex.quote(str(png_path))} --out {shlex.quote(str(iconset / 'icon_16x16.png'))} >/dev/null && "
        f"sips -z 32 32 {shlex.quote(str(png_path))} --out {shlex.quote(str(iconset / 'icon_16x16@2x.png'))} >/dev/null && "
        f"sips -z 32 32 {shlex.quote(str(png_path))} --out {shlex.quote(str(iconset / 'icon_32x32.png'))} >/dev/null && "
        f"sips -z 64 64 {shlex.quote(str(png_path))} --out {shlex.quote(str(iconset / 'icon_32x32@2x.png'))} >/dev/null && "
        f"sips -z 128 128 {shlex.quote(str(png_path))} --out {shlex.quote(str(iconset / 'icon_128x128.png'))} >/dev/null && "
        f"sips -z 256 256 {shlex.quote(str(png_path))} --out {shlex.quote(str(iconset / 'icon_128x128@2x.png'))} >/dev/null && "
        f"sips -z 256 256 {shlex.quote(str(png_path))} --out {shlex.quote(str(iconset / 'icon_256x256.png'))} >/dev/null && "
        f"sips -z 512 512 {shlex.quote(str(png_path))} --out {shlex.quote(str(iconset / 'icon_256x256@2x.png'))} >/dev/null && "
        f"sips -z 512 512 {shlex.quote(str(png_path))} --out {shlex.quote(str(iconset / 'icon_512x512.png'))} >/dev/null && "
        f"sips -z 1024 1024 {shlex.quote(str(png_path))} --out {shlex.quote(str(iconset / 'icon_512x512@2x.png'))} >/dev/null && "
        f"iconutil -c icns {shlex.quote(str(iconset))} -o {shlex.quote(str(out_icns))} && "
        f"rm -rf {shlex.quote(str(iconset))}"
    )
    log_fn(f"[i] Converto PNG in ICNS: {png_path.name} -> {out_icns.name}\n")
    run_cmd(cmd, log_fn)
