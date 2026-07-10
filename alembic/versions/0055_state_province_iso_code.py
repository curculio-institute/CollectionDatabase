"""state_province.iso_code — the ISO 3166-2 code of the first-order subdivision

The code is a property of the *state*, not of the event, so it lives once on the vocab
row rather than on every collecting_event (contrast `dwc:countryCode`, which predates the
vocab tables). The geocoder already reads it: Overpass tags the containing first-order
subdivision with `ISO3166-2` (`DE-BY`, `GR-J`, `CN-YN`), and that tag is what identifies
the state relation in the first place — it was simply discarded afterwards.

Why it earns a column: `label_text.format_country()` already degrades a long country name
to its ISO code so it fits an 18x7 mm label. `stateProvince` had no code to degrade to, so
`Baden-Wuerttemberg` (17 chars) went onto the label in full. Storing the code makes the
same rule available one tier down.

Nullable, and deliberately NOT unique. Existing rows have no code and stay valid (the code
is not required); a code is filled in the next time that state is geocoded. Uniqueness would
turn a legitimate save into a crash: a hand-typed `Bayern` row and a geocoded `Bavaria` row
both denote DE-BY, and blocking the second is worse than tolerating the duplicate — merging
the two vocab rows is exactly what the Vocabulary merge tool is for.

Native ADD COLUMN (no table rebuild), so the vocab table keeps its UNIQUE(name) and any FK
actions untouched (CLAUDE.md "Migration discipline — never lose constraints").

`sa.Text()`, not `sa.String()`: state_province is STRICT, and STRICT accepts only
TEXT/INTEGER/REAL/BLOB/ANY — a String emits VARCHAR and SQLite refuses the ADD COLUMN with
"unknown datatype".
"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa

revision: str = "0055"
down_revision: Union[str, None] = "0054"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.add_column("state_province", sa.Column("iso_code", sa.Text(), nullable=True))


def downgrade() -> None:
    op.drop_column("state_province", "iso_code")
