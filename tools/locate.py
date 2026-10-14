from geopy.geocoders import Nominatim
from geopy.point import Point
import exiv2

class Locate (object):
    def __init__(self, place=None, gps=None) -> None:
        
        self.place = place
        self.gps=None
        if gps is not None:
            self.gps = f'{gps[0]},{gps[1]}'

        self.loc = Nominatim (user_agent="GetLoc", timeout=10)
        self.address, self.point =self.get_address ()

    def get_address (self):
        if self.gps != None:
            locname = self.loc.reverse(self.gps)
            point = Point(self.gps)
            locname = self.loc.reverse(point)
            address = locname.raw['address']
        elif self.place != None:
            getLoc = self.loc.geocode(self.place)
            point = Point ( str(getLoc.latitude)+','+str(getLoc.longitude))
            locname = self.loc.reverse(point)
            address = locname.raw['address']
        else:
            address =  {}
            point = Point('0,0')

        return (address, point)    


    def getExifGPS (self):

        d = self.point.format_unicode().split()
        strLatitude = f'{d[0][:-1]}/1 {d[1][:-1]}/1 {d[2][:-1].split('.')[0]}/1'
        strLongitude = f'{d[4][:-1]}/1 {d[5][:-1]}/1 {d[6][:-1].split('.')[0]}/1'
        return (    
                {'GPSVersionID': '2 2 0 0',
                'GPSLatitudeRef': d[3][0],
                'GPSLatitude': strLatitude,
                'GPSLongitudeRef': d[7][0],
                'GPSLongitude': strLongitude}
        )
    
    def getIPTC (self):
                          
        subLocation= ''
        if 'place' in self.address.keys():
            subLocation = self.address['place']
        elif 'quarter' in self.address.keys():
            subLocation =self.address['quarter']
        elif 'hamlet' in self.address.keys():
            subLocation =self.address['hamlet']
        elif 'suburb' in self.address.keys():
            subLocation =self.address['suburb']
        elif 'road' in self.address.keys():
            subLocation =self.address['road']

        city = ''
        if 'town' in self.address.keys():
            city = self.address['town']
        elif 'city' in self.address.keys():
            city = self.address['city']
        elif 'village' in self.address.keys():
            city = self.address['village']

        provinceState=''
        if 'state' in self.address.keys():
            provinceState = self.address['state']
        elif 'county' in self.address.keys():
            provinceState = self.address['county']

        countryName = self.address['country']


        return (
            {'Iptc.Application2.SubLocation':subLocation,
            'Iptc.Application2.City': city,
            'Iptc.Application2.ProvinceState':provinceState,
            'Iptc.Application2.CountryName':countryName}
        )
    
def getLocation (coordinate=None, place=None):
            
    if coordinate is not None:
        l = Locate (gps=list(coordinate.convert_decimal()))
    elif place is not None:
        l = Locate (place=place)
    else:
        return ({},{})
       
    return (l.getIPTC(), l.getExifGPS())

