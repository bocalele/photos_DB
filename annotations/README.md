# Application globale d'annotation

Une seule fenêtre pour les cinq applications du fonds photo :

**Classement · Géotag · Faune · Visages · Mots-clés**

L'application **Sélection** est désormais autonome : elle se lance séparément
et ne fait plus partie de cette fenêtre.

La barre du haut permet de passer de l'une à l'autre sans quitter la page et
**sans changer de dossier** : les cinq apps travaillent toujours sur le même
répertoire de photos.

## Lancement

Double-cliquer sur `Annotations.app`. Le lanceur démarre le serveur en
arrière-plan, **sans ouvrir de Terminal**, trouve un port libre à partir de
8000 puis ouvre l'interface dans le navigateur. L'icône du Dock sert de
lanceur : elle ne reste pas active pendant que le serveur tourne ; un nouveau
clic rouvre l'interface déjà démarrée. `Lancer Annotations.command` reste
disponible pour un lancement manuel avec Terminal.

Le bouton **Quitter** de la barre globale arrête les cinq serveurs et ferme
la fenêtre du navigateur. Si `photos.db` n’est plus à jour par rapport aux
métadonnées, une fenêtre propose de la synchroniser avant l'arrêt. La
synchronisation est aussi refusée tant que Faune contient des déterminations
choisies mais non validées ou non supprimées.

Les cinq applications sont démarrées automatiquement, chacune dans son
environnement Pixi, sur des ports locaux. Le premier affichage peut prendre
quelques secondes (modèle de visages, index TAXREF) ; les suivants sont
instantanés. L'index TAXREF de Faune est mis en cache après le premier
lancement.

## Utilisation

- **Icônes en haut** : Classement, Géotag, Faune, Visages, Mots-clés. Un clic change d'app.
  Chaque app reste chargée en arrière-plan ; si une autre app a modifié des
  métadonnées entre-temps, sa vue est rechargée automatiquement pour éviter
  d'afficher des informations obsolètes.
- **Dossier** : le chemin courant est affiché à droite. « Changer de dossier »
  ouvre le sélecteur macOS puis propage le dossier aux cinq apps, sans les
  redémarrer.
- **Changement depuis une app** : si vous changez de dossier à l'intérieur
  d'une app, l'app active reste affichée sans rechargement complet. Les autres
  apps reprennent le nouveau dossier à leur prochaine activation.
- **Pastille d'état** : verte (prête), orange (démarrage), rouge (arrêt ou
  erreur). En cas d'erreur, un panneau propose « Relancer l'application » et
  « Voir le journal ».
- **Ouvrir dans un onglet** : le bouton ↗ ouvre l'app active seule, en plein
  écran, dans un nouvel onglet.
- **Raccourcis** : `⌥1` à `⌥4` (ou `⌘1` à `⌘4`) pour changer d'app.
- **photos.db** : les applications écrivent les métadonnées dans les fichiers ;
  le bouton « photos.db » reconstruit ensuite la base du dossier courant
  (personnes, mots-clés, taxons, lieux) avec `dataBase.sync update`. Une
  pastille orange indique que la base doit être mise à jour, une pastille verte
  qu’elle est à jour. Le résultat s'affiche en bas de la fenêtre. Seule la
  création d’une personne dans Visages écrit directement le catalogue `people`,
  qui ne se déduit pas des métadonnées des photos.
- **Panneaux** : dans Géotag et Visages, la poignée entre les deux panneaux se
  glisse à la souris (ou avec ←/→) ; un double-clic rétablit la taille par
  défaut. Le réglage est mémorisé dans le navigateur.

Chaque app garde son interface et ses gestes habituels : valider un lieu, une
espèce, un visage ou des mots-clés fonctionne exactement comme avant.

## Environnement (chemins de la machine)

Tout ce qui dépend de la machine est décrit une seule fois dans le
`preferences.json` **à la racine du projet**, section `environment` :

```json
"environment": {
  "photos_root": "/Volumes/WD/PHOTOS",
  "data_dir": "/Volumes/WD/PHOTOS/_DATA",
  "photos_db": "/Volumes/WD/PHOTOS/_DATA/photos.db",
  "face_db": "/Volumes/WD/PHOTOS/_DATA/face.db",
  "previews_dir": "/Volumes/WD/PHOTOS/_DATA/previews",
  "thesaurus_dir": "/Users/pc/DVPT/photos/faune/Thesaurus",
  "taxref_dir": "/Users/pc/DVPT/photos/faune/TAXREF",
  "legacy_photo_roots": ["/Users/pc/PHOTOS"],
  "exiftool": "",
  "geocoding": "nominatim"
}
```

- `photos_root` : racine du fonds photo (les bases stockent des chemins relatifs).
- `data_dir` : dossier des bases et index (`_DATA`).
- `photos_db` : base du sélecteur ; `face_db` : index des visages.
- `previews_dir` : cache des aperçus ; `thesaurus_dir` : thésaurus naturalistes
  (déplacés dans `faune/Thesaurus`, à côté de TAXREF).
- `taxref_dir` : dossier (ou fichier) TAXREF utilisé par Faune.
- `legacy_photo_roots` : anciennes racines présentes dans `photos.db`, utilisées
  pour retrouver les photos après un déménagement du fonds.
- `exiftool` : chemin absolu facultatif ; vide = ExifTool du `PATH` (fourni par Pixi).
- `geocoding` : `nominatim` (défaut) ou `off` pour couper l'envoi de coordonnées
  et de texte à OpenStreetMap (voir Confidentialité).

Un champ vide est déduit du précédent (`data_dir` de `photos_root`, `photos_db`
de `data_dir`, etc.). Les variables `PHOTOS_ROOT`, `PHOTOS_DATA_DIR`,
`PHOTOS_DB`, `PHOTOS_FACE_DB`, `PHOTOS_PREVIEWS_DIR`, `PHOTOS_THESAURUS_DIR`,
`PHOTOS_TAXREF` et `EXIFTOOL` restent prioritaires (tests, lancements
ponctuels). Après une modification, relancer l'application : les chemins sont
lus au démarrage.

Le reste du fichier est partagé par les applications :

```json
{
  "initial_directory": "/Volumes/WD/PHOTOS/2026/DIVERS/PSD",
  "active_app": "faune",
  "default_active_app": "faune",
  "default_select_app": "faune",
  "metadata_revision": 12,
  "environment": { "...": "..." },
  "apps": {
    "motscles": {"default_author": "Pierre", "default_rating": 3}
  }
}
```

- `default_active_app` : onglet ouvert **au lancement** d'Annotations (`geotag`,
  `faune`, `face` ou `motscles`). Modifiez cette valeur pour changer l'écran
  d'accueil.
- `default_select_app` : outil ouvert **au lancement** de l'application
  Sélection (mêmes valeurs). Il est indépendant du réglage d'Annotations.
- `active_app` : dernier onglet utilisé, mis à jour automatiquement.
- `metadata_revision` : compteur interne incrémenté à chaque écriture de
  métadonnées. Il sert à rafraîchir les vues et à proposer la sauvegarde de
  `photos.db` avant l'arrêt.

`tools/preferences.py` centralise lecture et écriture (verrou inter-processus,
remplacement atomique). Chaque app ne modifie que sa section `apps.<id>` ; le
dossier courant est commun.

## Architecture

```
preferences.json               préférences communes + chemins de la machine
annotations/
├── server.py                  orchestration + coquille (barre + cadres)
├── index.html, app.js, styles.css
├── Annotations.app            lanceur macOS
├── Lancer Annotations.command lanceur Terminal
├── AppIcon.svg                source de l’icône
└── logs/                      journaux des cinq serveurs

dataBase/
├── photos_db.py               accès lecture/écriture à photos.db
├── face_db.py                 accès à face.db (index des visages)
└── export.py                  DataFrames + export CSV
```

Le principe : **ne rien réimplémenter**. Chaque application reste autonome,
avec son `server.py`, son interface, son environnement Pixi et son README. Le
serveur d'annotations :

1. démarre les cinq `server.py` en sous-processus, sur des ports libres en
   `127.0.0.1`, en leur passant **le même dossier de photos** ;
2. affiche leur page dans un cadre (`iframe`) sous la barre globale ;
3. propage le dossier courant à toutes les apps quand il change.

Aucun fichier photo n'est copié ni dupliqué : les apps lisent le même dossier.

Pour permettre la propagation sans redémarrage, chaque `server.py` expose une
petite route supplémentaire :

```http
POST /api/directory
{"directory": "/Volumes/WD/PHOTOS/2015/Queyras"}
```

Elle change le dossier en mémoire (sans dialogue, sans réécrire les
préférences). Les routes existantes, dont `/api/change-directory` (dialogue
macOS), ne changent pas : les apps restent utilisables seules.

Le bouton « photos.db » appelle `POST /api/sync-database` : le serveur exécute
`python -m dataBase.sync update <dossier courant> --prune --json` dans
l'environnement racine (pandas + ExifTool) et renvoie les compteurs affichés en
bas de la fenêtre. `--prune` retire de la base les photos qui ne sont plus sur
le disque dans ce dossier. La synchronisation est limitée au dossier courant ;
pour tout reparcourir, se placer à la racine. Un reset complet se fait avec
`python -m dataBase.sync reset`. Les écritures passent par
`dataBase/photos_db.py`.

Après une synchronisation réussie, le cadre actif de **Classement** est
prévenu par `postMessage` et relit immédiatement l'arborescence et les statuts,
sans recharger la page. Les autres cadres sont marqués obsolètes et rechargés à
leur prochaine activation.

La synchronisation enregistre dans la table `meta` de `photos.db` la révision
des métadonnées (`synced_revision`), la portée traitée (`synced_scope`) et le
résultat (`sync_complete`, `sync_errors`). L’état global compare ces valeurs à
`metadata_revision` et au dossier courant : la base est signalée « à mettre à
jour » si une écriture est arrivée depuis, si le dossier n’a pas été
synchronisé, ou si la dernière synchronisation a rencontré une erreur. Une
synchronisation incomplète ne peut donc pas être présentée comme réussie.

Le dossier global, le dernier onglet (`active_app`) et l'onglet de démarrage
(`default_active_app`) sont retenus dans le `preferences.json` commun. Une app
qui change de dossier écrit `initial_directory` ; la surveillance d'Annotations
le voit et aligne les autres apps. Une app relancée seule retrouve le même
dossier.

## Ajouter une application

1. Ajouter une entrée dans `APPS` (`annotations/server.py`) :

   ```python
   {
       "id": "macapp",
       "label": "Mon app",
       "icon": "tag",
       "directory": PROJECT_DIR / "macapp",
       "environment": PROJECT_DIR / "macapp",   # dossier du pixi.toml
       "description": "Ce que fait l'app",
   }
   ```

2. Ajouter son icône et sa couleur dans `app.js` (`ICONS`, `COLORS`).
3. Ajouter la route `/api/directory` à son `server.py` (voir les cinq apps
   existantes : quelques lignes).
4. Vérifier que son serveur accepte `dossier`, `--host` et `--port`.

## Notes et limites

- **Instances multiples** : si une app autonome est déjà ouverte (par exemple
  `Faune` sur le port 8000), l'application globale démarre sa propre instance.
  Fermer les apps autonomes évite de doubler la mémoire, en particulier pour
  Visages (modèle InsightFace, environ 1 Go).
- **Deux barres** : la barre globale reste au-dessus de celle de chaque app.
  Quand une app est affichée dans Annotations, ses propres boutons « Changer
  de dossier » et « Quitter » sont masqués (l'URL contient `?embed=1`) : il ne
  reste que ceux de la barre globale. Ouverte seule, l'app conserve les siens.
- **Ports** : le port de la coquille est choisi par le lanceur ; ceux des apps
  sont éphémères et changent à chaque lancement. Rien à configurer.
- **Journaux** : `annotations/logs/<app>.log`. Ils sont complétés à chaque
  démarrage et accessibles depuis le panneau d'erreur.
- **Arrêt** : le bouton **Quitter** arrête la coquille et les cinq apps
  qu'elle a lancées, puis ferme la fenêtre du navigateur. En lancement manuel
  avec Terminal, `Ctrl-C` produit le même arrêt.

## Confidentialité et connexions réseau

**Aucun fichier photo n'est envoyé sur Internet.** Les seules connexions
sortantes sont :

| Application | Connexion | Ce qui sort |
| --- | --- | --- |
| Géotag | Leaflet depuis `unpkg.com` | chargement de la bibliothèque |
| Géotag | fonds de carte OpenStreetMap, OpenTopoMap, Esri | zones de carte affichées, adresse IP |
| Géotag | géocodage Nominatim | texte recherché ; coordonnées du point cliqué (géocodage inverse) |
| Faune | modèle BioCLIP (Hugging Face), au premier usage | rien d'autre |
| Visages | modèles InsightFace (GitHub), au premier usage | rien d'autre |
| Annotations, Mots-clés | aucune | — |

Les modèles sont ensuite en cache : Faune et Visages fonctionnent hors ligne
(on peut forcer `HF_HUB_OFFLINE=1` pour BioCLIP). Pour le Géotag, les fonds de
carte et le géocodage ont besoin d'Internet ; `"geocoding": "off"` supprime
l'envoi des coordonnées et du texte à Nominatim (les champs de lieu restent
modifiables à la main). Les tuiles de carte, elles, révèlent seulement la zone
consultée, jamais la photo.

## Dépannage

- « L'application ne répond pas » : ouvrir « Voir le journal » dans le panneau.
  Le plus souvent, l'environnement Pixi de l'app n'est pas installé
  (`cd app && pixi install`).
- Le sélecteur de dossier ne s'ouvre pas : autoriser l'application (ou le
  Terminal en lancement manuel) à contrôler les dialogues dans Réglages
  Système > Confidentialité et sécurité > Automatisation.
- Un dossier a disparu (disque externe débranché) : rebrancher le disque puis
  choisir à nouveau le dossier depuis la barre globale.
