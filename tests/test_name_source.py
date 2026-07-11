"""Offline name sources — the generic DwC-Archive engine behind WCVP and user datasets.

The archive describes itself (meta.xml: core file, delimiter, fields by term + index, and a
`default` nomenclaturalCode), so these tests build real archives in-memory and assert the
reader honours the declaration rather than any convention of ours.
"""
import io
import zipfile

import pytest

from app.models import Taxon
from app.services import name_source as ns
from app.services.taxa import RANKS_BY_CODE, get_or_create_from_chain

ICZN_RANKS = frozenset(RANKS_BY_CODE["ICZN"])


def _spec(**kw):
    base = dict(slug="test", label="Test checklist", nomenclatural_code="ICZN",
                supported_ranks=ICZN_RANKS)
    base.update(kw)
    return ns.NameSourceSpec(**base)


def _meta_xml(core: str, fields: list[tuple[int, str]], *, delim="|", code="ICZN") -> str:
    rows = "\n".join(
        f'<field index="{i}" term="http://rs.tdwg.org/dwc/terms/{t}"/>' for i, t in fields
    )
    return f"""<?xml version='1.0' encoding='utf-8'?>
<archive xmlns="http://rs.tdwg.org/dwc/text/">
  <core encoding="UTF-8" fieldsTerminatedBy="{delim}" fieldsEnclosedBy=''
        ignoreHeaderLines="1" rowType="http://rs.tdwg.org/dwc/terms/Taxon">
    <files><location>{core}</location></files>
    <id index="0" />
    {rows}
    <field default="{code}" term="http://rs.tdwg.org/dwc/terms/nomenclaturalCode"/>
  </core>
</archive>"""


_FIELDS = [(0, "taxonID"), (1, "family"), (2, "genus"), (3, "scientificName"),
           (4, "scientificNameAuthorship"), (5, "taxonRank"), (6, "taxonomicStatus"),
           (7, "acceptedNameUsageID"), (8, "parentNameUsageID")]


def _archive(tmp_path, rows: list[str], *, header: str, code="ICZN", core="t.csv"):
    """Build a real .zip DwC archive. `header` is the CSV header line verbatim — the point of
    several tests is that its SPELLING must not matter."""
    p = tmp_path / "a.zip"
    with zipfile.ZipFile(p, "w") as zf:
        zf.writestr("meta.xml", _meta_xml(core, _FIELDS, code=code))
        zf.writestr(core, header + "\n" + "\n".join(rows) + "\n")
    return p


# A minimal beetle tree: family > genus > subgenus > species, plus a synonym in its OWN
# lineage whose accepted name sits under a different genus.
# 9 columns, in the order _FIELDS declares them.
_ROWS = [
    "1|Carabidae||Carabidae||Family|Accepted||",
    "2|Carabidae|Calosoma|Calosoma|Weber, 1801|Genus|Accepted||1",
    "3|Carabidae|Calosoma|Calosoma reticulatum|(Fabricius, 1787)|Species|Accepted||2",
    "4|Carabidae|Callisthenes|Callisthenes|Fischer, 1820|Genus|Accepted||1",
    "5|Carabidae|Callisthenes|Callisphaena|Motschulsky, 1859|Subgenus|Accepted||4",
    "6|Carabidae|Callisthenes|Callisthenes reticulatus|(Fabricius, 1787)|Species|Synonym|3|5",
]
_HEADER_CORRECT = ("taxonID|family|genus|scientificName|scientificNameAuthorship|"
                   "taxonRank|taxonomicStatus|acceptedNameUsageID|parentNameUsageID")
# Kew's real archive misspells two headers; a hand-built one spells them correctly. Both exist.
_HEADER_KEW = ("taxonid|family|genus|scientfiicname|scientfiicnameauthorship|"
               "taxonrank|taxonomicstatus|acceptednameusageid|parentnameusageid")


class TestKeyNormalisation:
    """A field is identified by its TERM, not by the spelling of a header."""

    @pytest.mark.parametrize("raw", [
        "http://rs.tdwg.org/dwc/terms/taxonID",
        "taxonID", "taxonid", "TaxonID",
        "dwc:taxonID", "dwc_taxonid", "dwc.taxonID",
        "taxon_id", "Taxon ID", "taxon-id",
    ])
    def test_every_spelling_of_taxon_id_collapses(self, raw):
        assert ns._key(raw) == "taxonid"

    def test_kew_misspelling_and_correct_spelling_both_resolve(self):
        assert ns._BY_ALIAS[ns._key("scientfiicName")] == "name"
        assert ns._BY_ALIAS[ns._key("scientificName")] == "name"

    def test_snake_case_is_not_split_into_its_last_word(self):
        # A naive split on "_" would turn scientific_name into "name" — a different field.
        assert ns._key("scientific_name") == "scientificname"
        assert ns._BY_ALIAS[ns._key("scientific_name")] == "name"


class TestLayout:
    def test_reads_delimiter_core_and_code_from_meta_xml(self, tmp_path):
        with zipfile.ZipFile(_archive(tmp_path, _ROWS, header=_HEADER_CORRECT)) as zf:
            lay = ns.read_layout(zf)
        assert lay.core_file == "t.csv"
        assert lay.delimiter == "|"
        assert lay.header_lines == 1
        # The archive states its own code — it is never guessed from the taxa inside.
        assert lay.nomenclatural_code == "ICZN"
        assert lay.columns["name"] == 3

    def test_an_archive_without_meta_xml_is_refused(self, tmp_path):
        p = tmp_path / "b.zip"
        with zipfile.ZipFile(p, "w") as zf:
            zf.writestr("t.csv", _HEADER_CORRECT)
        with zipfile.ZipFile(p) as zf:
            with pytest.raises(ns.NameSourceError, match="no meta.xml"):
                ns.read_layout(zf)


class TestBuildAndSearch:
    @pytest.mark.parametrize("header", [_HEADER_CORRECT, _HEADER_KEW],
                             ids=["correct-headers", "kew-misspelled-headers"])
    def test_builds_identically_whatever_the_header_spelling(self, tmp_path, header):
        db_path = tmp_path / f"{hash(header)}.sqlite"
        rep = ns.build_index(_archive(tmp_path, _ROWS, header=header), db_path, _spec())
        assert rep.rows == 6
        assert rep.replaced == 1              # the one Synonym
        assert rep.dangling_accepted_ids == 0
        db = ns.open_index(db_path)
        assert ns.get(db, "6").name == "Callisthenes reticulatus"
        db.close()

    def test_count_is_not_broken_by_an_empty_refused_set(self, tmp_path):
        """`status NOT IN (NULL)` is NULL, never true: a source with nothing refused would
        otherwise report ZERO importable names."""
        db_path = tmp_path / "c.sqlite"
        spec = _spec(status_refused=frozenset())
        ns.build_index(_archive(tmp_path, _ROWS, header=_HEADER_CORRECT), db_path, spec)
        db = ns.open_index(db_path)
        importable, total = ns.count(db, spec)
        db.close()
        assert (importable, total) == (6, 6)

    def test_unknown_status_is_refused_loudly_not_guessed(self, tmp_path):
        rows = _ROWS + ["7|Carabidae|Calosoma|Calosoma dubium||Species|Doubtful||2"]
        with pytest.raises(ns.NameSourceError, match="unknown taxonomicStatus"):
            ns.build_index(_archive(tmp_path, rows, header=_HEADER_CORRECT),
                           tmp_path / "d.sqlite", _spec())


class TestChainAndImport:
    @pytest.fixture
    def db(self, tmp_path):
        p = tmp_path / "e.sqlite"
        ns.build_index(_archive(tmp_path, _ROWS, header=_HEADER_CORRECT), p, _spec())
        d = ns.open_index(p)
        yield d
        d.close()

    def test_a_synonym_keeps_its_own_lineage(self, db):
        """Epic #30: the synonym is parented under ITS OWN genus/subgenus, never under the
        accepted name's. Parenting it under the accepted lineage would RENAME it."""
        chain = ns.chain_for(db, ns.get(db, "6"), _spec())
        own = [e["name"] for e in chain["chain"]]
        acc = [e["name"] for e in chain["accepted_chain"]]
        assert own == ["Carabidae", "Callisthenes", "Callisphaena", "Callisthenes reticulatus"]
        assert acc == ["Carabidae", "Calosoma", "Calosoma reticulatum"]

    def test_import_composes_the_zoological_names(self, session, db):
        chain = ns.chain_for(db, ns.get(db, "6"), _spec())
        leaf = get_or_create_from_chain(
            session, chain["chain"], accepted_chain=chain["accepted_chain"])
        session.flush()
        # The archive stores the flat "Callisthenes reticulatus"; the subgenus in its parent
        # chain is interpolated by our composition rules (ICZN brackets).
        assert leaf.scientific_name == "Callisthenes (Callisphaena) reticulatus"
        assert leaf.nomenclatural_code == "ICZN"
        accepted = session.get(Taxon, leaf.accepted_name_usage_id)
        assert accepted.scientific_name == "Calosoma reticulatum"

    def test_import_is_idempotent(self, session, db):
        chain = ns.chain_for(db, ns.get(db, "6"), _spec())
        get_or_create_from_chain(session, chain["chain"],
                                 accepted_chain=chain["accepted_chain"])
        session.flush()
        n = session.query(Taxon).count()
        get_or_create_from_chain(session, chain["chain"],
                                 accepted_chain=chain["accepted_chain"])
        session.flush()
        assert session.query(Taxon).count() == n

    def test_a_rank_the_code_lacks_is_refused_not_coerced(self, db):
        """`variety` is an ICN rank; an ICZN source may not offer it (RANKS_BY_CODE)."""
        row = ns.get(db, "6")
        icn_only = _spec(supported_ranks=frozenset({"family", "genus", "variety"}))
        assert row.is_refused(icn_only)          # its rank (species) is not in that set
        with pytest.raises(ns.NotImportable):
            ns.chain_for(db, row, icn_only)


class TestMissingSpeciesAncestor:
    """Real checklists parent a subspecies straight under a SUBGENUS, skipping the species —
    all 692 subspecies in the Coleoptera archive do. The infraspecific name then has nothing
    to compose from, which silently produced names like 'Carabus (Megodontus) None germarii'.
    """

    # A subspecies parented under the SUBGENUS (id 5), whose species ("Callisthenes elegans")
    # is nowhere in the archive — the 475-of-692 case: a sheet that splits a species into
    # subspecies lists only the subspecies, never the species itself.
    ROWS = _ROWS + [
        "8|Carabidae|Callisthenes|Callisthenes elegans alpinus|Meier, 1900"
        "|Subspecies|Accepted||5",
    ]

    @pytest.fixture
    def db(self, tmp_path):
        p = tmp_path / "f.sqlite"
        ns.build_index(_archive(tmp_path, self.ROWS, header=_HEADER_CORRECT), p, _spec())
        d = ns.open_index(p)
        yield d
        d.close()

    def test_the_species_ancestor_is_recovered_from_the_trinomial(self, db):
        chain = ns.chain_for(db, ns.get(db, "8"), _spec())
        ranks = [e["rank"] for e in chain["chain"]]
        assert "species" in ranks, "the missing species parent must be inserted"
        names = [e["name"] for e in chain["chain"]]
        assert names[-2] == "Callisthenes elegans"   # directly above the leaf

    def test_no_name_is_ever_composed_with_a_missing_part(self, session, db):
        chain = ns.chain_for(db, ns.get(db, "8"), _spec())
        leaf = get_or_create_from_chain(session, chain["chain"])
        session.flush()
        assert "None" not in leaf.scientific_name
        assert leaf.scientific_name == (
            "Callisthenes (Callisphaena) elegans alpinus")

    def test_a_reconstructed_ancestor_is_marked_as_not_from_the_archive(self, db):
        """The reconstruction is a DEFECT WORKAROUND, and it must be visible as one.

        A reconstructed entry carries no source_id — that is what `datasets.import_all` counts
        into `ImportReport.reconstructed_species`. A well-formed archive (one that ships the
        species its subspecies point at) reconstructs NOTHING, so a non-zero count is the
        signal that an archive is missing rows, not a routine statistic.
        """
        chain = ns.chain_for(db, ns.get(db, "8"), _spec())["chain"]
        reconstructed = [e for e in chain if e["source_id"] is None]
        assert [e["rank"] for e in reconstructed] == ["species"]
        # Authorship is left blank rather than guessed: the NAME is certain, the author is not.
        assert reconstructed[0]["authorship"] is None
        # Everything the archive DID supply keeps its id.
        assert all(e["source_id"] for e in chain if e["rank"] != "species")


class TestWellFormedArchiveReconstructsNothing:
    """The counterpart: when the archive supplies the species, nothing is invented."""

    ROWS = _ROWS + [
        # the species the subspecies belongs to, present and correctly parented
        "7|Carabidae|Callisthenes|Callisthenes reticulatus|(Fabricius, 1787)"
        "|Species|Accepted||5",
        "8|Carabidae|Callisthenes|Callisthenes reticulatus alpinus|Meier, 1900"
        "|Subspecies|Accepted||7",
    ]

    def test_nothing_is_reconstructed(self, tmp_path):
        p = tmp_path / "g.sqlite"
        ns.build_index(_archive(tmp_path, self.ROWS, header=_HEADER_CORRECT), p, _spec())
        db = ns.open_index(p)
        try:
            chain = ns.chain_for(db, ns.get(db, "8"), _spec())["chain"]
        finally:
            db.close()
        assert all(e["source_id"] for e in chain), \
            "a well-formed archive must reconstruct nothing"
        assert [e["rank"] for e in chain][-2:] == ["species", "subspecies"]
