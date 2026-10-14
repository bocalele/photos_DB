
from tools.display import summary
from tools.setMetaData import set_metadata

from tqdm import tqdm
import pandas as pd
import ast

def df2Categories (df, i):

    exif = {
        "GPSLatitude": "GPSLatitude",
        "GPSLongitude": "GPSLongitude",
        "GPSAltitude": "GPSAltitude",
        "GPSLatitudeRef": "GPSLatitudeRef",
        "GPSLongitudeRef": "GPSLongitudeRef",
        "GPSVersionID": "GPSVersionID",
        "GPSAltitudeRef": "GPSAltitudeRef",
        "Model": "Model",
        "Focal Lens": "LensModel",
        "Focal": "FocalLengthIn35mmFormat",
        "Aperture": "FNumber",
        "Speed": "ShutterSpeedValue",
        "ISO": "iso",
        "ExposureCompensation": "ExposureCompensation",
        "Date": "DateTimeOriginal",
        "Author": "Artist",
    }
    iptc = {
        "Country": "Country-PrimaryLocationName",
        "SubLocation": "Sub-location",
        "Keywords": "Keywords",
    }
    xmp={
        "Rating": "Rating",
        "HKeywords": "HierarchicalSubject",
    }
    C={}
    C["EXIF"] = {exif[col]: df.loc[i, col]  for col in exif if col in df.columns}
    C["IPTC"] = {iptc[col]: df.loc[i, col]  for col in iptc if col in df.columns}
    C["XMP"] = {xmp[col]: df.loc[i, col]  for col in xmp if col in df.columns}
    return (C)


def read_metadata_fromCSV (filename, version):
    df = pd.read_csv (filename, sep=';',encoding="utf-8-sig")
    if version == 1:
        df["GPS"] = df["GPS"].apply(
            lambda x: ast.literal_eval(x) if pd.notna(x) else {}
        )
        gps = pd.json_normalize(df["GPS"])
        return pd.concat([df.drop(columns=["GPS"]), gps], axis=1)
    else:
        return df




df = read_metadata_fromCSV ("photos.csv", 1)
for i, row in tqdm(df.iterrows(), total=len(df), desc="Traitement"):
    C = df2Categories (df, i)
    file = row.file
    set_metadata(file, C, dry_run=False)

