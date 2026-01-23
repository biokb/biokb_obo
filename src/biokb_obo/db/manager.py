"""MySQL database importer module."""

import logging
import os
from collections import defaultdict
from typing import Dict, Optional
from urllib.request import urlretrieve

from owlready2 import get_ontology
from sqlalchemy import Engine, create_engine
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


class DbManager:
    """Database import for DOID data."""

    def __init__(
        self,
        engine: Optional[Engine] = None,
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
        owl_file_path = os.path.join(self.__data_folder, f"{obo_name}.owl")
        if not os.path.exists(owl_file_path) or force_download:
            logger.info(f"Downloading {obo_name} OWL file...")
            urlretrieve(URL_DOWNLOAD_TEMPLATE.format(obo_name), owl_file_path)
            logger.info(f"Downloaded {obo_name} OWL file to {owl_file_path}")
        else:
            logger.info(f"{obo_name} OWL file already exists at {owl_file_path}")
        return owl_file_path

    def import_data(
        self,
        obo_names=[],
        rebuild: bool = False,
        overwrite=False,
        force_download: bool = False,
        keep_files: bool = False,
    ) -> Dict[str, int]:
        returned_data = defaultdict(int)
        if rebuild:
            models.Base.metadata.drop_all(self.__engine)
        models.Base.metadata.create_all(self.__engine)

        with self.session as session:
            for obo_name in obo_names:
                # check if ontology already exists
                existing_ontology: models.Ontology | None = (
                    session.query(models.Ontology)
                    .filter(models.Ontology.id == obo_name)
                    .first()
                )

                if existing_ontology and not overwrite:
                    logger.info(
                        f"Ontology {obo_name} already exists in the database. Skipping import."
                    )
                    continue

                if overwrite and existing_ontology:
                    logger.info(
                        f"Overwriting existing ontology {obo_name} in the database."
                    )

                    session.query(models.Ontology).filter(
                        models.Ontology.id == obo_name
                    ).delete()
                    session.commit()
                logger.info("Parsing ontology: %s", obo_name)
                owl_file_path = self.__download_data(
                    obo_name=obo_name, force_download=force_download
                )
                onto = get_ontology(f"file://{owl_file_path}").load()

                ontology = models.Ontology(
                    id=onto.name,  # Usually 'go'
                    iri=onto.base_iri,
                    # Metadata fields in OBO are often lists, so we take the first element
                    version=(
                        onto.metadata.versionInfo[0]
                        if onto.metadata.versionInfo
                        else None
                    ),
                    description=(
                        "\n".join(onto.metadata.description)
                        if onto.metadata.description
                        else None
                    ),
                    title=onto.metadata.title[0] if onto.metadata.title else None,
                )
                session.add(ontology)
                session.flush()

                # Prepare bulk insert data
                terms_data = []
                synonyms_data = []
                identifiers_data = []
                xrefs_data = []

                # Parse the ontology
                for cls in tqdm(onto.classes(), desc="Processing classes"):
                    if hasattr(cls, "label") and cls.label:
                        term_id = cls.name
                        name = str(cls.label[0]) if cls.label else term_id
                        definition = (
                            str(cls.IAO_0000115[0])
                            if hasattr(cls, "IAO_0000115") and cls.IAO_0000115
                            else None
                        )

                        terms_data.append(
                            {
                                "id": term_id,
                                "name": name,
                                "definition": definition,
                                "ontology_id": ontology.id,
                            }
                        )

                        # Extract synonyms
                        if hasattr(cls, "hasExactSynonym"):
                            for syn in cls.hasExactSynonym:
                                synonyms_data.append(
                                    {
                                        "term_id": term_id,
                                        "synonym": str(syn),
                                        "type": "exact",
                                    }
                                )
                        if hasattr(cls, "hasRelatedSynonym"):
                            for syn in cls.hasRelatedSynonym:
                                synonyms_data.append(
                                    {
                                        "term_id": term_id,
                                        "synonym": str(syn),
                                        "type": "related",
                                    }
                                )
                        if hasattr(cls, "hasNarrowSynonym"):
                            for syn in cls.hasNarrowSynonym:
                                synonyms_data.append(
                                    {
                                        "term_id": term_id,
                                        "synonym": str(syn),
                                        "type": "narrow",
                                    }
                                )
                        if hasattr(cls, "hasBroadSynonym"):
                            for syn in cls.hasBroadSynonym:
                                synonyms_data.append(
                                    {
                                        "term_id": term_id,
                                        "synonym": str(syn),
                                        "type": "broad",
                                    }
                                )

                        # Extract xrefs
                        if hasattr(cls, "hasDbXref"):
                            for xref in cls.hasDbXref:
                                if ":" in xref:
                                    database, ref_id = xref.split(":", 1)
                                    xrefs_data.append(
                                        {
                                            "term_id": term_id,
                                            "database": database,
                                            "reference_id": ref_id,
                                        }
                                    )

                        # Extract alternative identifiers
                        if hasattr(cls, "hasAlternativeId"):
                            for alt_id in cls.hasAlternativeId:
                                identifiers_data.append(
                                    {"term_id": term_id, "identifier": str(alt_id)}
                                )

                logger.info(f"Found {len(terms_data)} terms")
                logger.info(f"Inserting data into database...")

                # Bulk insert for optimized performance
                session.bulk_insert_mappings(models.Term.__mapper__, terms_data)
                session.bulk_insert_mappings(models.Synonym.__mapper__, synonyms_data)
                session.bulk_insert_mappings(
                    models.Identifier.__mapper__, identifiers_data
                )
                session.bulk_insert_mappings(models.XRef.__mapper__, xrefs_data)

                session.commit()
                session.close()

                returned_data["terms"] += len(terms_data)
                returned_data["synonyms"] += len(synonyms_data)
                returned_data["identifiers"] += len(identifiers_data)
                returned_data["xrefs"] += len(xrefs_data)

                if not keep_files:
                    os.remove(owl_file_path)
                    logger.info(f"Removed downloaded file {owl_file_path}")

        return returned_data


def import_data(
    engine: Optional[Engine] = None,
    force_download: bool = False,
    keep_files: bool = False,
) -> Dict[str, int]:
    """Import all data in database.

    Args:
        engine (Optional[Engine]): SQLAlchemy engine. Defaults to None.
        force_download (bool, optional): If True, will force download the data, even if
            files already exist. If False, it will skip the downloading part if files
            already exist locally. Defaults to False.
        keep_files (bool, optional): If True, downloaded files are kept after import.
            Defaults to False.

    Returns:
        Dict[str, int]: table=key and number of inserted=value
    """
    db_manager = DbManager(engine)
    return db_manager.import_data(force_download=force_download, keep_files=keep_files)


def get_session(engine: Optional[Engine] = None) -> Session:
    """Get a new SQLAlchemy session.

    Returns:
        Session: SQLAlchemy session
    """
    db_manager = DbManager(engine)
    return db_manager.session
