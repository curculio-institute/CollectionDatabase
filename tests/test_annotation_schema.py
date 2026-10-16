"""Migration 0073: annotation_term + annotation, and the TaxonWorks link columns on
biological_relationship. Every rule here is DB-enforced — the service layer adds clearer
messages, but a raw INSERT must fail just the same (CLAUDE.md §2)."""
import sqlite3

import pytest
from alembic.command import downgrade, upgrade
from alembic.config import Config
from sqlalchemy import text
from sqlalchemy.exc import IntegrityError

from app.models import CollectionObject
from tests.helpers import ensure_repo

NOW = "2026-10-16T00:00:00+00:00"


def _insert(s, table: str, **cols) -> int:
    cols = {"created_at": NOW, "updated_at": NOW, **cols}
    keys = ", ".join(f'"{k}"' for k in cols)
    params = ", ".join(f":{k}" for k in cols)
    return s.execute(
        text(f"INSERT INTO {table} ({keys}) VALUES ({params}) RETURNING id"), cols
    ).scalar_one()


def _fails(s, table: str, **cols) -> None:
    with pytest.raises(IntegrityError):
        with s.begin_nested():
            _insert(s, table, **cols)


def _specimen(s, cat: str) -> int:
    co = CollectionObject(catalog_number=cat, repository_id=ensure_repo(s, "ANN"))
    s.add(co)
    s.flush()
    return co.id


# ── annotation_term ────────────────────────────────────────────────────────────

def test_term_kind_is_closed(session):
    _fails(session, "annotation_term", kind="note", name="x")


def test_term_name_not_blank(session):
    _fails(session, "annotation_term", kind="tag", name="   ")


def test_term_name_unique_per_kind(session):
    _insert(session, "annotation_term", kind="tag", name="Adventive")
    _insert(session, "annotation_term", kind="data_attribute", name="Adventive")
    _fails(session, "annotation_term", kind="tag", name="Adventive")


def test_tw_name_iff_tw_id(session):
    _fails(session, "annotation_term", kind="tag", name="a", tw_id=1)
    _fails(session, "annotation_term", kind="tag", name="b", tw_name="Adventive")


def test_tw_host_needs_tw_id(session):
    _fails(session, "annotation_term", kind="tag", name="a", tw_host="sfg.taxonworks.org")


@pytest.mark.parametrize("bad", ["Adv\tentive", "Adv\nentive", "Adv\rentive", "  "])
def test_tw_name_clean(session, bad):
    _fails(session, "annotation_term", kind="tag", name="a", tw_id=1, tw_name=bad)


def test_tw_id_unique_per_kind(session):
    _insert(session, "annotation_term", kind="tag", name="a", tw_id=7, tw_name="A")
    _insert(session, "annotation_term", kind="data_attribute", name="b", tw_id=7,
            tw_name="B")
    _fails(session, "annotation_term", kind="tag", name="c", tw_id=7, tw_name="C")


@pytest.mark.parametrize("good", ["#abc", "#AABBCC", "#254d0d"])
def test_css_color_accepts_hex(session, good):
    _insert(session, "annotation_term", kind="tag", name=f"t{good}", css_color=good)


@pytest.mark.parametrize("bad", ["red", "#abcd", "abc", "#12345", "#ggg", ""])
def test_css_color_rejects_non_hex(session, bad):
    _fails(session, "annotation_term", kind="tag", name="t", css_color=bad)


# ── annotation ─────────────────────────────────────────────────────────────────

def test_annotation_exclusive_arc(session):
    t = _insert(session, "annotation_term", kind="tag", name="t")
    _fails(session, "annotation", term_id=t, term_kind="tag")             # no target
    co = _specimen(session, "ANN-00001")
    ev = _insert(session, "collecting_event")
    _fails(session, "annotation", term_id=t, term_kind="tag",
           collection_object_id=co, collecting_event_id=ev)              # two targets


def test_value_by_kind(session):
    tag = _insert(session, "annotation_term", kind="tag", name="t")
    da = _insert(session, "annotation_term", kind="data_attribute", name="d")
    co = _specimen(session, "ANN-00002")
    _fails(session, "annotation", term_id=tag, term_kind="tag",
           collection_object_id=co, value="x")                           # tag with value
    _fails(session, "annotation", term_id=da, term_kind="data_attribute",
           collection_object_id=co)                                      # DA without value
    _fails(session, "annotation", term_id=da, term_kind="data_attribute",
           collection_object_id=co, value="  ")
    _fails(session, "annotation", term_id=da, term_kind="data_attribute",
           collection_object_id=co, value="a\tb")


def test_composite_fk_rejects_kind_mismatch(session):
    tag = _insert(session, "annotation_term", kind="tag", name="t")
    co = _specimen(session, "ANN-00003")
    _fails(session, "annotation", term_id=tag, term_kind="data_attribute",
           collection_object_id=co, value="x")


def test_used_term_cannot_be_deleted_or_change_kind(session):
    tag = _insert(session, "annotation_term", kind="tag", name="t")
    co = _specimen(session, "ANN-00004")
    _insert(session, "annotation", term_id=tag, term_kind="tag", collection_object_id=co)
    with pytest.raises(IntegrityError):
        with session.begin_nested():
            session.execute(text("DELETE FROM annotation_term WHERE id = :i"), {"i": tag})
    with pytest.raises(IntegrityError):
        with session.begin_nested():
            session.execute(text("UPDATE annotation_term SET kind = 'data_attribute' "
                                 "WHERE id = :i"), {"i": tag})


def test_one_annotation_per_record_and_term(session):
    tag = _insert(session, "annotation_term", kind="tag", name="t")
    co = _specimen(session, "ANN-00005")
    _insert(session, "annotation", term_id=tag, term_kind="tag", collection_object_id=co)
    _fails(session, "annotation", term_id=tag, term_kind="tag", collection_object_id=co)


def test_deleting_the_specimen_cascades(session):
    tag = _insert(session, "annotation_term", kind="tag", name="t")
    co = _specimen(session, "ANN-00006")
    _insert(session, "annotation", term_id=tag, term_kind="tag", collection_object_id=co)
    session.execute(text("DELETE FROM collection_object WHERE id = :i"), {"i": co})
    assert session.execute(text("SELECT COUNT(*) FROM annotation WHERE term_id = :t"),
                           {"t": tag}).scalar() == 0


# ── biological_relationship link columns ───────────────────────────────────────

def test_relationship_tw_name_iff_id_is_a_trigger(session):
    _fails(session, "biological_relationship", name="feeds on", taxonworksID=11)
    rid = _insert(session, "biological_relationship", name="feeds on")
    with pytest.raises(IntegrityError):
        with session.begin_nested():
            session.execute(text('UPDATE biological_relationship SET "taxonworksID" = 11 '
                                 "WHERE id = :i"), {"i": rid})


def test_relationship_tw_host_needs_id(session):
    _fails(session, "biological_relationship", name="x", tw_host="sfg.taxonworks.org")


def test_relationship_taxonworks_id_unique_among_set(session):
    _insert(session, "biological_relationship", name="a", taxonworksID=5, tw_name="A")
    _insert(session, "biological_relationship", name="b")
    _insert(session, "biological_relationship", name="c")       # NULLs never collide
    _fails(session, "biological_relationship", name="d", taxonworksID=5, tw_name="D")


# ── migration behaviour on existing data ───────────────────────────────────────

def _cfg(tmp_path) -> tuple[Config, str]:
    db = tmp_path / "m.db"
    cfg = Config("alembic.ini")
    cfg.set_main_option("sqlalchemy.url", f"sqlite:///{db}")
    return cfg, str(db)


def test_existing_links_are_backfilled_unverified(tmp_path):
    cfg, db = _cfg(tmp_path)
    upgrade(cfg, "0072")
    con = sqlite3.connect(db)
    con.execute('INSERT INTO biological_relationship (name, "taxonworksID", created_at, '
                "updated_at) VALUES ('feeds on', 11, ?, ?)", (NOW, NOW))
    con.execute("INSERT INTO biological_relationship (name, created_at, updated_at) "
                "VALUES ('local only', ?, ?)", (NOW, NOW))
    con.commit()
    con.close()
    upgrade(cfg, "0073")
    con = sqlite3.connect(db)
    rows = dict((n, (tn, th)) for n, tn, th in con.execute(
        "SELECT name, tw_name, tw_host FROM biological_relationship"))
    con.close()
    assert rows["feeds on"] == ("feeds on", None)       # name copied, NOT verified
    assert rows["local only"] == (None, None)


def test_upgrade_refuses_duplicate_relationship_ids(tmp_path):
    cfg, db = _cfg(tmp_path)
    upgrade(cfg, "0072")
    con = sqlite3.connect(db)
    for name in ("feeds on", "eats"):
        con.execute('INSERT INTO biological_relationship (name, "taxonworksID", '
                    "created_at, updated_at) VALUES (?, 11, ?, ?)", (name, NOW, NOW))
    con.commit()
    con.close()
    with pytest.raises(RuntimeError, match="id 11"):
        upgrade(cfg, "0073")


def test_downgrade_round_trips_an_empty_vocabulary(tmp_path):
    cfg, db = _cfg(tmp_path)
    upgrade(cfg, "0073")
    downgrade(cfg, "0072")
    con = sqlite3.connect(db)
    cols = {r[1] for r in con.execute("PRAGMA table_info(biological_relationship)")}
    tables = {r[0] for r in con.execute("SELECT name FROM sqlite_master WHERE type='table'")}
    con.close()
    assert "tw_name" not in cols and "tw_host" not in cols
    assert "annotation_term" not in tables and "annotation" not in tables
