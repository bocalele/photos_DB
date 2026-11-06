# dataBase

Accès centralisé aux bases SQLite et outils de reconstruction/export.

## Schéma `photos.db` (v4)

`photos.db` est un catalogue de photos annotées, pas un cache :

- `photos` : chemin relatif unique (`relpath`), identifiant entier, date,
  boîtier, objectif, focale, ouverture, vitesse, ISO, correction, flash,
  dimensions, note, auteur et **groupe d'accès** (`groupe`, entier positif,
  `1` par défaut). Le nom de fichier n'est pas dupliqué : il se déduit de
  `relpath` ;
- `people` + `photo_people` : personnes et liens photo-personne. Le catalogue
  familial (`id_pere`, `id_mere`) est conservé lors d'un reset ;
- `keywords` + `photo_keywords` : mots-clés **atomiques et plats**. Une photo
  peut pointer vers plusieurs mots-clés. Les champs de référence sont
  `IPTC:Keywords` et `XMP-dc:Subject` ; les champs dérivés de Lightroom
  (`XMP-lr:WeightedFlatSubject`, `LastKeywordXMP`) sont ignorés à la
  reconstruction pour ne pas réintroduire d'anciens mots-clés supprimés ;
- `taxa` + `photo_taxa` : taxons observés et leur hiérarchie
  (`parent_id`, `path`) ;
- `locations` : pays, lieu-dit, coordonnées.

Un terme reconnu dans TAXREF (nom vernaculaire ou scientifique) est un taxon.
Le thésaurus local sert de repli pour les noms absents de TAXREF (races,
groupes, anciens libellés). TAXREF est donc prioritaire : il évite les
conflits comme « Mouflon » / « Mouton domestique ». Un taxon vit dans `taxa`
et `photo_taxa`, pas dans `keywords`. Les personnes ne sont jamais dupliquées
dans les mots-clés. Les albums ne sont plus persistés : une sélection de
photos est volatile.

Les champs `thumbPath`, `icone`, `jpeg`, `fullsize` et `unique_id` ont
disparu. Les vignettes sont des fichiers dérivés, générés à la demande dans
`previews_dir`.

Les colonnes `flash` et `groupe` ont été ajoutées après la première version du
schéma v4. Une base existante est migrée en place à sa première ouverture en
écriture : `groupe` reçoit `1` pour toutes les photos déjà présentes et
l'index `idx_photos_groupe` est créé. Le groupe est un attribut de l'index,
pas une métadonnée XMP : une synchronisation qui ne fournit pas de nouvelle
valeur le conserve.

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

## Nettoyer les photos absentes du disque

Le script `dataBase.clean_missing` ne supprime jamais de fichier : il retire
seulement les lignes de `photos.db` dont le fichier n'existe plus. Le contrôle
porte sur toutes les lignes de `photos` (PSD, JPEG, RAW, etc.), pas seulement
sur les formats suivis par Classement. La racine utilisée pour vérifier les
chemins vient de `meta.source_root`, ou de `--root` ; si elle n'existe pas, le
script refuse d'agir.

```bash
# Dry-run (mode par défaut) : liste les identifiants et chemins absents
pixi run python -m dataBase.clean_missing

# Même chose en JSON, pratique pour un script
pixi run python -m dataBase.clean_missing --json

# Écrit la liste id<TAB>chemin dans un fichier
pixi run python -m dataBase.clean_missing --output-file absentes.txt

# Ou redirige la sortie standard : le résumé part sur la sortie d'erreur
pixi run python -m dataBase.clean_missing > absentes.txt

# Supprime une liste d'identifiants, uniquement s'ils sont absents
pixi run python -m dataBase.clean_missing --ids 12 34 56 --yes
pixi run python -m dataBase.clean_missing --ids-file absentes.txt --yes

# Supprime toutes les lignes dont le fichier est absent
pixi run python -m dataBase.clean_missing --all --yes
```

Le dry-run produit une ligne `id<TAB>chemin` par photo absente, précédée d'un
en-tête commenté. C'est directement le format attendu par `--ids-file`, qui
accepte aussi `id chemin`, un identifiant seul par ligne, plusieurs identifiants
séparés par des espaces ou des virgules, et les commentaires `#`.
`--output-file` n'est disponible qu'en dry-run.

Sans `--yes`, le script demande une confirmation dans un terminal et refuse
la suppression en mode non interactif. Les identifiants inconnus et les photos
encore présentes sont signalés et ne sont pas supprimés. Si `--all`
supprimerait toute la base (racine suspecte ou disque vide), le script refuse
sans `--force`.

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

- `photos_db.py` : schéma, connexions, recherche et catalogue des personnes ;
- `annotations.py` : décodage des valeurs XMP, éclatement des mots-clés,
  classement personnes / taxons / mots-clés ;
- `sync.py` : reset et mise à jour dossier par dossier ;
- `clean_missing.py` : liste ou supprime les lignes dont le fichier a disparu ;
- `rebuild.py` : reconstruction complète depuis les fichiers ;
- `face_db.py` : schéma et accès à `face.db` ;
- `export.py` : DataFrames et export CSV.

Les applications n’écrivent pas les tables dérivées des photos : elles
écrivent les métadonnées, puis `dataBase.sync` reconstruit `photos.db`. Seule la
création d’une personne dans Visages écrit directement le catalogue `people`,
car il ne se déduit pas des métadonnées des photos. L’application globale fait
seulement une lecture en mode lecture seule de la table `meta` pour vérifier la
fraîcheur de l’index. Les chemins viennent de `preferences.json` via
`tools/environment.py`.

`update` enregistre aussi `synced_revision`, `synced_scope`, `sync_complete` et
`sync_errors` dans `meta`. Ces valeurs permettent à l’application globale de
savoir si la base couvre vraiment le dossier courant et si la dernière
synchronisation s’est terminée sans erreur.

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
