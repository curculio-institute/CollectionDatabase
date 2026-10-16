"""Pure decision helpers of the TaxonWorks vocab sections (no NiceGUI page needed)."""
from app.ui.tw_vocab_section import chip_label, chip_tooltip, needs_name_choice


def test_chip_label_appends_tw_name_only_when_linked_and_different():
    assert chip_label("TaxonWorks ✓", "Adventive", "Endemic", True) == "TaxonWorks ✓ as 'Endemic'"
    assert chip_label("not verified", "A", "B", True) == "not verified as 'B'"
    assert chip_label("TaxonWorks ✓", "A", "A", True) == "TaxonWorks ✓"
    assert chip_label("local only", "A", None, False) == "local only"
    assert chip_label("local only", "A", "B", False) == "local only"


def test_chip_tooltip():
    assert chip_tooltip(True, "Endemic") == \
        "Linked to TaxonWorks 'Endemic' — unlink first to change"
    assert chip_tooltip(False, None) == "Click to link to a TaxonWorks term"


def test_needs_name_choice_is_case_sensitive_after_strip():
    assert not needs_name_choice("Adventive", " Adventive ")
    assert needs_name_choice("adventive", "Adventive")
    assert needs_name_choice("Adventive", "Endemic")
