# AppBuilder_PyInstaller

## Descrizione Breve

App Builder (PyInstaller) - Tkinter GUI - Versione: 3.1.0 - CLD

## Uso e Parametri

```
usage: AppBuilder_PyInstaller.py [-h] [--base_path BASE_PATH]
                                 [--icon_path ICON_PATH]
                                 [--config_path CONFIG_PATH]

Python App Builder con PyInstaller

options:
  -h, --help                    show this help message and exit
  --base_path BASE_PATH         Cartella contenente gli script .py
                                (default: ~/Dropbox/.../Python)
  --icon_path ICON_PATH         Cartella di default per le icone .icns
                                (default: ~/Dropbox/.../Python/Icons)
  --config_path CONFIG_PATH     Cartella _Config dove salvare i build.json
                                (default: ~/Dropbox/.../Python/_Config)
```

## Descrizione Completa

App Builder (PyInstaller) - Tkinter GUI
Versione: 3.1.0

Funzionalità:
- Seleziona uno script .py (da base_path)
- Rileva automaticamente moduli locali importati dallo script (RICORSIVO)
- Opzionale: icona .icns o .png (i .png vengono convertiti in .icns via sips+iconutil)
- Opzionale: hidden-import per moduli non rilevati automaticamente
- Build con PyInstaller → .app
- Rimozione quarantena (xattr via find) → copia nella cartella di installazione (ditto)
- Pulizia bundle (dot_clean + xattr)
- Codesign ad hoc
- Salvataggio automatico build.json in `_Config/<AppName>/` dopo ogni build manuale
- **Tab Manuale** — selezione singola, configurazione, build
- **Tab Batch / Catalog** — gestione blocco delle app via build.json
- **Tab Archiviazione** (v2.3+) — generazione/aggiornamento zip in `_Archivio/`
- **Diagnostica disallineamenti** (v2.3+, migliorata v2.4) — riconosce automaticamente i collegamenti cartella-app ↔ cartella-config
- **Archiviazione snapshot intermedio** (v2.5) — include `.command`, `.sh`, `.json`, `.yaml`, ecc. oltre a `.py` e `.md`
- **Pulisci log** (v2.5) — bottone nei tab Batch e Archiviazione
- **Dry-run e Visualizza ZIP** (v3.1) — anteprima dell'archiviazione senza creare nulla, e visualizzazione albero dello zip già creato
- **Filtri esclusione raffinati** (v3.1) — duplicati Finder ("nome copy.ext") e file di backup ("nome_bak", "*.bak") sono esclusi dagli snapshot, con regex case-sensitive che NON colpisce nomi legittimi (es. "Obsidian MD Copy.md")
- **Logging su file** (v3.1) — ogni operazione (build manuale, build batch, archiviazione) produce un log in `~/Documents/log/` accessibile da bottone "📂 Apri cartella log"

## Architettura UI

L'interfaccia è divisa in due sezioni:

### Intestazione comune (sempre visibile)
- **Cartella script (.py)** — base_path, modificabile
- **Python builder** — interprete Python con PyInstaller, rilevato automaticamente
- **Installa in** — cartella di destinazione delle .app (default: /Applications/Python Apps)
- **Cartella _Config** — cartella dove vengono salvati i build.json (default: Python/_Config)

### Tab Manuale
Modalità di lavoro classica: selezione script dalla lista, configurazione opzioni, build singolo.

Alla selezione di uno script:
- Viene eseguita analisi ricorsiva degli import locali
- Se lo script dichiara la costante `APP_NAME`, viene usata come nome app al posto del nome derivato dal nome file
- Se esiste un build.json per quell'app, i campi vengono pre-popolati automaticamente
- Viene rilevato automaticamente il Python builder del progetto

Dopo un build riuscito:
- Salva automaticamente `_Config/<AppName>/build.json` con tutti i parametri usati
- Scrive un log dettagliato in `~/Documents/log/AppBuilder_build_<ts>_<NomeApp>.log` (v3.1)

**Bottoni in basso (v3.1):**
- Esegui build
- 📂 Apri cartella log — apre `~/Documents/log/` nel Finder
- 🧹 Pulisci log — svuota il riquadro log
- Esci

### Tab Batch / Catalog
Gestione di più applicazioni in blocco, basata sui file build.json presenti in `_Config/`.

**Colonne della tabella:**
- App — nome dell'applicazione
- Script — percorso relativo a Python/ (o assoluto)
- Versione — estratta dallo script
- Stato — codice colore: Verde ✓ aggiornata, Giallo ↺ rebuild necessario, Grigio disabilitata, Rosso ✗ errore

**Bottoni:**
- Aggiorna lista, Verifica tutto, Build obsolete, Build selezionate, Build tutte, Abilita/Disabilita

A fine sessione batch, viene scritto un log riepilogativo in `~/Documents/log/AppBuilder_build_<ts>.log` con sezione "RIEPILOGO PER APP" (✓ OK / ✗ FAIL con messaggio) seguita dal log a video verbatim (v3.1).

**Bottoni accessori sotto il log batch (v3.1):**
- 📂 Apri cartella log
- 🧹 Pulisci log

### Tab Archiviazione (v2.3, riorganizzato v3.1)

Gestione automatica degli zip di backup in `_Archivio/`. Stessa logica del tab Batch ma applicata agli zip invece che ai bundle `.app`.

**Colonne della tabella:**
- App, Versione, Ultimo zip, Stato

**Pulsanti — Riga 1 (esplorazione, v3.1):**
- ↻ Aggiorna lista — ricarica la lista dei build.json
- ✓ Verifica tutto — ricalcola lo stato di tutte le app
- 🔍 Verifica disallineamenti — apre finestra di diagnostica (vedi sotto)

**Pulsanti — Riga 2 (esecuzione, v3.1):**
- 🔬 Dry-run — simulazione senza creare nulla (vedi sotto)
- ↺ Archivia obsolete — crea zip solo per le app con sorgenti più recenti dell'ultimo zip
- ▶ Archivia selezionate
- ▶▶ Archivia tutte
- 🗂 Visualizza ZIP — apre l'albero del contenuto dell'ultimo zip dell'app selezionata (vedi sotto)

La disposizione in due righe segue il workflow naturale: prima si **esplora** lo stato (riga 1), poi si **esegue** dal dry-run alla consultazione del risultato (riga 2).

**Bottoni accessori sotto il log di archiviazione (v3.1):**
- 📂 Apri cartella log
- 🧹 Pulisci log

**Contenuto di ogni zip** (snapshot intermedio v2.5):
```
NomeApp_v<versione>_<YYYYMMDD>.zip
├── app/                          ← cartella sorgente
│   ├── script.py
│   ├── modulo_locale.py
│   ├── backup_iClip.command      ← script shell macOS attivi
│   ├── README.md
│   ├── docs/  (e sottocartelle)
│   ├── config.yaml
│   ├── requirements.txt
│   └── ...
└── _Config/<NomeApp>/            ← cartella di config
    ├── build.json
    └── *.json / *.yaml
```

Sono inclusi:
- Script principale + moduli locali da `local_modules` in build.json (esclusi moduli di `shared/`)
- **Ricorsivamente dalla cartella app**: file con estensione `.py`, `.md`, `.command`, `.sh`, `.json`, `.yaml`, `.yml`, `.toml`, `.cfg`, `.txt`, `.ini`, `.plist`
- Tutto il contenuto di `_Config/<NomeApp>/` (ricorsivo)

Sono **esclusi** dalla scansione della cartella app:
- Cartelle (match case-insensitive sul nome esatto, v3.1): `__pycache__`, `.venv`, `venv`, `env`, `build`, `dist`, `.git`, `.svn`, `.hg`, `.idea`, `.vscode`, `.pytest_cache`, `.mypy_cache`, `.ruff_cache`, `node_modules`, `.DS_Store`, **e dalla v3.1 anche** `old`, `copy`, `copies`, `bak`, `backup`, `backups`, `archive`, `archives`, `tmp`, `temp`, `deprecated`, `obsolete`
- File nascosti (`.qualcosa`)
- File con estensione non in `ARCHIVE_EXTENSIONS` (es. `.pdf`, `.png`, `.zip`, `.xlsx`)
- **File con pattern Finder/backup (v3.1)**: vedi sezione dedicata sotto

Per modificare le estensioni incluse/escluse, vedi le costanti `ARCHIVE_EXTENSIONS` e `ARCHIVE_EXCLUDED_DIRS` in cima a `ab_archive.py`.

**Logica "obsoleto":** se `mtime_max(sorgenti) > mtime(ultimo_zip)` → da riarchiviare. Lo zip più recente per app è quello con mtime massimo tra tutti i file che iniziano per `<app_name>_` o `<app_name>.zip` (la ricerca è limitata a `_Archivio/`, non scende in `_Archivio/_Backup/`).

**Naming zip:** `NomeApp_v<versione>_<YYYYMMDD>.zip`.

**Rotazione automatica in `_Backup/` (v3.0.1):** prima di creare un nuovo zip, AppBuilder sposta in `_Archivio/_Backup/` tutti gli zip precedenti della stessa app. In `_Archivio/` resta quindi solo lo zip più recente per ciascuna app; tutto lo storico vive in `_Archivio/_Backup/`. Se in `_Backup/` esiste già uno zip con lo stesso nome (caso raro di doppia archiviazione nella stessa giornata con stessa versione), viene sovrascritto: si mantiene al massimo un backup per data per versione. La cartella `_Backup/` viene creata solo quando serve davvero spostare qualcosa.

### Filtri esclusione file: duplicati Finder e backup (v3.1)

Oltre ai filtri per cartelle e estensione, dalla v3.1 la scansione esclude due categorie di file dal contenuto dello zip:

**1. Duplicati Finder** — file generati da Cmd-D o Cmd-C/Cmd-V nel Finder.

Schema reale prodotto dal Finder:
```
script.py        →  script copy.py
script copy.py   →  script copy 2.py
script copy 2.py →  script copy 3.py
```

Pattern di rilevamento (in `ab_archive.py`, costante `_FINDER_COPY_RE`):
```python
re.compile(r" copy(?: \d+)?$")
```
- Case-sensitive: solo `copy` minuscolo (mai `Copy`, `COPY`)
- Ancorato a fine stem
- Preceduto da uno spazio, eventualmente seguito da spazio + numero

**Nomi legittimi che NON vengono colpiti** (esempi reali nel sistema):
- `Obsidian MD Copy.md` — parte del nome dell'applicativo "Gestione MD Obsidian" (parola "Copy" con C maiuscola)
- `my_copy_tool.py`, `copy_helper.py`, `recopy.py` — "copy" come parte legittima del nome
- `policy.py` — contiene "cop" come sottostringa

**2. File di backup** — pattern per identificare file di backup espliciti:

Pattern (costante `_BAK_TOKEN_RE`):
```python
re.compile(r"(?:^|_)bak(?:_|$)", re.IGNORECASE)
```
- Case-insensitive
- Solo come token delimitato da inizio stringa o underscore

Più check separato per estensione `.bak`:
```python
if path.suffix.lower() == ".bak":
    return "backup (estensione .bak)"
```

**File esclusi**: `script_bak.py`, `file.bak`, `20260507_bak.zip`, `_bak_1.zip`.
**File NON esclusi**: `bakery.py`, `Bakelite.md`, `MyBakapp.py` (non match come token).

**Forzatura tramite build.json**: lo script principale e i moduli dichiarati in `local_modules` del `build.json` sono SEMPRE inclusi, anche se il nome cadrebbe sotto un pattern di esclusione (scelta consapevole dell'utente).

### Dry-run (v3.1)

Il bottone **🔬 Dry-run** apre una finestra che mostra, **senza creare nulla**, cosa entrerebbe nello zip se si lanciasse l'archiviazione adesso.

Workflow:
- Se sono selezionate una o più app nella tree → simula solo quelle
- Se nessuna selezione → chiede conferma per simulare tutte le app

Output formattato per ogni app:
- 📁 **Cartella app** (path relativo a `Documenti_IRC/Python/`):
  - Inclusi (lista file con tick verde)
  - Esclusi dai filtri (lista file con motivo: "duplicato Finder", "backup", ecc.)
- 📁 **Configurazione** (`_Config/<app>/`):
  - Inclusi
- Totale per app: N inclusi, M esclusi

In cima alla finestra: riepilogo globale (N app simulate, totale inclusi, totale esclusi, eventuali app con file critici mancanti).

ESC chiude la finestra.

### Visualizza ZIP (v3.1)

Il bottone **🗂 Visualizza ZIP** apre l'albero del contenuto dell'ultimo zip dell'app selezionata (quello in `_Archivio/`, non in `_Archivio/_Backup/`).

Caratteristiche:
- Vista ad albero gerarchica espandibile, cartelle e file
- Icone per estensione: 🐍 `.py`, 📝 `.md`, `{}` `.json`, 🖼 `.png/.icns/.jpg`, ⚙️ `.yaml/.toml/.cfg/.ini/.plist`, ⚡ `.command/.sh`, 📦 `.zip`, 📄 default
- Colonne: Nome, Dimensione, Data
- Bottone "Apri in Finder" per rivelare il file zip nel Finder
- ESC chiude la finestra

### Diagnostica disallineamenti (v2.4)

Verifica la coerenza tra cartelle app (in `stable/`) e cartelle config (in `_Config/`).

**Criteri di riconoscimento (in ordine di priorità):**
1. **Nome diretto** — `stable/X/` e `_Config/X/` hanno lo stesso nome
2. **APP_NAME nel sorgente** — uno script in `stable/X/` contiene `APP_NAME = "Y"` ed esiste `_Config/Y/`
3. **build.json.script** — un `_Config/Y/build.json` ha `"script": "stable/X/..."`, collegando X a Y

Le cartelle con prefisso `_` sono escluse dalla diagnostica (convenzione "fuori produzione").

**Anomalie segnalate:**
- **Cartelle app senza _Config**: cartelle in `stable/` che non risultano collegate ad alcuna `_Config/`. Generalmente sono app mai impacchettate come `.app`.
- **Cartelle _Config orfane**: `_Config/<X>/` a cui nessuna app sorgente fa riferimento. Possono essere:
  - Veri residui di app cancellate
  - Cartelle di config dati di script in `Python launcher/Apps to launch/` (dati vivi)
  - Predecessori di config attuali (vedi `Disallineamenti_da_sistemare.md`)
- **app_name mismatch**: `_Config/<X>/build.json` ha `app_name` diverso da `<X>`. È un vero bug perché la coppia `(nome cartella _Config, app_name)` deve sempre combaciare (è usata dal rebuild).

**Sezione "Collegamenti riconosciuti"** (informativa, in fondo): elenco di tutti i match validi con indicazione del metodo di linking.

ESC chiude la finestra (v3.1).

## Logging su file (v3.1)

A partire dalla v3.1, ogni operazione di AppBuilder produce un file di log dettagliato nella cartella canonica `~/Documents/log/`, sincronizzata in iCloud (quindi i log sono accessibili da tutti i Mac).

Modulo dedicato: `ab_log.py`.

**Convenzioni di naming:**

| Operazione | File di log | Granularità |
|---|---|---|
| Build manuale (tab Manuale) | `AppBuilder_build_<YYYYMMDD_HHMMSS>_<NomeApp>.log` | uno per build |
| Build batch (tab Batch) | `AppBuilder_build_<YYYYMMDD_HHMMSS>.log` | uno per sessione |
| Archiviazione (tab Archiviazione) | `AppBuilder_archive_<YYYYMMDD_HHMMSS>.log` | uno per sessione |

**Contenuto comune a tutti i log:**
- Intestazione con timestamp, label sessione, esito (n_ok / n_fail)
- Sezione dettaglio specifica per operazione (vedi sotto)
- In coda: dump verbatim del log a video, riga per riga

**Sezione dettaglio per operazione:**

- **Build manuale**: metadati nell'intestazione (script_path, target, python_builder, version_detected, icon, windowed, hidden_imports, local_modules)
- **Build batch**: "RIEPILOGO PER APP" con per ciascuna app `✓ OK <Nome> v<versione>` o `✗ FAIL <Nome>` + messaggio errore + path target
- **Archiviazione**: "DETTAGLIO FILE PER APP" con per ciascuna app la lista dei file inclusi nello zip + i file scartati dai filtri (con motivo). Audit trail utile per verificare l'effetto dei filtri esclusione.

**Gestione errori**: la scrittura del log è protetta da `try/except` diagnostico che stampa il traceback su `stderr` (visibile dal terminale di lancio di AppBuilder) se la scrittura fallisce. A video viene comunque mostrato il feedback:
- `📄 Log sessione salvato: <path>` (successo)
- `⚠ Impossibile salvare il log su file (vedi traceback nel terminale di lancio)` (fallimento)

**Bottoni di accesso ai log** (presenti in tutti e tre i tab):
- **📂 Apri cartella log** — apre `~/Documents/log/` nel Finder
- **🧹 Pulisci log** — svuota solo il riquadro log a video (i file su disco non vengono toccati)

## Struttura modulare (v3.0+)

A partire dalla v3.0 il codice di AppBuilder è suddiviso in moduli con responsabilità separate:

| File | Responsabilità |
|---|---|
| `AppBuilder_PyInstaller.py` | Entry-point, argparse |
| `ab_gui_main.py` | Shell principale, intestazione comune, gestione tab |
| `ab_gui_manual.py` | Tab Manuale (build singola) |
| `ab_gui_batch.py` | Tab Batch / Catalog (build di gruppo) + costanti colore/stato |
| `ab_gui_archive.py` | Tab Archiviazione + dry-run + visualizza ZIP |
| `ab_build.py` | Engine PyInstaller (build, codesign, ditto, ecc.) |
| `ab_archive.py` | Engine archiviazione zip + diagnostica disallineamenti |
| `ab_config.py` | Lettura/scrittura build.json, risoluzione path, check_needs_rebuild |
| `ab_log.py` (v3.1) | Logging su file: `LOG_DIR`, `write_archive_session_log`, `write_build_log`, `write_build_session_log` |
| `ab_utils.py` | Utility comuni: `find_local_imports` (ricorsivo), `extract_app_name`, `extract_version`, `max_mtime`, `run_cmd`, ecc. |

Gli engine (`ab_build.py`, `ab_archive.py`) **non scrivono direttamente su file di log**: ricevono una callback `log_fn` dal tab GUI e parlano solo a video. La scrittura su file è responsabilità del tab GUI, che bufferizza le righe e a fine operazione chiama una delle funzioni di `ab_log.py`. Questa separazione mantiene gli engine puri e riutilizzabili.

## Struttura file build.json

```json
{
  "app_name": "AggiornaPortafoglio",
  "script": "stable/Analisi portafoglio/AggiornaPortafoglio.py",
  "icon": "stable/Icons/AggiornaPortafoglio_1024.png",
  "windowed": true,
  "hidden_imports": [],
  "local_modules": ["path_widgets"],
  "install_dir": "/Applications/Python Apps",
  "python_builder": "~/Python_venv/stable/bin/python3",
  "version_detected": "1.0",
  "built_at": "2026-05-18T11:30:00",
  "source_mtime": 1747570000.0,
  "enabled": true
}
```

Vincolo importante: `app_name` **deve** coincidere con il nome della cartella `_Config/<app_name>/` che contiene il `build.json`. Il diagnostico segnala come anomalia ogni divergenza.

I campi `built_at` e `source_mtime` vengono aggiornati ad ogni build riuscito.

## Costante APP_NAME

Per default AppBuilder deriva il nome dell'app dal nome del file script con capitalizzazione automatica (es. `ms_reconciler.py` → `MsReconciler`).

Per usare un nome diverso, dichiarare `APP_NAME` nel sorgente:
```python
APP_NAME = "Riconciliazione Moneyspire"
```

AppBuilder legge la costante con regex su riga intera e la usa come nome app. Effetti:
- Build.json salvato in `_Config/<APP_NAME>/build.json`
- Bundle installato `<APP_NAME>.app`

## Logica di rilevamento aggiornamento (tab Batch)

Per ogni app, confronta **localmente sulla macchina corrente**:
- `mtime_max(script principale, moduli locali rilevati)`
- vs. `mtime(bundle .app installato in install_dir)`

Funziona cross-macchina (Dropbox condiviso): ogni Mac confronta i propri valori locali. Se il bundle non esiste o le sorgenti sono più nuove → rebuild.

Il campo `source_mtime` nel build.json è un riferimento storico, non usato per la verifica.

**Caveat sull'mtime (v3.1)**: il check si basa sull'mtime dei sorgenti, che può essere "ingannato" da copie con `cp -p`, da sincronizzazioni Dropbox, da rsync, da copie tra Mac. Se sostituisci i `.py` con `cp -p`, l'mtime originale viene preservato e il check potrebbe non vedere il cambiamento. In questi casi:

```bash
touch <file_modificato>.py
```

forza l'mtime a "adesso", rendendo il file più recente del bundle e quindi visibile come "Da ricostruire".

## Bootstrap (prima installazione di AppBuilder stesso)

```bash
cd ~/Dropbox/.../Python/AppBuilder_3.0
python3 AppBuilder_PyInstaller.py
```

Dopo il primo build manuale, `AppBuilder.app` viene installato in `/Applications/Python Apps/` e il suo build.json viene scritto in `_Config/AppBuilder/build.json`. Da quel momento `AppBuilder.app` può gestire tutti gli altri build in batch.

**Suggerimento operativo importante**: **durante lo sviluppo di AppBuilder stesso, lanciarlo SEMPRE da terminale** (`python3 AppBuilder_PyInstaller.py`) e non dal bundle.

Motivazione:
1. Il bundle `.app` contiene una **copia interna** dei `.py` al momento del build. Se modifichi un sorgente in `stable/`, il bundle in esecuzione NON vede le modifiche (legge la sua copia interna).
2. Il bundle non può sovrascrivere se stesso durante un rebuild (file in uso).

Workflow consigliato per modifiche ad AppBuilder:
1. Chiudere eventuali istanze di `AppBuilder.app` in esecuzione
2. Modificare i `.py` in `stable/AppBuilder_3.0/`
3. `rm -rf __pycache__` per evitare che Python carichi bytecode obsoleto
4. `python3 AppBuilder_PyInstaller.py` per test diretto
5. Quando soddisfatto, fare un rebuild da terminale e poi usare il bundle aggiornato

## Note tecniche

- I percorsi `script` e `icon` nel build.json sono relativi a `Python/` (cartella padre di `_Config/`). Per script fuori dalla struttura standard, vengono salvati come assoluti.
- Il Python builder viene cercato nell'ordine: variabile `PYTHON_BUILDER`, `sys.executable` (se non frozen), `.venv` del progetto, percorsi noti, `python3` di sistema.
- Richiede PyInstaller installato nell'interprete builder.
- Tutti gli iid del Treeview nei tab Batch e Archiviazione sono stringhe Tk (`"I001"`, `"I002"`, ...). Quando si lavora sulla selezione, usare l'helper `_selected_configs()` invece di tentare `int(iid)`.

## Gestione script lanciati come sottoprocessi (v2.1)

Per app che usano `subprocess.Popen([sys.executable, "engine.py", ...])` invece di `import`: AppBuilder scansiona la cartella dello script principale e aggiunge automaticamente `--add-data file.py:.` per i `.py` non importati. Nel log appaiono come righe `[add-data] nome.py`.

Pattern consigliato per identificare il Python interpreter dentro un bundle:
```python
def _get_python() -> str:
    if not getattr(sys, "frozen", False):
        return sys.executable
    candidates = [Path(sys._MEIPASS).parent / "python3", ...]
    for c in candidates:
        if c.exists():
            return str(c)
    import shutil
    return shutil.which("python3") or sys.executable
```

## Convenzione cartelle ignorate (prefisso `_`)

A partire dalla v2.3, tutte le cartelle in `stable/` che iniziano con underscore (`_AppBuilder_2.0 copy/`, `_Riconciliazione Money copy.CLD/`, ecc.) sono **ignorate** dal tab Batch, dal tab Archiviazione e dalla diagnostica disallineamenti. Utile per mantenere copie di backup accanto a quelle in produzione senza che vengano elaborate.

Lo stesso prefisso si usa per **utility spot** (script di servizio non destinati a essere impacchettati come `.app`, es. `_Utility spot/`, `_Venv/`).

## Predecessore: ArchiviaProgetto

Fino a v3.0, esisteva un applicativo separato `ArchiviaProgetto.py` (v1.3.0, maggio 2026) con funzionalità di archiviazione. La v3.1 di AppBuilder ha assorbito tutte le sue capacità nel tab Archiviazione:

| Funzionalità ArchiviaProgetto | Equivalente AppBuilder v3.1 |
|---|---|
| Anteprima file inclusi/esclusi | 🔬 Dry-run |
| Visualizza ZIP a treeview | 🗂 Visualizza ZIP |
| Filtri esclusione "copy" / "bak" | Filtri raffinati (case-sensitive, non distruttivi) |
| Log su file in `~/Documents/log/` | Log dedicato `AppBuilder_archive_*.log` |
| Backup zip precedenti | Rotazione `_Archivio/_Backup/` |
| Naming `App.zip` | Naming versionato `App_v<ver>_<YYYYMMDD>.zip` |

`ArchiviaProgetto.py` e la sua `_Config/ArchiviaProgetto/` sono stati rimossi dopo la migrazione delle funzionalità.

---

## Storico modifiche

- **v3.1.0 (26/05/2026)** — Diverse novità nel tab Archiviazione e nel sistema di logging:
  - Tab Archiviazione: riorganizzazione bottoni in due righe per workflow (esplorazione vs esecuzione)
  - Nuovo bottone **🔬 Dry-run** — simulazione archiviazione senza creare nulla, con sezioni "Cartella app" e "Configurazione" + Inclusi/Esclusi (e motivo per gli esclusi)
  - Nuovo bottone **🗂 Visualizza ZIP** — vista ad albero del contenuto dello zip con icone per estensione, dimensioni e date
  - Filtri esclusione raffinati: regex case-sensitive per duplicati Finder (`" copy"`, `" copy N"`), regex case-insensitive ma tokenizzata per backup (`^bak`, `_bak_`, `_bak$`, `.bak`). Non scarta nomi legittimi come `Obsidian MD Copy.md`, `bakery.py`, `my_copy_tool.py`
  - Cartelle escluse estese: `old`, `copy`, `copies`, `bak`, `backup`, `backups`, `archive`, `archives`, `tmp`, `temp`, `deprecated`, `obsolete` (match case-insensitive)
  - Nuovo modulo `ab_log.py` con logging centralizzato per le tre operazioni (archive_session, build_manuale, build_batch_session) in `~/Documents/log/`
  - Tab Manuale, Batch, Archiviazione: nuovi bottoni **📂 Apri cartella log** e **🧹 Pulisci log** dove non già presenti
  - Tutti i log includono dump verbatim del log video + sezione dettaglio per operazione (file inclusi/esclusi per archive, riepilogo per app per batch, metadati build per manuale)
  - Fix: `view_zip_selected` e `dry_run_selected` usavano `int(iid)` su iid Tk in formato `"I001"` (latente in `dry_run_selected`, manifesto in `view_zip_selected`); ora entrambi usano `_selected_configs()`
  - ESC chiude tutte le finestre Toplevel del tab Archiviazione (Dry-run, Visualizza ZIP, Verifica disallineamenti)
  - Migrazione/dismissione di ArchiviaProgetto: funzionalità assorbite, applicativo separato rimosso
- **v3.0.1 (22/05/2026)** — In archiviazione, gli zip precedenti di ciascuna app vengono spostati automaticamente in `_Archivio/_Backup/` prima della creazione del nuovo zip. In `_Archivio/` resta solo lo zip più recente per app. Sovrascrittura in `_Backup/` se esiste già zip con lo stesso nome (massimo uno per data/versione).
- **v3.0** — Refactoring modulare: il singolo file storico è stato suddiviso in più moduli con responsabilità separate (`ab_archive.py` engine di archiviazione, `ab_build.py` build PyInstaller, `ab_config.py` gestione build.json e percorsi, `ab_gui_main.py` shell principale, `ab_gui_batch.py` / `ab_gui_archive.py` / `ab_gui_manual.py` i tab, `ab_utils.py` utility comuni). `AppBuilder_PyInstaller.py` resta come entry-point. Versioni intermedie (v2.6–v2.9) non documentate qui.
- **v2.5 (18/05/2026)** — Archiviazione "snapshot intermedio": include `.command`, `.sh`, `.json`, `.yaml`, `.toml`, `.cfg`, `.ini`, `.plist` oltre a `.py` e `.md`. Esclusione esplicita di `__pycache__`, `.venv`, `build`, `dist`, `.git`, ecc. Bottone "Pulisci log" nei tab Batch e Archiviazione.
- **v2.4 (18/05/2026)** — Diagnostica disallineamenti potenziata con 3 criteri di riconoscimento (nome diretto, APP_NAME, build.json.script). Aggiunta sezione "Collegamenti riconosciuti" nella finestra di report. Riorganizzazione UI tab Archiviazione: pulsanti su due righe (operazioni e verifica).
- **v2.3 (18/05/2026)** — Aggiunto tab Archiviazione (zip in `_Archivio/`) e prima versione della diagnostica disallineamenti. Convenzione `_` per cartelle ignorate.
- **v2.2** — Costante `APP_NAME` per controllare il nome cartella `_Config` indipendentemente dal nome del file `.py`.
- **v2.1** — `--add-data` automatico per script lanciati come sottoprocessi.
- **v2.0** — Salvataggio automatico build.json + modalità Batch.

---

*Documentazione aggiornata: 2026-05-26*
