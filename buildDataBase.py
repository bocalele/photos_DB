import os
import glob
import pandas as pd
import hashlib


root = "/Users/pc/PHOTOS"


# def checksum(file):
#     h = hashlib.sha256()
#     with open(file, "rb") as f:
#         while chunk := f.read(8192):
#             h.update(chunk)
#     return h.hexdigest()

def checksum(filepath, algo='md5', block_size=65536):
    """
    Calcule le checksum d'un fichier de manière efficace et sûre.
    
    Parameters:
        filepath (str): Chemin vers le fichier.
        algo (str): Algorithme de hachage (par défaut 'md5').
        block_size (int): Taille des blocs lus (par défaut 64 Ko).
    
    Returns:
        str: Hash hexadécimal, ou None en cas d'erreur.
    """
    try:
        hasher = hashlib.new(algo)
        with open(filepath, 'rb') as f:
            for chunk in iter(lambda: f.read(block_size), b''):
                hasher.update(chunk)
        return hasher.hexdigest()
    except Exception as e:
        print(f"[Erreur] Impossible de calculer le checksum pour {filepath} : {e}")
        return None


fichiers = glob.glob(os.path.join(root, '**', '*.psd'), recursive=True)
# print (fichiers)
# for f in fichiers:
#     print(os.path.basename(f),os.path.abspath(f), checksum(f))

data = [[os.path.basename(f),os.path.abspath(f), checksum(f)] for f in fichiers]
file = pd.DataFrame (data=data, columns=['file', 'path', 'checksum'])

print (file)