# État du projet — 9 novembre 2026

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

- Valider sur Bose/Linux/LXC le contrôle WebSocket ponctuel à l'ouverture de
  l'interface et la reprise manuelle dans **⚙ → Enceintes** après coupure électrique.
  Le contrôle local, les délais, les échecs et la concurrence sont testés avec
  des sockets simulées et HTTP sur boucle locale, sans validation matérielle.
- Valider sur Mac puis Linux/LXC la découverte SSDP et la récupération d’identité
  par IP de l’assistant d’ajout d’enceinte. Les tests de développement simulent le
  réseau ; aucun appareil réel n’a été contacté pour cette évolution.
- Tester les six boutons sur la seconde enceinte après redémarrage électrique.
- Vérifier l'indépendance des deux enceintes et la reprise après redémarrage serveur.
- Fun Radio diffuse encore une publicité au lancement malgré son URL directe.
  La recherche d'un autre flux officiel reste ouverte ; aucun flux permanent,
  préchauffage ni transcodage n'est prévu.
- Valider sur Linux la stack réorganisée avant de remplacer l'installation actuelle.

## Reconnexion ponctuelle des boutons — 10 novembre 2026

Le listener propose désormais un contrôle HTTP limité à la boucle locale,
utilisé par Python pour vérifier les WebSockets à l'ouverture de l'interface et
sur action **Connecter** (icône de lien) dans **⚙ → Enceintes**. Aucun ping périodique n'est
ajouté. Une connexion qui échoue à la vérification puis à la reconnexion est
fermée et suspendue jusqu'à une nouvelle demande ou une reconfiguration.
L'accueil signale les anomalies par une icône près du nom de l'enceinte.
L'icône utilise le SVG de warning orange également présent à droite du nom
dans les réglages ; ceux-ci restent sur une ligne, avec Connecter avant Modifier.

Les trois suites JavaScript passent, dont les cas de connexion silencieuse,
reprise manuelle, concurrence, retrait et événements tardifs d'une ancienne
socket. Les syntaxes JavaScript et `git diff --check` sont vérifiés.
Sur Windows, 47 des 48 tests Python passent avec un chemin temporaire complet ;
le test existant des permissions Unix `0640` échoue sur les permissions Windows.
Les quatre nouveaux tests Python passent. Docker/Compose n'est pas disponible
dans cet environnement ; aucune image n'a été construite et aucun service n'a
été déployé. Ces validations utilisent des simulations et la boucle locale,
sans contact avec les enceintes ni validation matérielle/Linux/LXC.

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

## Assistant d’ajout d’enceinte

L’interface propose une découverte SSDP ponctuelle, la sélection d’un résultat et
la récupération des informations par IP. Ces opérations préremplissent le
formulaire sans écrire de configuration. Les appareils connus sont reconnus par
`deviceID` ; l’enregistrement conserve les presets et détecte les modifications
concurrentes depuis l’ouverture de la liste. Les tests couvrent les réponses
invalides, les doublons, les erreurs réseau, l’annulation et les routes HTTP avec
des simulations, sans validation matérielle.

## Découpage du code Python et extraction web

`bose_radio.py` est désormais le point d’entrée minimal de `soundtouch.cli`.
Les fonctions sont réparties entre configuration, découverte, primitives Bose,
lecture, streaming, watchdog et serveur HTTP. Chaque verrou conserve un propriétaire
unique. L’interface est extraite dans `web/index.html`, `web/styles.css` et
`web/app.js`, sans réécriture des interactions ; seuls les fichiers explicitement
autorisés sont servis. Les commandes des lanceurs restent identiques.

Vérifications effectuées sur le Mac pour ce découpage :

- Avant modification : 32 tests Python et les deux suites JavaScript réussis.
- Après extraction : 42 tests Python réussis, comprenant le nettoyage des flux
  pendant le transfert et les décisions du watchdog avec horloge et réseau simulés.
- Suites du listener et de l’interface réussies ; syntaxe des deux scripts JS
  et aide CLI vérifiées.
- Imports sans réseau, processus ou thread, chemins par défaut et personnalisés
  (`BOSE_CONFIG_DIR` / `BOSE_DATA_DIR`) depuis un autre répertoire vérifiés.
- HTML, CSS, JS et réponses 404 vérifiés par HTTP sur boucle locale, avec des
  fichiers temporaires sans enceinte ; lanceur de développement vérifié par simulation.
- Comparaison des fonctions extraites : logique conservée hors qualification des
  dépendances ; contenu HTML/CSS/JS conservé hors liens vers les ressources externes.
- Image Python construite avec le paquet et les ressources web, sans lancement
  des services. Le moteur Docker local est disponible ; le plugin Compose reste
  absent, donc `docker compose config` n’a pas pu être exécuté.

Lors du découpage initial, aucune configuration réelle ni enceinte n’a été modifiée ;
aucun déploiement, commit ou push n’a été effectué.
Ces simulations et cette construction locale ne valident pas le fonctionnement
matériel ni l’exploitation de la stack sur Linux/LXC.

L’anomalie préexistante de nettoyage des flux est corrigée : un bloc `finally`
protège désormais toute la phase suivant l’ouverture réussie de l’amont, y compris
l’envoi des en-têtes HTTP. Le compteur n’est décrémenté que pour un transfert
effectivement comptabilisé. Des tests déterministes ont reproduit la fuite avant
correction pour `send_response`, les deux `send_header` et `end_headers`. Ils
vérifient la fermeture et le compteur après ces erreurs, une fin normale, des
erreurs de lecture, d’écriture et de fermeture, avec ou sans autre connexion active
pour la même IP. Toutes les assertions antérieures sont conservées. Les 44 tests
Python et les deux suites JavaScript passent après correction. Ces vérifications
utilisent des simulations et des serveurs HTTP sur boucle locale ; aucune enceinte
ni aucun flux réel n’a été contacté, sans validation matérielle ou Linux/LXC.


### Contrôle du correctif de nettoyage

Une relecture du correctif confirme que le `finally` de fermeture englobe l’envoi
des en-têtes, tandis que le `finally` du compteur reste placé après l’incrément.
Les erreurs d’en-têtes conservent ainsi leur propagation sans laisser le flux
amont ouvert ni retirer une autre connexion active de la même IP.

Les 44 tests Python ont été réexécutés avec succès, ainsi que les suites du
listener et de l’interface, les contrôles de syntaxe JavaScript et l’aide CLI.
Les tests comprennent les chemins depuis un autre répertoire, les imports sans
démarrage réseau et le service HTTP des ressources autorisées. `git diff --check`
ne signale aucune erreur. L’image Python incluant le correctif a également été
reconstruite avec succès, sans lancement des services. Le plugin Compose est
toujours absent ; aucune validation matérielle ni d’exploitation Linux/LXC n’est ajoutée par ce contrôle.
