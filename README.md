# Bose SoundTouch — radios et boutons locaux

Cette adaptation du projet [fredgaiotti/bose-soundtouch](https://github.com/fredgaiotti/bose-soundtouch)
permet de lancer des radios avec les six boutons physiques de Bose SoundTouch 10.
Deux services tournent sur un serveur Linux : un proxy Python qui relaie le MP3
à la demande, et un listener Node.js qui reçoit les événements des enceintes.
La Bose décode le son ; aucun transcodage ni flux radio permanent n'est ajouté.
Les radios Internet, et TuneIn pour les sources `tunein:…`, restent nécessaires.

Les scripts Python et Node restent à la racine pour garder les commandes simples.

```text
bose_radio.py            Proxy, interface web et commandes de contrôle
button_listener.js      Écoute des boutons physiques
compose.yml             Stack Linux : proxy + listener
config/                 Modèles versionnés et configuration locale ignorée
  *.example.json        Modèles à copier pour une nouvelle installation
  *.json                Configuration effective, hors Git
docker/                 Recettes de construction des deux images
scripts/init_config.py  Initialisation sans écraser les fichiers existants
data/                   État de lecture persistant, hors Git
docs/                   Fonctionnement, exploitation et état du projet
examples/               Services systemd, launchd et tâches Windows
local/                  Historiques de conversation et sauvegardes, hors Git
```

## Démarrage sur un nouveau serveur Linux

Prérequis : Python 3 pour préparer les fichiers, Docker Engine et Docker Compose v2.
Depuis la racine du dépôt :

```bash
python3 scripts/init_config.py
# Renseigner les IP, noms et deviceID dans config/speakers.json.
# Vérifier config/config.json, stations.json et presets.json.
docker compose config
docker compose up -d --build
docker compose logs --tail=50 bose-radio bose-buttons
curl -sS http://127.0.0.1:8077/api/status
```

Le script crée `.env` et copie les quatre modèles JSON uniquement si les fichiers
cibles sont absents. Les fichiers copiés du serveur sont déjà présents sur ce Mac :
ils n'ont pas été remplacés par les modèles.

L'adresse de l'interface web est `http://<IP-du-serveur>:8077/`.
Le contrôle segmenté permet de choisir une enceinte. Pour une lecture synchronisée,
choisir **Toutes**, puis toucher une station. Lecture et volume
principal suivent ce choix, ainsi que le bouton d’arrêt en haut à droite du bloc
de lecture, visible uniquement quand la sélection joue. Le panneau **Speakers** propose en plus le
volume individuel de chaque enceinte. Lancer une station ou arrêter la lecture
sur une enceinte seule la détache de son groupe éventuel ; si elle était maître,
le groupe est dissous.

Les stations sont affichées dans l'ordre des presets physiques 1 à 6, avec leur
numéro sur chaque bouton, à partir de `config/presets.json`. Les correspondances
propres à l'enceinte choisie remplacent celles de `default` ; en mode toutes les
enceintes, l'interface affiche `default`. Les stations sans preset viennent après.
Le choix explicite de toutes les enceintes active une lecture synchronisée même
si `sync` vaut `false` (ce réglage reste utilisé par les boutons physiques).

Le proxy et l'API n'ont pas d'authentification : les conserver sur le réseau local.
La stack utilise le réseau hôte Linux, notamment pour annoncer une adresse
accessible aux enceintes. Le Mac sert ici à développer et préparer le dépôt ;
le déploiement visé reste le serveur Linux/LXC.

## Configuration

| Fichier local | Rôle |
|---|---|
| `.env` | Chemins des dossiers montés par Compose ; voir `.env.example` |
| `config/config.json` | `proxy_host`, `proxy_port`, `sync`, `watchdog` |
| `config/speakers.json` | Noms, IP et identifiants réels des enceintes |
| `config/stations.json` | Identifiants et URL des stations |
| `config/presets.json` | Correspondance entre boutons et stations |
| `data/state.json` | Dernière intention de lecture enregistrée par Python |

`proxy_host: "auto"` détermine l'IP annoncée aux enceintes. Si l'interface choisie
est incorrecte, renseigner l'IP LAN du serveur dans `config/config.json`.
Les modèles gardent `sync: false` et `watchdog: false`, comme la stack actuelle.
Le port se règle dans le JSON ; avec le réseau hôte, il n'y a pas de mapping de port.

`.env` est lu par **Compose** et par le lanceur local `scripts/dev.py`.
Les scripts lancés directement ne chargent pas ce fichier.
Par défaut ils lisent `config/` et écrivent dans `data/`, relativement au
code. Ils acceptent aussi les variables d'environnement `BOSE_CONFIG_DIR` et
`BOSE_DATA_DIR` (cette dernière concerne Python). Compose fixe leurs valeurs
internes à `/app/config` et `/app/data`, distinctes des chemins sur l'hôte.

Les réglages locaux sont ignorés par Git ; pour partager de nouveaux réglages par
défaut, mettre à jour volontairement le modèle correspondant après vérification.
Aucun mot de passe n'est requis par cette application.

## Développement sans Docker

Python n'utilise que sa bibliothèque standard. Le listener nécessite Node.js et `ws`.

```bash
python3 scripts/init_config.py
npm ci
python3 bose_radio.py --help
python3 bose_radio.py list
python3 scripts/dev.py server
# Dans un autre terminal :
python3 scripts/dev.py buttons
```

Ouvrir `http://localhost:8077/` (ou le port défini dans `config/config.json`).
Le serveur web seul suffit pour tester l'interface ; le listener est nécessaire
pour tester les boutons physiques. Pour utiliser les enceintes depuis le Mac,
leurs IP doivent être joignables et `proxy_host` doit être l'IP LAN du Mac, ou
`auto`, plutôt que `localhost`. Le port du proxy doit être accessible aux Bose.
Éviter de lancer deux listeners pour les mêmes enceintes : arrêter celui de la
LXC pendant les essais des boutons. Les commandes de lecture et d'arrêt de
l'interface agissent sur les enceintes réellement configurées.

Dans **VS Code**, installer les extensions Python et Python Debugger de Microsoft,
sélectionner l'interpréteur Python 3, puis lancer **Bose : serveur web local** avec
F5. Les points d'arrêt fonctionnent dans `bose_radio.py`. Le menu
**Terminal → Exécuter la tâche** propose la préparation des fichiers et le listener
(après `npm ci`). Arrêter le serveur avec le débogueur ou Ctrl-C dans le terminal.

Pour isoler des essais, copier `.env.example` vers `.env.local`, y indiquer des
dossiers de configuration et de données distincts, puis lancer :

```bash
python3 scripts/dev.py server --env-file .env.local
```

Préparer ces dossiers avec une copie des modèles `config/*.example.json`, renommés
sans `.example`, et un dossier de données vide. Un `speakers.json` contenant `{}`
permet de tester l'affichage sans interroger d'enceinte. Dans VS Code, ajouter
`"--env-file", "${workspaceFolder}/.env.local"` à `args` pour utiliser ce fichier.
Le format pris en charge est `KEY=value`, avec guillemets facultatifs et commentaires
sur des lignes séparées ; pas d'interpolation `${…}` ni de commande shell.
Les variables déjà présentes dans l'environnement ont priorité sur le fichier.
Les chemins de dossiers relatifs sont résolus depuis la racine du projet.

`python3 bose_radio.py discover` utilise `dns-sd` sur macOS et écrit dans
`config/speakers.json`. Pour une configuration manuelle, récupérer le `deviceID`
avec `http://<IP-enceinte>:8090/info`. Les définitions de services dans `examples/`
supposent que le dossier complet est installé au chemin indiqué ; adapter les chemins.

## Exploitation et état connu

Voir [le guide d'exploitation](docs/operations.md) pour migrer la stack existante,
modifier les radios et sauvegarder les réglages, et [l'état du projet](docs/project-status.md)
pour les validations et les problèmes ouverts. La documentation d'origine est
conservée dans [docs/upstream.md](docs/upstream.md) à titre de référence ; ses chemins
à la racine et ses réglages par défaut précèdent cette réorganisation.

## Git et futur dépôt GitHub

L'historique Git d'origine est conservé. Le remote `origin` pointe encore vers le
projet de fredgaiotti ; aucun commit ni envoi distant n'a été effectué ici.
Avant le premier commit, contrôler `git status` et `git diff --cached` après sélection
des fichiers. `local/`, les configurations réelles, `.env` et l'état de lecture ne
doivent pas apparaître dans les fichiers à publier.

Après création de votre dépôt GitHub, il sera possible de renommer le remote
existant en `upstream`, puis d'ajouter votre dépôt comme `origin`.

Licence MIT ; attribution et licence du projet d'origine conservées dans `LICENSE`.
