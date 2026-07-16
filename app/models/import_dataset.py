from __future__ import annotations
from sqlalchemy import (
    CheckConstraint, ForeignKey, Integer, String, Text, UniqueConstraint,
)
from sqlalchemy.orm import Mapped, mapped_column, relationship
from .base import Base, TimestampMixin


# The kinds of source a staged import can be. Only 'taxon' (a name checklist) is
# implemented; 'occurrence' is reserved for the second core (TaxonWorks splits the
# two the same way — checklist.rb vs occurrences.rb).
IMPORT_KINDS = ("taxon",)

# Dataset lifecycle. Staging writes nothing to the real tables; import does, in a
# resumable loop; completed = the cursor has passed the last ready record.
DATASET_STATUSES = ("staged", "importing", "completed")

# Per-record status, the heart of the two-phase model (mirrors TW's DatasetRecord):
#   ready    — stages cleanly, importable as-is
#   blocked  — cannot import until something is resolved (reason in error_message);
#              TW calls this NotReady. This is where an unresolvable parent surfaces.
#   imported — a taxon row was created/matched (taxon_id set)
#   errored  — the import attempt raised (reason in error_message); retryable
RECORD_STATUSES = ("ready", "blocked", "imported", "errored")


class ImportDataset(Base, TimestampMixin):
    """An uploaded file staged for wholesale import (#39), modelled on TaxonWorks'
    ImportDataset. Durable and resumable: the whole file becomes one dataset with a
    per-row record and status, so a large import can be inspected before it writes,
    resumed after a restart, and de-duplicated against what is already imported.

    Nomenclatural code is a **dataset-level default** the user picks at upload — a
    checklist is one code (an ICZN beetle list, an ICN plant list). A row may still
    override it with its own `nomenclaturalCode` column. It is never guessed from the
    names (CLAUDE.md §2)."""

    __tablename__ = "import_dataset"

    id:              Mapped[int] = mapped_column(Integer, primary_key=True)
    kind:            Mapped[str] = mapped_column(String, nullable=False)
    name:            Mapped[str] = mapped_column(String, nullable=False)
    source_filename: Mapped[str | None] = mapped_column(String)
    # The dataset-level default nomenclatural code (a per-row column may override it).
    nomenclatural_code: Mapped[str | None] = mapped_column(String)
    status:          Mapped[str] = mapped_column(
        String, nullable=False, server_default="staged")
    # Resume cursor: the next record row_index to import. TW persists import_start_id
    # the same way so a chunked import continues where it left off.
    import_cursor:   Mapped[int] = mapped_column(
        Integer, nullable=False, server_default="0")

    records: Mapped[list["ImportDatasetRecord"]] = relationship(
        back_populates="dataset", cascade="all, delete-orphan",
        order_by="ImportDatasetRecord.row_index",
    )

    __table_args__ = (
        UniqueConstraint("name", name="uq_import_dataset_name"),
        CheckConstraint(f"kind IN {IMPORT_KINDS!r}", name="ck_import_dataset_kind"),
        CheckConstraint(f"status IN {DATASET_STATUSES!r}",
                        name="ck_import_dataset_status"),
    )


class ImportDatasetRecord(Base, TimestampMixin):
    """One staged source row. `data` is the raw row as JSON (nothing is dropped);
    `status` + `error_message` are the staging verdict; `taxon_id` is the row created
    or matched on import (NULL until then)."""

    __tablename__ = "import_dataset_record"

    id:                Mapped[int] = mapped_column(Integer, primary_key=True)
    import_dataset_id: Mapped[int] = mapped_column(
        ForeignKey("import_dataset.id", ondelete="CASCADE"), nullable=False)
    row_index:         Mapped[int] = mapped_column(Integer, nullable=False)
    status:            Mapped[str] = mapped_column(String, nullable=False)
    # The raw source row, JSON-encoded TEXT (STRICT has no JSON type). Nothing dropped.
    data:              Mapped[str] = mapped_column(Text, nullable=False)
    # The composed name this row resolved to (for the preview grid); NULL if unresolved.
    resolved_name:     Mapped[str | None] = mapped_column(String)
    error_message:     Mapped[str | None] = mapped_column(Text)
    taxon_id:          Mapped[int | None] = mapped_column(
        ForeignKey("taxon.id", ondelete="SET NULL"))

    dataset: Mapped["ImportDataset"] = relationship(back_populates="records")

    __table_args__ = (
        UniqueConstraint("import_dataset_id", "row_index",
                         name="uq_import_dataset_record_row"),
        CheckConstraint(f"status IN {RECORD_STATUSES!r}",
                        name="ck_import_dataset_record_status"),
    )
