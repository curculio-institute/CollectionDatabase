"""Media store + repository: content-addressing, de-dup, integrity, attach/detach (#48)."""
import importlib

import pytest
from sqlalchemy.orm import sessionmaker

import app.config as config
import app.services.media as media_svc
from app.models import Media, MediaAttachment, CollectingEvent


@pytest.fixture
def media_env(engine, tmp_path, monkeypatch):
    """Point the media store at a temp dir and yield a session bound to the migrated DB."""
    monkeypatch.setattr(config, "_instance", config.AppConfig(media_dir=str(tmp_path / "media")))
    SessionLocal = sessionmaker(engine)
    with SessionLocal() as s:
        yield s, tmp_path / "media"


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


def test_attach_list_and_delete_cleans_up(media_env):
    s, store = media_env
    ev = CollectingEvent(country="Germany")
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

    # deleting the only attachment removes the orphaned media row + bytes
    media_svc.delete_attachment(s, att.id)
    s.flush()
    assert media_svc.list_attachments(s, target_kind="collecting_event", target_id=ev.id) == []
    assert s.query(Media).count() == 0
    assert not media_svc.abs_path(rel).is_file()


def test_shared_media_not_deleted_while_referenced(media_env):
    s, _ = media_env
    e1 = CollectingEvent(country="Germany"); e2 = CollectingEvent(country="Austria")
    s.add_all([e1, e2]); s.flush()
    data = b"shared-bytes"
    a1 = media_svc.add_attachment(s, target_kind="collecting_event", target_id=e1.id, data=data, filename="x.jpg")
    a2 = media_svc.add_attachment(s, target_kind="collecting_event", target_id=e2.id, data=data, filename="x.jpg")
    s.flush()
    assert s.query(Media).count() == 1          # de-duped to one asset
    rel = s.get(Media, a1.media_id).relative_path
    media_svc.delete_attachment(s, a1.id); s.flush()
    assert s.query(Media).count() == 1          # still referenced by a2
    assert media_svc.abs_path(rel).is_file()
