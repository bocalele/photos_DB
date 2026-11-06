# Classement

Application web locale pour déplacer des photos et des dossiers dans le fonds
photo, avec mise à jour immédiate des chemins dans `photos.db` et `face.db`.

## Fonctionnalités

- **Arborescence style VS Code** : navigation sombre dans les dossiers du
  fonds photo, chargement progressif des sous-dossiers, chemin courant partagé
  avec les autres applications d'Annotations.
- **État de synchronisation** : le nom du dossier est coloré directement dans
  l'arborescence. Vert : toutes les photos sont présentes et à jour dans
  `photos.db` ; orange : au moins une photo a été modifiée ou n'est pas
  indexée ; rose : un dossier contient des PSD/JPEG mais aucune photo de ce
  dossier n'est en base ; rouge : au moins une photo de la base est absente du
  disque. La couleur d'un dossier parent est la plus critique de ses enfants,
  dans l'ordre rouge, rose, orange, vert.
- **Périmètre des couleurs** : seuls les fichiers `.psd`, `.jpg` et `.jpeg`
  sont suivis, quel que soit le nom du dossier qui les contient. Les fichiers
  RAW et les autres formats restent neutres, même s'ils sont indexés. Une
  légende rappelle ces quatre couleurs sous l'en-tête de l'arborescence.
- **Rafraîchissement après synchronisation** : lorsque `photos.db` est mise à
  jour depuis Annotations, Classement relit automatiquement l'arborescence et
  les statuts sans recharger la page.
- **Deux modes de déplacement** : **Photos** pour déplacer des fichiers et
  **Dossiers** pour sélectionner des sous-dossiers. Les deux modes acceptent le
  clic simple, `Cmd`/`Ctrl` + clic, `Shift` + clic, « Tout sélectionner » et
  « Désélectionner ».
- **Glisser-déposer** : glissez les photos ou dossiers sélectionnés vers un
  dossier de l'arborescence, une carte de dossier ou la grille courante.
- **Import depuis le Finder** : déposez des fichiers ou des dossiers du Finder
  sur un dossier de l'arborescence. Les fichiers sont **copiés** dans le fonds
  photo en conservant leur arborescence relative ; les collisions et formats
  non photo sont refusés, rien n'est écrasé.
- **Déplacement vers…** : alternative au glisser-déposer, avec la liste des
  dossiers de destination.
- **Création et renommage** : création d'un dossier dans le dossier courant,
  renommage d'un dossier ou d'une photo sélectionnée. L'extension d'une photo
  est conservée.
- **Notation** : cinq étoiles sur chaque photo en mode icônes et dans le
  diaporama. La note est écrite dans les métadonnées XMP/EXIF et répercutée
  dans `photos.db` ; cliquer sur l'étoile active retire la note.
- **Corbeille** : le bouton corbeille déplace la photo et ses sidecars XMP
  dans la corbeille macOS, puis retire ses entrées de `photos.db` et `face.db`.
  Une confirmation est demandée.
- **Diaporama** : mode dédié à la notation et à la suppression, avec navigation
  au clavier (`←`/`→`), compteur et affichage plein cadre.
- **Sidecars XMP** : lors du déplacement, du renommage ou de la mise à la
  corbeille d'une photo, les fichiers `.xmp` associés suivent automatiquement.
- **Mise à jour des index** : les chemins sont modifiés dans `photos.db`
  (`photos.relpath`) et `face.db` (`Photo`, `Face`, `RefFace`). Les décisions
  de visages, les mots-clés et les métadonnées des fichiers ne sont pas
  modifiés par un déplacement.
- **Sécurité** : les collisions de noms sont refusées, un dossier ne peut pas
  être déplacé dans lui-même, et rien ne peut sortir de la racine photo.
  En cas d'échec de la mise à jour des bases, le déplacement physique est
  annulé.

## Préférences

`initial_directory` est commun à toutes les applications. Cliquer sur un
dossier dans l'arborescence met à jour ce dossier courant ; les autres
applications d'Annotations suivent le changement.

## Lancement

Depuis la racine du projet :

```bash
pixi run python organize/server.py [dossier_photos] --port 8770
```

Puis ouvrir http://127.0.0.1:8770/.

L'application est aussi lancée automatiquement par le lanceur global
`annotations/server.py`, dans l'onglet **Classement**.

## Raccourcis clavier

- `Cmd`/`Ctrl` + `A` : tout sélectionner dans le mode courant
- `F2` ou `Cmd` + `Entrée` : renommer l'élément sélectionné
- `←` / `→` : photo précédente / suivante en diaporama
- `Échap` : désélectionner, ou revenir aux icônes depuis le diaporama

## Limites

- Le déplacement est immédiat, sans boîte de confirmation, comme dans le
  Finder. Les collisions sont signalées et rien n'est écrasé.
- L'import depuis le Finder copie les fichiers ; il ne les déplace pas hors du
  Finder. Les fichiers importés ne sont pas indexés automatiquement dans
  `photos.db` : un dossier déjà indexé passe en orange jusqu'à la prochaine
  synchronisation, tandis qu'un dossier PSD/JPEG entièrement nouveau passe en
  rose et remonte cette information dans l'arborescence.
- Les fichiers qui ne sont pas des photos (notes, documents…) ne sont pas
  affichés, mais un dossier déplacé les emporte avec lui.
- Les vignettes et les aperçus du diaporama sont recréés à la demande dans le
  dossier d'aperçus partagé. Les formats JPEG, PNG, GIF, WebP et AVIF sont
  servis directement ; les RAW et formats non navigateur sont convertis.
