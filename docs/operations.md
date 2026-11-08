# Exploitation et migration

## Déploiement sur la LXC

La cible reste une LXC Linux avec Docker Engine et Compose v2 fonctionnels.
Le même code tourne localement avec Python et en production dans les deux
services `bose-radio` (proxy, interface web et API) et `bose-buttons` (listener).
Il n'y a pas de troisième conteneur web à ajouter : l'interface est servie par
`bose_radio.py serve`, sur le même port que les flux radio.
VS Code et les dépendances de débogage ne sont pas requis
sur la LXC. Le lanceur `scripts/dev.py` sert uniquement au lancement local.

Installer le dépôt dans un dossier dédié de la LXC, puis suivre la procédure de
migration ci-dessous si une ancienne stack existe déjà. Pour une installation
neuve : lancer `python3 scripts/init_config.py`, renseigner les quatre JSON, puis
exécuter `docker compose config` et `docker compose up -d --build`.
Utiliser un `.env` propre à la LXC avec les chemins de cette installation ; ne pas
copier les données ou les réglages de test du Mac sur une installation existante.

Le réseau hôte de Compose est celui de la LXC : `proxy_host` doit annoncer son
IP LAN joignable depuis les enceintes. Vérifier l'accès à l'interface avec
`http://<IP-LXC>:8077/`, puis aux API Bose depuis la LXC sur les ports 8090/8091
et au WebSocket sur 8080. Conserver l'interface et l'API sur le réseau local.
Les contraintes de Docker imbriqué dépendent de la configuration de l'hôte LXC ;
elles restent à vérifier sur le serveur avant le premier déploiement.

## Passer de l'ancienne stack à cette organisation

Cette préparation a été faite sur la copie du Mac. Le serveur n'a pas été modifié.
Ne pas copier aveuglément ce dossier sur une stack en fonctionnement : les chemins
et Dockerfiles ont changé.

1. Sauvegarder le dossier complet de la stack sur le serveur, y compris les JSON,
   l'état de lecture et les anciens exports XML.
2. Arrêter l'ancienne stack avec `docker compose down`, depuis son dossier actuel.
3. Installer le nouveau code dans un dossier distinct, puis exécuter
   `python3 scripts/init_config.py`.
4. Copier les **quatre JSON actuels du serveur** (`config.json`, `speakers.json`,
   `stations.json`, `presets.json`) dans `config/` en remplaçant les modèles locaux.
   Le serveur peut avoir évolué depuis la copie sur le Mac.
5. Copier l'ancien `state.json` dans `data/state.json` si sa conservation est utile.
   Son absence est acceptée ; Python le créera lors d'un enregistrement de lecture.
6. Vérifier `.env`, puis `docker compose config`. Les dossiers source doivent exister.
7. Démarrer avec `docker compose up -d --build`. Une reconstruction est nécessaire
   pour cette migration, car les chemins de configuration dans le code ont changé.
8. Vérifier les logs, `/api/status`, puis les boutons sur chaque enceinte séparément.
   Conserver l'ancien dossier pour pouvoir revenir à la stack précédente.

Les noms de conteneurs et le port restent identiques : arrêter l'ancienne stack
avant de lancer la nouvelle. La migration n'écrit aucune présélection dans les Bose.

### Ancienne installation avec seulement le proxy/presets et le listener

Avant la migration, relever les noms et montages réels, sans supposer que les
conteneurs de l'ancienne installation portent les noms du nouveau Compose :

```bash
docker ps --format 'table {{.Names}}\t{{.Image}}\t{{.Ports}}'
docker inspect NOM_ANCIEN_CONTENEUR --format '{{json .Mounts}}'
```

Sauvegarder aussi les fichiers des montages situés hors du dossier de l'ancienne
stack. S'ils sont uniquement dans le conteneur, les extraire avec `docker cp`
avant de supprimer celui-ci. Les quatre JSON du serveur font référence ; ne pas
les remplacer par les configurations du Mac ou les modèles.

Pour transférer le code sans publier les réglages privés, pousser le commit du
Mac sur le dépôt Git, puis cloner ce dépôt dans un **nouveau dossier** de la LXC.
Autre possibilité sans push : créer une archive du commit sur le Mac avec
`git archive --format=tar.gz --output=/tmp/bose-soundtouch-code.tar.gz HEAD`,
la transférer par `scp` et l'extraire dans le nouveau dossier de la LXC.
Cette archive contient uniquement les fichiers versionnés.

Suivre ensuite les étapes 1 à 7 ci-dessus. Après le démarrage :

```bash
docker compose ps
docker compose logs --tail=50 bose-radio bose-buttons
curl -fS http://127.0.0.1:8077/ -o /dev/null
curl -fS http://127.0.0.1:8077/api/status
```

Adapter `8077` si `proxy_port` est différent. Depuis un navigateur du réseau local,
ouvrir `http://<IP-LXC>:8077/`, tester une station sur une enceinte puis le mode
**Toutes**, et vérifier les boutons physiques. Le port doit être accessible depuis
le LAN et les enceintes ; le réseau hôte ne demande pas de mapping Docker.
Ne laisser tourner qu'un seul listener pour les mêmes enceintes.

En cas de problème, arrêter la nouvelle stack avec `docker compose down`, puis
relancer l'ancienne avec `docker compose up -d` depuis son dossier sauvegardé.
Conserver les anciens fichiers et images jusqu'à validation de la migration.

## Opérations courantes

```bash
docker compose ps
docker compose logs -f --tail=20 bose-buttons bose-radio
curl -sS http://127.0.0.1:8077/api/status
```

- `config/presets.json` est relu par le listener à chaque événement de bouton.
- Après modification de `config/config.json` ou `config/speakers.json`, redémarrer
  les deux services : `docker compose restart bose-radio bose-buttons`.
- Après modification de `config/stations.json`, redémarrer le proxy par précaution :
  `docker compose restart bose-radio`.
- Un changement JSON ne nécessite pas de reconstruire les images.
- Après changement du code ou des Dockerfiles : `docker compose up -d --build`.
- Après changement de `.env` ou de Compose : `docker compose up -d` pour recréer
  les conteneurs avec les nouveaux montages.

Pour envoyer une commande Python dans le conteneur :

```bash
docker compose exec bose-radio python3 bose_radio.py status "Nom enceinte"
docker compose exec bose-radio python3 bose_radio.py play groove-salad "Nom enceinte" --volume 15
```

La commande `play` est une commande UPnP directe ; pour changer de source, le
firmware peut nécessiter une transition préalable vers `INVALID_SOURCE`, gérée
par le listener des boutons. Éviter les commandes répétées rapidement.

## Boutons et diagnostic

Les stations jouées sont définies dans `config/presets.json`, avec un bloc `default`
et éventuellement un bloc par nom d'enceinte. Un bouton doit également avoir une
présélection interne Bose pour émettre l'événement attendu. Un changement dans
le JSON ne programme pas cette présélection interne.

- `http://<IP-enceinte>:8090/presets` : présélections internes.
- `http://<IP-enceinte>:8090/now_playing` : source et état de lecture.
- Logs du listener : bouton reçu et station choisie.
- Logs du proxy : requête de lecture et ouverture du flux amont.

Les identifiants TuneIn internes doivent être distincts entre boutons. Sur la
première enceinte, un redémarrage électrique puis celui du listener a été nécessaire
après ajout des boutons. Les exports XML dans `local/backups/` sont documentaires :
ne pas les réinjecter tels quels dans l'API.

## Sauvegardes et confidentialité

Conserver une copie privée de `config/`, `.env`, `data/` et `local/` : ces éléments
ne sont pas sauvegardés par un push Git. Les historiques originaux sont conservés
intacts dans `local/history/`, et les anciens fichiers `.backup`, `.orig` et les
exports Bose dans `local/backups/`. Ils peuvent contenir des IP, identifiants de
matériel ou autres informations personnelles.

`.dockerignore` exclut également ces dossiers et configurations du contexte de
construction. Les images ne contiennent que le code et les dépendances ; la
configuration effective est montée en lecture seule. Le dossier `data/` est monté
en écriture pour permettre aussi la suppression de `state.json` lors d'un arrêt.
