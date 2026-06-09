#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
ab_archive.py - Engine di archiviazione zip in _Archivio/

Crea snapshot zip di ogni app contenenti:
- script principale + moduli locali dichiarati in build.json
- ricorsivamente .py .md .command .sh .json .yaml .toml .ini .plist ...
  della cartella app (escludendo .venv, build, dist, cache, .git, ecc.)
- tutto il contenuto di _Config/<app_name>/

Contiene:
- ARCHIVE_EXTENSIONS, ARCHIVE_EXCLUDED_DIRS
- archive_root()                cartella _Archivio/ (sorella di _Config/)
- backup_root()                 cartella _Archivio/_Backup/ (versioni vecchie)
- collect_archive_files()       raccoglie i file di un'app
- archive_max_mtime()           mtime massimo dei file raccolti
- find_latest_archive()         zip piu' recente di un'app
- build_archive_name()          costruisce il nome zip "<App>_v<ver>_<data>.zip"
- check_needs_archive()         confronta sorgenti vs ultimo zip
- move_previous_archives_to_backup()  sposta gli zip precedenti in _Backup/
- create_archive_zip()          crea fisicamente il zip
- diagnose_config_misalignments()  diagnostica disallineamenti app <-> _Config

Logging su file (LOG_DIR, write_*_log) e' nel modulo ab_log.
"""

import datetime
import difflib
import re
import shutil
import zipfile
from pathlib import Path

from irc_paths import SHARED_ROOT
from ab_utils import extract_app_name, find_local_imports
from ab_config import (
    config_root,
    load_build_json,
    resolve_script_path,
)


# Estensioni "snapshot intermedio" raccolte ricorsivamente dalla cartella app.
# La filosofia: includiamo codice/script/configurazione/documentazione che
# fanno parte dell'app, escludiamo cache, artefatti di build e dati binari
# pesanti irrilevanti per il portabilita' tra macchine.
#
# Per app come AggiornaiClip i file .command sono parti attive dell'app
# (lanciano backup/restore) e devono essere archiviati.
ARCHIVE_EXTENSIONS = {
    ".py",        # script principale e moduli locali (raccolti anche via build.json)
    ".md",        # documentazione
    ".command",   # script shell macOS (lanciabili da Finder)
    ".sh",        # script shell generici
    ".json",      # config eventualmente in cartella app
    ".yaml", ".yml",
    ".toml",      # pyproject.toml ecc.
    ".cfg",       # setup.cfg ecc.
    ".txt",       # requirements.txt, README.txt ecc.
    ".ini",       # configurazioni stile ini
    ".plist",     # property list macOS
}

# Cartelle che non vanno mai esplorate ricorsivamente in cartella app.
# Coprono: cache Python, ambienti virtuali, artefatti PyInstaller, VCS,
# IDE, dot-dirs di sistema e metadati Dropbox.
# Match case-insensitive sul nome esatto della cartella.
ARCHIVE_EXCLUDED_DIRS = {
    "__pycache__", ".venv", "venv", "env",
    "build", "dist",
    ".git", ".svn", ".hg",
    ".idea", ".vscode",
    ".pytest_cache", ".mypy_cache", ".ruff_cache",
    "node_modules",
    ".DS_Store",  # tecnicamente file, ma per sicurezza
    # Convenzionali "fuori produzione" / lavoro temporaneo
    "old", "copy", "copies",
    "bak", "backup", "backups",
    "archive", "archives",
    "tmp", "temp",
    "deprecated", "obsolete",
}


# Pattern per riconoscere file da escludere.
#
# 1) Duplicati creati dal Finder via Cmd-D o Cmd-C/Cmd-V: lo schema reale
#    e' SEMPRE " copy" minuscolo a fine stem, eventualmente seguito da
#    " N" (spazio + numero progressivo). Esempi prodotti dal Finder:
#         "script.py"        -> "script copy.py"
#         "script copy.py"   -> "script copy 2.py"
#    Il match e' case-sensitive (solo minuscolo) e ancorato a fine stem,
#    per NON escludere file legittimi come "Obsidian MD Copy.md" (parte
#    integrante del nome dell'applicativo "Gestione MD Obsidian", dove
#    "Copy" e' una parola scelta con la C maiuscola).
_FINDER_COPY_RE = re.compile(r" copy(?: \d+)?$")

# 2) File di backup espliciti: "bak" come token tra underscore o come
#    suffisso finale ("script_bak", "20260507_bak", "_bak_1"). Match
#    case-insensitive ma SOLO come token delimitato, per non colpire
#    parole come "bakery", "Bakelite", ecc.
_BAK_TOKEN_RE = re.compile(r"(?:^|_)bak(?:_|$)", re.IGNORECASE)


def file_exclude_reason(path: Path) -> str | None:
    """
    Restituisce il motivo di esclusione del file, oppure None se va incluso.

    Filtra:
      - duplicati Finder ("nome copy.ext", "nome copy 2.ext", ecc.)
      - file di backup espliciti ("nome_bak.ext", "nome.bak")

    Non filtra file che contengono "copy" o "bak" come parte legittima del
    nome (es. "Obsidian MD Copy.md", "bakery.py", "my_copy_tool.py").
    """
    stem = path.stem
    if _FINDER_COPY_RE.search(stem):
        return "duplicato Finder ('copy' a fine nome)"
    if path.suffix.lower() == ".bak":
        return "backup (estensione .bak)"
    if _BAK_TOKEN_RE.search(stem):
        return "backup ('bak' come suffisso/token)"
    return None


def archive_root(config_path: Path) -> Path:
    """
    Restituisce la cartella _Archivio/ (sorella di _Config/, sotto Python/).
    """
    return config_path.parent / "_Archivio"


def backup_root(config_path: Path) -> Path:
    """
    Restituisce la cartella _Archivio/_Backup/ dove vengono spostate le
    versioni precedenti degli zip durante la ri-archiviazione di una app.
    In _Archivio/ resta sempre e solo lo zip piu' recente di ciascuna app;
    tutti i precedenti finiscono in _Archivio/_Backup/ (uno per data: se
    nello stesso giorno si archivia due volte, lo zip in _Backup viene
    sovrascritto, comportamento storico voluto).
    """
    return archive_root(config_path) / "_Backup"


def collect_archive_files(cfg: dict, config_path: Path) -> dict:
    """
    Raccoglie tutti i file da includere nello zip di un'app.

    Strategia "snapshot intermedio":
      1. Script principale (da build.json["script"])
      2. Moduli locali dichiarati in build.json["local_modules"]
         (cercati nella cartella app; quelli da shared/ vengono esclusi)
      3. Scansione ricorsiva della cartella app per file con estensione in
         ARCHIVE_EXTENSIONS (.py .md .command .sh .json .yaml .toml ecc.),
         escludendo cartelle in ARCHIVE_EXCLUDED_DIRS (cache, .venv, build,
         .git, ecc.) e file nascosti.
      4. Tutto il contenuto di _Config/<app_name>/ (ricorsivo).

    Filosofia: includere codice/script/configurazione/documentazione che fanno
    parte dell'app, escludere cache, artefatti di build, ambienti virtuali e
    metadati di sistema (irrilevanti tra macchine diverse).

    Restituisce un dict:
      {
        "app_files": [Path, ...],   # file dalla cartella dell'app
        "app_root": Path,           # cartella radice dell'app (per path relativi)
        "config_files": [Path, ...],# file da _Config/<app_name>/
        "config_root": Path,        # cartella _Config/<app_name>/ (per path relativi)
        "missing": [str, ...],      # eventuali moduli locali non trovati
      }
    """
    result = {
        "app_files": [],
        "app_root": None,
        "shared_files": [],   # moduli da shared/ -> arcname "shared/<mod>.py"
        "config_files": [],
        "config_root": None,
        "missing": [],
        "excluded": [],  # [(Path, motivo), ...] file scartati dai filtri copy/bak
    }

    app_name = cfg.get("app_name", "")
    script_field = cfg.get("script", "")

    script_path = resolve_script_path(script_field, config_path)
    if not script_path.exists():
        result["missing"].append(f"script: {script_field}")
        return result

    local_modules = find_local_imports(script_path)

    app_dir = script_path.parent
    result["app_root"] = app_dir

    # 1) Script principale
    result["app_files"].append(script_path)

    # 2) Moduli locali dichiarati in build.json - cerco prima nella cartella app.
    #    Se non trovati in app_dir, li cerca in shared/ e li include nell'archivio
    #    sotto "shared/<mod>.py"
    for mod in local_modules:
        candidate = app_dir / f"{mod}.py"
        if candidate.exists():
            if candidate not in result["app_files"]:
                result["app_files"].append(candidate)
        else:
            shared_candidate = SHARED_ROOT / f"{mod}.py"
            if shared_candidate.exists():
                if shared_candidate not in result["shared_files"]:
                    result["shared_files"].append(shared_candidate)
            else:
                result["missing"].append(f"modulo locale: {mod}.py")

    # 3) Scansione ricorsiva della cartella app per file rilevanti.
    #    Include tutti i file con estensione in ARCHIVE_EXTENSIONS, escludendo:
    #    - cartelle in ARCHIVE_EXCLUDED_DIRS (cache, venv, build, .git, ecc.;
    #      match case-insensitive sul nome esatto della cartella)
    #    - cartelle nascoste (.qualcosa)
    #    - file nascosti (.DS_Store, .gitignore inutili, ecc.)
    #    - file Finder "copy" e file di backup (vedi file_exclude_reason)
    #    Lo script principale e i moduli locali sono gia' nel set, evita duplicati.
    #
    #    NOTA: script principale e moduli da local_modules sono gia' stati
    #    aggiunti sopra senza filtri ("force"). Se l'utente ha esplicitamente
    #    dichiarato un modulo in build.json, lo includiamo anche se il nome
    #    cadrebbe sotto un pattern di esclusione (scelta consapevole).
    excluded_dirs_lower = {d.lower() for d in ARCHIVE_EXCLUDED_DIRS}
    already_in_set = {f.resolve() for f in result["app_files"]}
    for f in sorted(app_dir.rglob("*")):
        if not f.is_file():
            continue
        # Filtra cartelle escluse o nascoste in qualunque punto del path relativo
        rel_parts = f.relative_to(app_dir).parts
        if any(part.lower() in excluded_dirs_lower or part.startswith(".")
               for part in rel_parts[:-1]):
            continue
        # Filtra file nascosti
        if f.name.startswith("."):
            continue
        # Filtra per estensione
        if f.suffix.lower() not in ARCHIVE_EXTENSIONS:
            continue
        # Skip duplicati (script principale, moduli locali gia' aggiunti)
        if f.resolve() in already_in_set:
            continue
        # Filtra duplicati Finder e backup, tracciando il motivo per il log
        reason = file_exclude_reason(f)
        if reason:
            result["excluded"].append((f, reason))
            continue
        result["app_files"].append(f)
        already_in_set.add(f.resolve())

    # 4) Tutto il contenuto di _Config/<app_name>/
    cfg_dir = config_root(config_path) / app_name
    if cfg_dir.exists():
        result["config_root"] = cfg_dir
        for f in sorted(cfg_dir.rglob("*")):
            if f.is_file() and not f.name.startswith("."):
                result["config_files"].append(f)
    else:
        result["missing"].append(f"_Config/{app_name}/ (cartella mancante)")

    return result


def archive_max_mtime(files: dict) -> float:
    """
    mtime massimo di tutti i file raccolti da collect_archive_files.
    """
    mtimes = []
    for f in files.get("app_files", []) + files.get("shared_files", []) + files.get("config_files", []):
        try:
            mtimes.append(f.stat().st_mtime)
        except Exception:
            pass
    return max(mtimes) if mtimes else 0.0


def find_latest_archive(config_path: Path, app_name: str) -> Path | None:
    """
    Trova lo zip piu' recente per un'app in _Archivio/.
    Cerca pattern: <app_name>*.zip (es. AppBuilder_v2.3_20260518.zip).
    Restituisce il Path con mtime piu' alto, oppure None se non ce ne sono.
    """
    arch = archive_root(config_path)
    if not arch.exists():
        return None
    # Pattern: <app_name> seguito eventualmente da _v... e _data
    # Match esatto sul prefisso per evitare collisioni (es. "App" vs "AppBuilder")
    candidates = []
    for z in arch.glob("*.zip"):
        stem = z.stem
        # Match: stem == app_name OR stem inizia con app_name + "_"
        if stem == app_name or stem.startswith(f"{app_name}_"):
            candidates.append(z)
    if not candidates:
        return None
    return max(candidates, key=lambda p: p.stat().st_mtime)


def build_archive_name(app_name: str, version: str | None) -> str:
    """
    Costruisce il nome del file zip: NomeApp_v2.3_20260518.zip
    Se version e' None: NomeApp_20260518.zip
    """
    today = datetime.date.today().strftime("%Y%m%d")
    if version:
        return f"{app_name}_v{version}_{today}.zip"
    return f"{app_name}_{today}.zip"


def check_needs_archive(cfg: dict, config_path: Path) -> tuple[bool, str, dict]:
    """
    Verifica se un'app deve essere ri-archiviata.
    Confronta mtime massimo delle sorgenti vs mtime dello zip piu' recente.
    Restituisce (needs_archive: bool, reason: str, files_info: dict).
    """
    files_info = collect_archive_files(cfg, config_path)

    if files_info.get("missing"):
        return False, f"File mancanti: {', '.join(files_info['missing'][:2])}", files_info

    source_mtime = archive_max_mtime(files_info)

    app_name = cfg.get("app_name", "")
    latest_zip = find_latest_archive(config_path, app_name)

    if latest_zip is None:
        return True, "Nessuno zip esistente", files_info

    try:
        zip_mtime = latest_zip.stat().st_mtime
    except Exception:
        return True, "Impossibile leggere zip esistente", files_info

    if source_mtime > zip_mtime:
        mod_time = datetime.datetime.fromtimestamp(source_mtime).strftime("%d/%m/%y %H:%M")
        return True, f"Sorgenti modificate ({mod_time})", files_info

    zip_date = datetime.datetime.fromtimestamp(zip_mtime).strftime("%d/%m/%y")
    return False, f"Allineato (zip: {zip_date})", files_info


_DATE_RE = re.compile(r"_(\d{8})\.zip$")


def _zip_date(path: Path) -> datetime.date | None:
    m = _DATE_RE.search(path.name)
    if not m:
        return None
    try:
        return datetime.date(int(m.group(1)[:4]),
                             int(m.group(1)[4:6]),
                             int(m.group(1)[6:8]))
    except ValueError:
        return None


def prune_backup(app_name: str, config_path: Path, log_fn=None) -> int:
    """
    Applica la retention policy agli zip in _Archivio/_Backup/ per un'app.

    Retention:
      - < 14 giorni:        tieni tutto
      - 14-90 giorni:       1 per settimana ISO (il più recente)
      - 90 giorni - 1 anno: 1 per mese (il più recente)
      - > 1 anno:           1 per anno (il più recente)

    Restituisce il numero di file eliminati.
    """
    backup_dir = backup_root(config_path)
    if not backup_dir.exists():
        return 0

    today = datetime.date.today()

    # Raccoglie zip dell'app con data parsabile
    dated: list[tuple[datetime.date, Path]] = []
    for z in backup_dir.glob("*.zip"):
        if not (z.stem == app_name or z.stem.startswith(f"{app_name}_")):
            continue
        d = _zip_date(z)
        if d is None:
            continue
        dated.append((d, z))

    if not dated:
        return 0

    # Separa per bucket: keep_all / weekly / monthly / yearly
    keep_all: list[tuple[datetime.date, Path]] = []
    weekly: dict[tuple[int, int], list[tuple[datetime.date, Path]]] = {}
    monthly: dict[tuple[int, int], list[tuple[datetime.date, Path]]] = {}
    yearly: dict[int, list[tuple[datetime.date, Path]]] = {}

    for d, z in dated:
        age = (today - d).days
        if age < 14:
            keep_all.append((d, z))
        elif age < 90:
            key = (d.isocalendar()[0], d.isocalendar()[1])  # (anno ISO, settimana ISO)
            weekly.setdefault(key, []).append((d, z))
        elif age < 365:
            key = (d.year, d.month)
            monthly.setdefault(key, []).append((d, z))
        else:
            yearly.setdefault(d.year, []).append((d, z))

    # Per ogni bucket tieni il più recente, elimina gli altri
    to_delete: list[Path] = []
    for bucket in (weekly, monthly, yearly):
        for entries in bucket.values():
            entries.sort(key=lambda t: t[0], reverse=True)
            to_delete.extend(z for _, z in entries[1:])

    deleted = 0
    for z in to_delete:
        try:
            z.unlink()
            deleted += 1
            if log_fn:
                log_fn(f"   [info] retention: rimosso {z.name}\n")
        except Exception as e:
            if log_fn:
                log_fn(f"   [warn] impossibile rimuovere {z.name}: {e}\n")

    return deleted


def move_previous_archives_to_backup(
        app_name: str,
        config_path: Path,
        keep_zip_name: str | None = None,
        log_fn=None) -> int:
    """
    Sposta in _Archivio/_Backup/ tutti gli zip di un'app presenti in _Archivio/,
    escludendo (se indicato) `keep_zip_name` che resta in _Archivio/.

    Pattern di matching: stesso usato da find_latest_archive (stem == app_name
    OR stem inizia con app_name + "_"), per evitare collisioni tra prefissi
    simili (es. "App" vs "AppBuilder").

    Se nel _Backup esiste gia' uno zip con lo stesso nome, viene sovrascritto
    (comportamento voluto: doppia archiviazione in giornata non genera due
    backup distinti, mantiene uno per data).

    Ritorna il numero di file effettivamente spostati. Errori sui singoli
    file vengono loggati ma non interrompono l'operazione.
    """
    arch = archive_root(config_path)
    if not arch.exists():
        return 0

    backup_dir = backup_root(config_path)
    moved = 0

    for z in arch.glob("*.zip"):
        if not z.is_file():
            continue
        stem = z.stem
        if not (stem == app_name or stem.startswith(f"{app_name}_")):
            continue
        if keep_zip_name and z.name == keep_zip_name:
            continue

        # Lazy mkdir: creo _Backup/ solo se serve davvero spostare qualcosa
        if moved == 0:
            backup_dir.mkdir(parents=True, exist_ok=True)

        dest = backup_dir / z.name
        try:
            # shutil.move sovrascrive se dest esiste (comportamento voluto)
            if dest.exists():
                dest.unlink()
            shutil.move(str(z), str(dest))
            moved += 1
        except Exception as e:
            if log_fn:
                log_fn(f"   [warn] impossibile spostare {z.name} in _Backup/: {e}\n")

    return moved


def create_archive_zip(cfg: dict, config_path: Path, log_fn) -> tuple[bool, str]:
    """
    Crea il file zip per un'app in _Archivio/.
    Struttura interna:
      <NomeApp>_v<ver>_<data>.zip
        app/                    <- contenuto cartella app (script, .md, ecc.)
          script.py
          modulo_locale.py
          README.md
        _Config/<NomeApp>/      <- configurazioni
          build.json
          dati.json

    Prima di creare il nuovo zip, sposta in _Archivio/_Backup/ tutti gli zip
    precedenti della stessa app, in modo che in _Archivio/ resti solo lo zip
    piu' recente.

    Restituisce (success: bool, message: str).
    """
    app_name = cfg.get("app_name", "")
    if not app_name:
        return False, "app_name mancante in build.json"

    files_info = collect_archive_files(cfg, config_path)
    if files_info.get("missing"):
        return False, f"File mancanti: {', '.join(files_info['missing'])}"

    arch_dir = archive_root(config_path)
    arch_dir.mkdir(parents=True, exist_ok=True)

    version = cfg.get("version_detected")
    zip_name = build_archive_name(app_name, version)
    zip_path = arch_dir / zip_name

    # Sposta in _Backup/ gli zip precedenti dell'app (esclude lo zip che
    # stiamo per creare: se ha lo stesso nome del piu' recente esistente,
    # tipicamente perche' stiamo ri-archiviando lo stesso giorno, lo
    # sovrascriviamo direttamente in _Archivio/ senza passare da _Backup).
    moved = move_previous_archives_to_backup(
        app_name, config_path, keep_zip_name=zip_name, log_fn=log_fn)
    if moved:
        log_fn(f"   [info] {moved} zip precedente/i spostato/i in _Backup/\n")

    pruned = prune_backup(app_name, config_path, log_fn=log_fn)
    if pruned:
        log_fn(f"   [info] retention policy: {pruned} zip obsoleto/i rimosso/i da _Backup/\n")

    app_root = files_info["app_root"]
    config_subdir = files_info["config_root"]

    n_files = 0
    try:
        with zipfile.ZipFile(zip_path, "w", zipfile.ZIP_DEFLATED) as zf:
            # File dalla cartella app -> sotto "app/"
            for f in files_info["app_files"]:
                try:
                    rel = f.relative_to(app_root)
                    arcname = Path("app") / rel
                    zf.write(f, arcname.as_posix())
                    n_files += 1
                except Exception as e:
                    log_fn(f"   [warn] skip {f.name}: {e}\n")

            # Moduli da shared/ -> sotto "shared/"
            for f in files_info.get("shared_files", []):
                try:
                    arcname = Path("shared") / f.name
                    zf.write(f, arcname.as_posix())
                    n_files += 1
                except Exception as e:
                    log_fn(f"   [warn] skip shared {f.name}: {e}\n")

            # File da _Config/<app>/ -> sotto "_Config/<app>/"
            if config_subdir is not None:
                for f in files_info["config_files"]:
                    try:
                        rel = f.relative_to(config_subdir)
                        arcname = Path("_Config") / app_name / rel
                        zf.write(f, arcname.as_posix())
                        n_files += 1
                    except Exception as e:
                        log_fn(f"   [warn] skip config {f.name}: {e}\n")
    except Exception as e:
        return False, f"Errore creazione zip: {e}"

    return True, f"{zip_name} ({n_files} file)"


def diagnose_config_misalignments(base_path: Path, config_path: Path) -> dict:
    """
    Diagnostica disallineamenti tra cartelle app (in base_path) e cartelle
    di configurazione (in _Config/).

    Logica di riconoscimento: una cartella app si considera collegata a una
    cartella _Config quando vale ALMENO UNA di queste condizioni:
      (a) Nome diretto: cartella stable e _Config hanno lo stesso nome.
      (b) APP_NAME nel sorgente: lo script contiene APP_NAME = "X" e esiste
          _Config/X/.
      (c) build.json.script: una _Config/<X>/build.json ha un campo "script"
          che punta a un file dentro questa cartella app.
    Esempio (c): stable/Analisi portafoglio/AggiornaPortafoglio.py compare
    in _Config/AggiornaPortafoglio/build.json["script"], quindi le due
    cartelle sono legittimamente collegate anche se i nomi differiscono.

    Restituisce un dict con:
      - "missing_config": [(app_dir_name, suggested_config_match_or_None), ...]
          cartelle app sorgente che non risultano collegate ad alcuna _Config
      - "orphan_config": [(config_dir_name, suggested_app_match_or_None), ...]
          cartelle _Config/<X>/ a cui nessuna app fa riferimento (residui)
      - "app_name_mismatch": [(config_dir_name, app_name_in_json), ...]
          _Config/<X>/build.json con app_name diverso da <X> (vero bug)
      - "matched": [(app_dir_name, config_dir_name, link_method), ...]
          collegamenti riconosciuti; link_method e' uno di:
            "nome_diretto", 'APP_NAME="..."', "build.json.script"
    """
    result = {
        "missing_config": [],
        "orphan_config": [],
        "app_name_mismatch": [],
        "matched": [],
    }

    if not base_path.exists() or not config_path.exists():
        return result

    # Cartelle app candidate (in base_path), escludendo cartelle che iniziano
    # con _ (convenzione IRC: fuori produzione) o con punto (hidden)
    app_dirs = sorted([
        d for d in base_path.iterdir()
        if d.is_dir()
        and not d.name.startswith(".")
        and not d.name.startswith("_")
        and any(d.glob("*.py"))  # deve contenere almeno un .py a livello radice
    ])
    app_dir_names = [d.name for d in app_dirs]

    # Cartelle config esistenti (escludendo Logs e nascoste)
    config_dirs = sorted([
        d.name for d in config_path.iterdir()
        if d.is_dir() and not d.name.startswith(".") and d.name != "Logs"
    ])
    config_dirs_set = set(config_dirs)

    # Per ogni cartella app, costruisce la lista degli APP_NAME dichiarati
    # nei suoi script .py (radice della cartella) e nel build.json se presente
    # nella _Config con lo stesso nome.
    # app_to_appnames: { "Caricamento Temperature": {"CaricaTemperature"} }
    app_to_appnames: dict[str, set[str]] = {}
    for app_dir in app_dirs:
        names = set()
        # 1) Estrai APP_NAME dagli script .py della cartella radice
        for py in app_dir.glob("*.py"):
            extracted = extract_app_name(py)
            if extracted:
                names.add(extracted)
        # 2) Se esiste _Config/<stesso nome>/build.json, usa anche quello
        same_named_cfg = config_path / app_dir.name / "build.json"
        if same_named_cfg.exists():
            data = load_build_json(same_named_cfg)
            if data and data.get("app_name"):
                names.add(data["app_name"])
        # 3) Il nome cartella stesso e' sempre un candidato implicito
        names.add(app_dir.name)
        app_to_appnames[app_dir.name] = names

    # Costruisce il legame INVERSO: per ogni _Config/<X>/, qual e' la cartella
    # sorgente dichiarata nel suo build.json["script"]?
    # Questa e' la fonte di verita' piu' robusta perche' il build.json e'
    # stato salvato proprio per quella build, con il path corretto.
    # cfg_to_app_dir: { "AggiornaPortafoglio": "Analisi portafoglio" }
    cfg_to_app_dir: dict[str, str] = {}
    for cfg_dir in config_dirs:
        json_path = config_path / cfg_dir / "build.json"
        if not json_path.exists():
            continue
        data = load_build_json(json_path)
        if not data:
            continue
        script_field = data.get("script", "")
        if not script_field:
            continue
        # Risolve in path assoluto e estrae la cartella app
        try:
            script_abs = resolve_script_path(script_field, config_path)
            # La cartella app e' parent di script_abs, relativa a base_path
            try:
                rel = script_abs.relative_to(base_path)
                app_dir_from_json = rel.parts[0] if rel.parts else None
                if app_dir_from_json:
                    cfg_to_app_dir[cfg_dir] = app_dir_from_json
            except ValueError:
                # script fuori da base_path
                pass
        except Exception:
            pass

    # Risolve i collegamenti app -> _Config
    # Una cartella app si dichiara "collegata" se:
    #   a) il suo nome coincide con una _Config esistente, oppure
    #   b) ALMENO UNO dei suoi candidati APP_NAME corrisponde a una _Config, oppure
    #   c) ESISTE una _Config il cui build.json["script"] punta dentro questa cartella
    matched_configs = set()  # config_dirs che risultano collegate

    # Indice inverso per ricerca veloce: app_dir -> cfg_dir (da build.json)
    app_dir_to_cfg_via_json: dict[str, str] = {}
    for cfg_dir, app_dir_name in cfg_to_app_dir.items():
        # Preferisci il primo legame trovato (gli ulteriori vengono ignorati)
        if app_dir_name not in app_dir_to_cfg_via_json:
            app_dir_to_cfg_via_json[app_dir_name] = cfg_dir

    for app_dir_name, candidates in app_to_appnames.items():
        matched_config = None
        link_method = None
        # a) match diretto per nome
        if app_dir_name in config_dirs_set:
            matched_config = app_dir_name
            link_method = "nome_diretto"
        # b) match via APP_NAME nel sorgente
        if not matched_config:
            for cand in candidates:
                if cand != app_dir_name and cand in config_dirs_set:
                    matched_config = cand
                    link_method = f'APP_NAME="{cand}"'
                    break
        # c) match via build.json.script che punta a questa cartella
        if not matched_config and app_dir_name in app_dir_to_cfg_via_json:
            matched_config = app_dir_to_cfg_via_json[app_dir_name]
            link_method = "build.json.script"

        if matched_config:
            matched_configs.add(matched_config)
            result["matched"].append((app_dir_name, matched_config, link_method))
        else:
            # Nessun collegamento trovato: suggerisce match piu' probabile
            matches = difflib.get_close_matches(app_dir_name, config_dirs,
                                                n=1, cutoff=0.5)
            suggested = matches[0] if matches else None
            if not suggested:
                for cand in candidates:
                    fm = difflib.get_close_matches(cand, config_dirs,
                                                   n=1, cutoff=0.5)
                    if fm:
                        suggested = fm[0]
                        break
            result["missing_config"].append((app_dir_name, suggested))

    # Cartelle _Config orfane = non collegate a nessuna app
    for cfg_dir in config_dirs:
        if cfg_dir not in matched_configs:
            # Suggerisce app simile per nome
            matches = difflib.get_close_matches(cfg_dir, app_dir_names,
                                                n=1, cutoff=0.5)
            suggested = matches[0] if matches else None
            result["orphan_config"].append((cfg_dir, suggested))

    # build.json con app_name diverso dal nome cartella _Config parent.
    # Questo resta un disallineamento "duro" — il nome cartella _Config
    # DEVE essere uguale all'app_name del JSON (lo usa il rebuild).
    for cfg_dir in config_dirs:
        json_path = config_path / cfg_dir / "build.json"
        if json_path.exists():
            data = load_build_json(json_path)
            if data and data.get("app_name") and data["app_name"] != cfg_dir:
                result["app_name_mismatch"].append((cfg_dir, data["app_name"]))

    return result
