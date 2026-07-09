"""Taxon editor: a taxon with a bad rank must stay editable.

`ui.select` raises ValueError when its `value` is not among its options. A taxon whose rank
is outside TAXON_RANKS — e.g. 'spec.', IPNI's abbreviation, written by the old POWO path when
its fetch failed silently (#96) — therefore aborted the form build, and the Edit Taxon dialog
never reached the code that enables the Delete button. The row that most needed repairing was
the only row the editor refused to open.
"""
from app.services.taxa import TAXON_RANKS_BY_USE
from app.ui.taxon_editor import rank_options


def test_rank_options_are_the_vocabulary_for_a_normal_taxon():
    assert rank_options("species") == TAXON_RANKS_BY_USE


def test_rank_options_are_the_vocabulary_for_a_new_taxon():
    assert rank_options(None) == TAXON_RANKS_BY_USE


def test_a_rank_outside_the_vocabulary_is_offered_so_it_can_be_corrected():
    """Without this, ui.select(value='spec.') raises and the taxon is uneditable AND
    undeletable."""
    opts = rank_options("spec.")
    assert opts[0] == "spec."                       # present, so ui.select accepts the value
    assert opts[1:] == TAXON_RANKS_BY_USE           # ...and the real ranks follow
    assert "spec." not in TAXON_RANKS_BY_USE        # it is not silently blessed as a rank


def test_a_stray_rank_is_offered_only_once():
    assert rank_options("species").count("species") == 1
