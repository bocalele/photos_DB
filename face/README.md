# Reconnaissance de visages

Reconnaît les personnes déjà identifiées dans le fonds photo, en local, avec
InsightFace (`buffalo_l` : RetinaFace + ArcFace) sur CPU.

La galerie de référence n'est pas saisie à la main : elle est déduite des
régions visage XMP posées dans Lightroom (`RegionName`), que `photos.db` connaît
déjà via `people` et `photo_people`.

## Installation

```sh
cd face
pixi install
```

Les modèles InsightFace sont téléchargés au premier lancement dans
`~/.insightface/models/buffalo_l` (182 Mo utiles : `det_10g` et `w600k_r50`).
Pour une utilisation hors ligne, copier ce dossier dans `face/models/`.

## Utilisation

```sh
# Construire / mettre à jour l'index (une seule fois, incrémental ensuite)
pixi run python index.py --from-db --only-labelled

# Construire la galerie de référence et calibrer le seuil
pixi run python reference.py

# Reconnaître sur un dossier, sans rien écrire
pixi run python recognize.py "/chemin/vers/le/dossier-de-photos"
pixi run python recognize.py "/chemin/vers/le/dossier-de-photos" --show-xmp
```

`index.py` accepte aussi des dossiers : `pixi run python index.py "/chemin/vers/le/dossier-de-photos"`.
Seules les photos dont la taille ou la date de modification a changé sont
réindexées ; `--force` réindexe tout.

## Application

L'interface web locale sert à **valider** les reconnaissances et à écrire les
noms dans les métadonnées des photos.

```sh
pixi run python server.py                       # choisit un dossier
pixi run python server.py "/chemin/vers/le/dossier-de-photos"
```

Ou double-cliquer `Face.app` (icône « Visages »). Le lanceur ouvre le Terminal,
trouve un port libre et démarre le serveur.

Deux vues :

- **Photos** : vignettes du dossier, avec le nombre de visages et de noms.
  « Analyser ce dossier » lance la détection en tâche de fond (barre de
  progression), sans rien écrire.
- **Visages à vérifier** : les visages sans nom, triés du plus douteux au plus
  sûr, avec la proposition et son score. C'est la vue de validation en série.

Dans la photo, chaque visage est encadré. Un clic sur un visage affiche les
trois meilleures propositions ; on choisit, on cherche dans les personnes du catalogue,
ou on laisse sans nom. La recherche accepte les flèches `↑`/`↓` et `Entrée`.
Si le nom cherché n'existe pas encore, une entrée « Créer « … » » l'ajoute au
catalogue `photos.db` (table `people`) : il devient immédiatement disponible
pour les autres photos et pour le sélecteur. Raccourcis : `←`/`→` visage précédent/suivant, `Entrée`
accepte la proposition, `1`–`3` choisit une alternative, `Retour arrière`
efface le nom.

Chaque validation est écrite **immédiatement dans la photo**, au format
Lightroom :

- `XMP-mwg-rs:RegionInfo` : une région `Face` par personne nommée (boîte
  normalisée, dimensions de l'image affichée, orientation EXIF respectée) ;
- `XMP-dc:Subject`, `XMP-lr:HierarchicalSubject` et
  `XMP-lr:WeightedFlatSubject` : les noms des personnes, sans toucher aux
  autres mots-clés.

Les régions déjà présentes qui ne correspondent à aucun visage détecté sont
conservées. Une correction remplace l'ancienne région et retire l'ancien nom
des mots-clés. Le visage validé est ajouté à la galerie de référence
(`RefFace`), ce qui améliore les propositions suivantes ; `reference.py` peut
reconstruire cette galerie depuis les étiquettes XMP.

Les écritures passent par ExifTool avec `-overwrite_original` : pas de fichier
`_original` à côté des photos, comme pour l'app Géotag.

Le XMP reste la source de vérité, mais `photos.db` est mise à jour juste après
l'écriture de la photo : les liens `photo_people` sont réécrits pour cette
photo. Si la base est absente ou en erreur, l'écriture XMP reste prioritaire et
l'application continue de fonctionner. « Créer « … » » ajoute aussi la personne
dans la table `people`.

## Indexer tout le fonds

L'application analyse le dossier ouvert. En ligne de commande :

```sh
pixi run python index.py --from-db             # toutes les photos de photos.db
pixi run python index.py --from-db --only-labelled
pixi run python index.py "/chemin/vers/le/dossier-de-photos"   # un dossier ou un fichier
pixi run python index.py --force --from-db     # tout réanalyser
```

L'indexation est incrémentale : seules les photos dont la taille, la date ou la
version de traitement (`PIPELINE_VERSION`) a changé sont reprises — relancer la
commande ne coûte que quelques secondes si rien n'a bougé.

Chaque photo est enregistrée **une seule fois** : son chemin relatif à la racine
est la clé primaire de `Photo`, et une réindexation remplace ses visages au lieu
de les ajouter. Deux copies physiques du même fichier restent deux entrées (deux
chemins différents) : elles peuvent gonfler la galerie de référence, il vaut
mieux les supprimer ou les exclure du fonds.

## Mettre à jour photos.db (le sélecteur)

Les validations mettent à jour le XMP et `photo_people`. Le bouton **photos.db**
de la barre d'Annotations (ou la commande ci-dessous) reprend aussi les
modifications faites hors application. La mise à jour est limitée au dossier
demandé et passe par le script unique `dataBase.sync` (personnes, mots-clés,
taxons et lieux).

```sh
# Depuis la racine du projet, avec l'environnement racine
pixi run python -m dataBase.sync update 2026/Angleterre/PSD
pixi run python -m dataBase.sync update 2026/Angleterre --prune
pixi run python -m dataBase.sync update 2026/Angleterre --dry-run
```

Le chemin est relatif à la racine photo de `preferences.json` (ou absolu).
`--prune` retire aussi les photos absentes du disque dans ce dossier.

## Base

`face.db` est créé à côté de `photos.db`, dans `_DATA` du fonds photo, et
peut être supprimé sans perte : tout est reconstructible. Ces chemins sont
décrits dans la section `environment` du
[`preferences.json`](../preferences.json) commun (`photos_root`, `data_dir`,
`photos_db`, `face_db`, `previews_dir`). Le code d'accès aux deux bases vit
dans `dataBase/` (`photos_db.py`, `face_db.py`).

| Table | Contenu |
|---|---|
| `Photo` | photos indexées (chemin relatif, dimensions, taille, mtime, version de traitement) |
| `Face` | un visage par ligne : boîte normalisée, score, embedding 512 (2 Ko) |
| `FaceLabel` | étiquette connue d'un visage (région XMP ou validation dans l'app) |
| `RefPerson` | une ligne par personne de référence (centroïde, seuil) |
| `RefFace` | exemplaires de référence utilisés pour la comparaison |
| `Meta` | seuil calibré et statistiques |

Les chemins sont **relatifs à la racine du fonds photo**, ce qui rend la base
indépendante du point de montage. Le catalogue des personnes (`people`) reste
dans `photos.db` et est lu au démarrage : une personne créée dans l'application
est disponible tout de suite, sans redémarrer.

À chaque validation, `face.db` est mis à jour immédiatement : les étiquettes de
la photo sont réécrites, ses exemplaires de référence sont remplacés (une
correction ne laisse pas l'ancien nom) et la galerie en mémoire est rechargée.
`reference.py` peut la reconstruire entièrement depuis les XMP. Les variables
`PHOTOS_ROOT`, `PHOTOS_DATA_DIR`, `PHOTOS_DB` et `PHOTOS_FACE_DB` restent
prioritaires sur le fichier de préférences pour un lancement ponctuel.

## Mesures sur le fonds actuel

761 photos (fonds `REPRODUCTION`, majoritairement des scans de tirages anciens),
2 318 visages détectés, 1 317 étiquetés par les régions XMP, 59 personnes avec
au moins 3 exemplaires.

Indexation : ~230 ms/photo sur M5 Pro à `det_size=640`, soit 3 minutes pour ces
761 photos et environ 1 h 15 pour 20 000 photos.

Calibration (un visage n'est jamais comparé aux références de sa propre photo) :

| Mesure | Valeur |
|---|---|
| bonne personne au 1er rang | 94,1 % |
| seuil retenu (précision ≥ 97 %) | 0,43 |
| rappel / précision à ce seuil | 85,9 % / 97,2 % |
| seuil « à vérifier » | 0,33 |
| validation par année entière absente de la référence | 80,4 % au 1er rang |

La validation par année est la plus réaliste : sur des photos éloignées dans le
temps des exemplaires de référence, la précision reste ≥ 98 % à partir de 0,40,
mais le rappel tombe autour de 40 %. C'est le compromis attendu sur un fonds de
scans : le programme propose, l'humain valide.

## Limites connues

- Les personnes vues moins de 3 fois n'ont pas de référence (18 des 77
  personnes étiquetées).
- Les visages de profil, très flous, ou de moins de 3 % de la hauteur d'image
  sont écartés de la référence.
- Les tirages scannés plusieurs fois avec des étiquettes contradictoires sont
  détectés et écartés automatiquement (`--conflict`, 0,85 par défaut).
- Les photos PSD/RAW/HEIC ne sont pas décodées par OpenCV. L'aperçu intégré
  est utilisé s'il est grand ; sinon l'app fait rendre l'image complète par
  macOS (`sips`, ImageIO), dans un cache `_DATA/previews/`. Un PSD peut ainsi
  être analysé en 2048 px au lieu de sa vignette de 160 px, et recevoir des
  étiquettes XMP comme les JPEG.
- Changer `PIPELINE_VERSION` (`dataBase/face_db.py`) marque les photos comme à
  réanalyser : c'est ce qui a permis de refaire les PSD après cette
  amélioration.

## Aperçus (cache)

Les aperçus 2048 px utilisés pour la détection sont créés à la demande quand
OpenCV ne peut pas décoder la photo (PSD, RAW, HEIC). Ils sont stockés dans
`previews_dir` sous un nom `sha1(chemin + mtime)[:24].jpg` : **il n'existe pas
de lien en base**, le nom se recalcule à partir de la photo. Ce sont des données
dérivées et volatiles : si la photo change, un nouvel aperçu est créé ; les
anciens fichiers restent dans le dossier jusqu'à un nettoyage manuel.

`photos.db` (schéma v4) ne contient plus de colonne de vignette : le sélecteur
génère ses aperçus à la demande dans `previews_dir/selector`. Les aperçus de
détection de Visages restent eux aussi un cache reconstructible.
