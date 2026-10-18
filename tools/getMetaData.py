import json
import shutil
import subprocess
from collections import defaultdict

import pandas as pd


def _categories_from_record(record: dict) -> dict[str, dict]:
    categories = defaultdict(dict)
    for grouped_tag, value in record.items():
        if ":" not in grouped_tag or grouped_tag == "SourceFile":
            continue
        group, tag = grouped_tag.split(":", 1)
        if group in {"EXIF", "IFD0", "ExifIFD", "GPS", "IFD1"}:
            categories["EXIF"][tag] = value
            legacy_group = {
                "IFD0": "Image",
                "ExifIFD": "Photo",
                "GPS": "GPSInfo",
                "IFD1": "Thumbnail",
            }.get(group)
            if legacy_group:
                categories["EXIF"][f"{legacy_group}.{tag}"] = value
            if group == "ExifIFD":
                legacy_tag = {
                    "ISO": "ISOSpeedRatings",
                    "FocalLengthIn35mmFormat": "FocalLengthIn35mmFilm",
                    "ExposureCompensation": "ExposureBiasValue",
                }.get(tag)
                if legacy_tag:
                    categories["EXIF"][f"Photo.{legacy_tag}"] = value
        elif group.startswith("IPTC"):
            categories["IPTC"][tag] = value
            legacy_iptc = {
                "Sub-location": "SubLocation",
                "Country-PrimaryLocationName": "CountryName",
                "Country-Name": "CountryName",
            }.get(tag)
            if legacy_iptc:
                categories["IPTC"][f"Application2.{legacy_iptc}"] = value
        elif group.startswith("XMP"):
            categories["XMP"][tag] = value
            if group == "XMP-xmp":
                categories["XMP"][f"xmp.{tag}"] = value
            elif group == "XMP-dc":
                categories["XMP"][f"dc.{tag[0].lower()}{tag[1:]}"] = value
            elif group == "XMP-lr":
                categories["XMP"][f"lr.{tag[0].lower()}{tag[1:]}"] = value
        elif group == "MakerNotes":
            categories["MakerNotes"][tag] = value
        elif group == "Composite":
            categories["Composite"][tag] = value
        else:
            categories["OTHER"][tag] = value
    return categories


def get_metadata_many(
    filepaths: list[str], tags: list[str] | None = None
) -> dict[str, dict[str, dict]]:
    """Read several files in one ExifTool process, keyed by input path."""
    executable = shutil.which("exiftool")
    if executable is None:
        raise FileNotFoundError("ExifTool est introuvable dans le PATH.")
    results = {}
    for offset in range(0, len(filepaths), 100):
        paths = filepaths[offset:offset + 100]
        result = subprocess.run(
            [
                executable,
                "-json",
                "-G1",
                "-a",
                "-s",
                "-n",
                *(tags or []),
                *paths,
            ],
            capture_output=True,
            text=True,
            check=False,
            timeout=120,
        )
        if result.returncode:
            message = result.stderr.strip() or result.stdout.strip() or "Erreur ExifTool."
            raise RuntimeError(f"Impossible de lire les métadonnées : {message}")
        try:
            records = json.loads(result.stdout)
        except json.JSONDecodeError as error:
            raise RuntimeError("Réponse ExifTool invalide.") from error
        for record in records:
            source = record.get("SourceFile")
            if source:
                results[source] = _categories_from_record(record)
    return results


def get_metadata(filepath: str) -> dict[str, dict]:
    """Read metadata with ExifTool, preserving the EXIF/IPTC/XMP category API."""
    result = get_metadata_many([str(filepath)])
    try:
        return result[str(filepath)]
    except KeyError as error:
        raise RuntimeError(f"ExifTool n’a renvoyé aucune métadonnée pour {filepath}.") from error


def getComment (exif, xmp, iptc):
    comment = ''
    try:
        comment = xmp['Description']
    except:
        try:
            comment=exif['UserComment']
        except:
            try:
                comment=iptc['Caption-Abstract']
            except:
                pass
    return comment
def getTitle (exif, xmp, iptc):
    title =''
    try:
        title = xmp['Title']
    except:
        try:
            title=exif['ImageDescription']
        except:
            try:
                title=iptc['ObjectName']
            except:
                pass
    return title

def getGPS (exif):
    gps = {'GPSVersionID':None, 'GPSLatitudeRef':None, 'GPSLatitude':None, 'GPSLongitudeRef':None, 'GPSLongitude':None,
           'GPSAltitudeRef':None, 'GPSAltitude':None}
    for k, v in gps.items():
        value = exif.get(k)
        if value is None:
            value = exif.get(f"GPSInfo.{k}")
        gps[k] = value
    gps["GPSLatitudeRef"] = {"North": "N", "South": "S"}.get(
        gps["GPSLatitudeRef"], gps["GPSLatitudeRef"]
    )
    gps["GPSLongitudeRef"] = {"East": "E", "West": "W"}.get(
        gps["GPSLongitudeRef"], gps["GPSLongitudeRef"]
    )
    return (gps)


def getCamera (exif, xmp):
    camera = {'Model':None, 'Focal Lens':None, 'Focal':None, 'Aperture':None, 'Speed':None, 'ISO':None, 'ExposureCompensation':None}
    try:
        camera['Model'] = exif ['Image.Model']
    except:
        pass
    try:
       camera['Focal Lens'] = exif['Photo.LensModel']
    except:
       pass
    try:
        camera['Focal'] = exif ['Photo.FocalLengthIn35mmFilm']
    except:
        pass
    try:
        camera['Aperture'] =exif ['Photo.FNumber']
    except:
        pass
    try:
        camera['Speed'] = exif['Photo.ShutterSpeedValue']

    except:
        pass
    try:
        camera['ISO'] = exif ['Photo.ISOSpeedRatings']
    except:
        pass
    try:
        camera['ExposureCompensation'] = exif ['Photo.ExposureBiasValue']
    except:
        pass
    return (camera)

def getDate (exif, iptc, xmp):
    Date = None

    try:
        Date = exif ['Photo.DateTimeOriginal']
    except:
        pass
    if Date == None:
        try:
            Date = iptc ['Application2.DateCreated']
        except:
            pass 

    if Date == None:
        try:
            Date = xmp ['xmp.CreateDate']
        except:
            pass 
    return({'Date':Date})

def getLocation (iptc):
    location = {'SubLocation':None, 'Country':None}
    try:
        location['SubLocation'] = iptc ['Application2.SubLocation']
    except:
        pass
    try:
       location['Country'] = iptc ['Application2.CountryName']
    except:
        pass
    return(location)

def getPerso (exif, iptc, xmp):
    perso = {'Author':None, 'Rating':None, 'Keywords':None, 'HKeywords':None}
    try:
        perso['Author'] = exif ['Image.Artist']
    except:
        pass
    try:
        perso['Rating'] = xmp ['Rating']
    except:
        pass
    try:
        perso['Keywords'] = xmp ['dc.subject']
    except:
        pass
    if perso['Keywords'] == None:
        try:
            perso['Keywords'] = xmp ['lr.weightedFlatSubject']
        except:
            pass
    try:
        perso['HKeywords'] = xmp ['lr.hierarchicalSubject']
    except:
        pass       
        
    return(perso)


def getCameraDf (exif, xmp):
    camera = getCamera (exif, xmp)
    return (pd.DataFrame (data=[camera.values()], columns = camera.keys()))

def getInfoDf (exif, iptc, xmp):
    info = getDate(exif, iptc, xmp) | {"GPS":getGPS(exif)} | getLocation (iptc)
    return (pd.DataFrame (data=[info.values()], columns = info.keys()))

def getPersoDf (exif, iptc, xmp):
    perso = getPerso (exif, iptc, xmp)
    return (pd.DataFrame (data=[perso.values()], columns = perso.keys()))