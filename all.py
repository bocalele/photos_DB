import pyexiv2
from tools.parse import parse_all, getFiles, getDates, getCoordinate, getAuthor, getTitle
from tools.convert import Coordinate
from tools.locate import Locate, getLocation, extractGps
from tools.display import display


#Traitement des arguments
args = parse_all ()
start, end = getDates (args)
lFiles = getFiles (args, start, end)
coordinate = getCoordinate (args)
iptc_default, exif_default = getLocation (coordinate, args.place)
iptc_auth = getAuthor (args)
iptc_title = getTitle (args)
run = args.run


print (f"{150*'-'}")
if start is not None and end is not None:
    print (f' Creation date between \033[34m  {start.strftime("%d/%m/%Y")} {end.strftime("%d/%m/%Y")} \033[0m ')

for file in lFiles:
    print ()
    print (f"\033[1m File: \033[34m \t {file} \033[0m") 

    img = pyexiv2.Image (file)
    exifGps = extractGps(img.read_exif())

    if exifGps['Exif.Image.GPSTag'] != None:
        c = Coordinate (gps=exifGps)
        l = Locate (gps=list(c.convert_decimal()))
        iptc = {**l.getIPTC(), **iptc_auth}
        exif = l.getExifGPS()
        if len (iptc_title):
            createTitlefromKeys () 
        if run:
            img.modify_exif(l.getExifGPS())
            img.modify_iptc(iptc)
        display (exif, iptc, 'file')  
    elif len (iptc_default) != 0:
        iptc = {**iptc_default, **iptc_auth}
        exif = exif_default
        if run:
            img.modify_exif(exif)
            img.modify_iptc(iptc)  
        display (exif, iptc, 'line') 
    else:
        print ('no position specified')
        exit()        

print (f"{150*'-'}")

