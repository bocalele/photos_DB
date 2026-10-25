# dataBase

Accès centralisé aux bases SQLite et outils de reconstruction/export.

## Schéma `photos.db` (v4)

`photos.db` est un catalogue de photos annotées, pas un cache :

- `photos` : chemin relatif unique (`relpath`), identifiant entier, date,
  boîtier, objectif, focale, ouverture, vitesse, ISO, correction, dimensions,
  note et auteur. Le nom de fichier n'est pas dupliqué : il se déduit de
  `relpath` ;
- `people` + `photo_people` : personnes et liens photo-personne. Le catalogue
  familial (`id_pere`, `id_mere`) est conservé lors d'un reset ;
- `keywords` + `photo_keywords` : mots-clés **atomiques et plats**. Une photo
  peut pointer vers plusieurs mots-clés ;
- `taxa` + `photo_taxa` : taxons observés et leur hiérarchie
  (`parent_id`, `path`) ;
- `locations` : pays, lieu-dit, coordonnées.

Un terme reconnu dans le thésaurus (nom vernaculaire ou scientifique) est un
taxon : il vit dans `taxa` et `photo_taxa`, pas dans `keywords`. Les personnes
ne sont jamais dupliquées dans les mots-clés. Les albums ne sont plus
persistés : une sélection de photos est volatile.

Les champs `thumbPath`, `icone`, `jpeg`, `fullsize` et `unique_id` ont
disparu. Les vignettes sont des fichiers dérivés, générés à la demande dans
`previews_dir`.

## Reset et mise à jour

Un seul script gère le cycle de vie de la base :

```bash
# Réinitialise la base (sauvegarde + personnes conservées par défaut)
pixi run python -m dataBase.sync reset

# Réinitialise sans conserver le catalogue des personnes
pixi run python -m dataBase.sync reset --empty-people

# Met à jour uniquement un dossier (ou un fichier)
pixi run python -m dataBase.sync update /Volumes/WD/PHOTOS/2026/Angleterre/PSD

# Retire aussi les photos absentes du disque dans ce dossier
pixi run python -m dataBase.sync update /Volumes/WD/PHOTOS/2026/Angleterre --prune

# Simulation
pixi run python -m dataBase.sync update /Volumes/WD/PHOTOS/2026/Angleterre --dry-run
```

`reset` crée une sauvegarde `photos.backup-AAAAMMJJ-HHMMSS.db` puis reprend le
catalogue des personnes (noms et liens familiaux). Toutes les photos,
mots-clés, taxons et lieux sont supprimés. `update` ne parcourt que la
portée demandée : les autres dossiers de la base ne sont pas relus.

L'import décode les anciennes valeurs XMP qui contiennent une liste Python
(`"['A', 'B']"`) et éclate les listes de tags collées avec des espaces
(`"ESPAGNE MONTAGNES VACANCES"`). Les phrases ordinaires comme `king's college`
ou `Porto da Cruz` sont conservées telles quelles.

La reconstruction complète reste disponible pour des tests ou un import
initial :

```bash
pixi run python -m dataBase.rebuild --limit 200 --db /tmp/photos-test.db
pixi run python -m dataBase.rebuild --replace
```

## Modules

- `photos_db.py` : schéma, connexions, recherche et écritures unitaires
  utilisées par les applications ;
- `annotations.py` : décodage des valeurs XMP, éclatement des mots-clés,
  classement personnes / taxons / mots-clés ;
- `sync.py` : reset et mise à jour dossier par dossier ;
- `rebuild.py` : reconstruction complète depuis les fichiers ;
- `face_db.py` : schéma et accès à `face.db` ;
- `export.py` : DataFrames et export CSV.

Aucune application ne doit ouvrir directement `photos.db` : elle passe par
`photos_db.connect()`. Les chemins viennent de `preferences.json` via
`tools/environment.py`.

## Export CSV

```bash
# Vue complète des photos (personnes, mots-clés, taxons, lieu)
pixi run python dataBase/export.py --all --out photos.csv

# Toutes les tables, un CSV par DataFrame
pixi run python dataBase/export.py --all --per-table --out-dir export_photos

# Un DataFrame précis
pixi run python dataBase/export.py --table people --out personnes.csv

# Résultat d'une requête SQL
pixi run python dataBase/export.py --query "SELECT * FROM photos WHERE rating >= 4" --out top.csv

# Inventaire des DataFrames
pixi run python dataBase/export.py --list
```

Les DataFrames sont `photos`, `photos_complete`, `people`, `photo_people`,
`keywords`, `photo_keywords`, `taxa`, `photo_taxa` et `locations`. Les CSV sont
en UTF-8 avec BOM pour Excel ; `--sep ";"` change le séparateur.

## Anciens scripts

`add.py`, `graph.py`, `read.py`, `repair_keywords.py` et l'ancien
`thesaurus.py` ont été supprimés : ils importaient les anciens CSV ou
réparaient l'ancien schéma. `people.py` et `db.py` sont adaptés au schéma
courant.
