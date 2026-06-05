#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
ab_log.py - Modulo centrale per il logging su file di AppBuilder.

Contiene la cartella canonica dei log e le funzioni che scrivono i file
di log di sessione per le diverse operazioni di AppBuilder:

  - write_archive_session_log()  -> log di sessione tab Archiviazione
  - write_build_session_log()    -> log di sessione tab Batch
  - write_build_log()            -> log di singola build (tab Manuale)

Tutti i log finiscono in ~/Documents/log/, che e' sincronizzato in iCloud
e accessibile da tutti i Mac (BdS, Gignese, CCM, Punta Ala). Stesso path
usato dal vecchio ArchiviaProgetto.

Naming convenzionale:
  - AppBuilder_archive_<YYYYMMDD_HHMMSS>.log     -> sessione archiviazione
  - AppBuilder_build_<YYYYMMDD_HHMMSS>.log       -> sessione batch build
  - AppBuilder_build_<YYYYMMDD_HHMMSS>_<App>.log -> build manuale singola
"""

import datetime
import sys
import traceback
from pathlib import Path


# Cartella canonica dei log di AppBuilder.
LOG_DIR = Path.home() / "Documents" / "log"


def _log_path(prefix: str, app_name: str | None = None) -> Path:
    """
    Costruisce un path log con timestamp corrente.
    Se app_name e' fornito, lo include nel filename (uso per build singola).
    """
    ts = datetime.datetime.now().strftime("%Y%m%d_%H%M%S")
    if app_name:
        # Sanitizzazione minima: spazi -> underscore, niente "/" o ":"
        safe = app_name.replace("/", "_").replace(":", "_").replace(" ", "_")
        return LOG_DIR / f"{prefix}_{ts}_{safe}.log"
    return LOG_DIR / f"{prefix}_{ts}.log"


def _write_text_safe(log_path: Path, lines: list[str]) -> Path | None:
    """
    Helper che scrive le righe nel file di log. Cattura qualsiasi eccezione
    e la stampa su stderr (visibile dal terminale di lancio) invece di
    inghiottirla silenziosamente.
    """
    try:
        LOG_DIR.mkdir(parents=True, exist_ok=True)
        log_path.write_text("\n".join(lines) + "\n", encoding="utf-8")
        return log_path
    except Exception as e:
        print(f"\n[ab_log] ERROR writing {log_path}: "
              f"{type(e).__name__}: {e}", file=sys.stderr)
        traceback.print_exc(file=sys.stderr)
        return None


# ---------------------------------------------------------------------------
# Archiviazione (sessione = tutte le app archiviate in un click)
# ---------------------------------------------------------------------------

def write_archive_session_log(
        log_buffer: list[str],
        summary: dict,
        included_per_app: dict[str, list[Path]] | None = None,
        excluded_per_app: dict[str, list[tuple[Path, str]]] | None = None,
        ) -> Path | None:
    """
    Scrive il log della sessione di archiviazione in
    ~/Documents/log/AppBuilder_archive_<YYYYMMDD_HHMMSS>.log

    Il file contiene:
      - intestazione con timestamp, totale app, esito (n_ok / n_fail)
      - per ogni app: lista file inclusi nello zip e file esclusi dai filtri
        (duplicati Finder 'copy', file di backup), utile come audit trail
        per verificare l'effetto dei filtri di esclusione
      - in coda: il dump completo del log a video (stesso testo)

    Args:
      log_buffer: lista di stringhe gia' mostrate a video (verbatim)
      summary: {"n_ok": int, "n_fail": int, "label": str}
      included_per_app: {app_name: [Path, ...]} file effettivamente archiviati
      excluded_per_app: {app_name: [(Path, motivo), ...]} file filtrati

    Restituisce il Path del log creato, o None se la scrittura fallisce.
    """
    log_path = _log_path("AppBuilder_archive")
    lines: list[str] = []
    lines.append("AppBuilder — log archiviazione sessione")
    lines.append(f"Timestamp: {datetime.datetime.now().strftime('%Y-%m-%d %H:%M:%S')}")
    lines.append(f"Sessione: {summary.get('label', 'n/d')}")
    n_ok = summary.get("n_ok", 0)
    n_fail = summary.get("n_fail", 0)
    lines.append(f"Esito: {n_ok} OK, {n_fail} errori (totale {n_ok + n_fail})")
    lines.append("=" * 70)
    lines.append("")

    if included_per_app or excluded_per_app:
        lines.append("DETTAGLIO FILE PER APP")
        lines.append("-" * 70)
        all_apps = sorted(set(
            (included_per_app or {}).keys()
            | (excluded_per_app or {}).keys()
        ))
        for app in all_apps:
            lines.append("")
            lines.append(f"[{app}]")
            inc = (included_per_app or {}).get(app, [])
            exc = (excluded_per_app or {}).get(app, [])
            lines.append(f"  Inclusi ({len(inc)}):")
            for f in inc:
                lines.append(f"    + {f}")
            if exc:
                lines.append(f"  Esclusi dai filtri ({len(exc)}):")
                for f, motivo in exc:
                    lines.append(f"    - {f}   [{motivo}]")
        lines.append("")
        lines.append("=" * 70)
        lines.append("")

    lines.append("LOG A VIDEO (verbatim)")
    lines.append("-" * 70)
    lines.extend(s.rstrip("\n") for s in log_buffer)

    return _write_text_safe(log_path, lines)


# ---------------------------------------------------------------------------
# Build manuale (singola app, un log per build)
# ---------------------------------------------------------------------------

def write_build_log(
        log_buffer: list[str],
        app_name: str,
        success: bool,
        details: dict | None = None,
        ) -> Path | None:
    """
    Scrive il log di una build manuale singola in
    ~/Documents/log/AppBuilder_build_<YYYYMMDD_HHMMSS>_<App>.log

    Il filename include il nome dell'app, cosi' a colpo d'occhio si sa
    a quale build appartiene ogni log file.

    Args:
      log_buffer: lista di stringhe del log a video durante la build
      app_name: nome dell'app
      success: True se build OK, False se errore
      details: dict opzionale con info aggiuntive (script_path, target,
               python_builder, version, ecc.) -- vengono inserite
               nell'intestazione del log per audit veloce

    Restituisce il Path del log creato, o None in caso di errore scrittura.
    """
    log_path = _log_path("AppBuilder_build", app_name=app_name)
    lines: list[str] = []
    lines.append("AppBuilder — log build manuale")
    lines.append(f"Timestamp: {datetime.datetime.now().strftime('%Y-%m-%d %H:%M:%S')}")
    lines.append(f"App: {app_name}")
    lines.append(f"Esito: {'OK' if success else 'ERRORE'}")

    if details:
        # Stampa solo i campi noti utili, nell'ordine preferito
        for key in ("script_path", "target", "python_builder",
                    "version_detected", "icon", "windowed",
                    "hidden_imports", "local_modules"):
            if key in details and details[key] not in (None, "", [], {}):
                val = details[key]
                if isinstance(val, list):
                    val = ", ".join(str(x) for x in val)
                lines.append(f"{key}: {val}")

    lines.append("=" * 70)
    lines.append("")
    lines.append("LOG A VIDEO (verbatim)")
    lines.append("-" * 70)
    lines.extend(s.rstrip("\n") for s in log_buffer)

    return _write_text_safe(log_path, lines)


# ---------------------------------------------------------------------------
# Build batch (sessione = tutte le app ricostruite in un click)
# ---------------------------------------------------------------------------

def write_build_session_log(
        log_buffer: list[str],
        summary: dict,
        per_app_results: list[dict] | None = None,
        ) -> Path | None:
    """
    Scrive il log della sessione di build batch in
    ~/Documents/log/AppBuilder_build_<YYYYMMDD_HHMMSS>.log

    A differenza della build manuale, qui ce N app in un unico log, con
    una sezione di riepilogo per ciascuna (ok/errore + builder usato +
    eventuali messaggi di errore).

    Args:
      log_buffer: lista di stringhe del log a video durante l'intero batch
      summary: {"n_ok": int, "n_fail": int, "label": str}
      per_app_results: [{"app_name": str, "success": bool, "message": str,
                          "version": str, "target": str}, ...]

    Restituisce il Path del log creato, o None in caso di errore.
    """
    log_path = _log_path("AppBuilder_build")
    lines: list[str] = []
    lines.append("AppBuilder — log build sessione batch")
    lines.append(f"Timestamp: {datetime.datetime.now().strftime('%Y-%m-%d %H:%M:%S')}")
    lines.append(f"Sessione: {summary.get('label', 'n/d')}")
    n_ok = summary.get("n_ok", 0)
    n_fail = summary.get("n_fail", 0)
    lines.append(f"Esito: {n_ok} OK, {n_fail} errori (totale {n_ok + n_fail})")
    lines.append("=" * 70)
    lines.append("")

    if per_app_results:
        lines.append("RIEPILOGO PER APP")
        lines.append("-" * 70)
        for r in per_app_results:
            status = "✓ OK   " if r.get("success") else "✗ FAIL "
            name = r.get("app_name", "?")
            version = r.get("version") or ""
            ver_s = f" v{version}" if version else ""
            lines.append(f"  {status} {name}{ver_s}")
            msg = r.get("message", "")
            if msg:
                lines.append(f"           {msg}")
            target = r.get("target", "")
            if target and r.get("success"):
                lines.append(f"           -> {target}")
        lines.append("")
        lines.append("=" * 70)
        lines.append("")

    lines.append("LOG A VIDEO (verbatim)")
    lines.append("-" * 70)
    lines.extend(s.rstrip("\n") for s in log_buffer)

    return _write_text_safe(log_path, lines)
