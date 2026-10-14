import pandas as pd
import exiftool
import json
from collections import defaultdict
from tools.convert import to_degree
from tools.locate import Locate, getLocation
from tools.exiv import extractGps


def get_metadata(filepath):
    with exiftool.ExifTool() as et:
        output = et.execute(b"-j", filepath.encode("utf-8"))
        metadata_list = json.loads(output)
        if metadata_list:
            metadata = metadata_list[0]
            categories = defaultdict(dict)
            for tag, value in metadata.items():
                if tag.startswith("EXIF:"):
                    categories["EXIF"][tag[5:]] = value
                elif tag.startswith("IPTC:"):
                    categories["IPTC"][tag[5:]] = value
                elif tag.startswith("XMP:"):
                    categories["XMP"][tag[4:]] = value
                elif tag.startswith("MakerNotes:"):
                    categories["MakerNotes"][tag[11:]] = value
                elif tag.startswith("Composite:"):
                    categories["Composite"][tag[10:]] = value
                else:
                    categories["OTHER"][tag] = value
    return categories

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
        try:
            gps[k] = exif[k]
        except:
            pass
    return (gps)


def getCamera (exif, xmp):
    camera = {'Model':None, 'Focal Lens':None, 'Focal':None, 'Aperture':None, 'Speed':None, 'ISO':None, 'ExposureCompensation':None}
    try:
        camera['Model'] = exif ['Model']
    except:
        pass
    try:
       camera['Focal Lens'] = exif['LensModel']
    except:
       pass
    try:
        camera['Focal'] = exif ['FocalLengthIn35mmFormat']
    except:
        pass
    try:
        camera['Aperture'] =exif ['FNumber']
    except:
        pass
    try:
        camera['Speed'] = f'{float(exif ['ShutterSpeedValue']):5.3e}'

    except:
        pass
    try:
        camera['ISO'] = exif ['ISO']
    except:
        pass
    try:
        camera['ExposureCompensation'] = exif ['ExposureCompensation']
    except:
        pass
    return (camera)

def getDate (exif, iptc, xmp):
    Date = None

    try:
        Date = exif ['DateTimeOriginal']
    except:
        pass
    if Date == None:
        try:
            Date = iptc ['DateCreated']
        except:
            pass 

    if Date == None:
        try:
            Date = xmp ['CreateDate']
        except:
            pass 
    return({'Date':Date})

def getLocation (iptc):
    location = {'SubLocation':None, 'Country':None}
    try:
        location['SubLocation'] = iptc ['Sub-location']
    except:
        pass
    try:
       location['Country'] = iptc ['Country-PrimaryLocationName']
    except:
        pass
    return(location)

def getPerso (exif, iptc, xmp):
    perso = {'Author':None, 'Rating':None, 'Keywords':None, 'HKeywords':None}
    try:
        perso['Author'] = exif ['Artist']
    except:
        pass
    try:
        perso['Rating'] = xmp ['Rating']
    except:
        pass
    try:
        perso['Keywords'] = iptc ['Keywords']
    except:
        pass
    if perso['Keywords'] == None:
        try:
            perso['Keywords'] = xmp ['WeightedFlatSubject']
        except:
            pass
    try:
        perso['HKeywords'] = xmp ['HierarchicalSubject']
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