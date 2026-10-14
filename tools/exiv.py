import exiv2

def extractGps (img) :
    img.readMetadata()
    keys = ['Exif.GPSInfo.GPSVersionID','Exif.GPSInfo.GPSLatitudeRef','Exif.GPSInfo.GPSLatitude',
            'Exif.GPSInfo.GPSLongitudeRef','Exif.GPSInfo.GPSLongitude']
    exifGps = {}
    try:
         for key in keys:
            exifGps [key] = f'{img.exifData().findKey(exiv2.ExifKey(key)).value()}'
    except:
         print ('pas d\'information GPS')
         for key in keys:
            exifGps [key] = None  
    return (exifGps)


def modifyIPTC (img, iptcData):
     for k, v in iptcData.items():
        key = exiv2.IptcKey(k)
        try:
             img.iptcData().findKey(key).setValue(v)
        except:
             d = exiv2.Iptcdatum(exiv2.IptcKey(k))
             d.setValue(v)
             img.iptcData().add (d)
