import hashlib
import os

from PIL import Image

try:
    from tqdm import tqdm
except ImportError:
    tqdm = None


IMAGE_EXTS = {
    '.jpg', '.jpeg', '.png', '.bmp', '.tiff', '.tif',
    '.gif', '.webp', '.heic', '.psd', '.nef'
}


def get_image_files(root_dir, ignored_dirs=None):
    """Recupere tous les fichiers images dans les sous-repertoires."""
    ignored_dirs = [os.path.abspath(path) for path in (ignored_dirs or [])]
    for dirpath, dirnames, files in os.walk(root_dir):
        abs_dirpath = os.path.abspath(dirpath)
        if any(abs_dirpath == ignored or abs_dirpath.startswith(ignored + os.sep) for ignored in ignored_dirs):
            dirnames[:] = []
            continue
        dirnames[:] = [
            dirname for dirname in dirnames
            if dirname != 'thumbnail'
            and not any(
                os.path.abspath(os.path.join(dirpath, dirname)) == ignored
                or os.path.abspath(os.path.join(dirpath, dirname)).startswith(ignored + os.sep)
                for ignored in ignored_dirs
            )
        ]
        for filename in files:
            if os.path.splitext(filename)[1].lower() in IMAGE_EXTS:
                yield os.path.join(dirpath, filename)


def create_thumbnail(image_path, thumb_dir, size=(256, 256)):
    """Cree une miniature pour une image et la sauvegarde dans thumb_dir."""
    os.makedirs(thumb_dir, exist_ok=True)
    base = os.path.basename(image_path)
    thumb_path = os.path.join(thumb_dir, base)
    if os.path.exists(thumb_path):
        return thumb_path
    try:
        with Image.open(image_path) as img:
            img.thumbnail(size)
            img.save(thumb_path)
        return thumb_path
    except Exception as e:
        print(f"Erreur miniature {image_path}: {e}")
        return None


def extract_metadata(image_path):
    """Extrait les dimensions de base d'une image."""
    try:
        with Image.open(image_path) as img:
            width, height = img.size
        return width, height
    except Exception as e:
        print(f"Erreur metadata {image_path}: {e}")
        return None, None


def hash_file(path):
    """Hash SHA256 d'un fichier."""
    h = hashlib.sha256()
    with open(path, 'rb') as f:
        while True:
            chunk = f.read(8192)
            if not chunk:
                break
            h.update(chunk)
    return h.hexdigest()


def get_unique_id(path):
    """Retourne un identifiant unique pour la photo."""
    return hash_file(path)


def as_list(value):
    """Normalise une valeur metadata en liste."""
    if not value:
        return []
    if isinstance(value, list):
        return value
    return [value]


def with_progress(items, disabled=False, desc=None, unit=None):
    if disabled or tqdm is None:
        return items
    return tqdm(items, desc=desc, unit=unit, dynamic_ncols=True)
