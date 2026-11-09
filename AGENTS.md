# Consignes pour les agents

## Projet et repères

Ce dépôt permet de lancer des radios avec les six boutons physiques des Bose
SoundTouch et une interface web locale. Le Mac sert au développement ; la cible
de déploiement est un serveur Linux/LXC avec Docker Compose en réseau hôte.

- `bose_radio.py` : point d’entrée minimal vers `soundtouch.cli.main()`.
- `soundtouch/` : configuration, découverte, primitives Bose, lecture, streaming,
  watchdog, serveur HTTP et CLI, répartis en modules sans démarrage à l’import.
- `web/` : interface servie par Python (`index.html`, `styles.css`, `app.js`).
- `button_listener.js` : événements WebSocket Bose et gestion des boutons.
- `config/*.example.json` et `.env.example` : modèles versionnés.
- `scripts/init_config.py` : initialisation sans écrasement des fichiers existants.
- `scripts/dev.py` : lancement local avec prise en charge d'un fichier `.env`.
- `tests/` : tests Python et JavaScript avec fichiers temporaires et simulations.
- `compose.yml` et `docker/` : services `bose-radio` et `bose-buttons`.

Lire `README.md` pour les commandes et le comportement attendu, puis
`docs/operations.md` pour l'exploitation. `docs/project-status.md` distingue les
validations historiques et les points ouverts. `docs/upstream.md` décrit la
version d'origine et peut contenir des chemins ou réglages devenus obsolètes.

## Conventions de travail

- Répondre en français et conserver la documentation du projet en français.
- Garder les deux scripts principaux à la racine. Python utilise uniquement sa
  bibliothèque standard ; Node.js dépend de `ws`, verrouillé par `package-lock.json`.
  Éviter d'ajouter une dépendance sans besoin concret.
- Suivre le style du fichier modifié et limiter les changements au besoin demandé.
- Conserver le relais audio à la demande : pas de flux permanent, préchauffage,
  transcodage ni modification du firmware.
- Préserver la cohérence entre Python, le listener et l'interface pour les presets :
  les listes par enceinte sont indépendantes de `default`
  (affichage Toutes). Une entrée absente ou `null` signifie « Sans affectation ».
  Le fichier contient uniquement les listes, sans version, migration ni héritage.
  Les identifiants des stations sont distincts des noms affichés.
- Préserver le rechargement de configuration sans redémarrage et la détection des
  modifications concurrentes lors des opérations qui la prennent en charge.
- Mettre à jour la documentation et les modèles si le comportement ou les réglages
  partagés changent. Ne pas présenter un test simulé comme une validation matérielle.

## Configuration et essais locaux

Les fichiers `.env`, `config/*.json` hors modèles, `data/` et `local/` contiennent
la configuration réelle ou des données privées. Préserver leurs exclusions Git,
ne pas recopier leurs adresses ou identifiants dans des fichiers versionnés et ne
pas les écraser pour préparer un test. Modifier les modèles pour partager des défauts.

Pour isoler les essais, utiliser des dossiers temporaires avec `BOSE_CONFIG_DIR`
et `BOSE_DATA_DIR`, ou `python3 scripts/dev.py server --env-file .env.local`.
Un fichier `speakers.json` contenant `{}` permet de tester sans enceinte.
Les scripts principaux lancés directement ne chargent pas `.env`.

Les commandes de lecture, volume, arrêt, découverte et le listener peuvent agir
sur les appareils réels ; utiliser des simulations pour les validations de code.
La découverte écrit la configuration des enceintes. Les essais matériels doivent
faire partie de la demande en cours ; éviter deux listeners sur les mêmes appareils.
L'API sans authentification est destinée au réseau local.

## Vérification

Depuis la racine, exécuter les tests concernés par le changement. Pour vérifier
l'ensemble des comportements couverts sans contacter les Bose :

```sh
python3 -m unittest discover -s tests -p 'test_*.py'
node tests/test_listener_config.js
node tests/test_web_ui.js
```

Vérifier aussi la syntaxe du listener après modification avec
`node --check button_listener.js` et `node --check web/app.js`. Pour une modification de la stack, utiliser
`docker compose config` si Compose est disponible. Indiquer les vérifications
non exécutées ; une validation locale ne remplace pas un essai sur Linux/LXC.

Avant livraison, contrôler `git diff --check` et `git status --short`, et résumer
les changements ainsi que les validations effectuées.
