"""Controlled-vocabulary invariants (app/vocab.py)."""
from app.vocab import SEX_OPTIONS, SEX_SYMBOLS


def test_every_sex_symbol_is_a_valid_option():
    # A glyph for a value that isn't a selectable sex is dead — keep them in step.
    for value in SEX_SYMBOLS:
        assert value in SEX_OPTIONS, f"{value!r} has a symbol but is not in SEX_OPTIONS"


def test_blank_sentinel_is_last_in_sex_options():
    # UI convention: the empty option renders last (CLAUDE.md → UI conventions).
    assert SEX_OPTIONS[-1] == ""


def test_undetermined_and_blank_have_no_glyph():
    # Deliberate: only determinate sexes get a typographic symbol.
    assert "undetermined" not in SEX_SYMBOLS
    assert "" not in SEX_SYMBOLS
