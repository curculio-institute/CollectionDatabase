"""Media comparison against TaxonWorks Depictions (#149 step 1.6).

Pure-computation half only (`compare_media`, `specimens_on_tw`, `stage_for_upload`) —
mirrors `test_tw_compare_repository.py`'s split: no network, no live TaxonWorks call.
The fetch functions (`fetch_depictions_by_object` / `fetch_image_fingerprints`) were
verified by hand against the live sandbox (see the module docstring); nothing here
re-tests the HTTP layer.
"""
import asyncio
import hashlib

from sqlalchemy.orm import sessionmaker

import app.config as config
import app.services.media as media_svc
import app.services.specimens as spec_svc
import app.services.tw_media_compare as twm
from app.services.tw_compare import build_catalog_index
from tests.helpers import ensure_repo

_TYPE = "Identifier::Local::CatalogNumber"


def _index(*rows: tuple[str, int]):
    return build_catalog_index([
        {
            "type": _TYPE,
            "identifier_object_type": "CollectionObject",
            "cached": cat,
            "identifier": cat.split("-", 1)[-1],
            "identifier_object_id": obj_id,
            "namespace_id": 1,
            "namespace": {"short_name": "JJPC"},
        }
        for cat, obj_id in rows
    ])


def _specimen(session, catalog_number: str, repo_id: int):
    co = spec_svc.create_collection_object(
        session, collecting_event_id=None, catalog_number=catalog_number,
        repository_id=repo_id,
    )
    session.flush()
    return co


def _md5(data: bytes) -> str:
    return hashlib.md5(data).hexdigest()


def test_specimens_on_tw_scoped_to_repository_and_index(session):
    repo = ensure_repo(session, "JJPC")
    other = ensure_repo(session, "OTHER")
    co1 = _specimen(session, "JJPC-00001", repo)
    _specimen(session, "JJPC-00002", repo)              # not on TW (no index entry)
    _specimen(session, "OTHER-00001", other)             # different collection

    idx = _index(("JJPC-00001", 501))
    out, ambiguous = twm.specimens_on_tw(session, repository_id=repo, index=idx)
    assert out == [("JJPC-00001", co1.id, 501)]
    assert ambiguous == ()


def test_specimens_on_tw_excludes_ambiguous_catalog_numbers(session):
    """#157: a catalog number the index reports under more than one TaxonWorks record
    (a cross-namespace duplicate) must not be silently resolved to the first entry —
    it is excluded from the compare and reported separately."""
    repo = ensure_repo(session, "JJPC")
    co1 = _specimen(session, "JJPC-00001", repo)
    co2 = _specimen(session, "JJPC-00002", repo)

    idx = build_catalog_index([
        {
            "type": _TYPE, "identifier_object_type": "CollectionObject",
            "cached": "JJPC-00001", "identifier": "00001",
            "identifier_object_id": obj_id, "namespace_id": ns,
            "namespace": {"short_name": short},
        }
        for obj_id, ns, short in [(501, 1, "JJPC"), (999, 2, "OTHER")]
    ] + [
        {
            "type": _TYPE, "identifier_object_type": "CollectionObject",
            "cached": "JJPC-00002", "identifier": "00002",
            "identifier_object_id": 502, "namespace_id": 1,
            "namespace": {"short_name": "JJPC"},
        },
    ])
    out, ambiguous = twm.specimens_on_tw(session, repository_id=repo, index=idx)
    assert out == [("JJPC-00002", co2.id, 502)]
    assert ambiguous == ("JJPC-00001",)


def test_compare_media_matches_by_fingerprint(media_env):
    session, _store = media_env
    repo = ensure_repo(session, "JJPC")
    co = _specimen(session, "JJPC-00001", repo)
    data = b"same bytes"
    media_svc.add_attachment(session, target_kind="collection_object", target_id=co.id,
                             data=data, filename="photo.jpg")
    session.flush()

    specimens = [("JJPC-00001", co.id, 501)]
    depictions = {501: [9001]}
    fingerprints = {9001: _md5(data)}
    result = twm.compare_media(session, specimens, depictions, fingerprints)

    assert result.checked_count == 1
    assert result.with_local_media_count == 1
    assert result.matched_count == 1
    assert result.gaps == ()
    assert result.tw_only_count == 0


def test_compare_media_reports_local_only_gap(media_env):
    session, _store = media_env
    repo = ensure_repo(session, "JJPC")
    co = _specimen(session, "JJPC-00001", repo)
    media_svc.add_attachment(session, target_kind="collection_object", target_id=co.id,
                             data=b"not on tw", filename="new_photo.jpg")
    session.flush()

    specimens = [("JJPC-00001", co.id, 501)]
    result = twm.compare_media(session, specimens, depictions_by_object={}, fingerprints_by_image={})

    assert result.matched_count == 0
    assert len(result.gaps) == 1
    gap = result.gaps[0]
    assert gap.catalog_number == "JJPC-00001"
    assert gap.tw_object_id == 501
    assert len(gap.local_only) == 1
    assert gap.local_only[0].original_filename == "new_photo.jpg"
    assert gap.local_only[0].missing_on_disk is False


def test_compare_media_flags_files_missing_on_disk(media_env):
    """#165: a file whose bytes are gone from disk (moved/deleted outside the app) must
    be distinguishable from an ordinary 'not yet uploaded' gap — the two need
    different remediation (fix the store vs. drag-and-drop upload).

    Only a legacy row (md5_fingerprint not yet cached, #154) actually consults the
    disk in ensure_md5 — a freshly-stored row's fingerprint is already cached at
    store time, so it must be nulled out here to simulate a pre-migration-0070 row."""
    session, _store = media_env
    repo = ensure_repo(session, "JJPC")
    co = _specimen(session, "JJPC-00001", repo)
    media_svc.add_attachment(session, target_kind="collection_object", target_id=co.id,
                             data=b"photo bytes", filename="weevil.jpg")
    session.flush()
    att = media_svc.list_attachments(
        session, target_kind="collection_object", target_id=co.id)[0]
    att.media.md5_fingerprint = None
    session.flush()
    media_svc.abs_path(att.media.relative_path).unlink()   # simulate lost bytes

    specimens = [("JJPC-00001", co.id, 501)]
    result = twm.compare_media(session, specimens, depictions_by_object={}, fingerprints_by_image={})

    assert len(result.gaps) == 1
    assert result.gaps[0].local_only[0].missing_on_disk is True


def test_compare_media_ignores_non_image_categories(media_env):
    """#156: TaxonWorks Depictions/Images can only ever be images — a Sound/Document/
    Sequence/Video/Other attachment can never match a TW image fingerprint, so it must
    not be reported as a gap the user is told to drag into TaxonWorks."""
    session, _store = media_env
    repo = ensure_repo(session, "JJPC")
    co = _specimen(session, "JJPC-00001", repo)
    media_svc.add_attachment(session, target_kind="collection_object", target_id=co.id,
                             data=b"%PDF-1.4 not an image", filename="notes.pdf")
    session.flush()
    assert media_svc.list_attachments(
        session, target_kind="collection_object", target_id=co.id)[0].media.category \
        == "Document"

    specimens = [("JJPC-00001", co.id, 501)]
    result = twm.compare_media(session, specimens, depictions_by_object={}, fingerprints_by_image={})

    assert result.with_local_media_count == 0
    assert result.gaps == ()


def test_compare_media_tw_only_is_informational_not_a_gap(media_env):
    """TaxonWorks has a depiction with no local match at all — reported in the count,
    never presented as something to upload (there is nothing local to push)."""
    session, _store = media_env
    repo = ensure_repo(session, "JJPC")
    co = _specimen(session, "JJPC-00001", repo)
    media_svc.add_attachment(session, target_kind="collection_object", target_id=co.id,
                             data=b"local file", filename="a.jpg")
    session.flush()

    specimens = [("JJPC-00001", co.id, 501)]
    depictions = {501: [9001]}
    fingerprints = {9001: "deadbeef" * 4}      # unrelated fingerprint — no local match
    result = twm.compare_media(session, specimens, depictions, fingerprints)

    assert result.matched_count == 0
    assert result.tw_only_count == 1
    assert len(result.gaps) == 1               # the local file is still a gap (not on TW)


def test_compare_media_specimen_with_no_local_media_is_not_a_gap(media_env):
    session, _store = media_env
    repo = ensure_repo(session, "JJPC")
    co = _specimen(session, "JJPC-00001", repo)   # no media attached at all

    specimens = [("JJPC-00001", co.id, 501)]
    depictions = {501: [9001]}
    fingerprints = {9001: "cafebabe" * 4}
    result = twm.compare_media(session, specimens, depictions, fingerprints)

    assert result.with_local_media_count == 0
    assert result.gaps == ()                     # nothing local to upload — not our gap
    assert result.tw_only_count == 1              # but still counted, never silently dropped


def test_compare_media_tw_only_dedupes_by_fingerprint_with_no_local_media(media_env):
    """#158: two TaxonWorks Depictions sharing one fingerprint (e.g. a re-uploaded
    duplicate) must count as 1 tw_only, the same as it would once any local media is
    attached — not the raw, undeduplicated image count."""
    session, _store = media_env
    repo = ensure_repo(session, "JJPC")
    co = _specimen(session, "JJPC-00001", repo)   # no local media at all

    specimens = [("JJPC-00001", co.id, 501)]
    depictions = {501: [9001, 9002]}               # two TW images...
    fingerprints = {9001: "cafebabe" * 4, 9002: "cafebabe" * 4}   # ...same fingerprint
    result = twm.compare_media(session, specimens, depictions, fingerprints)

    assert result.tw_only_count == 1


def test_stage_for_upload_copies_files_under_original_names(media_env, tmp_path):
    session, _store = media_env
    repo = ensure_repo(session, "JJPC")
    co = _specimen(session, "JJPC-00001", repo)
    media_svc.add_attachment(session, target_kind="collection_object", target_id=co.id,
                             data=b"photo bytes", filename="weevil.jpg")
    session.flush()

    specimens = [("JJPC-00001", co.id, 501)]
    result = twm.compare_media(session, specimens, {}, {})
    gap = result.gaps[0]

    staged_dir, skipped = twm.stage_for_upload(gap)
    try:
        assert skipped == ()
        files = list(staged_dir.iterdir())
        assert len(files) == 1
        assert files[0].name == "weevil.jpg"
        assert files[0].read_bytes() == b"photo bytes"
        # the canonical store is untouched — this was a copy, not a move
        assert media_svc.abs_path(gap.local_only[0].relative_path).is_file()
    finally:
        import shutil
        shutil.rmtree(staged_dir, ignore_errors=True)


def test_stage_for_upload_reports_files_missing_on_disk(media_env):
    """#159: a gap file whose bytes are gone from disk (moved/deleted outside the app)
    must be reported as skipped, never silently omitted from the staged folder."""
    session, _store = media_env
    repo = ensure_repo(session, "JJPC")
    co = _specimen(session, "JJPC-00001", repo)
    media_svc.add_attachment(session, target_kind="collection_object", target_id=co.id,
                             data=b"photo bytes", filename="weevil.jpg")
    session.flush()

    specimens = [("JJPC-00001", co.id, 501)]
    result = twm.compare_media(session, specimens, {}, {})
    gap = result.gaps[0]
    media_svc.abs_path(gap.local_only[0].relative_path).unlink()   # simulate lost bytes

    staged_dir, skipped = twm.stage_for_upload(gap)
    try:
        assert list(staged_dir.iterdir()) == []
        assert len(skipped) == 1
        assert skipped[0].original_filename == "weevil.jpg"
    finally:
        import shutil
        shutil.rmtree(staged_dir, ignore_errors=True)


def test_run_media_compare_offloads_hashing_to_a_thread(media_env, monkeypatch):
    """#155: `compare_media` (which reads files off disk via `ensure_md5`) must run off
    the event loop. Runs the real `run_media_compare` coroutine end to end — including a
    genuine `asyncio.to_thread` hop reusing the caller's ORM session from a worker thread
    — with the network fetches monkeypatched out (no live TaxonWorks call)."""
    session, _store = media_env
    repo = ensure_repo(session, "JJPC")
    co = _specimen(session, "JJPC-00001", repo)
    data = b"same bytes"
    media_svc.add_attachment(session, target_kind="collection_object", target_id=co.id,
                             data=data, filename="photo.jpg")
    session.flush()

    idx = _index(("JJPC-00001", 501))

    async def _fake_depictions(ids):
        assert ids == [501]
        return {501: [9001]}

    async def _fake_fingerprints(ids):
        assert ids == [9001]
        return {9001: _md5(data)}

    monkeypatch.setattr(twm, "fetch_depictions_by_object", _fake_depictions)
    monkeypatch.setattr(twm, "fetch_image_fingerprints", _fake_fingerprints)

    result = asyncio.run(
        twm.run_media_compare(session, repository_id=repo, index=idx))

    assert result.matched_count == 1
    assert result.gaps == ()
