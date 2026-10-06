from __future__ import annotations
from typing import Optional
from sqlalchemy import CheckConstraint, Integer, String, ForeignKey
from sqlalchemy.orm import Mapped, mapped_column, relationship
from .base import Base, TimestampMixin
from .taxon_determination import _QUAL_CHECK_SQL, _NO_INTERVAL_SQL

ARC_CHECK_SQL = (
    "(label_type = 'data'"
    "  AND collection_object_id IS NOT NULL"
    "  AND label_code_id IS NULL AND taxon_id IS NULL)"
    " OR "
    "(label_type = 'determination' AND label_code_id IS NULL"
    "  AND ((collection_object_id IS NOT NULL AND taxon_id IS NULL)"
    "    OR (collection_object_id IS NULL AND taxon_id IS NOT NULL)))"
    " OR "
    "(label_type = 'identifier'"
    "  AND label_code_id IS NOT NULL"
    "  AND collection_object_id IS NULL AND taxon_id IS NULL)"
)

# The plain-label content columns exist only on a plain row, which in turn is never
# pinned to a specimen's taxon_determination.
PLAIN_FIELDS_CHECK_SQL = (
    "(taxon_id IS NOT NULL AND taxon_determination_id IS NULL)"
    " OR "
    "(taxon_id IS NULL AND identified_by_id IS NULL"
    '  AND "dwc:dateIdentified" IS NULL AND "dwc:typeStatus" IS NULL'
    '  AND "dwc:identificationQualifier" IS NULL AND "dwc:sex" IS NULL)'
)


class PrintQueue(Base, TimestampMixin):
    __tablename__ = "print_queue"

    id:         Mapped[int] = mapped_column(Integer, primary_key=True)
    label_type: Mapped[str] = mapped_column(String, nullable=False)

    # Grouping for the printed sheet: rows enqueued in one operation share a
    # print_group_id and a `source` header (e.g. "Mounting Session"). Both
    # nullable — legacy rows render as one fallback group. See migration 0028.
    print_group_id: Mapped[Optional[int]] = mapped_column(Integer, nullable=True)
    source:         Mapped[Optional[str]] = mapped_column(String, nullable=True)

    # Print-only label text the user typed in the queue to fit the tiny label
    # (abbreviate / add). Overrides the auto-rendered text at print time WITHOUT
    # touching the record (record stays master — edit it in Records). Applies to
    # 'data' / 'determination' rows; never set on 'identifier' rows. See mig 0034.
    text_override: Mapped[Optional[str]] = mapped_column(String, nullable=True)

    collection_object_id: Mapped[Optional[int]] = mapped_column(
        Integer, ForeignKey("collection_object.id", ondelete="CASCADE"), nullable=True
    )
    label_code_id: Mapped[Optional[int]] = mapped_column(
        Integer, ForeignKey("label_code.id", ondelete="CASCADE"), nullable=True
    )

    # Which identification a 'determination' row prints. NULL → the specimen's
    # *current* determination (every create path; unchanged). Set only by the Records
    # reprint (#38), which reproduces EVERY identification a specimen carries — each
    # as its own row pinned to a specific taxon_determination. FK ON DELETE CASCADE
    # (migration 0066): deleting an identification drops any queued reprint of it.
    taxon_determination_id: Mapped[Optional[int]] = mapped_column(
        Integer, ForeignKey("taxon_determination.id", ondelete="CASCADE"), nullable=True
    )

    # ── Plain identification label (migration 0071) ──────────────────────────────
    # A 'determination' row queued from the Labels tab WITHOUT a specimen: there is no
    # record to derive it from, so the row carries the label's content itself. Set
    # together with taxon_id (the arc's third arm) and NULL on every other row
    # (ck_print_queue_plain_fields). The name is still read live from the taxon.
    taxon_id: Mapped[Optional[int]] = mapped_column(
        Integer, ForeignKey("taxon.id", ondelete="CASCADE"), nullable=True
    )
    identified_by_id: Mapped[Optional[int]] = mapped_column(
        Integer, ForeignKey("person.id", ondelete="RESTRICT"), nullable=True
    )
    date_identified: Mapped[Optional[str]] = mapped_column("dwc:dateIdentified", String, nullable=True)
    type_status: Mapped[Optional[str]] = mapped_column("dwc:typeStatus", String, nullable=True)
    identification_qualifier: Mapped[Optional[str]] = mapped_column("dwc:identificationQualifier", String, nullable=True)
    sex: Mapped[Optional[str]] = mapped_column("dwc:sex", String, nullable=True)

    collection_object  = relationship("CollectionObject")
    label_code         = relationship("LabelCode")
    taxon_determination = relationship("TaxonDetermination")
    taxon               = relationship("Taxon")
    identified_by_person = relationship("Person", foreign_keys=[identified_by_id])

    __table_args__ = (
        CheckConstraint(
            "label_type IN ('data', 'determination', 'identifier')",
            name="ck_print_queue_label_type",
        ),
        # Exactly one source per row: a specimen (data / determination), a taxon (a
        # plain determination label, 0071), or a label code (identifier).
        CheckConstraint(ARC_CHECK_SQL, name="ck_print_queue_exclusive_arc"),
        CheckConstraint(PLAIN_FIELDS_CHECK_SQL, name="ck_print_queue_plain_fields"),
        # Same two rules as taxon_determination (0058 / 0069), for the same reasons.
        CheckConstraint(_QUAL_CHECK_SQL, name="ck_print_queue_identification_qualifier"),
        CheckConstraint(_NO_INTERVAL_SQL, name="ck_print_queue_date_identified_no_interval"),
    )
