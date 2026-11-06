# Faune — classification locale des oiseaux

## Application web

L’application est indépendante de Geotag et du sélecteur relié à la base de
données. Ouvrir `Faune.app` depuis Finder, puis la glisser dans le Dock
pour la conserver. Le bundle doit rester dans le dossier `faune`, à côté
de `Lancer Faune.command`. L’icône du Dock sert de lanceur : le serveur est
détaché du bundle, donc l’icône ne clignote plus pendant le démarrage et un
nouveau clic rouvre l’interface déjà lancée. Un sélecteur macOS permet de
choisir le dossier de photos et ouvre ensuite l’interface dans le navigateur.
On peut aussi lancer directement `Lancer Faune.command` ou démarrer
l’application depuis un terminal :

```bash
cd faune
pixi run python server.py "/chemin/vers/le/dossier-de-photos"
```

Le serveur choisit `http://127.0.0.1:8000` (ou affiche l’adresse si le port est
déjà occupé). Pour arrêter le serveur, faire `Ctrl+C` dans le terminal.
Le dossier initial du sélecteur est défini par la clé `initial_directory` dans
le [`preferences.json`](../preferences.json) commun à toutes les applications.
Si ce volume ou dossier n’est pas disponible, le sélecteur s’ouvre dans le
dossier personnel.

Les photos du dossier s’affichent sous forme de vignettes à taille réglable.
Sélectionner les photos à traiter, puis cliquer sur **Classer les photos
sélectionnées**. Choisir l’une des cinq propositions, ou chercher une autre
espèce en saisissant son nom vernaculaire français **ou son nom scientifique**
dans le menu des choix. Si le nom saisi n’existe ni dans TAXREF ni dans les
thésaurus, un bouton permet de créer le taxon ; à la validation, l’application
demande à quelle grande classe le rattacher.
Les informations taxonomiques s’affichent pour les photos sélectionnées, déjà
classées ou dont l’espèce est déjà présente dans les métadonnées. La coche à
côté du nom écrit ou retire les mots-clés IPTC/XMP directement dans la photo.
Une bordure verte indique qu’un mot-clé d’espèce reconnu est présent.
Le bouton **Supprimer les données Faune** (ou la croix rouge sous la coche
verte d’une photo) efface le couple nom vernaculaire/nom scientifique et
l’arborescence taxonomique `XMP-lr:HierarchicalSubject`. Les relations
`photo_taxa` sont retirées à la prochaine synchronisation `photos.db`. Les
autres mots-clés et les arborescences de personnes sont conservés.

Faune n’écrit que dans les métadonnées des fichiers. `photos.db` reste un
index dérivé : il est reconstruit par le bouton **photos.db** de l’application
globale, ou proposé à la fermeture. Une détermination choisie mais non validée
est signalée **À valider** et bloque la synchronisation tant qu’elle n’a pas
été validée ou supprimée.

Lorsqu’un taxon inconnu est créé, la classe proposée est une grande classe des
thésaurus, comme dans le sélecteur : **Oiseaux**, **Plantes terrestres**,
**Insectes s.l.**, etc. **Plantes terrestres** est sélectionnée par défaut.

À l’ouverture, l’application lit les mots-clés IPTC et XMP ainsi que les
arborescences XMP de chaque photo. Les indicateurs près de la case de sélection
signalent si le mot-clé vernaculaire et l’arborescence correspondent à TAXREF.
Cliquer sur l’indicateur du mot-clé lance l’affichage de cette espèce comme une
proposition BioCLIP à 100 %. L’écriture conserve les mots-clés non taxonomiques
déjà présents dans la photo. Lorsqu’un taxon absent de TAXREF est remplacé
manuellement, le nouveau taxon remplace aussi l’ancien sur toutes les photos
qui le contiennent.

ExifTool est installé dans l’environnement Pixi de `faune` et sert à
extraire les aperçus intégrés aux fichiers PSD et RAW. Si aucun aperçu intégré
n’est disponible, macOS essaie de convertir le fichier avec `sips`. Les TIFF
sont convertis en JPEG pour leur affichage dans le navigateur.

Le premier lancement peut télécharger et mettre en cache les poids BioCLIP 2
ainsi que les embeddings taxonomiques nécessaires. Les classifications
suivantes utilisent ces fichiers locaux ; le calcul des photos est effectué sur
le Mac avec MPS.

Au démarrage, l’index TAXREF est construit une première fois (quelques
secondes), puis conservé dans `faune/.cache/`. Les lancements suivants chargent
ce cache en moins d’une seconde. Le dossier `.cache/` est recréé
automatiquement si TAXREF, TAXVERN ou les thésaurus changent.

## Classification d’une image en ligne de commande

Le script `classify_photo.py` classe une image avec BioCLIP 2, puis utilise les fichiers TAXREF et TAXVERN du dossier `TAXREF/` pour afficher le nom français et la classification taxonomique.

Depuis le dossier `faune` :

```bash
pixi run python classify_photo.py "/chemin/vers/une-photo.psd"
```

BioCLIP retourne les cinq meilleurs résultats par défaut. Pour en afficher davantage :

```bash
pixi run python classify_photo.py "/chemin/vers/une-photo.jpg" --top 10
```

Le calcul se fait sur le GPU Apple (`mps`) par défaut. Pour lancer sur le processeur :

```bash
pixi run python classify_photo.py "/chemin/vers/une-photo.jpg" --device cpu
```

Le script détecte automatiquement l’unique fichier `TAXREFv*.txt` dans `TAXREF/` et utilise le fichier `TAXVERNv*.txt` de même version situé à côté. Le dossier peut être déplacé : renseigner `taxref_dir` dans la section `environment` du [`preferences.json`](../preferences.json) (ou passer `--taxref`). Si plusieurs versions sont présentes, choisir explicitement le fichier :

```bash
pixi run python classify_photo.py photo.jpg --taxref TAXREF/TAXREFv18.txt
```

BioCLIP applique lui-même les transformations et la résolution attendues par son modèle ; l’image n’est pas étirée. Le script lit les formats gérés par Pillow (dont les PSD lisibles par Pillow) et essaie la conversion macOS `sips` pour les autres formats pris en charge par macOS.

Le nom scientifique prédit est rapproché de TAXREF, y compris via les noms synonymes. L’arborescence affiche les noms vernaculaires français quand TAXREF/TAXVERN en fournit un, et conserve le nom taxonomique comme solution de repli. Les noms TAXREF sont issus de la version locale téléchargée ; ils ne changent pas le classement fait par BioCLIP.

Si le nom scientifique proposé par BioCLIP est absent de TAXREF, l’application le cherche dans les thésaurus naturalistes de `faune/Thesaurus/` (format Lightroom, à côté de `TAXREF/`). Quand elle l’y trouve, elle utilise le nom vernaculaire et l’arborescence du thésaurus (par exemple `Arbres > … > Cyprès > Cyprès de Santa Cruz`). La recherche manuelle accepte aussi ces noms. Le dossier est configurable par `thesaurus_dir` dans `preferences.json`.
