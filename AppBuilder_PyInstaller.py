#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
App Builder (PyInstaller) - Tkinter GUI
Versione: 3.1.2

Lanciatore dell'applicazione. La logica e' suddivisa in 8 moduli ab_*.py:

  ab_utils.py         Utility pure (rilevamento Python builder, AST imports,
                      estrazione version/APP_NAME, mtime helpers).
  ab_config.py        Gestione build.json (load/save/discover, check_needs_rebuild).
  ab_build.py         execute_build() - flusso PyInstaller completo.
  ab_archive.py       Archiviazione zip in _Archivio/ e diagnostica
                      disallineamenti app <-> _Config.
  ab_gui_main.py      Classe BuilderUI con intestazione comune e Notebook.
  ab_gui_manual.py    Tab Manuale (build singola).
  ab_gui_batch.py     Tab Batch (build di gruppo) + costanti colori/stato.
  ab_gui_archive.py   Tab Archiviazione (zip in _Archivio/).

Storico funzionale (vedi commit precedenti per dettagli):
- [v2.3] Tab Archiviazione: genera/aggiorna automaticamente gli zip in
         _Archivio/ (script + moduli locali + .md ricorsivi + _Config/<app>/).
         Diagnostica disallineamenti tra cartelle app e cartelle _Config.
- [v2.4] Diagnostica APP_NAME-aware: riconosce il pattern
         "cartella stable italiana + _Config CamelCase" collegate tramite la
         costante APP_NAME nel sorgente, senza segnalarlo come anomalia.
- [v2.5] Archiviazione "snapshot intermedio": include nel zip non solo .py
         e .md, ma anche .command, .sh, .json, .yaml, .toml, .ini, .plist.
         Escluse esplicitamente cache, .venv, build/, dist/, .git/.
         Bottone "Pulisci log" nei tab Batch e Archiviazione.
- [v3.1.2] Fix: python_builder salvato home-relative in build.json (portabile
           tra Mac con username diversi); warning visibile se _Config/<app>/
           mancante durante archiviazione (non più bloccante)
- [v3.1.0] Fix: thread safety ArchiveTab, save_build_json merge,
           local_modules cablato, extract_version regex, filtro copy/bak add-data
- [v3.0] Refactoring: file unico (~2500 righe) spaccato in 8 moduli ab_*.py
         per migliorare la gestibilita' senza alterare il comportamento.

Parametri linea di comando:
  --base_path     Cartella contenente gli script .py
  --icon_path     Cartella di default per le icone .icns
  --config_path   Cartella _Config dove salvare i build.json

NOTE:
- Richiede PyInstaller installato nell'interprete che esegue questo script:
    python3 -m pip install pyinstaller
- La cartella _Config si trova un livello sopra base_path (es. Python/_Config/)
"""

VERSION = "3.1.2"

import argparse
import sys
from pathlib import Path

# Aggiungi la cartella shared/ al sys.path PRIMA di importare i moduli ab_*
# (necessario per path_widgets.py usato da quasi tutti i moduli)
if not getattr(sys, 'frozen', False):
    sys.path.insert(0, str(Path.home() /
        'Library/CloudStorage/Dropbox/Documenti_IRC/Python/shared'))

from ab_gui_main import BuilderUI


DEFAULT_BASE = str(Path.home() / "Library" / "CloudStorage" / "Dropbox" / "Documenti_IRC" / "Python")
DEFAULT_ICON_PATH = str(Path.home() / "Library" / "CloudStorage" / "Dropbox" / "Documenti_IRC" / "Python" / "Icons")
DEFAULT_CONFIG_PATH = str(Path.home() / "Library" / "CloudStorage" / "Dropbox" / "Documenti_IRC" / "Python" / "_Config")


def main():
    ap = argparse.ArgumentParser(description="Python App Builder con PyInstaller")
    ap.add_argument("--base_path", default=DEFAULT_BASE,
                    help="Cartella contenente gli script .py")
    ap.add_argument("--icon_path", default=DEFAULT_ICON_PATH,
                    help="Cartella di default per le icone .icns")
    ap.add_argument("--config_path", default=DEFAULT_CONFIG_PATH,
                    help="Cartella _Config dove salvare i build.json")
    args = ap.parse_args()

    ui = BuilderUI(Path(args.base_path), Path(args.icon_path), Path(args.config_path))
    ui.mainloop()


if __name__ == "__main__":
    main()
