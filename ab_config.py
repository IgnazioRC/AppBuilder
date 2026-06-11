#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
ab_config.py - Gestione dei file build.json

Ogni app builder ha un proprio file _Config/<AppName>/build.json che
conserva i parametri di build, l'mtime delle sorgenti al momento dell'ultima
build, e il flag enabled.

Contiene:
- config_root()              cartella radice _Config (oggi e' configurabile)
- build_json_path()          path del build.json di una specifica app
- save_build_json()          scrive (o aggiorna) il build.json
- load_build_json()          carica un build.json (None se errore)
- discover_build_configs()   lista di tutti i build.json sotto _Config/
- resolve_script_path()      risolve il campo "script" (assoluto o relativo)
- check_needs_rebuild()      confronta mtime sorgenti vs bundle .app installato
"""

import datetime
import json
from pathlib import Path

from ab_utils import extract_version, find_local_imports, max_mtime


def config_root(config_path: Path) -> Path:
    """Restituisce la cartella _Config (percorso esplicito, configurabile)."""
    return config_path


def build_json_path(config_path: Path, app_name: str) -> Path:
    """Percorso del file build.json per una specifica app."""
    return config_root(config_path) / app_name / "build.json"


def save_build_json(config_path: Path, base_path: Path, script_path: Path,
                    app_name: str, icon: str, windowed: bool,
                    hidden_imports: list[str],
                    install_dir: str, python_builder: str,
                    source_mtime: float):
    """
    Aggiorna build.json in _Config/<app_name>/build.json con merge:
    carica l'esistente, sovrascrive i campi noti, preserva i campi
    sconosciuti (es. local_modules). Il campo "enabled" non viene mai
    resettato se già presente nel file esistente.
    Restituisce il path del file salvato.
    """
    cfg_dir = config_root(config_path) / app_name
    cfg_dir.mkdir(parents=True, exist_ok=True)
    json_path = cfg_dir / "build.json"

    existing = load_build_json(json_path) or {}

    # Percorso script relativo a config_path.parent (es. Python/)
    # cosi' e' stabile indipendentemente da dove punta base_path
    python_root = config_path.parent
    try:
        script_rel = str(script_path.relative_to(python_root))
    except ValueError:
        script_rel = str(script_path)

    # Percorso icona relativo a python_root (Python/) se possibile
    icon_rel = ""
    if icon:
        try:
            icon_rel = str(Path(icon).relative_to(python_root))
        except ValueError:
            icon_rel = icon

    version = extract_version(script_path)

    data = {
        "app_name": app_name,
        "script": script_rel,
        "icon": icon_rel,
        "windowed": windowed,
        "hidden_imports": hidden_imports,
        "install_dir": install_dir,
        "python_builder": python_builder,
        "version_detected": version,
        "built_at": datetime.datetime.now().isoformat(timespec='seconds'),
        "source_mtime": source_mtime,
        "enabled": True,
    }

    merged = {**existing, **data}
    if "enabled" in existing:
        merged["enabled"] = existing["enabled"]

    json_path.write_text(json.dumps(merged, indent=2, ensure_ascii=False), encoding='utf-8')
    return json_path


def load_build_json(json_path: Path) -> dict | None:
    """Carica un build.json. Restituisce None in caso di errore."""
    try:
        return json.loads(json_path.read_text(encoding='utf-8'))
    except Exception:
        return None


def discover_build_configs(config_path: Path) -> list[Path]:
    """
    Trova tutti i file build.json in _Config/*/build.json,
    ordinati per nome cartella.
    """
    root = config_root(config_path)
    if not root.exists():
        return []
    result = sorted(root.glob("*/build.json"))
    return result


def resolve_script_path(script_field: str, config_path: Path) -> Path:
    """
    Risolve il campo 'script' del build.json in un Path assoluto.
    - Se e' un percorso assoluto (es. script fuori da python_root): usato direttamente
    - Se e' relativo: risolto rispetto a config_path.parent (Python/)
    """
    p = Path(script_field).expanduser()
    if p.is_absolute():
        return p
    return (config_path.parent / script_field).expanduser()


def check_needs_rebuild(cfg: dict, config_path: Path, install_dir: str) -> tuple[bool, str]:
    """
    Controlla se un'app deve essere ricostruita.
    Logica: confronta mtime sorgenti vs mtime bundle .app installato,
    entrambi letti localmente — funziona correttamente cross-macchina.
    Restituisce (needs_rebuild: bool, reason: str).
    """
    app_name = cfg.get("app_name", "")
    script_field = cfg.get("script", "")

    # Verifica se l'app e' installata
    app_bundle = Path(install_dir) / f"{app_name}.app"
    if not app_bundle.exists():
        return True, "Non installata"

    # Risolve lo script (relativo o assoluto)
    script_path = resolve_script_path(script_field, config_path)
    if not script_path.exists():
        return False, f"Script non trovato: {script_field}"

    # mtime massimo delle sorgenti (script + moduli locali)
    source_mtime = max_mtime(script_path, find_local_imports(script_path))

    # mtime del bundle .app installato su questa macchina
    try:
        bundle_mtime = app_bundle.stat().st_mtime
    except Exception:
        return True, "Impossibile leggere bundle"

    if source_mtime > bundle_mtime:
        mod_time = datetime.datetime.fromtimestamp(source_mtime).strftime("%d/%m/%y %H:%M")
        return True, f"Sorgenti modificate ({mod_time})"

    built_at = cfg.get("built_at", "")
    return False, f"Aggiornata (build: {built_at[:16] if built_at else '?'})"
