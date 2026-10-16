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
    _det_line1, _det_line2, _det_name_html, _det_line3, _data_line1, _data_line2,
    _fits_one_line, _grouped_html,
    grouped_sheet, LabelGroup, SpecimenLabels,
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
# Open-nomenclature qualifier — two-group placement, same rule as on screen
# (taxa.qualifier_is_prefix): cf./aff./nr./? prefix the whole name; the rest suffix it.
# --------------------------------------------------------------------------

def test_doubt_qualifier_prefixes_the_genus_line():
    lbl = DeterminationLabel(genus="Otiorhynchus", specific_epithet="armadillo",
                             authorship="(Rossi, 1792)", qualifier="cf.")
    l1, l2 = _det_line1(lbl), _det_line2(lbl)
    assert l1.startswith("cf. ")
    assert "cf." not in l2                      # not repeated on the epithet line
    assert "Otiorhynchus" in l1 and "armadillo" in l2


def test_scope_qualifier_with_epithet_follows_the_epithet():
    lbl = DeterminationLabel(genus="Rubus", specific_epithet="fruticosus", qualifier="agg.")
    l1, l2 = _det_line1(lbl), _det_line2(lbl)
    assert "agg." not in l1
    assert l2.replace("<strong><em>", "").replace("</em></strong>", "").strip() \
        == "fruticosus agg."


def test_scope_qualifier_on_a_genus_level_determination_rides_on_line_1():
    lbl = DeterminationLabel(genus="Otiorhynchus", qualifier="sp.")
    l1, l2 = _det_line1(lbl), _det_line2(lbl)
    assert l1.endswith(" sp.")
    assert l2 == ""


def test_qualifier_flows_into_the_one_line_fallback():
    lbl = DeterminationLabel(genus="Otiorhynchus", specific_epithet="armadillo",
                             authorship="(Rossi, 1792)", qualifier="cf.")
    h = _det_name_html(lbl)
    assert "cf." in h and "Otiorhynchus" in h and "armadillo" in h


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


def test_pathological_labels_render_without_error():
    # Pathologically long data + determination content must still render — they now
    # render only as bands inside the grouped queue sheet.
    specs = [SpecimenLabels(
        data=DataLabel(country="United Kingdom", country_code="GB",
            locality="A very long locality string that will certainly wrap several times over",
            recorded_by="John Doe", event_date="2025-06-14"),
        determination=DeterminationLabel(genus="Aaaaaaaaaaaa", specific_epithet="bbbbbbbbbbbb",
            authorship="(Wxxxxxxxx & Yyyyyyyy, 2019) nec Zzzzzzzz",
            determiner="Verylongsingletokensurnameindeed", year="2025"),
        id_code="JJPRC-00001")]
    pdf = grouped_sheet([LabelGroup(source="Mounting Session", specimens=specs)], "2026-06-15")
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
    ev = CollectingEvent(country_obj=Country(name="Germany", iso_code="DE"),
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
    _id_label_inner, _border_decl, _grouped_css,
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


def test_border_decl_choices():
    # WeasyPrint: a real 0.1mm border; "none"/anything else → no line (empty decl).
    assert _border_decl("black") == "border: 0.1mm solid #000;"
    assert _border_decl("none") == ""
    assert _border_decl("anything-else") == ""
    # Chromium floors a border to 1 device px, so the hairline is an inset box-shadow.
    assert _border_decl("black", "chromium") == "box-shadow: inset 0 0 0 0.1mm #000;"
    assert _border_decl("none", "chromium") == ""


def test_grouped_css_threads_per_type_borders():
    css = _grouped_css({"data": "none", "determination": "black", "identifier": "black"})
    # data band → no border; det + id bands → solid black (WeasyPrint default backend).
    assert css.count("0.1mm solid #000") == 2            # only det + id, not data
    # labels within a group are separated by a small gap (each its own border),
    # small enough for one cut per edge — not touching (bordered labels need space).
    assert "border-collapse: separate;" in css
    assert "border-spacing:" in css


def test_grouped_css_default_is_black_everywhere():
    css = _grouped_css(None)
    assert css.count("0.1mm solid #000") == 3            # all three bands


def test_grouped_css_chromium_uses_box_shadow_borders():
    """The Chromium backend draws hairlines as inset box-shadow (border is floored)."""
    css = _grouped_css(None, "chromium")
    assert css.count("box-shadow: inset 0 0 0 0.1mm #000;") == 3
    assert "0.1mm solid #000" not in css                 # no floored CSS borders


# ── "Country, State:" prefix — collapse ONE of the two, never both ───────────────
# An 18x7 mm label has no room for "Germany, Baden-Württemberg", but "DE, BY" is a cipher.
# One name always stays written out. See label_text.format_geo_prefix.

import pytest
from app.services.label_text import format_geo_prefix, format_locality_label


@pytest.mark.parametrize("country,ccode,state,scode,expected", [
    # both short: nothing to do
    ("Germany", "DE", "Bavaria", "DE-BY", ("Germany", "Bavaria")),
    ("China", "CN", "Yunnan", "CN-YN", ("China", "Yunnan")),
    ("India", "IN", "Punjab", "IN-PB", ("India", "Punjab")),
    # the longer name gives way — here the state, to its subdivision suffix
    ("Germany", "DE", "Baden-Württemberg", "DE-BW", ("Germany", "BW")),
    ("Kazakhstan", "KZ", "Almaty Region", "KZ-ALM", ("Kazakhstan", "ALM")),
    # the suffix must read as an abbreviation: >=2 letters, no digits. Otherwise the
    # state keeps its name and the COUNTRY collapses instead.
    ("Greece", "GR", "Peloponnese Region", "GR-J", ("GR", "Peloponnese Region")),      # 1 char
    ("Sri Lanka", "LK", "Central Province", "LK-2", ("LK", "Central Province")),       # digit
    ("Kenya", "KE", "Eastern Province", "KE-400", ("KE", "Eastern Province")),         # digits
    ("Nepal", "NP", "Madhesh Province", "NP-P2", ("NP", "Madhesh Province")),          # "P2"
    ("Czechia", "CZ", "Central Bohemian Region", "CZ-20", ("CZ", "Central Bohemian Region")),
    # under the budget (6 + 14 = 20): nothing collapses, so FR-2A never even comes up
    ("France", "FR", "Corsica Region", "FR-2A", ("France", "Corsica Region")),
    # country is the longer one
    ("Switzerland", "CH", "Basel-City", "CH-BS", ("CH", "Basel-City")),
    # an uncoded row cannot collapse; the other one does
    ("Germany", "DE", "Baden-Württemberg", None, ("DE", "Baden-Württemberg")),
    # no codes at all: keep both names, let the label grow rather than lose the locality
    ("Germany", None, "Baden-Württemberg", None, ("Germany", "Baden-Württemberg")),
    # no state → the plain long-country rule still applies
    ("United Kingdom", "GB", None, None, ("GB", "")),
    ("Germany", "DE", None, None, ("Germany", "")),
    # no country
    (None, None, "Bavaria", "DE-BY", ("", "Bavaria")),
])
def test_geo_prefix_collapses_exactly_one_side(country, ccode, state, scode, expected):
    assert format_geo_prefix(country, ccode, state, scode) == expected


@pytest.mark.parametrize("country,ccode,state,scode", [
    ("Germany", "DE", "Baden-Württemberg", "DE-BW"),
    ("Greece", "GR", "Peloponnese Region", "GR-J"),
    ("Switzerland", "CH", "Basel-City", "CH-BS"),
])
def test_never_both_collapsed(country, ccode, state, scode):
    """At least one side stays a readable name — "DE, BW" must never be printed."""
    c, s = format_geo_prefix(country, ccode, state, scode)
    assert c == country or s == state, f"both collapsed: {c!r}, {s!r}"


def test_locality_label_uses_the_comma_prefix():
    from app.models import CollectingEvent
    from app.models.geography import Country, StateProvince
    ev = CollectingEvent(
        country_obj=Country(name="Germany", iso_code="DE"),
        state_province_obj=StateProvince(name="Baden-Württemberg", iso_code="DE-BW"),
        locality="Kramerplateau", event_date="2025-06-14")
    assert format_locality_label(ev).startswith("Germany, BW: Kramerplateau")


def test_data_label_line1_uses_the_comma_prefix():
    line = _data_line1(DataLabel(
        country="Greece", country_code="GR",
        state_province="Peloponnese Region", state_province_code="GR-J",
        locality="Tripoli"))
    assert line.startswith("GR, Peloponnese Region: Tripoli")


def test_data_label_line1_includes_sampling_protocol():
    line = _data_line1(DataLabel(locality="Tripoli", habitat="Heath",
                                  sampling_protocol="Pitfall trap"))
    assert "Heath, Pitfall trap" in line



# ── Chromium print grid (uneven top clearance between identical labels) ──────

def test_chromium_print_grid_zoom_and_scale_cancel_out():
    """The sheet is laid out _PRINT_GRID x larger and printed 1/_PRINT_GRID as large,
    so physical sizes are unchanged while Chromium's whole-pixel rounding shrinks to a
    fraction of a paper pixel. The two factors must stay exact inverses, and the zoom
    must be on `body` — on `html` it also multiplies the @page margin."""
    import inspect
    from app.services import pdf_backend as B
    assert B._PRINT_GRID >= 4
    src = inspect.getsource(B._chromium_pdf_sync)
    assert 'body {{ zoom: {_PRINT_GRID}; }}' in src
    assert "scale=1 / _PRINT_GRID" in src
    assert "html {{ zoom" not in src


def test_label_css_carries_no_transform_workaround():
    """Anchoring labels with a transform was tried and rejected: per label/cell/row it
    collapses cut lanes to zero, per table it leaves rows 2+ unfixed (see
    pdf_backend._PRINT_GRID). Guard against it creeping back."""
    from app.services import labels as L
    assert "transform" not in L._grouped_css(backend="chromium")
    assert "transform" not in L._grouped_css(backend="weasyprint")


def test_grouped_sheet_is_standards_mode():
    """Without a doctype Chromium renders the sheet in quirks mode, where a table does not
    inherit `line-height` — labels then print with the font's `normal` line height instead
    of the designed _LINE_H (see pdf_backend._PRINT_GRID)."""
    from app.services import labels as L
    html = L._grouped_html([], "2026-10-16")
    assert html.lower().startswith("<!doctype html>")


# ── Identifier label: fixed 18 x 7 mm box, text shrinks to fit (2026-10-16) ─────────

_NAMES_BY_LENGTH = [
    "JJPC",                                                            # 4
    "Jakob Jilg Personal Collection",                                  # 30
    "Zoologische Staatssammlung München",                              # 34
    "Senckenberg Deutsches Entomologisches Institut",                  # 46
    "Naturhistorisches Museum Wien, Coleoptera Collection",            # 52
    "Muséum national d'Histoire naturelle, Entomologie, Coléoptères",  # 62
]
_TOO_LONG = ("Natural History Museum London, Department of Life Sciences, "
             "Insects Division, Coleoptera Section")                    # 96


def _id_boxes(html: str):
    """(lbl-id box, [line boxes inside it]) for every identifier label WeasyPrint lays out."""
    found, stack = [], [b for p in HTML(string=html).render().pages for b in [p._page_box]]
    while stack:
        box = stack.pop()
        el = getattr(box, "element", None)
        if (el is not None and "lbl-id" in (el.get("class") or "").split()
                and box.element_tag == "div" and hasattr(box, "border_width")):
            lines, inner = [], [box]
            while inner:
                b = inner.pop()
                if isinstance(b, LineBox):
                    lines.append(b)
                inner.extend(getattr(b, "children", []) or [])
            found.append((box, lines))
            continue
        stack.extend(getattr(box, "children", []) or [])
    return found


def test_identifier_label_box_is_fixed_whatever_the_name():
    """Every identifier label is exactly 18 x 7 mm and all its text stays inside — a long
    collection name used to widen the label (96 characters printed 39.6 mm wide)."""
    from app.services import labels as L
    px_mm = 25.4 / 96
    for name in _NAMES_BY_LENGTH:
        specs = [SpecimenLabels(id_code=c) for c in ("JJPC-00304", "JJPC-100000")]
        html = L._grouped_html([LabelGroup("t", specs)], "2026-10-16", {"JJPC": name})
        boxes = _id_boxes(html)
        assert len(boxes) == 2, name
        for box, lines in boxes:
            assert abs(box.border_width() * px_mm - 18.0) < 0.01, name
            assert abs(box.border_height() * px_mm - 7.0) < 0.01, name
            right = box.border_box_x() + box.border_width() - box.border_right_width
            bottom = box.border_box_y() + box.border_height() - box.border_bottom_width
            for ln in lines:
                text_right = ln.position_x + sum(c.margin_width() for c in ln.children)
                assert text_right <= right + 1e-6, name
                assert ln.position_y + ln.margin_height() <= bottom + 1e-6, name


def test_identifier_name_shrinks_then_wraps_and_the_number_makes_room():
    from app.services import labels as L
    short = L._id_layout("JJPC-00304", "Jakob Jilg Personal Collection")
    assert short.name_lines == ("Jakob Jilg Personal Collection",)
    assert short.name_pt == L._NAME_MAX_PT
    shrunk = L._id_layout("JJPC-00304", "Zoologische Staatssammlung München")
    assert len(shrunk.name_lines) == 1 and shrunk.name_pt < L._NAME_MAX_PT
    wrapped = L._id_layout("JJPC-00304", "Naturhistorisches Museum Wien, Coleoptera Collection")
    assert len(wrapped.name_lines) == 2 and wrapped.name_pt == L._NAME_MIN_PT
    assert " ".join(wrapped.name_lines) == "Naturhistorisches Museum Wien, Coleoptera Collection"
    assert L._ID_NUM_MIN_PT <= wrapped.num_pt < short.num_pt     # number gave up height
    assert L._id_layout("JJPC-100000", "JJPC").num_pt < short.num_pt   # and shrinks for width


def test_identifier_refuses_what_cannot_fit():
    """Never a wider or clipped label: a name that does not fit even on two lines at the
    minimum size, or a character the bundled font lacks, refuses the whole sheet."""
    import pytest
    from app.services import labels as L
    with pytest.raises(L.LabelDoesNotFit, match="Natural History Museum London"):
        L._id_layout("JJPC-00304", _TOO_LONG)
    with pytest.raises(L.LabelDoesNotFit, match="cannot print"):
        L._id_layout("JJPC-00304", "昆虫標本館")
    with pytest.raises(L.LabelDoesNotFit):
        L.grouped_sheet([LabelGroup("t", [SpecimenLabels(id_code="JJPC-00304")])],
                        "2026-10-16", {"JJPC": _TOO_LONG})


def test_identifier_name_problem_for_the_collections_dialog():
    from app.services import labels as L
    assert L.identifier_name_problem("Jakob Jilg Personal Collection", "JJPC") is None
    assert "Natural History Museum London" in L.identifier_name_problem(_TOO_LONG, "NHMUK")
    assert L.identifier_name_problem(_TOO_LONG, None) is None   # codeless: no identifier labels
