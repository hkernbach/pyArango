import json
from future.utils import with_metaclass

from .theExceptions import (CreationError, DeletionError, UpdateError, TraversalError)
from . import collection as COL
from . import document as DOC

__all__ = ["Graph", "getGraphClass", "isGraph", "getGraphClasses", "Graph_metaclass", "EdgeDefinition"]

class Graph_metaclass(type):
    """Keeps track of all graph classes and does basic validations on fields"""
    graphClasses = {}

    def __new__(cls, name, bases, attrs):
        clsObj = type.__new__(cls, name, bases, attrs)
        if name != 'Graph':
            try:
                if len(attrs['_edgeDefinitions']) < 1:
                    raise CreationError("Graph class '%s' has no edge definition" % name)
            except KeyError:
                raise CreationError("Graph class '%s' has no field _edgeDefinition" % name)

        if name != "Graph":
            Graph_metaclass.graphClasses[name] = clsObj
        return clsObj

    @classmethod
    def getGraphClass(cls, name):
        """return a graph class by its name"""
        try:
            return cls.graphClasses[name]
        except KeyError:
            raise KeyError("There's no child of Graph by the name of: %s" % name)

    @classmethod
    def isGraph(cls, name):
        """returns true/false depending if there is a graph called name"""
        return name in cls.graphClasses

def getGraphClass(name):
    """alias for Graph_metaclass.getGraphClass()"""
    return Graph_metaclass.getGraphClass(name)

def isGraph(name):
    """alias for Graph_metaclass.isGraph()"""
    return Graph_metaclass.isGraph(name)

def getGraphClasses():
    "returns a dictionary of all defined graph classes"
    return Graph_metaclass.graphClasses

class EdgeDefinition(object):
    """An edge definition for a graph"""

    def __init__(self, edgesCollection, fromCollections, toCollections):
        self.name = edgesCollection
        self.edgesCollection = edgesCollection
        self.fromCollections = fromCollections
        self.toCollections = toCollections

    def toJson(self):
        return { 'collection' : self.edgesCollection, 'from' : self.fromCollections, 'to' : self.toCollections }

    def __str__(self):
        return '<ArangoED>'+ str(self.toJson())

    def __repr__(self):
        return str(self)

class Graph(with_metaclass(Graph_metaclass, object)):
    """The class from witch all your graph types must derive"""

    _edgeDefinitions = []
    _orphanedCollections = []

    def __init__(self, database, jsonInit):
        self.database = database
        self.connection = self.database.connection
        try:
            self._key = jsonInit["_key"]
        except KeyError:
            self._key = jsonInit["name"]
        except KeyError:
            raise KeyError("'jsonInit' must have a field '_key' or a field 'name'")

        self.name = self._key
        self._rev = jsonInit["_rev"]
        self._id = jsonInit["_id"]

        orfs = set(self._orphanedCollections)
        for o in jsonInit["orphanCollections"]:
            if o not in orfs:
                self._orphanedCollections.append(o)
                if self.connection.verbose:
                    print("Orphan collection %s is not in graph definition. Added it" % o)

        self.definitions = {}
        edNames = set()
        for ed in self._edgeDefinitions:
            self.definitions[ed.edgesCollection] = ed.edgesCollection

        for ed in jsonInit["edgeDefinitions"]:
            if ed["collection"] not in self.definitions:
                self.definitions[ed["collection"]] = EdgeDefinition(ed["collection"], fromCollections = ed["from"], toCollections = ed["to"])
                if self.connection.verbose:
                    print("Edge definition %s is not in graph definition. Added it" % ed)

        for de in self._edgeDefinitions:
            if de.edgesCollection not in self.database.collections and not COL.isEdgeCollection(de.edgesCollection):
                raise KeyError("'%s' is not a valid edge collection" % de.edgesCollection)
            self.definitions[de.edgesCollection] = de

    def getURL(self):
        return "%s/%s" % (self.database.getGraphsURL(), self._key)

    def createVertex(self, collectionName, docAttributes, waitForSync = False):
        """adds a vertex to the graph and returns it"""
        url = "%s/vertex/%s" % (self.getURL(), collectionName)

        store = DOC.DocumentStore(self.database[collectionName], validators=self.database[collectionName]._fields, initDct=docAttributes)
        # self.database[collectionName].validateDct(docAttributes)
        store.validate()

        r = self.connection.session.post(url, data = json.dumps(docAttributes, default=str), params = {'waitForSync' : waitForSync})

        data = r.json()
        if r.status_code == 201 or r.status_code == 202:
            return self.database[collectionName][data["vertex"]["_key"]]

        raise CreationError("Unable to create vertice, %s" % data["errorMessage"], data)

    def deleteVertex(self, document, waitForSync = False):
        """deletes a vertex from the graph as well as al linked edges"""
        url = "%s/vertex/%s" % (self.getURL(), document._id)

        r = self.connection.session.delete(url, params = {'waitForSync' : waitForSync})
        data = r.json()
        if r.status_code == 200 or r.status_code == 202:
            return True

        raise DeletionError("Unable to delete vertice, %s" % document._id, data)

    def createEdge(self, collectionName, _fromId, _toId, edgeAttributes, waitForSync = False):
        """creates an edge between two documents"""

        if not _fromId:
            raise ValueError("Invalid _fromId: %s" % _fromId)

        if not _toId:
            raise ValueError("Invalid _toId: %s" % _toId)

        if collectionName not in self.definitions:
            raise KeyError("'%s' is not among the edge definitions" % collectionName)

        url = "%s/edge/%s" % (self.getURL(), collectionName)
        self.database[collectionName].validatePrivate("_from", _fromId)
        self.database[collectionName].validatePrivate("_to", _toId)
        
        ed = self.database[collectionName].createEdge()
        ed.set(edgeAttributes)
        ed.validate()

        payload = ed.getStore()
        payload.update({'_from' : _fromId, '_to' : _toId})

        r = self.connection.session.post(url, data = json.dumps(payload, default=str), params = {'waitForSync' : waitForSync})
        data = r.json()
        if r.status_code == 201 or r.status_code == 202:
            return self.database[collectionName][data["edge"]["_key"]]
        # print "\ngraph 160, ", data, payload, _fromId
        raise CreationError("Unable to create edge, %s" % r.json()["errorMessage"], data)

    def link(self, definition, doc1, doc2, edgeAttributes, waitForSync = False):
        "A shorthand for createEdge that takes two documents as input"
        if type(doc1) is DOC.Document:
            if not doc1._id:
                doc1.save()
            doc1_id = doc1._id
        else:
            doc1_id = doc1

        if type(doc2) is DOC.Document:
            if not doc2._id:
                doc2.save()
            doc2_id = doc2._id
        else:
            doc2_id = doc2

        return self.createEdge(definition, doc1_id, doc2_id, edgeAttributes, waitForSync)

    def unlink(self, definition, doc1, doc2):
        "deletes all links between doc1 and doc2"
        links = self.database[definition].fetchByExample( {"_from": doc1._id,"_to" : doc2._id}, batchSize = 100)
        for l in links:
            self.deleteEdge(l)

    def deleteEdge(self, edge, waitForSync = False):
        """removes an edge from the graph"""
        url = "%s/edge/%s" % (self.getURL(), edge._id)
        r = self.connection.session.delete(url, params = {'waitForSync' : waitForSync})
        if r.status_code == 200 or r.status_code == 202:
            return True
        raise DeletionError("Unable to delete edge, %s" % edge._id, r.json())

    def delete(self):
        """deletes the graph"""
        r = self.connection.session.delete(self.getURL())
        data = r.json()
        if r.status_code < 200 or r.status_code > 202 or data["error"]:
            raise DeletionError(data["errorMessage"], data)

    def traverse(self, startVertex, **kwargs):
        """Traversal! see: https://docs.arangodb.com/HttpTraversal/README.html for a full list of the possible kwargs.
        The function must have as argument either: direction = "outbout"/"any"/"inbound" or expander = "custom JS (see arangodb's doc)".
        The function can't have both 'direction' and 'expander' as arguments.
        """

        url = "%s/traversal" % self.database.getURL()
        if type(startVertex) is DOC.Document:
            startVertex_id = startVertex._id
        else:
            startVertex_id = startVertex

        payload = {"startVertex": startVertex_id, "graphName" : self.name}
        if "expander" in kwargs:
            if "direction" in kwargs:
                    raise ValueError("""The function can't have both 'direction' and 'expander' as arguments""") 
        elif "direction" not in kwargs:
            raise ValueError("""The function must have as argument either: direction = "outbout"/"any"/"inbound" or expander = "custom JS (see arangodb's doc)" """) 

        payload.update(kwargs)

        r = self.connection.session.post(url, data = json.dumps(payload, default=str))
        data = r.json()
        if r.status_code < 200 or r.status_code > 202 or data["error"]:
            raise TraversalError(data["errorMessage"], data)

        return data["result"]

    def __str__(self):
        return "ArangoGraph: %s" % self.name
