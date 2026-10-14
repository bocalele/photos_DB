import exiftool
import json


# =========================
# LECTURE DES METADONNEES
# =========================

def lire_metadonnees(fichier):
    with exiftool.ExifTool() as et:
        metadata = et.execute_json(
            "-G",
            "-XMP-dc:Subject",
            "-IPTC:Keywords",
            "-XMP-dc:Title",
            "-IPTC:ObjectName",
            "-EXIF:DateTimeOriginal",
            "-XMP:CreateDate",
            "-XMP-mwg-rs:RegionList",
            fichier
        )[0]

    return metadata


def extraire_infos(meta):
    infos = {}

    # Mots-clés
    infos["mots_cles"] = meta.get("XMP-dc:Subject") or meta.get("IPTC:Keywords")

    # Date de prise de vue
    infos["date_prise"] = (
        meta.get("EXIF:DateTimeOriginal")
        or meta.get("XMP:CreateDate")
    )

    # Titre
    infos["titre"] = (
        meta.get("XMP-dc:Title")
        or meta.get("IPTC:ObjectName")
    )

    # Personnes détectées
    infos["personnes"] = meta.get("XMP-mwg-rs:RegionList")

    return infos


# =========================
# MODIFICATION DES METADONNEES
# =========================

def modifier_metadonnees(
    fichier,
    mots_cles=None,
    date_prise=None,
    titre=None,
    personnes=None
):

    commandes = []

    # --- Mots-clés ---
    if mots_cles is not None:
        commandes.append("-IPTC:Keywords=")
        commandes.append("-XMP-dc:Subject=")
        for mot in mots_cles:
            commandes.append(f"-IPTC:Keywords+={mot}")
            commandes.append(f"-XMP-dc:Subject+={mot}")

    # --- Date ---
    if date_prise:
        commandes.append(f"-EXIF:DateTimeOriginal={date_prise}")
        commandes.append(f"-XMP:CreateDate={date_prise}")

    # --- Titre ---
    if titre:
        commandes.append(f"-IPTC:ObjectName={titre}")
        commandes.append(f"-XMP-dc:Title={titre}")

    # --- Personnes (faces Lightroom) ---
    if personnes:
        commandes.append("-XMP-mwg-rs:RegionList=")
        for personne in personnes:
            commandes.append(
                f"-XMP-mwg-rs:RegionList+={{Type=Face,Name={personne}}}"
            )

    commandes.append("-overwrite_original")

    with exiftool.ExifTool() as et:
        et.execute(*commandes, fichier)


# =========================
# EXEMPLE UTILISATION
# =========================

fichier = "/Users/pc/PHOTOS/REPRODUCTION/Groupe4/JPEG/DSC_1467.jpg"

print("📖 Lecture...")
meta = lire_metadonnees(fichier)
infos = extraire_infos(meta)
print(json.dumps(infos, indent=2, ensure_ascii=False))

print("\n✏️ Modification...")
modifier_metadonnees(
    fichier,
    mots_cles=["Portrait", "Famille"],
    date_prise="2024:12:25 14:32:10",
    titre="Noël en famille",
    personnes=["Alice", "Bob"]
)

print("✅ Terminé")



print("📖 Lecture des métadonnées...")
meta = lire_metadonnees(fichier)
print(json.dumps(meta, indent=2, ensure_ascii=False))


