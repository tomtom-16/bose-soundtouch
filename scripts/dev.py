#!/usr/bin/env python3
"""Lance les services locaux avec les chemins définis dans un fichier .env."""
import argparse
import os
from pathlib import Path
import re
import runpy
import subprocess
import sys

ROOT = Path(__file__).resolve().parent.parent


def load_environment(env_file):
    # Format volontairement simple, commun à Compose : KEY=value, quotes facultatives.
    # Aucune commande shell ni interpolation n'est exécutée.
    for number, raw in enumerate(env_file.read_text(encoding="utf-8").splitlines(), 1):
        line = raw.strip()
        if not line or line.startswith("#"):
            continue
        key, separator, value = line.partition("=")
        key, value = key.strip(), value.strip()
        if not separator or not re.fullmatch(r"[A-Za-z_][A-Za-z0-9_]*", key):
            raise ValueError(f"{env_file}:{number}: attendu KEY=value")
        if value.startswith(("'", '"')):
            if len(value) < 2 or value[-1] != value[0]:
                raise ValueError(f"{env_file}:{number}: guillemets non fermés")
            value = value[1:-1]
        os.environ.setdefault(key, value)
    for key, default in (("BOSE_CONFIG_DIR", "config"), ("BOSE_DATA_DIR", "data")):
        directory = Path(os.environ.get(key, default))
        if not directory.is_absolute():
            directory = ROOT / directory
        os.environ[key] = str(directory.resolve())


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("service", choices=("server", "buttons"), nargs="?", default="server")
    parser.add_argument("--env-file", type=Path, default=ROOT / ".env")
    args = parser.parse_args()
    try:
        load_environment(args.env_file)
    except (OSError, ValueError) as error:
        parser.exit(2, f"{error}\nPréparer les fichiers avec : python3 scripts/init_config.py\n")
    if args.service == "server":
        # Même processus : les points d'arrêt VS Code fonctionnent dans bose_radio.py.
        sys.argv = [str(ROOT / "bose_radio.py"), "serve"]
        sys.path.insert(0, str(ROOT))
        runpy.run_path(sys.argv[0], run_name="__main__")
    else:
        try:
            return subprocess.call(["node", str(ROOT / "button_listener.js")], cwd=ROOT)
        except FileNotFoundError:
            parser.exit(2, "Node.js est requis pour le listener. Installer Node.js puis lancer npm ci.\n")
        except KeyboardInterrupt:
            return 130
    return 0


if __name__ == "__main__":
    sys.exit(main())
