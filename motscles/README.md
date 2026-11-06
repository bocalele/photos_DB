# Mots-clés

Application web locale pour éditer les mots-clés, les notes (étoiles) et l'auteur des photos.

## Fonctionnalités

- **Mots-clés** : édition par lot dans une popup, sous forme de pastilles. Les mots-clés communs à toutes les photos sélectionnées apparaissent normalement, ceux qui ne sont pas partagés par toutes les photos apparaissent grisés. On peut retirer une pastille (×) ou ajouter un mot-clé (Entrée ou virgule).
- **Protection des lots** : seuls les ajouts et retraits explicitement demandés sont écrits. Les mots-clés grisés ne sont jamais recopiés automatiquement sur les autres photos. Les changements à plusieurs photos sont appliqués directement, sans confirmation supplémentaire.
- **Sans mots-clés** : bouton qui compte et affiche uniquement les photos dont les métadonnées ont été lues et qui n’ont aucun mot-clé.
- **Notes (étoiles)** : 5 étoiles cliquables directement sous chaque vignette (enregistrement immédiat) et dans la popup. Notes différentes = grisées ; la note par défaut est configurable.
- **Auteur** : champ texte dans la même popup que les mots-clés ; auteur par défaut configurable.
- **Groupe d’accès** : champ entier dans la popup, avec la valeur par défaut configurable. Le groupe est stocké uniquement dans `photos.db` (`photos.groupe`), pas dans les métadonnées du fichier. Les valeurs différentes sont signalées comme pour l’auteur et la note.
- **Compléter auteur et note** : bouton qui applique l’auteur et la note par défaut à toutes les photos qui n’en ont pas, sans modifier les valeurs déjà présentes.
- **Sélection multiple** : clic simple pour basculer, **Shift + clic** pour sélectionner une plage, boutons « Tout sélectionner » / « Désélectionner ».
- **Retour visuel** : la vignette enregistrée est encadrée en vert, comme dans Faune.
- **Vignettes** : chargement progressif (lazy loading) et mise à jour ciblée des cartes : une sélection ne recharge pas les images.
- **Écriture dans les fichiers** : métadonnées IPTC, XMP et EXIF.

## Préférences

Le fichier [`preferences.json`](../preferences.json), commun à toutes les
applications, contient :

```json
{
  "initial_directory": "/Volumes/WD/PHOTOS/2026/DIVERS/PSD",
  "apps": {
    "motscles": {"default_author": "Pierre", "default_rating": 3, "default_group": 1}
  }
}
```

- `initial_directory` : dernier dossier ouvert (mis à jour automatiquement).
- `apps.motscles.default_author` : auteur proposé quand les photos n'en ont pas.
- `apps.motscles.default_rating` : note proposée quand les photos n'en ont pas.
- `apps.motscles.default_group` : groupe d’accès proposé quand la photo n'en a pas.

## Lancement

Le lanceur utilise Pixi, comme Geotag et Faune, afin de disposer d'ExifTool :

```bash
# Double-clic sur Motscles.app ou sur :
./Lancer\ Mots-clés.command
```

Ou en ligne de commande, depuis la racine du projet :

```bash
pixi run python motscles/server.py [dossier_photos] [--host 127.0.0.1] [--port 8000]
```

Puis ouvrir http://127.0.0.1:8000 dans un navigateur.

## Dépendances

- Python 3.11+
- ExifTool (fourni par l'environnement Pixi du projet)
- sips (inclus dans macOS) pour les aperçus RAW/PSD

## Raccourcis clavier

- `Échap` : fermer la popup d'édition
- `Shift + clic` : sélectionner une plage de photos
- `Clic` sur une vignette : basculer la sélection
- `Entrée` / `,` dans le champ mot-clé : ajouter le mot-clé

## Métadonnées écrites

| Champ | IPTC | XMP | EXIF |
|-------|------|-----|------|
| Mots-clés | Keywords | dc:subject | - |
| Note (1-5) | - | xmp:Rating | Rating |
| Auteur | By-line | dc:creator | Artist |

Les mots-clés hiérarchiques (`XMP-lr:HierarchicalSubject`) ne sont volontairement pas modifiés, afin de préserver l'arborescence TAXREF écrite par l'application Faune.

Les écritures ne concernent que les métadonnées des fichiers. `photos.db` n’est
pas mise à jour immédiatement : le bouton **photos.db** de la barre
d’Annotations reconstruit l’index du dossier courant (mots-clés, note, auteur).
