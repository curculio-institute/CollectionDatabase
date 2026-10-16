"""Term pre-flight (spec "Term pre-flight"): for every emitted column, the id exists on
this host with this name, and the name resolves to exactly this id under TW's own lookup
(``Keyword.find_by(uri: selector)`` then name ILIKE — occurrence.rb:1672 / :1695; the
predicate selector is lower-cased, darwin_core.rb:82)."""
import pytest

from app.services.annotation_export import ExportTerm, evaluate_terms
from app.services.tw_links import KEYWORD, PREDICATE, TwTerm


def _col(kind="tag", tw_id=1764, tw_name="Adventive"):
    return ExportTerm(term_id=1, kind=kind, tw_class="CollectionObject", tw_id=tw_id,
                      tw_name=tw_name, local_name="local")


def _check(col, keywords=(), predicates=()):
    (c,) = evaluate_terms([col], {KEYWORD: list(keywords), PREDICATE: list(predicates)})
    return c


def test_ok():
    assert _check(_col(), [TwTerm(KEYWORD, 1764, "Adventive")]).ok


def test_deleted_on_taxonworks():
    c = _check(_col(), [TwTerm(KEYWORD, 5, "Endemic")])
    assert not c.ok and "no TaxonWorks keyword has id 1764" in c.reason


def test_renamed_on_taxonworks():
    c = _check(_col(), [TwTerm(KEYWORD, 1764, "Adventive species")])
    assert not c.ok and "renamed" in c.reason


def test_another_term_with_the_same_name_ignoring_case():
    c = _check(_col(), [TwTerm(KEYWORD, 1764, "Adventive"), TwTerm(KEYWORD, 8, "ADVENTIVE")])
    assert not c.ok and "id 8" in c.reason


def test_another_term_whose_uri_is_the_selector():
    c = _check(_col(), [TwTerm(KEYWORD, 1764, "Adventive"),
                        TwTerm(KEYWORD, 9, "Something", uri="Adventive")])
    assert not c.ok and "id 9" in c.reason


def test_predicate_uri_is_compared_against_the_lower_cased_selector():
    col = _col("data_attribute", 3, "EUNIS Habitat")
    c = _check(col, predicates=[TwTerm(PREDICATE, 3, "EUNIS Habitat"),
                                TwTerm(PREDICATE, 4, "Other", uri="eunis habitat")])
    assert not c.ok and "id 4" in c.reason
    ok = _check(col, predicates=[TwTerm(PREDICATE, 3, "EUNIS Habitat"),
                                 TwTerm(PREDICATE, 4, "Other", uri="EUNIS Habitat")])
    assert ok.ok                         # TW lower-cases the selector, so no clash


def test_a_tab_in_the_name_fails():
    c = _check(_col(tw_name="Adv\tentive"), [TwTerm(KEYWORD, 1764, "Adv\tentive")])
    assert not c.ok and "tab" in c.reason
