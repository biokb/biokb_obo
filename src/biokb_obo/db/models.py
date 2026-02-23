from sys import prefix

from sqlalchemy import ForeignKey, String, Text
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column, relationship

prefix = "obo_"


class Base(DeclarativeBase):
    pass


class Ontology(Base):
    __tablename__ = prefix + "ontology"

    id: Mapped[int] = mapped_column(primary_key=True, autoincrement=True)
    name: Mapped[str] = mapped_column(String(255))
    title: Mapped[str | None] = mapped_column(String(500))
    iri: Mapped[str | None] = mapped_column(String(500))
    version: Mapped[str | None] = mapped_column(String(100))
    date: Mapped[str | None] = mapped_column(String(100))
    description: Mapped[str | None] = mapped_column(Text)
    license: Mapped[str | None] = mapped_column(String(255))

    # relationships
    terms: Mapped[list["Term"]] = relationship(
        back_populates="ontology", cascade="all, delete-orphan"
    )


class Term(Base):
    """Class representing a OBO term.

    Attributes:
        id (str): The OBO unique term identifier.
        name (str): The name of the term.
        definition (str | None): The definition of the term.
        ontology_id (str): Foreign key to the ontology.
        ontology (Ontology): Relationship to the Ontology.
        synonyms (list[Synonym]): Relationship to the Synonyms.
        identifiers (list[Identifier]): Relationship to the Identifiers.
        xrefs (list[XRef]): Relationship to the XRefs.
    """

    __tablename__ = prefix + "term"

    id: Mapped[int] = mapped_column(primary_key=True, autoincrement=True)
    iri: Mapped[str] = mapped_column(String(255), index=True)
    name: Mapped[str] = mapped_column(String(500))
    definition: Mapped[str | None] = mapped_column(Text)

    # foreign key
    ontology_id: Mapped[str] = mapped_column(ForeignKey(prefix + "ontology.id"))

    # relationships
    ontology: Mapped["Ontology"] = relationship(back_populates="terms")
    synonyms: Mapped[list["Synonym"]] = relationship(
        back_populates="term", cascade="all, delete-orphan"
    )
    identifiers: Mapped[list["Identifier"]] = relationship(
        back_populates="term", cascade="all, delete-orphan"
    )
    xrefs: Mapped[list["XRef"]] = relationship(
        back_populates="term", cascade="all, delete-orphan"
    )

    children_relationships: Mapped[list["ParentChild"]] = relationship(
        foreign_keys="[ParentChild.parent_id]",
        back_populates="parent",
        cascade="all, delete-orphan",
    )
    parent_relationships: Mapped[list["ParentChild"]] = relationship(
        foreign_keys="[ParentChild.child_id]",
        back_populates="child",
        cascade="all, delete-orphan",
    )


class ParentChild(Base):
    __tablename__ = prefix + "parent_child"

    id: Mapped[int] = mapped_column(primary_key=True, autoincrement=True)
    parent_id: Mapped[int] = mapped_column(ForeignKey(prefix + "term.id"))
    child_id: Mapped[int] = mapped_column(ForeignKey(prefix + "term.id"))

    # TODO: IntegrityError: (pymysql.err.IntegrityError) (1452, 'Cannot add or update a child row: a foreign key constraint fails (`biokb`.`obo_parent_child`, CONSTRAINT `obo_parent_child_ibfk_1` FOREIGN KEY (`parent_id`) REFERENCES `obo_term` (`id`))')
    # [SQL: INSERT INTO obo_parent_child (parent_id, child_id) VALUES (%(parent_id)s, %(child_id)s)]
    # relationships
    parent: Mapped["Term"] = relationship(
        foreign_keys=[parent_id], back_populates="children_relationships"
    )
    child: Mapped["Term"] = relationship(
        foreign_keys=[child_id], back_populates="parent_relationships"
    )


class Synonym(Base):
    __tablename__ = prefix + "synonym"

    id: Mapped[int] = mapped_column(primary_key=True, autoincrement=True)
    term_id: Mapped[int] = mapped_column(ForeignKey(prefix + "term.id"))
    synonym: Mapped[str] = mapped_column(String(500))
    type: Mapped[str | None] = mapped_column(String(100))
    term: Mapped["Term"] = relationship(back_populates="synonyms")


class Identifier(Base):
    __tablename__ = prefix + "identifier"

    id: Mapped[int] = mapped_column(primary_key=True, autoincrement=True)
    term_id: Mapped[int] = mapped_column(ForeignKey(prefix + "term.id"))
    identifier: Mapped[str] = mapped_column(String(255))

    term: Mapped["Term"] = relationship(back_populates="identifiers")


class XRef(Base):
    __tablename__ = prefix + "xref"

    id: Mapped[int] = mapped_column(primary_key=True, autoincrement=True)
    term_id: Mapped[int] = mapped_column(ForeignKey(prefix + "term.id"))
    database: Mapped[str] = mapped_column(String(100))
    reference_id: Mapped[str] = mapped_column(String(255))

    term: Mapped["Term"] = relationship(back_populates="xrefs")
