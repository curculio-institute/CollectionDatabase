"""Label text-fitting tests (#17).

The determination and data/locality labels must fit the 18 mm label width
gracefully: keep the genus break / full personal names when they fit, flow /
abbreviate when they don't, and never clip (the label grows instead). Fit is
measured against the real font via `_fits_one_line`, so these tests exercise the
actual layout, not a character heuristic.
"""
import pytest

from app.services.labels import (
    DeterminationLabel, DataLabel,
    _det_line1, _det_name_html, _det_line3, _data_line2, _fits_one_line,
    determination_sheet, data_sheet, grouped_sheet, LabelGroup, SpecimenLabels,
)


def _keeps_genus_break(name_html: str) -> bool:
    return "</div><div>" in name_html


# --------------------------------------------------------------------------
# _fits_one_line — the measurement primitive
# --------------------------------------------------------------------------

def test_fits_one_line_short_true():
    assert _fits_one_line("det. Jakob Jilg  2025")


def test_fits_one_line_long_false():
    assert not _fits_one_line(
        "det. Wolfgang Maximilian von Habsburg-Lothringen-Este  2025"
    )


# --------------------------------------------------------------------------
# Determiner — full when it fits, abbreviated when too long
# --------------------------------------------------------------------------

@pytest.mark.parametrize("name", ["Jakob Jilg", "T. Schirok", "Müller", "Anna Klein",
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

@pytest.mark.parametrize("name", ["Jakob Jilg", "Müller"])
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
        determiner="Jakob Jilg", year="2025",
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
        recorded_by="Jakob Jilg", event_date="2025-06-14")]),
])
def test_pathological_labels_render_without_error(sheet_fn, labels):
    pdf = sheet_fn(labels)
    assert pdf[:4] == b"%PDF" and len(pdf) > 1000


def test_grouped_sheet_renders_mixed_lengths():
    specs = [
        SpecimenLabels(
            data=DataLabel(country="Germany", country_code="DE", locality="Kramerplateau",
                           recorded_by="Jakob Jilg", event_date="2025-06-14"),
            determination=DeterminationLabel(genus="Sitona", specific_epithet="lineatus",
                authorship="(Linnaeus, 1758)", determiner="Jakob Jilg", year="2025"),
            id_code="JJPRC-00001"),
        SpecimenLabels(
            determination=DeterminationLabel(genus="Ceutorhynchus", specific_epithet="chalybaeus",
                authorship="(Germar, 1824) sensu auctorum nec Boheman",
                determiner="Maximilian Alexander Friedländer", year="2025"),
            id_code="JJPRC-00002"),
    ]
    pdf = grouped_sheet([LabelGroup(source="Mounting Session", specimens=specs)], "2026-06-15")
    assert pdf[:4] == b"%PDF" and len(pdf) > 1000
