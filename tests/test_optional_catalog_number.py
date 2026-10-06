"""Foreign collections: a name is enough, and a catalog number is optional (migration 0072).

* A collection is identified by its NAME; its collectionCode is optional.
* A specimen in a FOREIGN collection may have no catalog number; in the default (own)
  collection it must have one — DB-enforced by triggers, so raw SQL cannot bypass it.
* A numberless specimen is never exported to TaxonWorks.
* Transfers keep the catalog number, and a number is never handed out twice.
"""
import pytest
from sqlalchemy import text
from sqlalchemy.exc import IntegrityError

import app.services.batch_ops as batch
import app.services.identifiers as id_svc
import app.services.repositories as repo_svc
from app.models import CollectionObject
from app.services.dwc_export import export_decision
from app.services.specimens import create_collection_object, update_collection_object
from tests.helpers import ensure_repo


@pytest.fixture
def home(session):
    rid = ensure_repo(session, "HOME72")
    repo_svc.set_default(session, rid)
    return rid


@pytest.fixture
def lange(session):
    return repo_svc.get_or_create_by_name(session, "Frank Lange collection").id


def _co(session, repo_id, catalog=None):
    return create_collection_object(
        session, collecting_event_id=None, catalog_number=catalog, repository_id=repo_id)


# ── collections ──────────────────────────────────────────────────────────────

def test_collection_needs_only_a_name(session):
    r = repo_svc.get_or_create_by_name(session, "  Frank Lange collection ")
    assert r.collection_full_name == "Frank Lange collection"
    assert r.collection_code is None and r.institution_code is None
    assert repo_svc.display_label(r) == "Frank Lange collection"
    # idempotent: the name is the identity
    assert repo_svc.get_or_create_by_name(session, "Frank Lange collection").id == r.id


def test_several_codeless_collections_coexist(session):
    a = repo_svc.get_or_create_by_name(session, "Collection A")
    b = repo_svc.get_or_create_by_name(session, "Collection B")
    assert a.id != b.id and a.collection_code is None and b.collection_code is None


def test_blank_collection_name_refused(session):
    with pytest.raises(ValueError):
        repo_svc.get_or_create_by_name(session, "   ")


def test_duplicate_collection_name_refused(session, lange):
    with pytest.raises(ValueError, match="already exists"):
        repo_svc.create_repository(
            session, collection_full_name="Frank Lange collection", collection_code="FL")


def test_duplicate_collection_code_refused(session):
    ensure_repo(session, "DUP72")
    with pytest.raises(ValueError, match="already used"):
        repo_svc.create_repository(
            session, collection_full_name="Another one", collection_code="DUP72")


def test_display_label_with_code(session):
    r = repo_svc.create_repository(
        session, collection_full_name="Jane Doe collection", collection_code="JD72")
    assert repo_svc.display_label(r) == "JD72 — Jane Doe collection"


def test_name_map_skips_codeless_collections(session, lange):
    assert None not in repo_svc.name_map(session)


def test_codeless_collection_cannot_be_default(session, lange):
    with pytest.raises(ValueError, match="no collection code"):
        repo_svc.set_default(session, lange)


def test_codeless_default_rejected_at_db_level(session, lange):
    with pytest.raises(IntegrityError):
        session.execute(text("UPDATE repository SET is_default = 1 WHERE id = :i"),
                        {"i": lange})


def test_default_collection_cannot_lose_its_code(session, home):
    with pytest.raises(ValueError, match="needs a collection code"):
        repo_svc.update_repository(
            session, home, collection_full_name="HOME72", collection_code=None)


# ── specimens without a catalog number ───────────────────────────────────────

def test_numberless_specimen_in_foreign_collection(session, lange):
    co = _co(session, lange)
    assert co.catalog_number is None
    assert id_svc.catalog_label(co.catalog_number) == "no number"


def test_blank_catalog_number_is_stored_as_null(session, lange):
    assert _co(session, lange, "   ").catalog_number is None


def test_many_numberless_specimens_share_a_collection(session, lange):
    a, b = _co(session, lange), _co(session, lange)
    assert a.id != b.id


def test_filled_number_stays_unique_within_a_collection(session, lange):
    _co(session, lange, "FL-1")
    with pytest.raises(ValueError, match="already used"):
        _co(session, lange, "FL-1")


def test_own_collection_requires_a_catalog_number(session, home):
    with pytest.raises(ValueError, match="own collection"):
        _co(session, home)


def test_own_collection_rule_holds_against_raw_sql(session, home):
    with pytest.raises(IntegrityError, match="must have a catalogNumber"):
        session.execute(text(
            "INSERT INTO collection_object (repository_id, created_at, updated_at) "
            "VALUES (:r, 'x', 'x')"), {"r": home})


def test_numberless_specimen_cannot_be_rehomed_into_own_collection(session, home, lange):
    co = _co(session, lange)
    with pytest.raises(ValueError, match="own collection"):
        update_collection_object(session, co.id, repository_id=home)


def test_rehoming_numberless_into_own_collection_blocked_at_db_level(session, home, lange):
    co = _co(session, lange)
    with pytest.raises(IntegrityError, match="must have a catalogNumber"):
        session.execute(text("UPDATE collection_object SET repository_id = :r WHERE id = :i"),
                        {"r": home, "i": co.id})


def test_collection_with_numberless_specimens_cannot_become_default(session):
    rid = ensure_repo(session, "OTHER72")
    _co(session, rid)
    with pytest.raises(ValueError, match="without a catalog number"):
        repo_svc.set_default(session, rid)


def test_that_default_switch_is_blocked_at_db_level(session):
    rid = ensure_repo(session, "OTHER72")
    _co(session, rid)
    session.flush()
    with pytest.raises(IntegrityError, match="cannot be the default"):
        session.execute(text("UPDATE repository SET is_default = 1 WHERE id = :i"),
                        {"i": rid})


def test_catalog_number_can_be_filled_once_then_is_immutable(session, lange):
    co = _co(session, lange)
    update_collection_object(session, co.id, catalog_number="FL-77")
    assert co.catalog_number == "FL-77"
    update_collection_object(session, co.id, catalog_number="FL-78")
    assert co.catalog_number == "FL-77"
    update_collection_object(session, co.id, catalog_number="")
    assert co.catalog_number == "FL-77"


def test_filling_a_number_already_used_there_is_refused(session, lange):
    _co(session, lange, "FL-1")
    co = _co(session, lange)
    with pytest.raises(ValueError, match="already used"):
        update_collection_object(session, co.id, catalog_number="FL-1")


# ── transfers out of the own collection ──────────────────────────────────────

def test_transfer_keeps_the_catalog_number_and_never_reuses_it(session, home, lange):
    _batch, codes = id_svc.reserve_sequential_codes(session, "HOME72", 1)
    co = _co(session, home, codes[0])
    id_svc.assign_code(session, codes[0], co.id)

    update_collection_object(session, co.id, repository_id=lange)   # give it away
    assert co.repository_id == lange
    assert co.catalog_number == codes[0]

    _batch, more = id_svc.reserve_sequential_codes(session, "HOME72", 1)
    assert more[0] != codes[0]
    assert int(more[0].split("-")[1]) == int(codes[0].split("-")[1]) + 1


# ── export ───────────────────────────────────────────────────────────────────

def test_numberless_specimen_is_not_exported(session, lange):
    decision = export_decision(_co(session, lange))
    assert not decision.eligible
    assert len(decision.identity_reasons) == 1
    assert "no catalog number" in decision.identity_reasons[0]
    assert decision.identity_reasons[0] in decision.reasons
    assert decision.privacy_reasons == ()


def test_numbered_specimen_has_no_identity_reason(session, lange):
    assert export_decision(_co(session, lange, "FL-9")).identity_reasons == ()


# ── batch tools ──────────────────────────────────────────────────────────────

def test_batch_move_into_own_collection_refuses_numberless(session, home, lange):
    numbered, numberless = _co(session, lange, "FL-5"), _co(session, lange)
    with pytest.raises(ValueError, match="no catalog number"):
        batch.apply_repository(
            session, source_repository_id=lange,
            co_ids=[numbered.id, numberless.id], target_repository_id=home)
    assert numbered.repository_id == lange          # nothing moved


def test_batch_refetch_by_id_reaches_numberless_specimens(session, lange):
    a, b = _co(session, lange), _co(session, lange, "FL-6")
    got = batch.fetch_by_ids(session, repository_id=lange, co_ids=[a.id, b.id])
    assert {m.co_id for m in got} == {a.id, b.id}
    assert {m.catalog for m in got} == {None, "FL-6"}


def test_batch_refetch_stays_collection_scoped(session, home, lange):
    mine = _co(session, home, "HOME72-90001")
    assert batch.fetch_by_ids(session, repository_id=lange, co_ids=[mine.id]) == []


# ── display ──────────────────────────────────────────────────────────────────

def test_summary_row_says_no_number_and_why_it_is_not_exported():
    import app.ui.record_summary as rs
    assert 'not exported to TaxonWorks' in rs._catalog_html(None)
    assert 'no number' in rs._catalog_html("FL · no number")
    assert 'title=' not in rs._catalog_html("JJPC-00001")
