"""Tests for the WCVP archive → SQLite index loader (app/services/wcvp.py).

The loader normalises two source quirks that every consumer would otherwise have to
remember (self-referencing accepted ids; the "ipni:" prefix), refuses an unknown
taxonomicStatus rather than guessing where it belongs, and must parse the archive's
*unquoted* CSV exactly as meta.xml declares it. Each of those is pinned here.
"""
import sqlite3
import zipfile

import pytest

from app.services import wcvp

# Real column order and names from the archive, misspellings included.
_HEADER = (
    "taxonid|family|genus|specificepithet|infraspecificepithet|scientfiicname|"
    "scientfiicnameauthorship|taxonrank|taxonomicstatus|acceptednameusageid|"
    "parentnameusageid|originalnameusageid|namepublishedin|nomenclaturalstatus|"
    "taxonremarks|scientificnameid|dynamicproperties|references"
)

_EML = """<?xml version='1.0' encoding='utf-8'?>
<eml:eml xmlns:eml="eml://ecoinformatics.org/eml-2.1.1">
  <dataset>
    <title>The World Checklist of Vascular Plants (WCVP)</title>
    <pubDate>2026-06-04</pubDate>
    <additionalMetadata>
      <citation>WCVP. Facilitated by the Royal Botanic Gardens, Kew.</citation>
      <version>16.0</version>
    </additionalMetadata>
  </dataset>
</eml:eml>"""


def _row(taxonid, name, *, auth="L.", rank="Species", status="Accepted",
         accepted="", parent="", ipni="", family="Fagaceae", genus="Quercus",
         remarks=""):
    cells = [""] * 18
    cells[0] = taxonid
    cells[1] = family
    cells[2] = genus
    cells[5] = name
    cells[6] = auth
    cells[7] = rank
    cells[8] = status
    cells[9] = accepted
    cells[10] = parent
    cells[14] = remarks
    cells[15] = f"ipni:{ipni}" if ipni else ""
    return "|".join(cells)


def _archive(tmp_path, rows, eml=_EML):
    path = tmp_path / "wcvp_dwca.zip"
    with zipfile.ZipFile(path, "w") as zf:
        zf.writestr("eml.xml", eml)
        zf.writestr("wcvp_taxon.csv", "\n".join([_HEADER, *rows]) + "\n")
    return path


def test_reads_version_and_citation_from_eml(tmp_path):
    archive = _archive(tmp_path, [_row("1", "Quercus robur", ipni="304293-2")])
    report = wcvp.build_index(archive, tmp_path / "wcvp.sqlite")
    assert report.meta.version == "16.0"
    assert report.meta.pub_date == "2026-06-04"
    assert report.meta.label == "WCVP v16.0 (2026-06-04)"


def test_accepted_self_reference_is_normalised_to_null(tmp_path):
    """Every Accepted row in WCVP points acceptednameusageid at its own taxonid.

    Stored verbatim that reads as "synonym of itself" — the trap this loader exists
    to close, since our model derives accepted-ness from that column being NULL.
    """
    archive = _archive(tmp_path, [_row("174750", "Quercus robur", accepted="174750")])
    db_path = tmp_path / "wcvp.sqlite"
    wcvp.build_index(archive, db_path)
    db = sqlite3.connect(db_path)
    assert db.execute("SELECT accepted_id FROM name WHERE taxonid='174750'").fetchone()[0] is None


def test_synonym_keeps_its_accepted_link(tmp_path):
    archive = _archive(tmp_path, [
        _row("174750", "Quercus robur", accepted="174750"),
        _row("1", "Quercus pedunculata", auth="Hoffm.", status="Synonym", accepted="174750"),
    ])
    db_path = tmp_path / "wcvp.sqlite"
    wcvp.build_index(archive, db_path)
    db = sqlite3.connect(db_path)
    assert db.execute("SELECT accepted_id FROM name WHERE taxonid='1'").fetchone()[0] == "174750"


def test_ipni_prefix_is_stripped(tmp_path):
    archive = _archive(tmp_path, [_row("1", "Quercus robur", ipni="304293-2")])
    db_path = tmp_path / "wcvp.sqlite"
    wcvp.build_index(archive, db_path)
    db = sqlite3.connect(db_path)
    assert db.execute("SELECT ipni_id FROM name").fetchone()[0] == "304293-2"


def test_unknown_status_raises_rather_than_guessing(tmp_path):
    """A new WCVP status must be classified by a human, not coerced into the nearest state."""
    archive = _archive(tmp_path, [_row("1", "Quercus robur", status="Deprecated")])
    with pytest.raises(wcvp.WcvpError, match="unknown taxonomicStatus 'Deprecated'"):
        wcvp.build_index(archive, tmp_path / "wcvp.sqlite")


def test_unquoted_csv_is_parsed_per_meta_xml(tmp_path):
    """meta.xml declares fieldsEnclosedBy='' — a quoted-looking field is literal text.

    With Python's default QUOTE_MINIMAL the leading quote is consumed and the following
    columns shift, silently corrupting scientificnameid.
    """
    archive = _archive(tmp_path, [
        _row("1", "Quercus robur", ipni="304293-2", remarks='"Shan Hills"'),
    ])
    db_path = tmp_path / "wcvp.sqlite"
    wcvp.build_index(archive, db_path)
    db = sqlite3.connect(db_path)
    name, ipni = db.execute("SELECT name, ipni_id FROM name").fetchone()
    assert name == "Quercus robur"
    assert ipni == "304293-2"


def test_status_partition_is_counted(tmp_path):
    archive = _archive(tmp_path, [
        _row("1", "Quercus robur", accepted="1"),
        _row("2", "Catinga media", status="Provisionally Accepted"),
        _row("3", "Quercus ped", status="Synonym", accepted="1"),
        _row("4", "Quercus ill", status="Illegitimate", accepted="1"),
        _row("5", "Juglans gonroku", status="Unplaced"),
        _row("6", "Paeonia officinalis", status="Misapplied", accepted="1"),
    ])
    report = wcvp.build_index(archive, tmp_path / "wcvp.sqlite")
    assert (report.accepted, report.replaced, report.refused) == (2, 2, 2)
    assert report.rows == 6


def test_dangling_references_are_reported_not_dropped(tmp_path):
    """Kew's data contains links to rows that do not exist. Count them; never silently drop."""
    archive = _archive(tmp_path, [
        _row("1", "Quercus robur", accepted="1"),
        _row("2", "Quercus ped", status="Synonym", accepted="999"),
        _row("3", "Quercus sp", parent="888"),
    ])
    report = wcvp.build_index(archive, tmp_path / "wcvp.sqlite")
    assert report.dangling_accepted_ids == 1
    assert report.dangling_parent_ids == 1
    db = sqlite3.connect(tmp_path / "wcvp.sqlite")
    assert db.execute("SELECT count(*) FROM name").fetchone()[0] == 3  # nothing dropped


def test_prefix_search_uses_the_nocase_index(tmp_path):
    """A BINARY index degrades a case-insensitive prefix LIKE to a full SCAN of 1.45M rows."""
    archive = _archive(tmp_path, [_row("1", "Quercus robur")])
    db_path = tmp_path / "wcvp.sqlite"
    wcvp.build_index(archive, db_path)
    db = sqlite3.connect(db_path)
    plan = db.execute(
        "EXPLAIN QUERY PLAN SELECT name FROM name WHERE name LIKE 'Quer%'"
    ).fetchall()
    detail = " ".join(str(r[-1]) for r in plan)
    assert "SEARCH" in detail and "ix_name_nocase" in detail, detail


def test_meta_table_records_provenance(tmp_path):
    archive = _archive(tmp_path, [_row("1", "Quercus robur")])
    db_path = tmp_path / "wcvp.sqlite"
    wcvp.build_index(archive, db_path)
    db = sqlite3.connect(db_path)
    meta = dict(db.execute("SELECT key, value FROM meta"))
    assert meta["label"] == "WCVP v16.0 (2026-06-04)"
    assert meta["nomenclatural_code"] == "ICN"
    assert meta["source_url"] == wcvp.WCVP_DWCA_URL
    assert meta["rows"] == "1"


def test_failed_build_leaves_nothing_behind(tmp_path):
    """A half-built index must never be left where the app would read it — nor beside it."""
    db_path = tmp_path / "wcvp.sqlite"
    archive = _archive(tmp_path, [_row("1", "Q", status="Bogus")])
    with pytest.raises(wcvp.WcvpError):
        wcvp.build_index(archive, db_path)
    assert not db_path.exists()
    assert not db_path.with_suffix(".building").exists()


def test_rebuild_replaces_an_existing_index(tmp_path):
    """Refreshing to a new WCVP release must not merge into the old rows."""
    db_path = tmp_path / "wcvp.sqlite"
    wcvp.build_index(_archive(tmp_path, [_row("1", "Quercus robur")]), db_path)
    wcvp.build_index(_archive(tmp_path, [_row("2", "Fagus sylvatica", genus="Fagus")]), db_path)
    db = sqlite3.connect(db_path)
    assert db.execute("SELECT name FROM name").fetchall() == [("Fagus sylvatica",)]


# ---------------------------------------------------------------------------
# Query layer
# ---------------------------------------------------------------------------

def _genus(taxonid, name, *, auth, family, status="Accepted", ipni=""):
    return _row(taxonid, name, auth=auth, rank="Genus", status=status,
                family=family, genus=name, ipni=ipni)


@pytest.fixture
def index(tmp_path):
    """A miniature checklist exercising every case the real archive contains."""
    archive = _archive(tmp_path, [
        # accepted species + its genus
        _genus("10", "Quercus", auth="L.", family="Fagaceae"),
        _row("11", "Quercus robur", accepted="11", parent="10", ipni="304293-2"),
        _row("12", "Quercus robur subsp. robur", rank="Subspecies", auth="",
             accepted="12", parent="11"),
        # a synonym: no parent_id, own genus in the genus column
        _genus("20", "Sarothamnus", auth="Wimm.", family="Fabaceae", status="Synonym"),
        _row("21", "Sarothamnus scoparius", auth="(L.) Wimm.", status="Synonym",
             accepted="31", family="Fabaceae", genus="Sarothamnus"),
        _genus("30", "Cytisus", auth="Desf.", family="Fabaceae"),
        _row("31", "Cytisus scoparius", auth="(L.) Link", accepted="31", parent="30",
             family="Fabaceae", genus="Cytisus"),
        # homonym genus across two families
        _genus("40", "Torreya", auth="Arn.", family="Taxaceae"),
        _genus("41", "Torreya", auth="Spreng.", family="Lamiaceae", status="Illegitimate"),
        _genus("42", "Torreya", auth="Raf.", family="Lamiaceae", status="Synonym"),
        # nothogenus carries a "×" marker on the row but not in the genus column
        _genus("50", "× Epicattleya", auth="Rolfe", family="Orchidaceae",
               status="Artificial Hybrid"),
        # refused
        _row("60", "Juglans gonroku", auth="Makino", status="Unplaced",
             family="Juglandaceae", genus="Juglans"),
        _row("61", "Quercus officinalis", auth="Thunb.", status="Misapplied",
             accepted="11", family="Fagaceae"),
        # dangling accepted link (Kew's own data has these)
        _row("70", "Quercus dangling", status="Synonym", accepted="99999"),
    ])
    db_path = tmp_path / "wcvp.sqlite"
    wcvp.build_index(archive, db_path)
    return wcvp.open_index(db_path)


def test_open_index_raises_when_not_built(tmp_path):
    with pytest.raises(wcvp.IndexMissing, match="build it with"):
        wcvp.open_index(tmp_path / "absent.sqlite")


def test_index_is_read_only(index):
    with pytest.raises(sqlite3.OperationalError, match="readonly"):
        index.execute("DELETE FROM name")


def test_search_ranks_accepted_before_replaced_before_refused(index):
    res = wcvp.search(index, "Quercus")
    kinds = [("accepted" if r.is_accepted else "refused" if r.is_refused else "replaced")
             for r in res]
    assert kinds.index("accepted") < kinds.index("replaced") < kinds.index("refused")


def test_search_shows_refused_names_rather_than_hiding_them(index):
    """Hiding them invites the user to hand-create an invented name instead."""
    names = [r.name for r in wcvp.search(index, "Juglans gonroku")]
    assert names == ["Juglans gonroku"]


def test_refused_names_are_capped_so_they_cannot_flood(index):
    res = wcvp.search(index, "Quercus", refused_limit=1)
    assert sum(r.is_refused for r in res) == 1


def test_search_needs_two_characters(index):
    assert wcvp.search(index, "Q") == []


def test_search_matches_later_tokens_anywhere(index):
    assert wcvp.search(index, "Quer rob")[0].name == "Quercus robur"


def test_refusal_reason_never_asserts_a_synonymy(index):
    misapplied = wcvp.get(index, "61")
    reason = wcvp.refusal_reason(index, misapplied)
    assert reason == "in WCVP this name is applied to Quercus robur"
    assert "synonym" not in reason.lower()
    unplaced = wcvp.get(index, "60")
    assert wcvp.refusal_reason(index, unplaced) == \
        "WCVP records no accepted placement for this name"


def test_accepted_name_of_an_accepted_row_is_none(index):
    assert wcvp.accepted_name(index, wcvp.get(index, "11")) is None


def test_accepted_name_follows_a_synonym_link(index):
    assert wcvp.accepted_name(index, wcvp.get(index, "21")).name == "Cytisus scoparius"


def test_dangling_accepted_link_returns_none_not_a_fabrication(index):
    assert wcvp.accepted_name(index, wcvp.get(index, "70")) is None


def test_resolve_genus_uses_family_to_separate_homonyms(index):
    """1894 genus names are homonyms; Torreya alone would parent a conifer in the mints."""
    assert wcvp.resolve_genus(index, "Torreya", "Taxaceae").authorship == "Arn."


def test_resolve_genus_prefers_the_single_accepted_candidate(index):
    """Fagaceae has one Quercus; Lamiaceae has two non-accepted Torreya rows."""
    assert wcvp.resolve_genus(index, "Quercus", "Fagaceae").authorship == "L."


def test_resolve_genus_returns_none_when_ambiguous_among_non_accepted(index):
    """Ascyrum L. (Synonym) vs Ascyrum Mill. (Illegitimate): pick neither, invent no author."""
    assert wcvp.resolve_genus(index, "Torreya", "Lamiaceae") is None


def test_resolve_genus_matches_a_nothogenus_through_its_marker(index):
    """The genus column says 'Epicattleya'; the genus row is named '× Epicattleya'."""
    got = wcvp.resolve_genus(index, "Epicattleya", "Orchidaceae")
    assert got is not None and got.name == "× Epicattleya"


def test_synonym_resolves_to_its_own_genus_not_the_accepted_names(index):
    """Epic #30: Sarothamnus scoparius stays under Sarothamnus, never under Cytisus.

    Parenting under the accepted name's genus would compose the synonym's name as
    'Cytisus scoparius' — its own accepted name — and get_or_create would merge them.
    """
    syn = wcvp.get(index, "21")
    assert syn.parent_id is None          # WCVP gives synonyms no parent
    assert syn.genus == "Sarothamnus"     # ...but the genus column is its OWN genus
    genus = wcvp.resolve_genus(index, syn.genus, syn.family)
    assert genus.name == "Sarothamnus"
    assert wcvp.accepted_name(index, syn).genus == "Cytisus"   # and that is a different genus


def test_typed_wildcards_are_not_treated_as_wildcards(index):
    """A bare '%' would otherwise match every one of the 1.45M names."""
    assert wcvp.search(index, "%") == []          # too short anyway
    assert wcvp.search(index, "%uercus") == []    # not a prefix match for anything
    assert wcvp.search(index, "Quercus%") == []   # literal '%' appears in no name


def test_escaped_search_still_seeks_the_index(index):
    """SQLite's LIKE optimization is fussy: an ESCAPE clause could downgrade the prefix
    seek to a full SCAN of 1.45M rows. Assert the seek, not merely that the index is named.
    """
    plan = " ".join(str(r[-1]) for r in index.execute(
        r"EXPLAIN QUERY PLAN SELECT name FROM name WHERE name LIKE 'Quer%' ESCAPE '\'"))
    assert "SEARCH" in plan and "ix_name_nocase" in plan, plan
