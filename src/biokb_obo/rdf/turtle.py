import logging
import os.path
import re
import shutil
from typing import Optional, TypeVar

from rdflib import RDF, XSD, Graph, Literal, Namespace, URIRef
from sqlalchemy import Engine, create_engine
from sqlalchemy.orm import sessionmaker
from tqdm import tqdm

from biokb_obo import constants
from biokb_obo.constants import EXPORT_FOLDER
from biokb_obo.db import models
from biokb_obo.rdf import namespaces

logger = logging.getLogger(__name__)
logger.addHandler(logging.NullHandler())


# Type variable for SQLAlchemy model classes
BaseModels = TypeVar("BaseModels", bound=models.Base)


def get_namespace(model_name: str) -> Namespace:
    """Generate an RDF namespace for a given SQLAlchemy model class.

    Args:
        model: SQLAlchemy model class to generate namespace for.

    Returns:
        RDF Namespace object with URI based on the model's class name.
    """
    return Namespace(f"{namespaces.BASE_URI}/{model_name}#")


def get_empty_graph() -> Graph:
    graph = Graph()
    # Bind generic ontology namespaces
    graph.bind(prefix="rel", namespace=namespaces.REL_NS)
    graph.bind(prefix="xs", namespace=XSD)
    graph.bind(prefix="n", namespace=namespaces.NODE_NS)
    graph.bind(prefix="a", namespace=namespaces.NAME_NS)
    graph.bind(prefix="obo", namespace=namespaces.OBO_NS)
    graph.bind(prefix="hgnc", namespace=namespaces.HGNC_NS)
    graph.bind(prefix="ncbi", namespace=namespaces.NCBIGENE_NS)

    return graph


def get_uri_ref(iri: str) -> URIRef:
    """Generate a URIRef for a given IRI string."""
    return URIRef(iri)


def get_resource_label(iri: str) -> str:
    """Return the node label of the resource a term belongs to.

    The resource is taken from the term's own IRI, not from the ontology file it
    was imported with, so a CHEBI class inside GO-plus is labeled ``DbChebi``.

    Examples:
        ``http://purl.obolibrary.org/obo/GO_0008150`` -> ``DbGo``
        ``http://purl.obolibrary.org/obo/CHEBI_15377`` -> ``DbChebi``
        ``http://identifiers.org/hgnc/5`` -> ``DbHgnc``
    """
    obo_base = str(namespaces.OBO_NS)
    if iri.startswith(obo_base):
        resource = re.split(r"[_#]", iri[len(obo_base) :])[0]
    else:
        resource = iri.rstrip("/").rsplit("/", 2)[-2]
    return "Db" + re.sub(r"[^A-Za-z0-9]", "", resource).capitalize()


class TurtleCreator:
    """Factory class for generating RDF Turtle files from OBO database."""

    pass  # noqa: PIE790

    def __init__(
        self,
        engine: Engine | None = None,
    ):
        self.__ttls_folder = EXPORT_FOLDER
        connection_str = os.getenv(
            "CONNECTION_STR", constants.DB_DEFAULT_CONNECTION_STR
        )
        self.__engine = engine if engine else create_engine(str(connection_str))
        self.Session = sessionmaker(bind=self.__engine)

    def _set_ttls_folder(self, export_to_folder: str) -> None:
        """Sets the export folder path.

        This is mainly for testing purposes.
        """
        self.__ttls_folder = export_to_folder

    def create_turtles(self) -> str:
        """Generate RDF Turtle files from the database.
        Returns:
            Path to the zip file containing all generated Turtle files.
        """
        logger.info("Starting turtle file generation process.")
        os.makedirs(self.__ttls_folder, exist_ok=True)
        self._create_nodes()
        self._create_parent_child()
        self._create_relations()

        # Package everything into a zip file
        path_to_zip_file: str = self._create_zip_from_all_ttls()
        logger.info(f"Turtle files successfully packaged in {path_to_zip_file}")
        return path_to_zip_file

    def _create_nodes(self) -> None:
        logger.info("Creating RDF terms turtle file.")

        graph = get_empty_graph()

        with self.Session() as session:
            # Query all terms
            terms: list[models.Term] = session.query(models.Term).all()

            for term in tqdm(terms, desc="Creating terms triples"):
                term_entity: URIRef = get_uri_ref(term.iri)
                # Add type declarations
                graph.add(
                    triple=(
                        term_entity,
                        RDF.type,
                        namespaces.NODE_NS[models.Term.__name__],
                    )
                )
                if term.iri.startswith("http://purl.obolibrary.org/obo/"):
                    graph.add(
                        triple=(
                            term_entity,
                            RDF.type,
                            namespaces.NODE_NS["OBO"],
                        )
                    )
                graph.add(
                    triple=(
                        term_entity,
                        RDF.type,
                        namespaces.NODE_NS[get_resource_label(term.iri)],
                    )
                )
                graph.add(
                    triple=(
                        term_entity,
                        namespaces.REL_NS["name"],
                        Literal(term.name, datatype=XSD.string),
                    )
                )
        ttl_path = os.path.join(self.__ttls_folder, f"{models.Term.__tablename__}.ttl")
        graph.serialize(ttl_path, format="turtle")
        del graph

    def _create_parent_child(self) -> None:
        logger.info("Creating parent-child relations file.")

        graph = get_empty_graph()

        with self.Session() as session:
            # Query all parent-child relations
            parent_child_relations: list[models.ParentChild] = session.query(
                models.ParentChild
            ).all()

            for relation in tqdm(
                parent_child_relations, desc="Creating parent-child triples"
            ):
                child: URIRef = get_uri_ref(relation.child.iri)
                parent: URIRef = get_uri_ref(relation.parent.iri)

                graph.add(
                    triple=(
                        child,
                        namespaces.REL_NS["IS_A"],
                        parent,
                    )
                )

        ttl_path = os.path.join(
            self.__ttls_folder, f"{models.ParentChild.__tablename__}.ttl"
        )
        graph.serialize(ttl_path, format="turtle")
        del graph

    @staticmethod
    def _format_relation_name(name: str) -> str:
        """Normalize a relation name for use as an RDF predicate local name."""
        return (
            re.sub(r"_+", "_", re.sub(r"[^A-Za-z0-9]+", "_", name)).strip("_").upper()
        )

    def _create_relations(self) -> None:
        logger.info("Creating domain-specific relations file.")

        graph = get_empty_graph()

        with self.Session() as session:
            relations: list[models.Relation] = session.query(models.Relation).all()

            for relation in tqdm(relations, desc="Creating relation triples"):
                subject: URIRef = get_uri_ref(relation.subject.iri)
                object_: URIRef = get_uri_ref(relation.object.iri)
                predicate = namespaces.REL_NS[
                    self._format_relation_name(relation.relation_type.name)
                ]

                graph.add((subject, predicate, object_))

        ttl_path = os.path.join(
            self.__ttls_folder,
            f"{models.Relation.__tablename__}.ttl",
        )
        graph.serialize(ttl_path, format="turtle")
        del graph

    def _create_zip_from_all_ttls(self) -> str:
        """Package all generated turtle files into a single zip archive.

        Creates a zip file containing all .ttl files in the export folder,
        then removes the temporary turtle files directory to clean up.

        Returns:
            Path to the created zip file.
        """
        logger.info("Packaging turtle files into zip archive.")

        # Create zip archive from all turtle files
        path_to_zip_file = shutil.make_archive(
            base_name=self.__ttls_folder, format="zip", root_dir=self.__ttls_folder
        )

        # Clean up temporary turtle files directory
        shutil.rmtree(self.__ttls_folder)

        return path_to_zip_file


def create_turtles(
    engine: Optional[Engine] = None,
    export_to_folder: Optional[str] = None,
) -> str:
    """Create all turtle files.

    If engine=None tries to get the settings from environment else uses the default engine.

    If export_to_folder is provided, turtle files are exported to this folder. Otherwise,
    the default export folder (~/.biokb/ipni/data) is used.

    Args:
        engine (Engine | None, optional): SQLAlchemy engine. Defaults to None.
        export_to_folder (str | None, optional): Folder to export ttl files.
            Defaults to None.

    Returns:
        str: path zipped file with ttls.
    """
    ttl_creator = TurtleCreator(engine=engine)
    if export_to_folder:
        ttl_creator._set_ttls_folder(export_to_folder)
    return ttl_creator.create_turtles()
