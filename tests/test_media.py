"""Media store + repository: content-addressing, de-dup, integrity, attach/detach (#48)."""
import importlib

import app.services.media as media_svc
from app.models import Media, MediaAttachment, CollectingEvent


def test_detect_category():
    assert media_svc.detect_category("image/jpeg", ".jpg") == "Image"
    assert media_svc.detect_category("audio/wav", ".wav") == "Sound"
    assert media_svc.detect_category("application/pdf", ".pdf") == "Document"
    assert media_svc.detect_category(None, ".fasta") == "Sequence"   # genetic data
    assert media_svc.detect_category(None, ".fastq") == "Sequence"
    assert media_svc.detect_category(None, ".xyz") == "Other"


def test_store_is_content_addressed_and_dedups(media_env):
    _, store = media_env
    m1 = media_svc.store_bytes(b">seq1\nACGT\n", "a.fasta")
    m2 = media_svc.store_bytes(b">seq1\nACGT\n", "renamed.fasta")  # same bytes
    assert m1["sha256"] == m2["sha256"]
    assert m1["relative_path"] == m2["relative_path"]
    assert m1["category"] == "Sequence"
    # one physical file under the sharded path
    files = list((store).rglob("*.fasta"))
    assert len(files) == 1
    assert media_svc.verify_integrity(m1["relative_path"], m1["sha256"]) is True
    assert media_svc.verify_integrity(m1["relative_path"], "0" * 64) is False


def test_store_bytes_computes_md5_eagerly(media_env):
    """#149 step 1.6: the join key against TaxonWorks' image_file_fingerprint (MD5,
    verified live — see tw_media_compare.py) is computed alongside sha256 at store
    time, not left for a later backfill."""
    import hashlib
    data = b">seq1\nACGT\n"
    meta = media_svc.store_bytes(data, "a.fasta")
    assert meta["md5_fingerprint"] == hashlib.md5(data).hexdigest()


def test_ensure_md5_backfills_legacy_rows(media_env):
    """A row stored before migration 0070 has md5_fingerprint=None; ensure_md5 computes
    it from the on-disk bytes once and caches it on the row (the user's choice: cache,
    not recompute on every compare)."""
    import hashlib
    s, _ = media_env
    meta = media_svc.store_bytes(b"legacy bytes", "legacy.txt")
    media, _created = media_svc._get_or_create_media(s, meta)
    media.md5_fingerprint = None                  # simulate a pre-migration row
    s.flush()

    got = media_svc.ensure_md5(s, media)
    assert got == hashlib.md5(b"legacy bytes").hexdigest()
    assert media.md5_fingerprint == got            # cached on the row

    # A second call must not recompute (nothing on disk changed, but prove idempotence).
    assert media_svc.ensure_md5(s, media) == got


def test_attach_list_and_delete_cleans_up(media_env):
    s, store = media_env
    ev = CollectingEvent(locality="Germany")
    s.add(ev); s.flush()

    att = media_svc.add_attachment(
        s, target_kind="collecting_event", target_id=ev.id,
        data=b"\x89PNG fake", filename="habitat.png", caption="habitat",
    )
    s.flush()
    rows = media_svc.list_attachments(s, target_kind="collecting_event", target_id=ev.id)
    assert len(rows) == 1 and rows[0].caption == "habitat"
    rel = rows[0].media.relative_path
    assert media_svc.abs_path(rel).is_file()

    # Deleting the only attachment removes the orphaned media row and REPORTS the bytes;
    # unlinking is the caller's job, after the commit (#63).
    orphaned = media_svc.delete_attachment(s, att.id)
    s.flush()
    assert media_svc.list_attachments(s, target_kind="collecting_event", target_id=ev.id) == []
    assert s.query(Media).count() == 0
    assert orphaned == rel
    assert media_svc.abs_path(rel).is_file()      # still there — not yet committed
    media_svc.delete_stored_file(orphaned)
    assert not media_svc.abs_path(rel).is_file()


def test_update_media_rights_holder_and_license(media_env):
    s, _ = media_env
    import app.services.person_defaults as pd_svc
    from app.models import Person, Media

    p = Person(full_name="Jane Photographer"); s.add(p); s.flush()
    ev = CollectingEvent(locality="Germany"); s.add(ev); s.flush()
    att = media_svc.add_attachment(
        s, target_kind="collecting_event", target_id=ev.id,
        data=b"img-bytes", filename="x.jpg",
    )
    s.flush()
    media_svc.update_media(s, att.media_id, license="CC BY", rights_holder_id=p.id)
    s.flush()
    m = s.get(Media, att.media_id)
    assert m.license == "CC BY"
    assert m.rights_holder_id == p.id

    # person_defaults now carries a third (rightsHolder) default, resolved by name
    pd_svc.set_defaults(s, identified_by_id=None, recorded_by_id=None, rights_holder_id=p.id)
    s.flush()
    assert pd_svc.get_defaults(s) == (None, None, "Jane Photographer")


def test_attach_stored_staged_commit(media_env):
    """Simulates the Digitize staged flow: bytes stored before the record exists, then
    committed onto the saved specimen via attach_stored (carrying metadata)."""
    s, _ = media_env
    from app.models import Person, CollectionObject
    from tests.helpers import ensure_repo
    p = Person(full_name="Cam Photographer"); s.add(p); s.flush()
    co = CollectionObject(catalog_number="JJPC-99999", repository_id=ensure_repo(s, "JJPC"))
    s.add(co); s.flush()

    meta = media_svc.store_bytes(b"\x89PNG staged", "field.png")  # stored while "staging"
    assert media_svc.count_attachments(s, target_kind="collection_object", target_id=co.id) == 0
    media_svc.attach_stored(
        s, target_kind="collection_object", target_id=co.id, meta=meta,
        caption="in the field", license="CC BY", rights_holder_id=p.id, is_primary=1,
    )
    s.flush()
    rows = media_svc.list_attachments(s, target_kind="collection_object", target_id=co.id)
    assert len(rows) == 1
    assert rows[0].caption == "in the field" and rows[0].is_primary == 1
    assert rows[0].media.license == "CC BY" and rows[0].media.rights_holder_id == p.id
    assert media_svc.count_attachments(s, target_kind="collection_object", target_id=co.id) == 1


def test_shared_media_not_deleted_while_referenced(media_env):
    s, _ = media_env
    e1 = CollectingEvent(locality="Germany"); e2 = CollectingEvent(locality="Austria")
    s.add_all([e1, e2]); s.flush()
    data = b"shared-bytes"
    a1 = media_svc.add_attachment(s, target_kind="collecting_event", target_id=e1.id, data=data, filename="x.jpg")
    a2 = media_svc.add_attachment(s, target_kind="collecting_event", target_id=e2.id, data=data, filename="x.jpg")
    s.flush()
    assert s.query(Media).count() == 1          # de-duped to one asset
    rel = s.get(Media, a1.media_id).relative_path
    orphaned = media_svc.delete_attachment(s, a1.id); s.flush()
    assert orphaned is None                     # content still referenced by a2
    assert s.query(Media).count() == 1
    assert media_svc.abs_path(rel).is_file()


# ── #63: attaching must not rewrite a shared asset's metadata ────────────────────
# The store is content-addressed, so byte-identical content resolves to ONE media row.
# licence / rightsHolder / category describe the photograph, not the record it hangs off.

def test_attaching_shared_content_does_not_clobber_its_metadata(media_env):
    s, _ = media_env
    e1 = CollectingEvent(locality="Staffelsee")
    e2 = CollectingEvent(locality="Staffelsee, 200 m east")
    s.add_all([e1, e2]); s.flush()

    meta = media_svc.store_bytes(b"one photograph of the shore", "habitat.jpg")
    media_svc.attach_stored(s, target_kind="collecting_event", target_id=e1.id, meta=meta,
                            category="Image", license="CC-BY 4.0")
    s.flush()

    # the SAME photo on a second event — a legitimate case (one place, two events)
    media_svc.attach_stored(s, target_kind="collecting_event", target_id=e2.id, meta=meta,
                            category="Document", license="CC0", caption="from the ridge")
    s.flush()

    assert s.query(Media).count() == 1               # still one asset, still de-duped
    asset = s.query(Media).one()
    assert asset.license == "CC-BY 4.0"              # NOT rewritten by the second attach
    assert asset.category == "Image"
    # per-usage metadata still lands on the attachment
    atts = media_svc.list_attachments(s, target_kind="collecting_event", target_id=e2.id)
    assert atts[0].caption == "from the ridge"


def test_new_content_still_gets_its_metadata(media_env):
    """The guard must not stop a *newly created* asset from receiving its metadata."""
    s, _ = media_env
    ev = CollectingEvent(locality="X")
    s.add(ev); s.flush()
    meta = media_svc.store_bytes(b"a brand new photo", "new.jpg")
    media_svc.attach_stored(s, target_kind="collecting_event", target_id=ev.id, meta=meta,
                            category="Document", license="CC0")
    s.flush()
    asset = s.query(Media).one()
    assert asset.license == "CC0" and asset.category == "Document"


# ── #63: a rolled-back delete must not destroy the bytes ─────────────────────────

def test_rollback_after_delete_leaves_the_file_intact(media_env):
    """delete_attachment reports the orphaned path; it does not unlink inside the tx.

    If it unlinked eagerly, this rollback would restore the media row while its bytes were
    already gone — a row pointing at nothing, unrecoverable.
    """
    s, _ = media_env
    ev = CollectingEvent(locality="X")
    s.add(ev); s.flush()
    att = media_svc.add_attachment(s, target_kind="collecting_event", target_id=ev.id,
                                   data=b"precious pixels", filename="p.jpg")
    s.flush()
    rel = s.get(Media, att.media_id).relative_path
    sp = s.begin_nested()                       # stand-in for the outer transaction

    orphaned = media_svc.delete_attachment(s, att.id)
    assert orphaned == rel
    sp.rollback()                               # the commit "fails"

    assert s.query(Media).count() == 1          # row is back...
    assert media_svc.abs_path(rel).is_file()    # ...and its bytes were never touched
