"""media.md5_fingerprint — cached MD5, the join key against TaxonWorks Images (#149 step 1.6)

TaxonWorks' `/images` endpoint reports `image_file_fingerprint` — verified live (2026-07-26,
against sandbox.taxonworks.org: downloaded an existing Image's "original" bytes and hashed
them locally) to be an **MD5** of the file, despite the local OpenAPI reference claiming
SHA256 — trust the measurement, not the doc. Content-addressed, so it survives TaxonWorks
renaming the file; our own store is SHA-256 (`media.sha256`, the de-dup key), a different
algorithm, so there is no shared column to compare on without computing MD5 too.

Cached rather than computed fresh on every compare (user's choice): computed eagerly at
store time going forward (`app/services/media.py`), and lazily backfilled — computed once
and written back — for pre-existing rows the first time a compare needs them
(`media.ensure_md5`), so this migration does not need to hash every file in the store.

Native ADD COLUMN (no table rebuild -> STRICT typing + existing CHECK/UNIQUE/FK on media
are preserved; CLAUDE.md migration discipline).

Revision ID: 0070
Revises: 0069
"""
from typing import Union

from alembic import op

revision: str = "0070"
down_revision: Union[str, None] = "0069"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.execute("ALTER TABLE media ADD COLUMN md5_fingerprint TEXT")


def downgrade() -> None:
    op.execute("ALTER TABLE media DROP COLUMN md5_fingerprint")
