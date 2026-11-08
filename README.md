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

`.env` est lu par **Compose**. Les scripts lancés directement ne chargent pas ce
fichier. Par défaut ils lisent `config/` et écrivent dans `data/`, relativement au
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
python3 bose_radio.py serve
# Dans un autre terminal :
node button_listener.js
```

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
