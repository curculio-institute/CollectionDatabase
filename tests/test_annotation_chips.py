"""Chip rendering (spec "Colour"): black or white text by the colour's luminance; names
are escaped; no colour = the neutral chip."""
from app.services.annotations import AnnotationView
from app.ui.annotation_chips import annotations_html, chip_html, swatch_html, text_color


def test_text_color_by_luminance():
    assert text_color("#254d0d") == "#fff"      # dark green (Curculionoidea associations)
    assert text_color("#ff8800") == "#000"      # orange (asserted distribution)
    assert text_color("#fff") == "#000"
    assert text_color("#000") == "#fff"


def test_chip_escapes_and_colours():
    html = chip_html("<b>Adventive</b>", "#254d0d", local_only=True)
    assert "&lt;b&gt;" in html and "background:#254d0d" in html and "color:#fff" in html
    assert "local" in html
    assert "background" not in chip_html("Adventive")


def test_swatch_without_colour_is_a_placeholder():
    assert "dashed" in swatch_html(None)
    assert "#abc" in swatch_html("#abc")


def test_annotations_html_tags_then_data_attributes():
    views = [AnnotationView(1, "tag", "Adventive", None, "#ff8800", True),
             AnnotationView(2, "data_attribute", "EUNIS habitat", "G1.6", None, False)]
    html = annotations_html(views)
    assert html.index("Adventive") < html.index("EUNIS habitat")
    assert "G1.6" in html
    assert annotations_html([]) == ""
