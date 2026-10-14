import exiv2
from tools.parse import parse_location, getFiles, getDates, getCoordinate
from tools.convert import Coordinate
from tools.locate import Locate, getLocation
from tools.exiv import extractGps, modifyIPTC


#Traitement des arguments
args = parse_location ()
start, end = getDates (args)
lFiles = getFiles (args, start, end)
coordinate = getCoordinate (args)
iptc_default, exif_default = getLocation (coordinate, args.place)
run = args.run


print (f"{150*'-'}")
# if start is not None and end is not None:
#     print (f' Creation date between \033[34m  {start.strftime("%d/%m/%Y")} {end.strftime("%d/%m/%Y")} \033[0m ')

for file in lFiles:
    print ()
    print (f"\033[1m File: \033[34m \t {file} \033[0m") 

    img = exiv2.ImageFactory ().open (file)
    gpsInfo = extractGps(img)
    if gpsInfo['Exif.GPSInfo.GPSVersionID'] is not None:
        c = Coordinate (gpsInfo, typ='exif')
        l = Locate (gps=list(c.convert_decimal()))
        modifyIPTC(img, l.getIPTC())
    if run:
        img.writeMetadata()


print (f"{150*'-'}")

'''


>>>                                                (reverse-i-search)`img': img=exiv2.ImageFactory ().open ("/Users/pc/PHOTOS/2025/Angleterre/PSD/DSC_8939.psd")
>>> 
>>> img.readMetadata()
>>>                                    (reverse-i-search)`iptc': iptc_data = img.iptcData()
>>> 
>>> for x in iptc_data:
...    print (x.key(), x.value())
... 
Iptc.Envelope.CharacterSet 
Iptc.Application2.RecordVersion 4
Iptc.Application2.DateCreated 2025-07-28
Iptc.Application2.TimeCreated 06:02:24+00:00
>>> key = exiv2.IptcKey("Iptc.Application2.City")
>>> datum = exiv2.Iptcdatum(key)
>>> datum.setValue("Rother")
0
>>> iptc_data.add(datum)
0
>>> for x in iptc_data:
...    print (x.key(), x.value())
... 
Iptc.Envelope.CharacterSet 
Iptc.Application2.RecordVersion 4
Iptc.Application2.DateCreated 2025-07-28
Iptc.Application2.TimeCreated 06:02:24+00:00
Iptc.Application2.City Rother

'''