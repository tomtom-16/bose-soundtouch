#!/usr/bin/env python3
"""Prépare la configuration locale sans écraser de fichier existant."""
from pathlib import Path
import shutil

root = Path(__file__).resolve().parent.parent
for example in sorted((root / "config").glob("*.example.json")):
    target = example.with_name(example.name.replace(".example.json", ".json"))
    if not target.exists():
        shutil.copyfile(example, target)
        print(f"Créé : {target.relative_to(root)}")
if not (root / ".env").exists():
    shutil.copyfile(root / ".env.example", root / ".env")
    print("Créé : .env")
(root / "data").mkdir(exist_ok=True)
print("Configuration prête. Vérifier config/speakers.json avant de démarrer.")
