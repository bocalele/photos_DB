# Sélection

Application autonome de sélection de photos et de création d'albums HTML dans
`photos.db`. Elle ne fait pas partie de la fenêtre globale d'annotation.

Elle propose trois modes de sélection :

- **Hiérarchie** : graphe des familles ou des taxons. Pour la faune, les
  grands groupes sont les classes (Oiseaux, Mammifères, Insectes…) et les
  couleurs regroupent les ordres ;
- **Carte** : sélection de lieux ;
- **Mots-clés** : sélection de mots-clés.

Dans le graphe, le menu **Étiquettes** évite la superposition des noms :
- **Automatique** masque les libellés qui se chevauchent, en gardant toujours
  le nœud courant et le nœud survolé visibles ;
- **Toutes** affiche tous les noms, même superposés ;
- **Au survol** ne montre que le nœud survolé, son voisinage et le nœud courant ;
- **Aucune** masque les noms, l’infobulle restant disponible.

Le survol met en avant le sous-graphe du nœud sur plusieurs niveaux : les
arêtes deviennent rouges et leur intensité décroît avec la distance, comme
l’opacité des nœuds. L’infobulle affiche une vignette d’une photo appartenant au
nœud. Les libellés du graphe sont volontairement courts : nom vernaculaire seul
pour la faune, prénom pour les personnes ; le nom scientifique complet reste
dans l’infobulle et le menu contextuel. Les boutons **Ajuster** (cadrer tous les
nœuds visibles) et **Recentrer** (revenir au nœud courant) facilitent la
navigation, avec la molette pour le zoom et le glisser-déposer pour déplacer la
vue.

Un clic sur un nœud le définit comme nœud courant et ouvre son menu. Ce menu
permet de déplier/replier ses relations, de **Garder uniquement ce nœud**, ou
d’afficher à nouveau tous les nœuds. Un **double-clic** sur un nœud ne conserve
que sa descendance ; dans la vue **Visages**, les conjoints des personnes
conservées sont ajoutés. Un clic dans le fond du graphe ouvre aussi un menu avec
**Afficher tous les nœuds**, l’isolation du nœud courant et les actions de
cadrage.

Le nombre total de photos présentes dans `photos.db` est affiché sous le titre
de l’application, indépendamment des filtres en cours.

L’outil ouvert au démarrage est défini par la préférence `default_select_app`
(`geotag`, `faune`, `face` ou `motscles`) dans `preferences.json`. La valeur
livrée par défaut est `faune`.

Les filtres (note, années, boîtier, objectif, ISO, focale) sont regroupés dans
un tiroir masqué par défaut. Le bouton **Afficher les photos** ouvre la
sélection courante.

## Sélection et albums

Dans la fenêtre d'affichage, chaque photo possède une pastille de sélection
(`+` / `✓`) : cliquer sur la photo ou sur la pastille l'ajoute ou la retire de
la sélection d'album. **Shift + clic** sur une autre photo sélectionne toute la
plage entre les deux, et **Cmd/Ctrl + clic** ajoute ou retire une photo
isolée. Les boutons **Tout sélectionner** et **Effacer** travaillent sur la
sélection courante.

Le sélecteur **Trier** de la fenêtre d'affichage propose :
- **Date · récentes** / **Date · anciennes** ;
- **Note · meilleures** / **Note · moins bonnes** ;
- **Nom · A → Z** / **Nom · Z → A** ;
- **Aléatoire**, avec le bouton **Mélanger** pour tirer un nouvel ordre.

Le tri est effectué côté serveur avant la limite d’affichage, puis mémorisé
pour les ouvertures suivantes.

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
