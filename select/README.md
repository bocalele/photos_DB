# Sélection

Application autonome de sélection de photos et de création d'albums HTML dans
`photos.db`. Elle ne fait pas partie de la fenêtre globale d'annotation.

Elle propose trois modes de sélection :

- **Hiérarchie** : graphe des familles ou des taxons. Pour la faune, les
  grands groupes sont les classes (Oiseaux, Mammifères, Insectes…) et les
  couleurs regroupent les ordres ;
- **Carte** : sélection de lieux ;
- **Mots-clés** : sélection de mots-clés.

Les filtres (note, années, boîtier, objectif, ISO, focale) sont regroupés dans
un tiroir masqué par défaut. Le bouton **Afficher les photos** ouvre la
sélection courante.

## Sélection et albums

Dans la fenêtre d'affichage, chaque photo possède une pastille de sélection
(`+` / `✓`) : cliquer sur la photo ou sur la pastille l'ajoute ou la retire de
la sélection d'album. Les boutons **Tout sélectionner** et **Effacer**
travaillent sur la sélection courante.

Le bouton **Créer l'album** bascule l'application en mode création :

- quatre templates inspirés de jAlbum : Grille, Mosaïque, Diaporama, Livre ;
- titre, sous-titre, introduction et auteur ;
- quatre ambiances : Sombre, Clair, Chaleureux, Papier ;
- nombre de colonnes, légendes et métadonnées ;
- choix de la photo de couverture et retrait de photos ;
- aperçu en direct dans un cadre isolé.

Chaque album repart d'une configuration neuve : revenir à la sélection puis
recréer un album ne reprend ni le titre, ni le template, ni les réglages de
l'album précédent. Les photos sélectionnées restent disponibles pour la
nouvelle création.

Les titres des photos ne sont plus les noms de fichiers par défaut, dans la
sélection comme dans l'album : le nom vernaculaire du taxon le plus précis est
utilisé pour la faune, puis les noms des personnes, un mot-clé, la ville, et
seulement en dernier recours le nom du fichier. Dans l'application, le nom du
fichier reste disponible dans l'infobulle.

Le template **Diaporama** propose un bouton **Infos techniques** qui ouvre un
panneau refermable avec le boîtier, l'objectif, la focale, l'ouverture, la
vitesse, l'ISO et la correction d'exposition de la photo affichée.

Le bouton **Exporter l'album (.zip)** produit une archive autonome contenant
`index.html` et un dossier `images/`. L'album fonctionne ensuite dans
n'importe quel navigateur, sans serveur ni connexion, avec la navigation au
clavier (diaporama, livre et visionneuse).

## Lancement

Double-cliquer sur `Selection.app`, ou lancer `Lancer Sélection.command`, ou
directement :

```bash
python select/server.py "/chemin/vers/le/dossier-de-photos" --port 8765
```

Le serveur lit `photos.db` (schéma v4) via `dataBase.photos_db`, génère les
vignettes à la demande dans `previews_dir/selector` et ne stocke aucun chemin
d'aperçu en base. L'export crée des images JPEG de 1600 px au maximum dans
l'archive ; les fichiers sources ne sont jamais modifiés.
