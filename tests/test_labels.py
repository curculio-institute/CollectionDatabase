"""Label text-fitting tests (#17).

The determination and data/locality labels must fit the 18 mm label width
gracefully: keep the genus break / full personal names when they fit, flow /
abbreviate when they don't, and never clip (the label grows instead). Fit is
measured against the real font via `_fits_one_line`, so these tests exercise the
actual layout, not a character heuristic.
"""
import pytest

from weasyprint import HTML
from weasyprint.formatting_structure.boxes import LineBox

from app.services.labels import (
    DeterminationLabel, DataLabel,
    _det_line1, _det_name_html, _det_line3, _data_line2, _fits_one_line,
    _grouped_html,
    determination_sheet, data_sheet, grouped_sheet, LabelGroup, SpecimenLabels,
)


def _keeps_genus_break(name_html: str) -> bool:
    return "</div><div>" in name_html


def _page_line_count(page) -> int:
    n, stack = 0, [page._page_box]
    while stack:
        box = stack.pop()
        if isinstance(box, LineBox):
            n += 1
        stack.extend(getattr(box, "children", None) or [])
    return n


# --------------------------------------------------------------------------
# _fits_one_line — the measurement primitive
# --------------------------------------------------------------------------

def test_fits_one_line_short_true():
    assert _fits_one_line("det. John Doe  2025")


def test_fits_one_line_long_false():
    assert not _fits_one_line(
        "det. Wolfgang Maximilian von Habsburg-Lothringen-Este  2025"
    )


# --------------------------------------------------------------------------
# Determiner — full when it fits, abbreviated when too long
# --------------------------------------------------------------------------

@pytest.mark.parametrize("name", ["John Doe", "T. Schirok", "Müller", "Anna Klein",
                                  "Maximilian Schuster"])  # the user's regression case
def test_determiner_kept_full_when_it_fits(name):
    out = _det_line3(DeterminationLabel(determiner=name, year="2025"))
    assert name in out, f"{name!r} should print in full: {out!r}"


@pytest.mark.parametrize("name,surname", [
    ("Wilhelmina Schmidt-Hohenberger-Lindqvist", "Schmidt-Hohenberger-Lindqvist"),
    ("Maximilian Alexander Friedländer", "Friedländer"),
])
def test_determiner_abbreviated_when_too_long(name, surname):
    out = _det_line3(DeterminationLabel(determiner=name, year="2025"))
    assert name not in out          # full form does not appear
    assert surname in out           # but the surname is preserved
    assert out.startswith("det. ")


# --------------------------------------------------------------------------
# Collector (leg.) on the locality label — same rule, for consistency
# --------------------------------------------------------------------------

@pytest.mark.parametrize("name", ["John Doe", "Müller"])
def test_collector_full_when_it_fits(name):
    out = _data_line2(DataLabel(recorded_by=name, event_date="2025-06-14"))
    assert name in out


def test_collector_abbreviated_when_too_long():
    out = _data_line2(DataLabel(recorded_by="Maximilian Alexander Friedländer",
                                event_date="2025-06-14"))
    assert "Maximilian Alexander Friedländer" not in out
    assert "Friedländer" in out


# --------------------------------------------------------------------------
# Scientific name — keep genus break when it fits, flow when long
# --------------------------------------------------------------------------

@pytest.mark.parametrize("genus,epithet,author", [
    ("Amara", "aenea", "(De Geer, 1774)"),
    ("Sitona", "lineatus", "(Linnaeus, 1758)"),
    ("Curculio", "nucum", "Linnaeus, 1758"),
])
def test_genus_break_kept_for_short_names(genus, epithet, author):
    h = _det_name_html(DeterminationLabel(genus=genus, specific_epithet=epithet, authorship=author))
    assert _keeps_genus_break(h)


@pytest.mark.parametrize("genus,epithet,author", [
    ("Ceutorhynchus", "chalybaeus", "(Germar, 1824) sensu auctorum nec Boheman"),
    ("Trichosirocalus", "troglodytes", "(Fabricius, 1787) sensu lato auctorum"),
])
def test_genus_break_flows_for_long_names(genus, epithet, author):
    h = _det_name_html(DeterminationLabel(genus=genus, specific_epithet=epithet, authorship=author))
    assert not _keeps_genus_break(h)


# --------------------------------------------------------------------------
# Subgenus — italic (not bold), on the same line as the genus
# --------------------------------------------------------------------------

def test_subgenus_is_italic_not_bold():
    h = _det_line1(DeterminationLabel(genus="Otiorhynchus", subgenus="Dorymerus",
                                      specific_epithet="sulcatus"))
    assert "(<em>Dorymerus</em>)" in h           # parenthesised italic
    assert "<strong><em>Dorymerus" not in h      # never bold


def test_subgenus_same_as_genus_uses_sstr_not_bold():
    h = _det_line1(DeterminationLabel(genus="Liparus", subgenus="Liparus",
                                      specific_epithet="coronatus"))
    assert "s.str." in h
    assert "<strong>s.str." not in h and "<em>s.str." not in h  # plain, not bold/italic


def test_subgenus_on_same_line_as_genus():
    h = _det_name_html(DeterminationLabel(genus="Otiorhynchus", subgenus="Dorymerus",
                                          specific_epithet="sulcatus", authorship="(Fabricius, 1775)"))
    # genus + subgenus share the first line block (before the genus -> epithet break)
    first_block = h.split("</div>")[0]
    assert "Otiorhynchus" in first_block and "Dorymerus" in first_block


# --------------------------------------------------------------------------
# Never lose text, and never fail to render (overflow:visible → grows)
# --------------------------------------------------------------------------

def test_long_name_text_is_preserved():
    lbl = DeterminationLabel(
        genus="Trichosirocalus", specific_epithet="troglodytes",
        authorship="(Fabricius, 1787) sensu lato auctorum",
        determiner="John Doe", year="2025",
    )
    h = _det_name_html(lbl)
    for token in ("Trichosirocalus", "troglodytes", "Fabricius", "auctorum"):
        assert token in h


@pytest.mark.parametrize("sheet_fn,labels", [
    (determination_sheet, [DeterminationLabel(genus="Aaaaaaaaaaaa", specific_epithet="bbbbbbbbbbbb",
        authorship="(Wxxxxxxxx & Yyyyyyyy, 2019) nec Zzzzzzzz",
        determiner="Verylongsingletokensurnameindeed", year="2025")]),
    (data_sheet, [DataLabel(country="United Kingdom", country_code="GB",
        locality="A very long locality string that will certainly wrap several times over",
        recorded_by="John Doe", event_date="2025-06-14")]),
])
def test_pathological_labels_render_without_error(sheet_fn, labels):
    pdf = sheet_fn(labels)
    assert pdf[:4] == b"%PDF" and len(pdf) > 1000


def test_grouped_sheet_renders_mixed_lengths():
    specs = [
        SpecimenLabels(
            data=DataLabel(country="Germany", country_code="DE", locality="Kramerplateau",
                           recorded_by="John Doe", event_date="2025-06-14"),
            determination=DeterminationLabel(genus="Sitona", specific_epithet="lineatus",
                authorship="(Linnaeus, 1758)", determiner="John Doe", year="2025"),
            id_code="JJPRC-00001"),
        SpecimenLabels(
            determination=DeterminationLabel(genus="Ceutorhynchus", specific_epithet="chalybaeus",
                authorship="(Germar, 1824) sensu auctorum nec Boheman",
                determiner="Maximilian Alexander Friedländer", year="2025"),
            id_code="JJPRC-00002"),
    ]
    pdf = grouped_sheet([LabelGroup(source="Mounting Session", specimens=specs)], "2026-06-15")
    assert pdf[:4] == b"%PDF" and len(pdf) > 1000


# --------------------------------------------------------------------------
# Pagination (#19) — a multi-page sheet must fill page 1 (flex did not
# paginate, which left page 1 empty) and never split a label across pages.
# --------------------------------------------------------------------------

def _multipage_groups(n_groups=50, per=6):
    """Many realistically-sized groups that together span more than one page."""
    groups = []
    for g in range(n_groups):
        specs = [SpecimenLabels(
            determination=DeterminationLabel(genus="Sitona", specific_epithet="lineatus",
                authorship="(Linnaeus, 1758)", determiner="J. Doe", year="2025"),
            id_code=f"JJPRC-{g:02d}{i:02d}") for i in range(per)]
        groups.append(LabelGroup(source=f"Batch {g + 1}", specimens=specs))
    return groups


def test_multipage_sheet_fills_first_page():
    doc = HTML(string=_grouped_html(_multipage_groups(), "2026-06-16")).render()
    assert len(doc.pages) >= 2, "test data should span more than one page"
    # Before the fix (flex .sheet) page 1 held only the 'Printed:' line (~1 line box);
    # block flow now fills it.
    assert _page_line_count(doc.pages[0]) > 20, "page 1 is nearly empty — not paginating"


def test_chunk_no_split_rule_present_and_paginates():
    css = _grouped_html(_multipage_groups(), "x")
    assert "page-break-inside: avoid" in css  # a label row is never split across pages
    doc = HTML(string=css).render()
    assert len(doc.pages) >= 2  # and it still actually paginates


# --------------------------------------------------------------------------
# Event preview (shared with the printed label text, date highlighted)
# --------------------------------------------------------------------------

def test_event_preview_uses_label_text_with_highlighted_date():
    from app.models import CollectingEvent
    from app.models.geography import Country
    from app.services.label_text import format_event_preview_html
    ev = CollectingEvent(country_obj=Country(name="Germany"), country_code="DE",
                         locality="Kramerplateau", event_date="2025-06-14")
    html = format_event_preview_html(ev)
    assert "Kramerplateau" in html           # same locality text as the label
    assert "2025-06-14</b>" in html          # date wrapped in a highlight
    assert "<b" in html


def test_event_preview_none_is_empty():
    from app.services.label_text import format_event_preview_html
    assert format_event_preview_html(None) == ""


def test_event_preview_includes_db_id():
    from app.models import CollectingEvent
    from app.services.label_text import format_event_preview_html
    ev = CollectingEvent(locality="Kramerplateau", event_date="2025-06-14")
    ev.id = 42
    assert "#42" in format_event_preview_html(ev)


# ---------------------------------------------------------------------------
# Identifier redesign + per-type borders + touching layout (2026-07-07)
# ---------------------------------------------------------------------------
from app.services.labels import (  # noqa: E402
    _id_label_inner, _border_rule, _grouped_css, identifier_sheet,
)


def test_identifier_splits_prefix_over_number():
    """The code prints as a small collection prefix line (with its hyphen, since the
    DB codes are 'JJPC-00304') over a big auto-sized number — not one inline string."""
    html = _id_label_inner("JJPC-00304", "Jakob Jilg Private Collection")
    assert '<div class="id-prefix">JJPC-</div>' in html   # hyphen kept
    assert '<div class="id-number" style="font-size:' in html and ">00304</div>" in html
    assert "Jakob Jilg Private Collection" in html       # tiny full-name line kept
    assert "JJPC-00304" not in html                       # not one inline string


def test_identifier_number_autosizes_down_for_long_codes():
    """A 6+ digit number shrinks below the 4–5 digit cap so it never overflows."""
    from app.services.labels import _id_number_font_pt
    assert _id_number_font_pt("00304") == _id_number_font_pt("42931")   # 5-digit at cap
    assert _id_number_font_pt("100000") < _id_number_font_pt("00304")   # 6-digit smaller
    assert _id_number_font_pt("1234567") < _id_number_font_pt("100000") # 7-digit smaller still


def test_identifier_legacy_code_without_hyphen():
    """A hyphen-less legacy code has no prefix line; the whole code is the number."""
    html = _id_label_inner("abcd", "")
    assert 'id-prefix' not in html
    assert 'class="id-number"' in html and ">abcd</div>" in html


def test_border_rule_choices():
    assert _border_rule("black") == "0.15mm solid #000"
    assert _border_rule("none") == "none"
    assert _border_rule("anything-else") == "none"


def test_grouped_css_threads_per_type_borders():
    css = _grouped_css({"data": "none", "determination": "black", "identifier": "black"})
    # data band → no border; det + id bands → solid black.
    assert ".lbl-data {" in css and "border: none;" in css
    assert "0.15mm solid #000" in css                     # det/id borders present
    # labels within a group are separated by a small gap (each its own border),
    # small enough for one cut per edge — not touching (bordered labels need space).
    assert "border-collapse: separate;" in css
    assert "border-spacing:" in css


def test_grouped_css_default_is_black_everywhere():
    css = _grouped_css(None)
    assert "border: none;" not in css
    assert css.count("0.15mm solid #000") >= 3            # all three bands


def test_identifier_sheet_border_toggle_changes_output():
    codes = ["JJPC-00304", "JJPC-00305"]
    black = identifier_sheet(codes, border="black")
    none = identifier_sheet(codes, border="none")
    assert black != none                                  # border choice reaches the PDF
    assert isinstance(black, bytes) and black[:4] == b"%PDF"
