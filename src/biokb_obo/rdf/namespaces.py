from rdflib.namespace import Namespace

BASE_URI = "http://purl.obolibrary.org/"


BIOKB_BASE = Namespace("https://biokb.scai.fraunhofer.de/obo/")

REL_NS = Namespace(BIOKB_BASE + "relation#")
NODE_NS = Namespace(BIOKB_BASE + "node#")
NAME_NS = Namespace(f"{BASE_URI}n/")
OBO_NS = Namespace(f"{BASE_URI}obo/")
LOCATION_NS = Namespace(f"{BIOKB_BASE}location/")
HGNC_NS = Namespace("http://identifiers.org/hgnc/")
NCBIGENE_NS = Namespace("http://identifiers.org/ncbigene/")
