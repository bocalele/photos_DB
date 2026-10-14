from treelib import Tree
from collections import deque
from Levenshtein import distance

class label (object):
    def __init__ (self, french, scientific, english):
        self.french = french
        self.scientific = scientific
        self.english = english

    def display (self):
        print (f'\033[34m {self.french:30} {self.scientific:30} {self.english:30} \033[0m')

def getlabels (lines, iC ):

    try:
        fr_line = lines[iC].split('\t') 
        fr_level = len (fr_line)+1
        fr = fr_line[-1].split('\n')[0]
        iC += 1

    except:
        return (None, None, None, 0, 0)

    try:
        sc_line = lines[iC].split('\t')
        sc_level = len (sc_line)
        if sc_level != fr_level:
            sc = fr
        else:
            label = sc_line[-1]
            try:
                sc = label.split ('}')[0].split('{')[1]
                iC += 1
            except:
                sc = fr
                pass
    except:
        sc = fr
        pass

    try:
        en_line = lines[iC].split('\t')
        en_level = len (en_line)
        if en_level != fr_level:
            en = fr
        else:
            label = en_line[-1]
            try:
                en = label.split ('}')[0].split('{')[1]
                iC += 1
            except:
                en = fr
                pass
    except:
        en = fr
        pass
        
    return (fr, sc, en, iC, fr_level)
        
    
class Thesaurus (object):
    def __init__(self, filename) -> None:
        self.tree = Tree ()
        nodes = deque ()
        self.ind=[]
        try:
            iC = 0
            f = open (filename, "r")
            lines = f.readlines()
            while iC < len (lines):
                (fr, sc, en, iC, level) = getlabels (lines, iC)
                indices = [i for i, item in enumerate(nodes) if item['level'] == level-1] 

                if len (indices) == 0 :
                    self.tree.create_node (tag=fr, identifier=fr, data=[fr, sc, en])
                else:
                    self.tree.create_node (tag=fr, identifier=fr, data=[fr, sc, en], parent=nodes[indices[0]]['identifier'])

                nodes.appendleft ({'identifier':fr, 'level':level})
                self.ind.append (iC)
        except:
            print ('thesaurus not found')

    def getnodes (self, label, option):
        lg=0 #Francais

        def equal (node, label):
            return (node.data[lg].lower() == label.lower())
        
        def content (node, label):
            return (label.lower() in node.data[lg].lower())

        def partial (node, label):
            threshold = len (label) //2
            return (distance (label.lower(), node.data[lg].lower()) <= threshold)
            
        def quasi (node, label):
            return (distance (label.lower(), node.data[lg].lower()) <= 3)

        f = {"equal":equal, "content":content, "partial":partial, "quasi":quasi}
        
        return (self.tree.filter_nodes(lambda node: f[option] (node, label)))

    def getNodesWithSameParent (self, label):
        
        sibling = []
        nodes = self.getnodes (label, "equal")
        for node in nodes:
            id = node.identifier

            parent = self.tree.parent(id)    
            for children in self.tree.children(parent.identifier):

                if children.identifier != id:
                    sibling.append (children)
    
        return (sibling)


    def getNodesWithSameGrandParent (self, label):
        
        cousins = []
        nodes = self.getnodes (label, "equal")
        for node in nodes:
            id = node.identifier

            parent = self.tree.parent(id)    
            gdparent = self.tree.parent(parent.identifier)    
            for cparent in self.tree.children(gdparent.identifier):
                for children in self.tree.children(cparent.identifier):
                    if children.identifier != id:
                        cousins.append (children)
    
        return (cousins)

    def display (self, nodes):

        for node in nodes:
            print (f"{node.data[0]:30}\t{node.data[1]:30}\t{node.data[2]:30}")