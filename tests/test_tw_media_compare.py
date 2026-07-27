"""Media comparison against TaxonWorks Depictions (#149 step 1.6).

Pure-computation half only (`compare_media`, `specimens_on_tw`, `stage_for_upload`) —
mirrors `test_tw_compare_repository.py`'s split: no network, no live TaxonWorks call.
The fetch functions (`fetch_depictions_by_object` / `fetch_image_fingerprints`) were
verified by hand against the live sandbox (see the module docstring); nothing here
re-tests the HTTP layer.
"""
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
    out = twm.specimens_on_tw(session, repository_id=repo, index=idx)
    assert out == [("JJPC-00001", co1.id, 501)]


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

    staged_dir = twm.stage_for_upload(gap)
    try:
        files = list(staged_dir.iterdir())
        assert len(files) == 1
        assert files[0].name == "weevil.jpg"
        assert files[0].read_bytes() == b"photo bytes"
        # the canonical store is untouched — this was a copy, not a move
        assert media_svc.abs_path(gap.local_only[0].relative_path).is_file()
    finally:
        import shutil
        shutil.rmtree(staged_dir, ignore_errors=True)
