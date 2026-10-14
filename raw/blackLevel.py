import rawpy
import numpy as np
import matplotlib.pyplot as plt
import re
from pathlib import Path
from tools.getMetaData import *

# Dossier contenant tes NEF
raw_folder = Path("/Users/pc/PHOTOS/tests/")

# On récupère tous les fichiers NEF du dossier
files = sorted(raw_folder.glob("*.NEF"))


for f in files:
    file = str(f)
    # Essaie d'extraire l'ISO du nom de fichier (ex: ISO800)
    C= get_metadata(file)
    with rawpy.imread(file) as raw:
        raw_data = raw.raw_image_visible.astype(np.float32)
        h, w = raw_data.shape
        bl = raw.black_level_per_channel
        wl = raw.white_level
        print(f"{file:20s} ISO={C['EXIF']['ISO']:5d}  BL={bl[0]:7.1f} {bl[1]:7.1f} {bl[2]:7.1f} {bl[3]:7.1f}   WL={wl:7.1f}")
        print (f'Nombre de pixels sous le bl {int(100*np.sum(raw_data < bl[0])/(h*w)):3} %')
