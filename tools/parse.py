import argparse
import glob
from datetime import datetime
from tools.convert import Coordinate
#import pyexiv2

def parse_files ():
    parser = argparse.ArgumentParser('writeLocation',description='Write Location in Meta-Data Image File')
    parser.add_argument('--file', nargs='*', help='filename or list of filenames or 2 integers given list of files from *_n1_* to *_n2_*')
    parser.add_argument('--date', nargs='*', help='creation date  or 2 dates given list of files from date_1 to date_2')
    parser.add_argument('--run', default=True, action=argparse.BooleanOptionalAction)
    args = parser.parse_args()

    return (args)

def parse_location ():
    parser = argparse.ArgumentParser('writeLocation',description='Write Location in Meta-Data Image File')
    parser.add_argument('--file', nargs='*', help='filename or list of filenames or 2 integers given list of files from *_n1_* to *_n2_*')
    parser.add_argument('--date', nargs='*', help='creation date  or 2 dates given list of files from date_1 to date_2')
    parser.add_argument('--place', help='place to be store in sub-emplacement')
    parser.add_argument('--gps', nargs='*', help='gps coordinate in decimal, MDS or Exif MDS') 
    parser.add_argument('--run', default=True, action=argparse.BooleanOptionalAction)
    args = parser.parse_args()

    return (args)

def parse_all ():
    parser = argparse.ArgumentParser('writeLocation',description='Write Location in Meta-Data Image File')
    parser.add_argument('--file', nargs='*', help='filename or list of filenames or 2 integers given list of files from *_n1_* to *_n2_*')
    parser.add_argument('--date', nargs='*', help='creation date  or 2 dates given list of files from date_1 to date_2')
    parser.add_argument('--place', help='place to be store in sub-emplacement')
    parser.add_argument('--title', nargs='*', help='Title of Image')
    parser.add_argument('--gps', nargs='*', help='gps coordinate in decimal, MDS or Exif MDS') 
    parser.add_argument('--auth', nargs=2, help='Author of Image')
    parser.add_argument('--run', default=True, action=argparse.BooleanOptionalAction)
    args = parser.parse_args()

    return (args)

def getDates (args):
    if args.date is not None:
        start = datetime.strptime(args.date[0], "%d/%m/%Y")
        if len (args.date) == 1:
            end = start
        elif len (args.date) == 2:
            end = datetime.strptime(args.date[1], "%d/%m/%Y")
        else:
            print ('Invalid date arguments')
            exit()
    else:
        start = None
        end = None

    return (start, end)

def getFiles (args, start, end):
    exts = ["png", "gif", "jpeg", "jpg", "tiff", "tif", "psd", "nef"]
    exts+= ["PNG", "GIF", "JPEG", "JPG", "TIFF", "TIF", "PSD", "NEF"]

    lFiles = []
    if args.file is not None:
        if len (args.file)==1 :
            lFiles = args.file
        else:
            if len (args.file) == 2:
                if args.file[0].isdigit() and args.file[1].isdigit():
                    allFiles = sorted(glob.glob ('*'))
                    for i in range (int (args.file[0]),  int (args.file[1])+1):
                        for file in allFiles:
                            if str(i) in file:
                                lFiles.append (file)
                else:
                    lFiles= args.file
            else:
                lFiles= args.file
    else:
        lFiles =sorted(glob.glob ('*'))

    lFiles = [ file for file in lFiles if  any ([ file.endswith(ext) for ext in exts])  == True ]
    
    if start is None or end is None:
        start = datetime.strptime("01/01/1900", "%d/%m/%Y")
        end = datetime.strptime("01/01/2100", "%d/%m/%Y")
    lFilteredFiles = lFiles
    # lFilteredFiles = []
    # for file in lFiles:
    #     img = pyexiv2.Image (file)
    #     date = datetime.strptime(img.read_iptc()['Iptc.Application2.DateCreated'], "%Y-%m-%d")
    #     if date >= start and date <= end:
    #         lFilteredFiles.append (file)

    return (lFilteredFiles)

def getCoordinate (args):
    if args.gps is not None:
        return Coordinate (args.gps)
    else:
        return (None)
    



def getAuthor (args):
    iptc = {}
    if args.auth is not None:
        iptc['Iptc.Application2.Byline'] = args.auth[0]+' '+args.auth[1]
    return (iptc)


def getTitle (args):
    iptc = {}
    if args.title is not None:
        for t in args.title:  
            iptc['Iptc.Application2.ObjectName'] += t+' '
    return (iptc)


