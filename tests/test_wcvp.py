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
