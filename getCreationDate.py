import pyexiv2
from datetime import datetime
from tools.parse import parse_files, getFiles, getDates, getSubLocation

args = parse_files ()
lFiles = getFiles (args)
start, end = getDates (args)

print (f"{46*'-'}")
print(f"|\033[1m {'File':30}|{'Created Date'} \033[0m|") 
print (f"{46*'-'}")

for file in lFiles:
    img = pyexiv2.Image (file)
    date = datetime.strptime(img.read_iptc()['Iptc.Application2.DateCreated'], "%Y-%m-%d")
    if date >= start and date <= end:
        print (f"|\033[34m {file:30}\033[0m|\033[34m {date.strftime("%d/%m/%Y")} \033[0m |") 

print (f"{46*'-'}")










