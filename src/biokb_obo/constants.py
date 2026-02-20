import os
from pathlib import Path

# standard for all biokb projects, but individual set
PROJECT_NAME = "obo"
BASIC_NODE_LABEL = "DbOBO"
# standard for all biokb projects
ORGANIZATION = "biokb"
LIBRARY_NAME = f"{ORGANIZATION}_{PROJECT_NAME}"
HOME = str(Path.home())
BIOKB_FOLDER = os.path.join(HOME, f".{ORGANIZATION}")
PROJECT_FOLDER = os.path.join(BIOKB_FOLDER, PROJECT_NAME)
DATA_FOLDER = os.path.join(PROJECT_FOLDER, "data")
EXPORT_FOLDER = os.path.join(DATA_FOLDER, "ttls")
ZIPPED_TTLS_PATH = os.path.join(DATA_FOLDER, "ttls.zip")
SQLITE_PATH = os.path.join(BIOKB_FOLDER, f"{ORGANIZATION}.db")
DB_DEFAULT_CONNECTION_STR = "sqlite:///" + SQLITE_PATH
NEO4J_PASSWORD = "neo4j_password"
NEO4J_URI = "bolt://localhost:7687"
NEO4J_USER = "neo4j"
LOGS_FOLDER = os.path.join(DATA_FOLDER, "logs")  # where to store log files
TABLE_PREFIX = f"{PROJECT_NAME}_"
os.makedirs(DATA_FOLDER, exist_ok=True)

# not standard for all biokb projects

URL_DOWNLOAD_TEMPLATE = "http://purl.obolibrary.org/obo/{}.owl"

# files on ftp server in FTP_DIR
BIOKB_URI = "https://biokb.scai.fraunhofer.de"
BASE_URI = f"{BIOKB_URI}/obo"


database_purls = {
    "DOID": "http://purl.obolibrary.org/obo/DOID_",
    "ICD9": "http://purl.bioontology.org/ontology/ICD9/",
    "MEDGEN": "https://www.ncbi.nlm.nih.gov/medgen/",
    "MESH": "http://purl.bioontology.org/ontology/MESH/",
    "NCIT": "http://purl.obolibrary.org/obo/NCIT_",
    "OGMS": "http://purl.obolibrary.org/obo/OGMS_",
    "Orphanet": "https://www.orpha.net/ORDO/Orphanet_",
    "SCTID": "http://purl.bioontology.org/ontology/SNOMEDCT/",
    "UMLS": "https://identifiers.org/umls:",
    "EFO": "http://www.ebi.ac.uk/efo/EFO_",
    "icd11.foundation": "https://id.who.int/icd/entity/",
    "OMIMPS": "https://omim.org/phenotypicSeries/",
    "GARD": "https://rarediseases.info.nih.gov/diseases/",
    "ICD10CM": "http://purl.bioontology.org/ontology/ICD10CM/",
    "MedDRA": "http://purl.bioontology.org/ontology/MDR/",
    "NANDO": "http://purl.obolibrary.org/obo/NANDO_",
    "HP": "http://purl.obolibrary.org/obo/HP_",
    "OMIM": "https://omim.org/entry/",
    "NORD": "https://rarediseases.org/rare-diseases/",
    "ICD10WHO": "http://purl.bioontology.org/ontology/ICD10/",
    "ICDO": "http://purl.bioontology.org/ontology/ICDO/",
    "ONCOTREE": "http://purl.obolibrary.org/obo/ONCOTREE_",
    "birnlex": "http://purl.obolibrary.org/obo/BIRNLEX_",
    "Wikipedia": "https://en.wikipedia.org/wiki/",
    "PMID": "https://pubmed.ncbi.nlm.nih.gov/",
    "HGNC": "https://identifiers.org/hgnc:",
    "DECIPHER": "https://www.deciphergenomics.org/ddd#",
    "OBI": "http://purl.obolibrary.org/obo/OBI_",
    "IDO": "http://purl.obolibrary.org/obo/IDO_",
    "CSP": "https://identifiers.org/csp:",
    "GTR": "https://www.ncbi.nlm.nih.gov/gtr/tests/",
    "MFOMD": "http://purl.obolibrary.org/obo/MFOMD_",
    "NDFRT": "http://purl.bioontology.org/ontology/NDFRT/",
    "nlxdys": "http://uri.neuinfo.org/nif/nifstd/nlx_dys_",
    "OMIA": "https://omia.org/",
    "ICD9CM": "http://purl.bioontology.org/ontology/ICD9CM/",
    "MTH": "http://purl.bioontology.org/ontology/MTH/",
    "SCDO": "http://purl.obolibrary.org/obo/SCDO_",
    "MPATH": "http://purl.obolibrary.org/obo/MPATH_",
}
