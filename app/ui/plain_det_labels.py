"""Labels tab → "Identification labels" card.

Queues N *plain* identification labels — determination labels that belong to no
specimen, to be pinned by hand — the same way the card above it queues identifier
labels. Built from the widgets the identification form uses (taxon search with
import-if-absent, person field, date input, type status, qualifier), so the fields
behave identically; only the write differs: `print_queue.enqueue_plain_determinations`.
"""
from __future__ import annotations

from nicegui import ui

import app.services.person_defaults as pd_svc
import app.services.print_queue as pq_svc
from app.ui.choice_field import build_choice_field
from app.ui.date_input import AUTO_CHANGED_CSS, attach_date_validation, append_year_pin
from app.ui.person_field import build_person_field
from app.ui.taxon_search import build_taxon_search
from app.ui.type_status_field import build_type_status_field
from app.vocab import (SEX_OPTIONS as _SEX_OPTIONS,
                       IDENTIFICATION_QUALIFIER_OPTIONS as _QUAL_OPTIONS)


def build_plain_det_labels_card(session_factory, *, on_queued: callable | None = None) -> None:
    ui.add_head_html(AUTO_CHANGED_CSS)

    def _default_idby() -> str | None:
        with session_factory() as s:
            return pd_svc.get_defaults(s)[0]

    with ui.card().classes("w-full shadow-sm"):
        ui.label("Identification labels").classes("section-label mb-2")
        ui.label(
            "Print identification labels that are not tied to a specimen record — "
            "e.g. for a series you identify at the bench. No identification is "
            "recorded: to record one, add it to the specimen in Records. (A determiner "
            "who is new is added to People.)"
        ).classes("text-sm mb-4").style("color:var(--tp-base-soft)")

        taxon_state = build_taxon_search(session_factory)

        with ui.row().classes("w-full flex-wrap gap-3 items-end mt-2"):
            with ui.row().classes("flex-1 min-w-40 items-center gap-1"):
                idby_state = build_person_field(
                    session_factory, "identifiedBy", default_fn=_default_idby)
            dtid = ui.input("dateIdentified", placeholder="YYYY-MM-DD").classes("w-36")
            append_year_pin(dtid)
            attach_date_validation(dtid, no_future=True)
            sex = ui.select(_SEX_OPTIONS, label="sex").classes("w-28")
            type_state = build_type_status_field(classes="w-36")
            qual_state = build_choice_field(_QUAL_OPTIONS, "qualifier", classes="w-28")

        with ui.row().classes("mt-4 gap-4 items-center"):
            count = ui.number("Number of labels", value=1, min=1,
                              max=pq_svc.MAX_PLAIN_LABELS, step=1).classes("w-40")
            add_btn = ui.button("Add to print queue", icon="queue")
            status = ui.label("").classes("text-sm").style("color:var(--tp-base-soft)")

        def _add():
            tid = taxon_state["taxon_id"]
            if not tid:
                ui.notify("Select a taxon first.", type="warning")
                return
            if tid == -1:
                ui.notify("Taxon is still importing — wait a moment.", type="warning")
                return
            raw = count.value
            if raw is None or float(raw) != int(raw):
                ui.notify("Number of labels must be a whole number.", type="warning")
                return
            n = int(raw)
            try:
                with session_factory() as s:
                    with s.begin():
                        pq_svc.enqueue_plain_determinations(
                            s, taxon_id=tid, count=n,
                            identified_by_id=idby_state["commit"](s),
                            date_identified=dtid.value or None,
                            type_status=type_state["get_value"]() or None,
                            identification_qualifier=qual_state["get_value"]() or None,
                            sex=sex.value or None,
                        )
            except Exception as exc:
                ui.notify(f"Failed: {exc}", type="negative")
                return
            # The form is left filled in: the next batch is usually the same determiner
            # and date with another name, and the queue is where a slip is undone.
            status.set_text(f"✓ {n} label(s) added to the print queue")
            if on_queued:
                on_queued()

        add_btn.on_click(_add)
