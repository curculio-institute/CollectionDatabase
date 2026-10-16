"""Pure bookkeeping of the annotation popup (no NiceGUI page needed)."""
import pytest

from app.ui.annotation_panel import (add_pending_pick, collect_items, collect_with_pending,
                                     pending_changed)

TERMS = {1: {"kind": "tag", "name": "Adventive"},
         2: {"kind": "data_attribute", "name": "EUNIS habitat"}}


def test_picked_but_not_added_data_attribute_is_folded_in():
    work = {1: None}
    add_pending_pick(work, 2, " G1.6 ")
    assert collect_items(work, TERMS) == [{"term_id": 1, "value": None},
                                          {"term_id": 2, "value": "G1.6"}]


def test_no_pick_leaves_work_untouched():
    work = {1: None}
    add_pending_pick(work, None, "x")
    assert work == {1: None}


def test_picked_with_empty_value_is_refused_naming_the_term():
    work = {}
    add_pending_pick(work, 2, "")
    with pytest.raises(ValueError, match="EUNIS habitat"):
        collect_items(work, TERMS)


def test_deleted_term_is_refused_by_collect_items():
    with pytest.raises(ValueError, match="term 9"):
        collect_items({9: None}, TERMS)


def test_pending_changed():
    db = [{"term_id": 1, "value": None}]
    assert not pending_changed(None, db)
    assert not pending_changed([{"term_id": 1, "value": None}], db)
    assert pending_changed([], db)


def test_refused_pending_pick_leaves_work_untouched():
    """Save & close folds the pick into a copy: a refused value must not leave a row-less
    entry in the working copy (it could not be removed, only aborted)."""
    work = {1: None}
    with pytest.raises(ValueError, match="EUNIS habitat"):
        collect_with_pending(work, 2, "", TERMS)
    assert work == {1: None}


def test_accepted_pending_pick_returns_new_work_and_items():
    work = {1: None}
    new_work, items = collect_with_pending(work, 2, " G1.6 ", TERMS)
    assert new_work == {1: None, 2: " G1.6 "} and work == {1: None}
    assert items == [{"term_id": 1, "value": None}, {"term_id": 2, "value": "G1.6"}]
