from __future__ import annotations
from sqlalchemy import Integer, String, UniqueConstraint
from sqlalchemy.orm import Mapped, mapped_column
from .base import Base, TimestampMixin


class Disposition(Base, TimestampMixin):
    """Controlled-vocabulary entry for a specimen's disposition / holding status
    (e.g. 'in collection', 'on loan', 'loaned to Jeffrey', 'in the drawer behind
    my bed'). An *open* single-name vocab referenced by collection_object.disposition_id;
    managed via app/services/vocab.py (edit / merge / delete like persons).

    Was a fixed CHECK-constrained TEXT column (`dwc:disposition`) — opened up to an
    editable vocab in migration 0048 (#76) so the user can record arbitrary holdings.
    DwC `disposition` is `[Not mapped]` by the TaxonWorks importer, so freeform values
    never reach TaxonWorks (resolved disposition.name → dwc:disposition at export only).
    """

    __tablename__ = "disposition"

    id:   Mapped[int] = mapped_column(Integer, primary_key=True)
    name: Mapped[str] = mapped_column(String, nullable=False)

    __table_args__ = (
        UniqueConstraint("name", name="uq_disposition_name"),
    )
