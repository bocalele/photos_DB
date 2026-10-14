import os
import sqlite3
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(__file__)))

from tools.getMetaData import get_metadata, getPerso
from tools.image_files import (
    as_list,
    create_thumbnail,
    extract_metadata,
    get_image_files,
    get_unique_id,
    with_progress,
)


def save_photo_links(cursor, id_photo, perso, people_map):
    """Remplace les liaisons gerees par cet import pour une photo."""
    cursor.execute('DELETE FROM Photo_Keywords WHERE id_photo=?', (id_photo,))
    cursor.execute('DELETE FROM Photo_Peoples WHERE id_photo=?', (id_photo,))

    # --- Keywords ---
    for kw in as_list(perso['Keywords']):
        # Si le mot clé correspond à une personne connue, on remplace par l'id_people
        id_people_kw = people_map.get(kw)
        if id_people_kw:
            cursor.execute(
                'INSERT OR IGNORE INTO Photo_Peoples (id_photo, id_people) VALUES (?, ?)',
                (id_photo, id_people_kw)
            )
        keyword_label = str(id_people_kw) if id_people_kw else kw
        cursor.execute('INSERT OR IGNORE INTO Keywords (label) VALUES (?)', (keyword_label,))
        cursor.execute('SELECT id_keyword FROM Keywords WHERE label=?', (keyword_label,))
        res = cursor.fetchone()
        if res:
            id_keyword = res[0]
            cursor.execute(
                'INSERT OR IGNORE INTO Photo_Keywords (id_photo, id_keyword, id_root) VALUES (?, ?, ?)',
                (id_photo, id_keyword, 0)
            )

    # --- Peoples ---
    if perso['Author']:
        id_people = people_map.get(perso['Author'])
        if id_people:
            cursor.execute(
                'INSERT OR IGNORE INTO Photo_Peoples (id_photo, id_people) VALUES (?, ?)',
                (id_photo, id_people)
            )


def parse_and_save(root_dir, db_path, dry_run=False, thumbnails_dir=None):
    print ("Extraction des tables d'asocciation .....", end='')
    conn_peoples = sqlite3.connect(db_path)
    cursor_peoples = conn_peoples.cursor()
    cursor_peoples.execute("SELECT id_people, nom, prenom, surnom FROM Peoples")
    people_rows = cursor_peoples.fetchall()
    # Création d'un mapping nom/prenom/surnom -> id_people
    people_map = {}
    for row in people_rows:
        id_people, nom, prenom, surnom = row
        # On crée plusieurs clés possibles pour la robustesse
        if nom and prenom:
            people_map[f"{prenom} {nom}".strip()] = id_people
        if nom:
            people_map[nom.strip()] = id_people
        if prenom:
            people_map[prenom.strip()] = id_people
        if surnom:
            people_map[surnom.strip()] = id_people
    conn_peoples.close()
    print ("OK",)

    if not dry_run:
        conn = sqlite3.connect(db_path)
        cursor = conn.cursor()
    else:
        conn = None
        cursor = None
    count = 0
    ignored_dirs = [thumbnails_dir] if thumbnails_dir else []
    print("Recherche des fichiers images .....", end='')
    imgs = list(get_image_files(root_dir, ignored_dirs=ignored_dirs))
    print(f"{len(imgs)} fichiers trouvés.") 
    for img_path in with_progress(
        imgs,
        disabled=dry_run,
        desc="Extraction metadata + enregistrement DB",
        unit="photo"
    ):
        rel_dir = os.path.relpath(os.path.dirname(img_path), root_dir)
        # Nouveau chemin pour les miniatures : thumbnails_dir/rel_dir/filename
        if thumbnails_dir:
            thumb_dir = os.path.join(thumbnails_dir, rel_dir)
        else:
            thumb_dir = os.path.join(root_dir, rel_dir, 'thumbnail')
        base = os.path.basename(img_path)
        thumb_path = create_thumbnail(img_path, thumb_dir)
        width, height = extract_metadata(img_path)
        filename = base
        unique_id = get_unique_id(img_path)
        # Extraction des métadonnées avancées
        try:
            meta = get_metadata(img_path)
            exif = meta.get('EXIF', {})
            iptc = meta.get('IPTC', {})
            xmp = meta.get('XMP', {})
            perso = getPerso(exif, iptc, xmp)
            # Extraction des champs EXIF principaux
            date = exif.get('DateTimeOriginal') or iptc.get('DateCreated') or xmp.get('CreateDate')
            model = exif.get('Model')
            lens = exif.get('LensModel')
            focal = exif.get('FocalLengthIn35mmFormat')
            aperture = exif.get('FNumber')
            speed = exif.get('ShutterSpeedValue')
            iso = exif.get('ISO')
            correction = exif.get('ExposureCompensation')
        except Exception as e:
            print(f"Erreur extraction metadata avancée {img_path}: {e}")
            perso = {'Author': None, 'Rating': None, 'Keywords': None, 'HKeywords': None}
            date = model = lens = focal = aperture = speed = iso = correction = None

        if not dry_run:
            # Vérifie si la photo existe déjà, même si elle a changé de chemin.
            cursor.execute(
                '''SELECT id_photo FROM Photos
                   WHERE path=? OR unique_id=?
                   ORDER BY CASE WHEN path=? THEN 0 ELSE 1 END
                   LIMIT 1''',
                (img_path, unique_id, img_path)
            )
            existing_photo = cursor.fetchone()
            photo_values = (
                filename, img_path, thumb_path, height, width, img_path,
                perso['Author'], perso['Rating'], date, model, lens, focal,
                aperture, speed, iso, correction, unique_id
            )
            if existing_photo:
                id_photo = existing_photo[0]
                cursor.execute('''UPDATE Photos SET
                    filename=?, path=?, thumbPath=?, hauteur=?, largeur=?, fullsize=?,
                    author=?, rating=?, date=?, model=?, Lens=?, focal=?,
                    aperture=?, speed=?, iso=?, correction=?, unique_id=?
                    WHERE id_photo=?''',
                    photo_values + (id_photo,))
            else:
                cursor.execute('''INSERT INTO Photos (
                    filename, path, thumbPath, hauteur, largeur, fullsize, author, rating,
                    date, model, Lens, focal, aperture, speed, iso, correction, unique_id
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)''',
                    photo_values)
                id_photo = cursor.lastrowid

            save_photo_links(cursor, id_photo, perso, people_map)
        else:
            print(f"[DRY RUN] {filename} | {img_path}")
            print(f"  - thumb: {thumb_path}")
            print(f"  - taille: {width}x{height}")
            # Affichage de l'auteur avec id ou en rouge si inconnu
            author = perso['Author']
            if author:
                id_people = people_map.get(author)
                if id_people:
                    print(f"  - auteur: {author} (id={id_people})")
                else:
                    print(f"  - auteur: \033[91m{author} (inconnu)\033[0m")
            else:
                print(f"  - auteur: {author}")
            # Affichage des mots-clés avec id_people si possible, sinon en rouge si personne inconnue
            kw_display = []
            for kw in as_list(perso['Keywords']):
                id_people_kw = people_map.get(kw)
                if id_people_kw:
                    kw_display.append(f"{kw} (id={id_people_kw})")
                else:
                    # Si le mot clé ressemble à une personne mais inconnue, on l'affiche en rouge
                    if kw.strip():
                        kw_display.append(f"\033[91m{kw} (inconnu)\033[0m")
                    else:
                        kw_display.append(kw)
            print(f"  - keywords: {kw_display}")
            print(f"  - rating: {perso['Rating']}")
            print(f"  - date: {date}")
            print(f"  - model: {model}")
            print(f"  - lens: {lens}")
            print(f"  - focal: {focal}")
            print(f"  - aperture: {aperture}")
            print(f"  - speed: {speed}")
            print(f"  - iso: {iso}")
            print(f"  - correction: {correction}")
            print(f"  - unique_id: {unique_id}")
        count += 1
    if not dry_run and conn:
        conn.commit()
        conn.close()
        print("Import terminé.")
    elif dry_run:
        print(f"[DRY RUN] {count} fichiers analysés.")

if __name__ == "__main__":
    import argparse
    parser = argparse.ArgumentParser(description="Importe les photos dans la base de données.")
    parser.add_argument("repertoire", help="Répertoire à parcourir")
    parser.add_argument("database", help="Fichier de base de données")
    parser.add_argument("--thumbnails-dir", help="Répertoire où stocker les miniatures (optionnel)")
    parser.add_argument("--dry-run", action="store_true", help="Simule l'import sans écrire dans la base")
    args = parser.parse_args()
    parse_and_save(args.repertoire, args.database, dry_run=args.dry_run, thumbnails_dir=args.thumbnails_dir)
