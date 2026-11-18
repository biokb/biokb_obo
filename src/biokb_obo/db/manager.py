"""MySQL database importer module."""

import logging
import os
from collections import defaultdict
from collections.abc import Iterable
from pathlib import Path

import owlready2.namespace
import requests
from owlready2 import ThingClass, get_ontology
from owlready2.class_construct import Restriction
from sqlalchemy import Delete, Engine, Row, Select, create_engine, delete, or_, select
from sqlalchemy.orm import sessionmaker
from sqlalchemy.orm.session import Session
from tqdm import tqdm

from biokb_obo.constants import (
    DATA_FOLDER,
    DB_DEFAULT_CONNECTION_STR,
    URL_DOWNLOAD_TEMPLATE,
)
from biokb_obo.db import models

logging.basicConfig(
    format="%(asctime)s - %(name)s - %(levelname)s - %(message)s", level=logging.INFO
)
logger: logging.Logger = logging.getLogger(__name__)
RO_RELATION_IRI_PREFIX = "http://purl.obolibrary.org/obo/RO_"
OBO_PURL_BASE = "http://purl.obolibrary.org/obo/"
# Xref prefixes that are OBO Foundry ontologies: PURL is OBO_PURL_BASE + PREFIX_ID
OBO_PURL_PREFIXES = {
    "BTO",
    "CHEBI",
    "CL",
    "DOID",
    "EMAPA",
    "GO",
    "HP",
    "MA",
    "MONDO",
    "NCIT",
    "OBI",
    "PATO",
    "PO",
    "RO",
    "SYMP",
    "UBERON",
    "OBA",
}
# Xref prefixes of other databases with a stable URI pattern ({} = identifier)
XREF_URI_TEMPLATES = {
    "PMID": "https://pubmed.ncbi.nlm.nih.gov/{}",
    "MESH": "http://purl.bioontology.org/ontology/MESH/{}",
    "ICD10CM": "http://purl.bioontology.org/ontology/ICD10CM/{}",
    "ICD9CM": "http://purl.bioontology.org/ontology/ICD9CM/{}",
    "OMIM": "https://omim.org/entry/{}",
    "MIM": "https://omim.org/entry/{}",
    "ORDO": "http://www.orpha.net/ORDO/Orphanet_{}",
}
SYNONYM_ATTRIBUTES = {
    "exact": "hasExactSynonym",
    "related": "hasRelatedSynonym",
    "narrow": "hasNarrowSynonym",
    "broad": "hasBroadSynonym",
}


def _ontology_name(obo_name: str) -> str:
    """Return the name under which an OBO ontology is stored in the database.

    owlready2 names an ontology after its local file, which is the OBO name with
    ``/`` replaced by ``_`` (``go/extensions/go-plus`` -> ``go_extensions_go-plus``).
    """
    return obo_name.replace("/", "_")


def _create_ontology(
    session: Session, onto: owlready2.namespace.Ontology
) -> models.Ontology:
    """Create the ontology record from the metadata of a loaded OWL file.

    Args:
        session (Session): Active SQLAlchemy session.
        onto (owlready2.namespace.Ontology): Loaded ontology.

    Returns:
        models.Ontology: The flushed ontology record (its ``id`` is set).
    """
    metadata = onto.metadata
    ontology = models.Ontology(
        name=onto.name,
        iri=onto.base_iri,
        version=metadata.versionInfo[0] if metadata.versionInfo else None,
        description="\n".join(metadata.description) if metadata.description else None,
        title=metadata.title[0] if metadata.title else None,
    )
    session.add(ontology)
    session.flush()
    return ontology


def _extract_hierarchy(
    owl_class: ThingClass,
    ro_relation_type_ids: dict[str, int],
    parent_child_data: list[dict],
    relations_data: list[dict],
) -> None:
    """Collect is-a parents and RO restrictions of an OWL class.

    Plain named superclasses are appended to ``parent_child_data``;
    existential restrictions on a known RO property are appended to
    ``relations_data``. Both use IRIs, which are resolved to ids later.
    """
    for parent in owl_class.is_a:
        if isinstance(parent, ThingClass):
            parent_child_data.append(
                {"parent_id": parent.iri, "child_id": owl_class.iri}
            )
        elif (
            isinstance(parent, Restriction)
            and parent.property.iri in ro_relation_type_ids
            and isinstance(parent.value, ThingClass)
        ):
            relations_data.append(
                {
                    "subject_iri": owl_class.iri,
                    "object_iri": parent.value.iri,
                    "relation_type_iri": parent.property.iri,
                }
            )


def _extract_synonyms(owl_class: ThingClass, term_id: int) -> list[dict]:
    """Return synonym rows (exact, related, narrow, broad) for an OWL class."""
    synonyms = []
    for synonym_type, attribute in SYNONYM_ATTRIBUTES.items():
        for synonym in getattr(owl_class, attribute, None) or []:
            synonyms.append(
                {"term_id": term_id, "synonym": str(synonym), "type": synonym_type}
            )
    return synonyms


def _build_xref_uri(database: str, reference_id: str) -> str | None:
    """Build a resolvable URI (PURL where one is known) for an xref.

    Args:
        database (str): Database prefix, e.g. ``CHEBI`` or ``MESH``.
        reference_id (str): Identifier inside that database.

    Returns:
        str | None: The URI, or None if no URI pattern is known for the database.
    """
    prefix = database.upper()
    if prefix in XREF_URI_TEMPLATES:
        return XREF_URI_TEMPLATES[prefix].format(reference_id)
    if prefix in OBO_PURL_PREFIXES:
        return f"{OBO_PURL_BASE}{prefix}_{reference_id}"
    return None


def _extract_xrefs(owl_class: ThingClass, term_id: int) -> list[dict]:
    """Return xref rows for an OWL class; values without ``DB:ID`` form are skipped.

    Xrefs that already are http(s) URLs are stored as database ``URL`` with the
    URL as reference id and URI. For ``DB:ID`` xrefs the ``uri`` is filled when
    the database is known (see ``_build_xref_uri``), otherwise it is None.
    """
    xrefs: list[dict] = []
    for xref in getattr(owl_class, "hasDbXref", None) or []:
        xref = str(xref)
        if xref.startswith(("http://", "https://")):
            database, reference_id, uri = "URL", xref, xref
        elif ":" in xref:
            database, reference_id = xref.split(sep=":", maxsplit=1)
            uri = _build_xref_uri(database, reference_id)
        else:
            continue
        xrefs.append(
            {
                "term_id": term_id,
                "database": database,
                "reference_id": reference_id,
                "uri": uri,
            }
        )
    return xrefs


def _extract_alternative_ids(owl_class: ThingClass, term_id: int) -> list[dict]:
    """Return alternative identifier rows for an OWL class."""
    return [
        {"term_id": term_id, "identifier": str(alt_id)}
        for alt_id in getattr(owl_class, "hasAlternativeId", None) or []
    ]


def _resolve_parent_child(
    parent_child_data: list[dict], term_iri_id_dict: dict[str, int]
) -> list[dict]:
    """Convert IRI based parent-child pairs to id pairs.

    Pairs referencing a term that was not imported are dropped to avoid
    foreign key violations.
    """
    resolved = [
        {
            "parent_id": term_iri_id_dict[pc["parent_id"]],
            "child_id": term_iri_id_dict[pc["child_id"]],
        }
        for pc in parent_child_data
        if pc["parent_id"] in term_iri_id_dict and pc["child_id"] in term_iri_id_dict
    ]
    logger.info(
        f"Filtered parent-child relationships from {len(parent_child_data)} "
        f"to {len(resolved)} (only including terms in this import)"
    )
    return resolved


def _resolve_relations(
    session: Session,
    relations_data: list[dict],
    ro_relation_type_ids: dict[str, int],
) -> list[tuple[int, int, int]]:
    """Convert IRI based RO relations to unique (subject, object, type) id tuples.

    Term ids are looked up in the database, so endpoints imported earlier
    from other ontologies are found as well. Relations with a missing
    endpoint are skipped with a warning.
    """
    relation_term_iris = {
        iri
        for relation in relations_data
        for iri in (relation["subject_iri"], relation["object_iri"])
    }
    term_ids = {
        iri: term_id
        for iri, term_id in session.query(models.Term.iri, models.Term.id)
        .filter(models.Term.iri.in_(relation_term_iris))
        .all()
    }
    resolved = list(
        dict.fromkeys(
            (
                term_ids[relation["subject_iri"]],
                term_ids[relation["object_iri"]],
                ro_relation_type_ids[relation["relation_type_iri"]],
            )
            for relation in relations_data
            if relation["subject_iri"] in term_ids
            and relation["object_iri"] in term_ids
        )
    )
    if len(resolved) < len(relations_data):
        logger.warning(
            "Skipped %s RO relations because one or both endpoint terms "
            "were not imported",
            len(relations_data) - len(resolved),
        )
    return resolved


class DbManager:
    """Database import for DOID data."""

    def __init__(
        self,
        engine: Engine | None = None,
    ):
        """Init DatabaseImporter

        Args:
            data_folder_path (Optional[str]): Folder downloadfiles. Defaults to None.
            engine (Optional[Engine]): SQLAlchemy engine. Defaults to None.
            redownload (bool): True if the data should be downloaded
                               even if they already exists. Default False.
        """
        self.__data_folder: str = DATA_FOLDER
        connection_str: str = os.getenv("CONNECTION_STR", DB_DEFAULT_CONNECTION_STR)
        self.__engine: Engine = engine if engine else create_engine(str(connection_str))
        self.Session: sessionmaker[Session] = sessionmaker(bind=self.__engine)
        logger.info(f"Using database connection: {self.__engine.url}")

    @property
    def session(self) -> Session:
        """Get a new SQLAlchemy session.

        Returns:
            Session: SQLAlchemy session
        """
        return self.Session()

    def _set_data_folder(self, data_folder: str) -> None:
        """Sets the data folder path.

        This is mainly for testing purposes.
        """
        self.__data_folder = data_folder

    def __download_data(self, obo_name: str, force_download: bool = False) -> str:
        # go/extensions/go-plus
        owl_file_path = os.path.join(
            self.__data_folder, f"{obo_name.replace('/', '_')}.owl"
        )
        if not os.path.exists(owl_file_path) or force_download:
            logger.info(f"Downloading {obo_name} OWL file...")
            url = URL_DOWNLOAD_TEMPLATE.format(obo_name)
            # Download to a temporary file so an interrupted download never
            # leaves a truncated file that later runs would treat as complete.
            partial_path = Path(f"{owl_file_path}.part")
            try:
                with requests.get(url, stream=True, timeout=120) as response:
                    response.raise_for_status()
                    # Content-Length is only comparable for uncompressed bodies.
                    expected_size = (
                        0
                        if response.headers.get("Content-Encoding")
                        else int(response.headers.get("Content-Length", 0))
                    )
                    with partial_path.open("wb") as f:
                        for chunk in response.iter_content(chunk_size=1024 * 1024):
                            if chunk:
                                f.write(chunk)
                actual_size = partial_path.stat().st_size
                if expected_size and actual_size != expected_size:
                    raise OSError(
                        f"Incomplete download of {obo_name}: "
                        f"got {actual_size} of {expected_size} bytes"
                    )
                partial_path.replace(target=owl_file_path)
            except Exception:
                logger.exception("Download of %s from %s failed", obo_name, url)
                partial_path.unlink(missing_ok=True)
                raise
            logger.info(f"Downloaded {obo_name} OWL file to {owl_file_path}")
        else:
            logger.info(f"{obo_name} OWL file already exists at {owl_file_path}")
        return owl_file_path

    def __load_ro_relation_types(
        self, session: Session, force_download: bool = False
    ) -> tuple[dict[str, int], int]:
        existing_relation_types: list[models.RelationType] = [
            relation_type
            for relation_type in session.query(models.RelationType).all()
            if relation_type.iri.startswith(RO_RELATION_IRI_PREFIX)
        ]
        if existing_relation_types:
            return (
                {
                    relation_type.iri: relation_type.id
                    for relation_type in existing_relation_types
                },
                0,
            )

        ro_file_path: str = self.__download_data(
            obo_name="ro", force_download=force_download
        )
        ro_ontology = get_ontology(base_iri=f"file://{ro_file_path}").load()
        relation_types: list[models.RelationType] = [
            models.RelationType(
                iri=prop.iri,
                name=prop.label[0] if prop.label else prop.iri.rsplit("/", 1)[-1],
            )
            for prop in ro_ontology.object_properties()
            if prop.iri.startswith(RO_RELATION_IRI_PREFIX)
        ]
        session.add_all(instances=relation_types)
        session.flush()
        return (
            {relation_type.iri: relation_type.id for relation_type in relation_types},
            len(relation_types),
        )

    def __delete_ontology(self, session: Session, ontology: models.Ontology) -> None:
        """Delete an ontology and all data that depends on it, then commit.

        Dependent rows (parent-child links, relations, synonyms, identifiers,
        xrefs and terms) are removed before the ontology itself so that no
        foreign key constraint is violated.

        Args:
            session (Session): Active SQLAlchemy session.
            ontology (models.Ontology): The ontology to remove.
        """
        term_ids: Select[tuple[int]] = select(models.Term.id).where(
            models.Term.ontology_id == ontology.id
        )
        statements: list[Delete] = [
            delete(models.ParentChild).where(
                or_(
                    models.ParentChild.parent_id.in_(other=term_ids),
                    models.ParentChild.child_id.in_(other=term_ids),
                )
            ),
            delete(models.Relation).where(
                or_(
                    models.Relation.subject_id.in_(term_ids),
                    models.Relation.object_id.in_(other=term_ids),
                )
            ),
            delete(table=models.Synonym).where(
                models.Synonym.term_id.in_(other=term_ids)
            ),
            delete(table=models.Identifier).where(
                models.Identifier.term_id.in_(other=term_ids)
            ),
            delete(table=models.XRef).where(models.XRef.term_id.in_(other=term_ids)),
            delete(table=models.Term).where(models.Term.ontology_id == ontology.id),
            delete(table=models.Ontology).where(models.Ontology.id == ontology.id),
        ]
        for statement in statements:
            session.execute(statement)
        session.commit()

    def __parse_terms(
        self,
        session: Session,
        onto: owlready2.namespace.Ontology,
        ontology: models.Ontology,
        ro_relation_type_ids: dict[str, int],
        term_iri_id_dict: dict[str, int],
    ) -> dict:
        """Insert terms of an ontology and collect all dependent data.

        Only classes with a label become terms. Terms are flushed one by one to
        obtain their ids; everything else is collected in memory for a later
        bulk insert.

        Args:
            session (Session): Active SQLAlchemy session.
            onto (owlready2.namespace.Ontology): Loaded ontology.
            ontology (models.Ontology): Ontology record the terms belong to.
            ro_relation_type_ids (dict[str, int]): RO property IRI -> relation type id.
            term_iri_id_dict (dict[str, int]): IRI -> term id mapping, updated in place.

        Returns:
            dict: Lists ``parent_child``, ``relations``, ``synonyms``,
                ``identifiers`` and ``xrefs`` plus the ``term_count``.
        """
        parsed: dict = {
            "parent_child": [],
            "relations": [],
            "synonyms": [],
            "identifiers": [],
            "xrefs": [],
            "term_count": 0,
        }
        for owl_class in tqdm(
            onto.classes(), desc=f"Processing classes for {onto.name}"
        ):
            if not (hasattr(owl_class, "label") and owl_class.label):
                continue
            definition = (
                str(owl_class.IAO_0000115[0])
                if hasattr(owl_class, "IAO_0000115") and owl_class.IAO_0000115
                else None
            )
            term = models.Term(
                iri=owl_class.iri,
                name=owl_class.label[0],
                definition=definition,
                ontology_id=ontology.id,
            )
            session.add(term)
            session.flush()
            term_iri_id_dict[owl_class.iri] = term.id
            parsed["term_count"] += 1

            _extract_hierarchy(
                owl_class,
                ro_relation_type_ids,
                parsed["parent_child"],
                parsed["relations"],
            )
            parsed["synonyms"] += _extract_synonyms(owl_class, term.id)
            parsed["xrefs"] += _extract_xrefs(owl_class, term.id)
            parsed["identifiers"] += _extract_alternative_ids(owl_class, term.id)
        return parsed

    def __import_ontology(
        self,
        session: Session,
        obo_name: str,
        ro_relation_type_ids: dict[str, int],
        term_iri_id_dict: dict[str, int],
        force_download: bool,
        delete_files: bool,
    ) -> dict[str, int]:
        """Download, parse and store a single ontology.

        Args:
            session (Session): Active SQLAlchemy session.
            obo_name (str): Name of the OBO ontology.
            ro_relation_type_ids (dict[str, int]): RO property IRI -> relation type id.
            term_iri_id_dict (dict[str, int]): IRI -> term id mapping, updated in place.
            force_download (bool): Download even if the file already exists.
            delete_files (bool): Delete the OWL file after the import.

        Returns:
            dict[str, int]: Number of inserted ``terms``, ``relations``,
                ``synonyms``, ``identifiers`` and ``xrefs``.
        """
        logger.info("Parsing ontology: %s", obo_name)
        owl_file_path = self.__download_data(
            obo_name=obo_name, force_download=force_download
        )
        onto = get_ontology(f"file://{owl_file_path}").load()
        ontology = _create_ontology(session, onto)

        parsed = self.__parse_terms(
            session, onto, ontology, ro_relation_type_ids, term_iri_id_dict
        )
        logger.info(f"Found {parsed['term_count']} terms")
        logger.info("Inserting data into database...")

        parent_child = _resolve_parent_child(parsed["parent_child"], term_iri_id_dict)
        relations = _resolve_relations(
            session, parsed["relations"], ro_relation_type_ids
        )

        # Bulk insert for optimized performance
        session.bulk_insert_mappings(models.ParentChild.__mapper__, parent_child)
        session.bulk_insert_mappings(
            models.Relation.__mapper__,
            [
                {
                    "subject_id": subject_id,
                    "object_id": object_id,
                    "relation_type_id": relation_type_id,
                }
                for subject_id, object_id, relation_type_id in relations
            ],
        )
        session.bulk_insert_mappings(models.Synonym.__mapper__, parsed["synonyms"])
        session.bulk_insert_mappings(
            models.Identifier.__mapper__, parsed["identifiers"]
        )
        session.bulk_insert_mappings(models.XRef.__mapper__, parsed["xrefs"])
        session.commit()
        session.close()

        if delete_files:
            os.remove(owl_file_path)
            logger.info(f"Removed downloaded file {owl_file_path}")

        return {
            "terms": parsed["term_count"],
            "relations": len(relations),
            "synonyms": len(parsed["synonyms"]),
            "identifiers": len(parsed["identifiers"]),
            "xrefs": len(parsed["xrefs"]),
        }

    def __decide_if_ontology_import(
        self, session: Session, obo_name: str, overwrite: bool
    ) -> bool:
        """Decide whether an ontology should be imported.

        An already existing ontology is skipped unless ``overwrite`` is set, in
        which case it is deleted first.

        Returns:
            bool: True if the ontology should be imported, False to skip it.
        """
        existing: models.Ontology | None = (
            session.query(models.Ontology)
            .filter(models.Ontology.name == _ontology_name(obo_name))
            .first()
        )
        if not existing:
            return True
        if not overwrite:
            logger.info(
                f"Ontology {obo_name} already exists in the database. Skipping import."
            )
            return False
        logger.info(f"Overwriting existing ontology {obo_name} in the database.")
        self.__delete_ontology(session, existing)
        return True

    def import_data(
        self,
        obo_names: Iterable[str] = (),
        rebuild: bool = False,
        overwrite=False,
        force_download: bool = False,
        delete_files: bool = False,
    ) -> dict[str, int]:
        """Import OBO ontologies into the database.

        Workflow:
            1. Optionally drop all tables (``rebuild``) and create missing ones.
            2. Load the RO relation types (from the database or the RO OWL file).
            3. For each ontology: skip or delete an existing one, download and
               parse the OWL file, then bulk insert terms, hierarchy, relations,
               synonyms, identifiers and xrefs.

        Args:
            obo_names (Iterable[str]): OBO ontology names to import. If empty,
                nothing is imported.
            rebuild (bool): If True, drop and recreate all tables before import.
            overwrite (bool): If True, replace ontologies that already exist in
                the database instead of skipping them.
            force_download (bool): If True, download the data even if files already
                exist locally. Defaults to False.
            delete_files (bool): If True, downloaded files are deleted after import.
                Defaults to False.

        Returns:
            dict[str, int]: Number of inserted records per table (``relation_types``,
                ``terms``, ``relations``, ``synonyms``, ``identifiers``, ``xrefs``).
        """
        returned_data: dict[str, int] = defaultdict(int)

        if rebuild:
            models.Base.metadata.drop_all(bind=self.__engine)
            logger.info(msg="Rebuild obo tables")
        models.Base.metadata.create_all(bind=self.__engine)

        term_iri_id_dict: dict[str, int] = {}  # iri: id

        with self.session as session:
            ro_relation_type_ids: dict[str, int] = {}
            if obo_names:
                ro_relation_type_ids, inserted = self.__load_ro_relation_types(
                    session, force_download=force_download
                )
                returned_data["relation_types"] += inserted
                if inserted:
                    session.commit()

            for obo_name in obo_names:
                if not self.__decide_if_ontology_import(session, obo_name, overwrite):
                    continue
                counts: dict[str, int] = self.__import_ontology(
                    session,
                    obo_name,
                    ro_relation_type_ids,
                    term_iri_id_dict,
                    force_download,
                    delete_files,
                )
                for key, value in counts.items():
                    returned_data[key] += value

        return returned_data

    def get_id_by_term(self, term: str) -> int | None:
        """Get the ID of a term by its name.

        Args:
            term (str): The name of the term.

        Returns:
            Optional[int]: The ID of the term if found, else None.
        """
        with self.session as session:
            result: Row[tuple[int]] | None = (
                session.query(_colexpr=models.Term.id)
                .filter(models.Term.name == term)
                .first()
            )
            return result[0] if result else None


def import_data(
    engine: Engine | None = None,
    force_download: bool = False,
    delete_files: bool = False,
) -> dict[str, int]:
    """Import all data in database.

    Args:
        engine (Optional[Engine]): SQLAlchemy engine. Defaults to None.
        force_download (bool, optional): If True, will force download the data, even if
            files already exist. If False, it will skip the downloading part if files
            already exist locally. Defaults to False.
        delete_files (bool, optional): If True, downloaded files are deleted after import.
            Defaults to False.

    Returns:
        Dict[str, int]: table=key and number of inserted=value
    """
    db_manager = DbManager(engine)
    return db_manager.import_data(
        force_download=force_download, delete_files=delete_files
    )


def get_session(engine: Engine | None = None) -> Session:
    """Get a new SQLAlchemy session.

    Returns:
        Session: SQLAlchemy session
    """
    db_manager = DbManager(engine)
    return db_manager.session
