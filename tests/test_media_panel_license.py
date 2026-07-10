"""#64: a media item with a non-standard licence must not blank the media popup.

`media.license` is free TEXT, so an imported row — or a hand-edited `config.default_license`
— can hold a value outside `LICENSE_OPTIONS`. NiceGUI's ChoiceElement raises
`ValueError: Invalid value` when a select's value is not among its options, which killed the
popup's rebuild mid-render.
"""
import pytest

from app.ui.media_panel import _license_options
from app.vocab import LICENSE_OPTIONS

BAD = "https://creativecommons.org/licenses/by/4.0/"


def test_standard_licence_leaves_the_option_list_untouched():
    assert _license_options("CC0") == LICENSE_OPTIONS
    assert _license_options("") == LICENSE_OPTIONS
    assert _license_options(None) == LICENSE_OPTIONS


def test_unknown_licence_is_appended_not_dropped():
    opts = _license_options(BAD)
    assert BAD in opts, "a stored licence must remain selectable — never silently discarded"
    assert opts[:len(LICENSE_OPTIONS)] == LICENSE_OPTIONS


def test_select_can_actually_be_constructed_with_an_unknown_licence():
    """The real failure: ui.select raises, so the popup renders blank."""
    from nicegui import ui
    with ui.card():
        with pytest.raises(ValueError, match="Invalid value"):
            ui.select(LICENSE_OPTIONS, value=BAD, label="licence")     # the bug
        ui.select(_license_options(BAD), value=BAD, label="licence")   # the fix


def test_whitespace_only_licence_is_treated_as_empty():
    assert _license_options("   ") == LICENSE_OPTIONS


def test_a_whitespace_licence_does_not_reintroduce_the_mismatch():
    """options and value must come from the SAME normalised string, or the select raises."""
    from nicegui import ui
    raw = "   "
    val = (raw or "").strip()
    with ui.card():
        ui.select(_license_options(val), value=val, label="licence")   # must not raise
        with pytest.raises(ValueError, match="Invalid value"):
            ui.select(_license_options(raw), value=raw, label="licence")   # the trap
