"""The popup's Save turns its working copy into items, refusing an unusable value by
name rather than dropping it (CLAUDE.md §2)."""
import pytest

from app.ui.annotation_panel import collect_items

TERMS = {1: {"kind": "tag", "name": "Adventive"},
         2: {"kind": "data_attribute", "name": "EUNIS habitat"}}


def test_collect_items_cleans_values():
    assert collect_items({1: None, 2: " G1.6 "}, TERMS) == [
        {"term_id": 1, "value": None}, {"term_id": 2, "value": "G1.6"}]


def test_collect_items_names_the_term_with_a_bad_value():
    with pytest.raises(ValueError, match="EUNIS habitat"):
        collect_items({2: "  "}, TERMS)


def test_collect_items_refuses_an_unknown_term():
    with pytest.raises(ValueError, match="no longer exists"):
        collect_items({9: None}, TERMS)
