# État du projet — 8 novembre 2026

Cette synthèse repose sur les deux historiques fournis et les fichiers copiés du
serveur. Elle ne constitue pas un nouveau test des enceintes depuis le Mac.

## Fonctionnement retenu

Deux services Docker sur Linux/LXC : proxy Python à la demande et listener Node.js.
WebSocket Bose : port 8080, sous-protocole `gabbo` ; API locale : 8090 ; UPnP : 8091.
Le proxy écoute par défaut sur 8077. Les enceintes restent indépendantes avec
`sync: false` ; le watchdog est désactivé. Pas de changement de firmware.

| Bouton | Station | Identifiant TuneIn interne documenté |
|---|---|---|
| 1 | Fun Radio | s74591 |
| 2 | Contact FM | s25843 |
| 3 | RMC | s14896 |
| 4 | Groove Salad | s2591 |
| 5 | Drone Zone | s51002 |
| 6 | NRJ | s2339 |

Les URL actuelles figurent dans `config/stations.example.json`. Contact FM utilise
une résolution TuneIn à la demande ; ne pas enregistrer d'URL finale à jeton temporaire.

## Ce qui est validé dans les historiques

- Première enceinte : six boutons testés avec confirmation auditive.
- Seconde enceinte : six présélections internes enregistrées et vérifiées ; le
  redémarrage électrique et la validation physique complète restent à faire.
- Le listener ignore l'événement synthétique `preset 0` produit par `forceInvalid()`.
- Le délai actuel après `forceInvalid()` est **1500 ms**, confirmé par le second
  historique et le fichier copié. Le premier historique mentionnait 3000 ms.
- L'absence de connexion radio persistante a été observée après un arrêt Power
  lors d'un test, sans validation exhaustive de tous les scénarios d'arrêt.

## Points ouverts

- Tester les six boutons sur la seconde enceinte après redémarrage électrique.
- Vérifier l'indépendance des deux enceintes et la reprise après redémarrage serveur.
- Fun Radio diffuse encore une publicité au lancement malgré son URL directe.
  La recherche d'un autre flux officiel reste ouverte ; aucun flux permanent,
  préchauffage ni transcodage n'est prévu.
- Valider sur Linux la stack réorganisée avant de remplacer l'installation actuelle.

Les deux historiques complets et les anciennes sauvegardes restent disponibles
localement, hors Git. La documentation publique omet les adresses réelles et les
identifiants des appareils.

## Vérifications de la réorganisation

Sur le Mac : syntaxe Python et JavaScript, validité des JSON et du YAML Compose,
chemins de configuration par défaut et personnalisés, écriture de l'état,
initialisation sans écrasement, et lecture des enceintes/présélections par le
listener avec WebSocket simulé. `npm ci` a installé la dépendance verrouillée.
Les exclusions Git ont été vérifiées pour les données locales et les historiques.

Le plugin Docker Compose n'étant pas installé sur ce Mac, `docker compose config`
et la construction/le démarrage des conteneurs restent à vérifier sur le serveur.
Aucun test matériel ni accès au serveur n'a été effectué pendant cette préparation.
