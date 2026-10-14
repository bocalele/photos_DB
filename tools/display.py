from tools.locate import Locate, getLocation
from tools.convert import to_degree
from tools.getMetaData import *
import pandas as pd


def display (exif=None, iptc=None, xmp=None, info=None):
    if info == 'file':
        print("\033[3m \t information from file \033[0m")
    else:
        print("\033[3m \t  information from command line \033[0m")

    if exif is not None:
        print("\033[1m \t EXIF \033[0m")
        for k, v in exif.items():
            print (f'\033[34m \t\t  {k:50}  {v}\033[0m')
        print ()
        
    if iptc is not None:
        print("\033[1m \t IPTC \033[0m")
        for k, v in iptc.items():
            print (f'\033[34m \t\t  {k:50}  {v}\033[0m')

    if xmp is not None:
        print("\033[1m \t XMP \033[0m")
        for k, v in xmp.items():
            print (f'\033[34m \t\t  {k:50}  {v}\033[0m')
    
def summary (exif=None, iptc=None, xmp=None):   

    print("\033[1m \t CAMERA \033[0m")
    for k, v in getCamera(exif, xmp).items():
        print (f'\033[34m \t\t  {k:50}  {v}\033[0m')
    
    print("\033[1m \t INFOS \033[0m")
    for k, v in getDate(exif, iptc, xmp).items():
        print (f'\033[34m \t\t  {k:50}  {v}\033[0m')
    gps = getGPS(exif)
    for k, v in to_degree (gps).items():
        print (f'\033[34m \t\t  {k:50}  {v}\033[0m')
 
    for k, v in getLocation(iptc).items():
        print (f'\033[34m \t\t  {k:50}  {v}\033[0m')

    print("\033[1m \t PERSO \033[0m")
    title = getTitle(exif, xmp, iptc)
    if title != '':
        print (f'\033[34m \t\t  {'Title':50}  {title}\033[0m')
     
    for k, v in getPerso(exif,iptc, xmp).items():
        print (f'\033[34m \t\t  {k:50}  {v}\033[0m')
    try:
        print (f'\033[34m \t\t  {'Subject':50}  {xmp['Subject']}\033[0m')
    except:
        pass
    comment = getComment (exif, xmp, iptc)
    if comment != '':
        print (f'\033[34m \t\t  {'Comment':50}  {comment}\033[0m')

