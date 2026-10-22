# Application globale d'annotation

Une seule fenêtre pour les quatre applications du fonds photo :

**Géotag · Faune · Visages · Mots-clés**

La barre du haut permet de passer de l'une à l'autre sans quitter la page et
**sans changer de dossier** : les quatre apps travaillent toujours sur le même
répertoire de photos.

## Lancement

Double-cliquer sur `Annotations.app`, ou lancer `Lancer Annotations.command`.
Le lanceur ouvre le Terminal, trouve un port libre à partir de 8000, démarre le
serveur puis ouvre l'interface dans le navigateur.

Les quatre applications sont démarrées automatiquement, chacune dans son
environnement Pixi, sur des ports locaux. Le premier affichage prend quelques
secondes (modèle de visages, index TAXREF) ; les suivants sont instantanés.

## Utilisation

- **Icônes en haut** : Géotag, Faune, Visages, Mots-clés. Un clic change d'app.
  Chaque app reste chargée en arrière-plan : on retrouve son état (photo
  affichée, recherche en cours, etc.) en revenant dessus.
- **Dossier** : le chemin courant est affiché à droite. « Changer de dossier »
  ouvre le sélecteur macOS puis propage le dossier aux quatre apps, sans les
  redémarrer.
- **Changement depuis une app** : si vous utilisez le bouton « Changer de
  dossier » à l'intérieur d'une app, les autres suivent automatiquement.
- **Pastille d'état** : verte (prête), orange (démarrage), rouge (arrêt ou
  erreur). En cas d'erreur, un panneau propose « Relancer l'application » et
  « Voir le journal ».
- **Ouvrir dans un onglet** : le bouton ↗ ouvre l'app active seule, en plein
  écran, dans un nouvel onglet.
- **Raccourcis** : `⌥1` à `⌥4` (ou `⌘1` à `⌘4`) pour changer d'app.
- **photos.db** : le bouton « photos.db » reporte dans la base du site les noms
  de personnes écrits dans les photos (XMP). Il est additif : aucun lien
  existant n'est retiré. Le résultat s'affiche en bas de la fenêtre.
- **Panneaux** : dans Géotag et Visages, la poignée entre les deux panneaux se
  glisse à la souris (ou avec ←/→) ; un double-clic rétablit la taille par
  défaut. Le réglage est mémorisé dans le navigateur.

Chaque app garde son interface et ses gestes habituels : valider un lieu, une
espèce, un visage ou des mots-clés fonctionne exactement comme avant.

## Environnement (chemins de la machine)

Tout ce qui dépend de la machine est décrit dans `annotations/preferences.json`,
section `environment` :

```json
"environment": {
  "photos_root": "/Volumes/WD/PHOTOS",
  "data_dir": "/Volumes/WD/PHOTOS/_DATA",
  "photos_db": "/Volumes/WD/PHOTOS/_DATA/photos.db",
  "face_db": "/Volumes/WD/PHOTOS/_DATA/face.db",
  "previews_dir": "/Volumes/WD/PHOTOS/_DATA/previews",
  "thesaurus_dir": "/Volumes/WD/PHOTOS/_DATA/Thesaurus",
  "taxref_dir": "/Users/pc/DVPT/photos/faune/TAXREF",
  "exiftool": "",
  "geocoding": "nominatim"
}
```

- `photos_root` : racine du fonds photo (les bases stockent des chemins relatifs).
- `data_dir` : dossier des bases et index (`_DATA`).
- `photos_db` : base du site de sélection ; `face_db` : index des visages.
- `previews_dir` : cache des aperçus ; `thesaurus_dir` : thésaurus du site.
- `taxref_dir` : dossier (ou fichier) TAXREF utilisé par Faune.
- `exiftool` : chemin absolu facultatif ; vide = ExifTool du `PATH` (fourni par Pixi).
- `geocoding` : `nominatim` (défaut) ou `off` pour couper l'envoi de coordonnées
  et de texte à OpenStreetMap (voir Confidentialité).

Un champ vide est déduit du précédent (`data_dir` de `photos_root`, `photos_db`
de `data_dir`, etc.). Les variables `PHOTOS_ROOT`, `PHOTOS_DATA_DIR`,
`PHOTOS_DB`, `PHOTOS_FACE_DB`, `PHOTOS_PREVIEWS_DIR`, `PHOTOS_TAXREF` et
`EXIFTOOL` restent prioritaires (tests, lancements ponctuels). Après une
modification, relancer l'application : les chemins sont lus au démarrage.

## Architecture

```
annotations/
├── server.py                  orchestration + coquille (barre + cadres)
├── index.html, app.js, styles.css
├── Annotations.app            lanceur macOS
├── Lancer Annotations.command lanceur Terminal
├── AppIcon.svg                source de l’icône
├── preferences.json           dossier, app active, chemins de la machine
└── logs/                      journaux des quatre serveurs
```

Le principe : **ne rien réimplémenter**. Chaque application reste autonome,
avec son `server.py`, son interface, son environnement Pixi et son README. Le
serveur d'annotations :

1. démarre les quatre `server.py` en sous-processus, sur des ports libres en
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
`face/sync_photos_db.py` dans l'environnement de Visages (même ExifTool) et
renvoie les compteurs affichés en bas de la fenêtre.

Le dossier global et l'app active sont retenus dans
`annotations/preferences.json`. À chaque changement de dossier, le programme
met aussi à jour le champ `initial_directory` du `preferences.json` de chaque
app : le sélecteur de dossier de chaque app s'ouvre au bon endroit, et une app
relancée seule retrouve le même dossier. Au démarrage, les préférences
propres aux apps ne sont pas modifiées.

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
3. Ajouter la route `/api/directory` à son `server.py` (voir les quatre apps
   existantes : quelques lignes).
4. Vérifier que son serveur accepte `dossier`, `--host` et `--port`.

## Notes et limites

- **Instances multiples** : si une app autonome est déjà ouverte (par exemple
  `Faune` sur le port 8000), l'application globale démarre sa propre instance.
  Fermer les apps autonomes évite de doubler la mémoire, en particulier pour
  Visages (modèle InsightFace, environ 1 Go).
- **Deux barres** : la barre globale reste au-dessus de celle de chaque app.
  Quand une app est affichée dans Annotations, son propre bouton « Changer de
  dossier » est masqué (l'URL contient `?embed=1`) : il ne reste que celui de la
  barre globale. Ouverte seule, l'app conserve le sien.
- **Ports** : le port de la coquille est choisi par le lanceur ; ceux des apps
  sont éphémères et changent à chaque lancement. Rien à configurer.
- **Journaux** : `annotations/logs/<app>.log`. Ils sont complétés à chaque
  démarrage et accessibles depuis le panneau d'erreur.
- **Arrêt** : fermer le Terminal (ou `Ctrl-C`) arrête la coquille et les quatre
  apps qu'elle a lancées.

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
- Le sélecteur de dossier ne s'ouvre pas : autoriser le Terminal à contrôler
  les dialogues dans Réglages Système > Confidentialité et sécurité >
  Automatisation.
- Un dossier a disparu (disque externe débranché) : rebrancher le disque puis
  choisir à nouveau le dossier depuis la barre globale.
