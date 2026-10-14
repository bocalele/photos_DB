class Keyword (object):
    def __init__(self, img=None) -> None:
        
        if img is not None:
            self.keywords = self.extractKeywords (img)
        else:
            self.keywords =[]
    
    def extractKeywords (self, img):
        iptc = img.read_iptc()
        if 'Iptc.Application2.Keywords'  in iptc.keys():
            return (iptc ['Iptc.Application2.Keywords'])
        else:
            return ([])
    
    def addKeywords (self, keywords, position):
        if position == 'top':
            self.keywords = keywords + self.keywords
        else:
            self.keywords += keywords
        return ()
    

def readThesaurus (thesaurus):
    f = open (thesaurus, "r")
    