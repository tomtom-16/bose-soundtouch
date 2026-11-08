# Exploitation et migration

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
