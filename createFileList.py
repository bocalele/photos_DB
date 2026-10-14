
from tools.parse import  getFiles, getDates, parse_all
from tools.getMetaData import *
from tqdm import tqdm


#Traitement des arguments
args = parse_all ()
start, end = getDates (args)
lFiles = getFiles (args, start, end)
df = pd.DataFrame() 
rows = []

for file in tqdm(lFiles, desc="Lecture des métadonnées", unit="fichier"):
    C= get_metadata (file)

    camera =  getCameraDf (C['EXIF'], C['XMP'])
    infos = getInfoDf (C['EXIF'], C['IPTC'], C['XMP'])
    perso = getPersoDf (C['EXIF'], C['IPTC'], C['XMP'])
    rows.append (pd.concat([pd.DataFrame (columns=['file'], data=[file]), camera, infos, perso], axis=1))

df = pd.concat(rows, ignore_index=True)

df.to_csv(
    "photos.csv",
    index=False,
    encoding="utf-8-sig"
)

