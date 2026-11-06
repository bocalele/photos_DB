import re, copy
from fractions import Fraction

class Coordinate (object):
    def __init__(self,gps, typ) -> None:

        if typ == 'exif':
            formatUnite = {2:'decimal', 6:'DMS'}
            GPSLatitude = re.split(' |/', gps['Exif.GPSInfo.GPSLatitude'])
            self.units = formatUnite[len (GPSLatitude)]
            self.latitudeRef=gps['Exif.GPSInfo.GPSLatitudeRef']
            self.latitude = gps['Exif.GPSInfo.GPSLatitude']
            self.longitudeRef=gps['Exif.GPSInfo.GPSLongitudeRef']
            self.longitude = gps['Exif.GPSInfo.GPSLongitude']
        else:
            print ('Unknown coordinate format')
            exit()   

            
   
    def convert_decimal (self):
        if self.units == 'decimal':
            return (self.latitude, self.longitude)
        elif self.units == 'DMS':
            #Latitude:
            parts = self.latitude.split()
            degrees = float(Fraction(parts[0]))
            minutes = float(Fraction(parts[1]))
            seconds = float(Fraction(parts[2]))
            d_latitude = degrees + minutes / 60 + seconds / 3600
            if self.latitudeRef == 'S':
                d_latitude = - d_latitude 
            parts = self.longitude.split()
            degrees = float(Fraction(parts[0]))
            minutes = float(Fraction(parts[1]))
            seconds = float(Fraction(parts[2]))
            d_longitude = degrees + minutes / 60 + seconds / 3600
            if self.longitudeRef == 'W':
                d_longitude = - d_longitude 
            return (d_latitude, d_longitude)
        else:
            print ('Unknown format')
            exit()

    def convert_dms (self):
        self.dms_latitude = extractAll (self.latitude) 
        self.dms_longitude = extractAll (self.longitude) 
        return (self.dms_latitude,self.dms_longitude)

    
 
def extractDegree (vDecimal):
    return (int(vDecimal))

def extractMinute (vDecimal, degre):
    reste = 1e2 * (vDecimal - degre)
    return (int (reste), int (reste *60/100))

def extractSecond (vDecimal, degre, d_minute):
    reste = 1e4 * (vDecimal - degre - d_minute *1.e-2)
    print (' ==== ', reste)
    return (int (reste), int (reste *60/100))

def extractAll (vDecimal):
    degre = extractDegree(vDecimal)
    print ('Degre -> ', degre)
    d_minute, minute = extractMinute (vDecimal, degre)
    print ('Minute -> ', minute, ' Reste -> ', d_minute)

    d_second, second = extractSecond (vDecimal, degre, d_minute)
    return ([degre, minute, second])   


def decimal_to_dms(coord):
    is_negative = coord < 0
    coord = abs(coord)

    degrees = int(coord)
    minutes_full = (coord - degrees) * 60
    minutes = int(minutes_full)
    seconds = (minutes_full - minutes) * 60

    if is_negative:
        degrees = -degrees

    return degrees, minutes, seconds

def to_degree (gps):
    new_gps = copy.deepcopy(gps)

    if gps['GPSLatitudeRef'] == 'S':
        latitude = - gps['GPSLatitude']
    else:
        latitude =  gps['GPSLatitude']

    if gps['GPSLongitudeRef'] == 'W':
        longitude = - gps['GPSLongitude']
    else:
        longitude =  gps['GPSLongitude']

    if latitude is None or longitude is None:
        return new_gps
    else:    
        lat_deg, lat_min, lat_sec = decimal_to_dms(latitude)
        lon_deg, lon_min, lon_sec = decimal_to_dms(longitude)  
        new_gps['GPSLatitudeRef'] = 'N'
        new_gps['GPSLatitude'] = f"{lat_deg}°{lat_min}'{lat_sec:.2f}"
        new_gps['GPSLongitudeRef'] = 'E'
        new_gps['GPSLongitude'] = f"{lon_deg}°{lon_min}'{lon_sec:.2f}"
    return new_gps
