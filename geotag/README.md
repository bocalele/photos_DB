# Géolocalisation des photos

Cette interface fonctionne indépendamment du sélecteur de photos relié à la
base de données. Elle lit les fichiers d’un dossier et affiche les métadonnées
GPS et de lieu déjà présentes dans les photos. Aucune donnée n’est stockée dans
`photos.csv`.

Lancer l’interface avec le dossier de photos à traiter :

```sh
pixi run python geotag/server.py "/Volumes/WD/PHOTOS/2026/Saisies-Ete/PSD"
```

Si aucun dossier n’est indiqué, une fenêtre système permet de choisir le dossier
au démarrage. Le dossier initial de cette fenêtre se configure dans
[`preferences.json`](./preferences.json), avec la clé `initial_directory`.
Si le chemin indiqué n’existe pas ou si le fichier est invalide, le dossier
courant est utilisé.

Pour lancer l’application depuis le Dock, ouvrez `Geotag.app` dans ce dossier
avec Finder, puis faites glisser son icône dans le Dock. L’application ouvre une
fenêtre Terminal dédiée, lance le serveur et ouvre Safari à la bonne adresse.
Laissez le fichier `Lancer Geotag.command` à côté de `Geotag.app` ; utilisez
`Ctrl+C` dans la fenêtre Terminal pour arrêter le serveur.

Puis ouvrir <http://127.0.0.1:8000> dans un navigateur. Sélectionnez une ou
plusieurs photos, puis faites un clic droit sur la carte. Les coordonnées GPS,
le sous-lieu et le pays proposés apparaissent dans une fenêtre modifiable.
« Enregistrer dans les photos » écrit directement les métadonnées dans les
fichiers sélectionnés : GPS EXIF, sous-lieu et pays IPTC, ainsi que lieu et pays
XMP. Une barre de progression suit les écritures en parallèle ; les erreurs
éventuelles sont indiquées par fichier.

Les métadonnées existantes sont lues à l’ouverture de la galerie pour afficher
les indicateurs GPS et lieu et le sous-lieu/pays associés à chaque photo. Les
vignettes des fichiers PSD et RAW sont extraites de leurs aperçus intégrés avec
ExifTool. Seules les vignettes visibles et proches de la zone affichée sont
demandées. Leur extraction est limitée à une photo à la fois afin d’éviter les
pointes de charge dans les dossiers volumineux.

La carte propose un plan de rue, un fond topographique de randonnée OpenTopoMap
par défaut et un fond clair Esri. La recherche et la reconnaissance d’adresses
utilisent OpenStreetMap Nominatim ; les fonds de carte et la recherche
nécessitent une connexion Internet.
