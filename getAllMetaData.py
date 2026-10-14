
from tools.parse import parse_location, getFiles, getDates, parse_all
from tools.display import display, summary
from tools.getMetaData import *
import numpy as np
import pandas as pd


#Traitement des arguments
args = parse_all ()
start, end = getDates (args)
lFiles = getFiles (args, start, end)
ldf = []
for file in lFiles:
    print (f"{150*'-'}")
    print ()
    print (f"\033[1m File: \033[34m \t {file} \033[0m") 
    C= get_metadata (file)
    summary (C["EXIF"], C["IPTC"],  C["XMP"])


