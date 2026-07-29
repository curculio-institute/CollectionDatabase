"""A specimen is only exported when its identification is certain (#149, 2026-07-26).

Two curatorial rules in `dwc_export.export_decision`, both about how sure the current
determination is — the mirror is a published statement of what this collection holds:

1. **any open-nomenclature qualifier withholds the record** — `cf.` `aff.` `nr.` `agg.`
   `gr.` `?` `sp.` `spp.` `indet.` all express doubt;
2. **the determination must reach species rank** (or below — a subspecies is *more*
   precise, not less), and a specimen with no current identification at all is withheld
   by the same rule.

Erring toward withholding is the safe direction: TaxonWorks' importer is CREATE-ONLY, so
a record published too early cannot be corrected or deleted through the API, whereas a
record withheld today exports fine tomorrow.

These are curatorial, not privacy — `ExportDecision.privacy_reasons` keeps the two apart
so a tidy-up is never reported as a confidentiality breach.
"""
import pytest

import app.config as config
from app.models import CollectingEvent, Person, Taxon
from app.models.base import _utcnow
from app.services.dwc_export import export_decision
from app.services.specimens import create_collection_object, create_determination
from app.services.taxa import compose_scientific_name
from app.vocab import IDENTIFICATION_QUALIFIERS
from tests.helpers import ensure_repo


def _taxon(session, element: str, rank: str, parent=None):
    tx = Taxon(name_element=element, scientific_name=element, taxon_rank=rank,
               parent_name_usage_id=parent.id if parent else None,
               nomenclatural_code="ICZN", created_at=_utcnow(), updated_at=_utcnow())
    session.add(tx)
    session.flush()
    tx.scientific_name = compose_scientific_name(session, tx)
    session.flush()
    return tx


@pytest.fixture
def species(session):
    """`Otiorhynchus crypticus` under its genus — the normal, exportable case."""
    genus = _taxon(session, "Otiorhynchus", "genus")
    return _taxon(session, "crypticus", "species", genus), genus


def _specimen(session, catalog_number, taxon=None, qualifier=None):
    co = create_collection_object(
        session, collecting_event_id=None, catalog_number=catalog_number,
        repository_id=ensure_repo(session, "JJPC"),
    )
    session.flush()
    if taxon is not None:
        create_determination(
            session, collection_object_id=co.id, taxon_id=taxon.id,
            identification_qualifier=qualifier, is_current=1,
        )
        session.flush()
    session.refresh(co)
    return co


def test_a_definite_species_determination_is_exportable(session, species):
    sp, _ = species
    decision = export_decision(_specimen(session, "JJPC-10001", sp))
    assert decision.eligible
    assert decision.reasons == ()


@pytest.mark.parametrize("qualifier", IDENTIFICATION_QUALIFIERS)
def test_every_qualifier_withholds_the_record(session, species, qualifier):
    """The whole closed set expresses doubt — none of them is a definite ID."""
    sp, _ = species
    decision = export_decision(
        _specimen(session, f"JJPC-11{IDENTIFICATION_QUALIFIERS.index(qualifier):03d}",
                  sp, qualifier=qualifier)
    )
    assert not decision.eligible
    assert any("expresses doubt" in r for r in decision.reasons)
    assert qualifier in " ".join(decision.reasons)


def test_a_genus_level_determination_is_withheld(session, species):
    _, genus = species
    decision = export_decision(_specimen(session, "JJPC-10002", genus))
    assert not decision.eligible
    assert any("not to species" in r for r in decision.reasons)
    # The name is named, so the report says which specimen to go and finish.
    assert any("Otiorhynchus" in r for r in decision.reasons)


def test_a_subspecies_is_more_precise_than_species_not_less(session, species):
    """Below species must not be mistaken for "short of species"."""
    sp, _ = species
    ssp = _taxon(session, "alpinus", "subspecies", sp)
    assert export_decision(_specimen(session, "JJPC-10003", ssp)).eligible


def test_a_specimen_with_no_current_identification_is_withheld(session):
    decision = export_decision(_specimen(session, "JJPC-10004"))
    assert not decision.eligible
    assert decision.reasons == ("no current identification",)


def test_a_subgenus_determination_is_withheld(session, species):
    """Subgenus sits above species in the one rank ordering, so it is not enough."""
    _, genus = species
    subg = _taxon(session, "Nihus", "subgenus", genus)
    decision = export_decision(_specimen(session, "JJPC-10005", subg))
    assert not decision.eligible
    assert any("not to species" in r for r in decision.reasons)


def test_an_unknown_rank_is_withheld_not_assumed_fine(session):
    """§2 — a value the model cannot place is a loud refusal, never a guess.

    And the reason must not claim more than we know: an unplaceable rank is reported as
    unplaceable, not as "not to species" (we never compared it to species at all).
    """
    tx = _taxon(session, "Weirdia", "genus")
    tx.taxon_rank = "cohort"          # not in taxa.TAXON_RANKS
    session.flush()
    decision = export_decision(_specimen(session, "JJPC-10006", tx))
    assert not decision.eligible
    assert any("not one this catalogue knows" in r for r in decision.reasons)
    assert not any("not to species" in r for r in decision.reasons)


# ── privacy vs. curation ────────────────────────────────────────────────────────

def test_a_curatorial_withholding_is_not_a_privacy_one(session, species):
    _, genus = species
    decision = export_decision(_specimen(session, "JJPC-10007", genus))
    assert decision.withheld and not decision.withheld_for_privacy
    assert decision.privacy_reasons == ()


def test_a_confidential_specimen_is_a_privacy_withholding(session, species):
    sp, _ = species
    co = _specimen(session, "JJPC-10008", sp)
    co.confidential = 1
    session.flush()
    decision = export_decision(co)
    assert decision.withheld_for_privacy
    assert decision.privacy_reasons == ("specimen is flagged confidential",)


def test_both_causes_are_reported_together(session, species):
    """A record can fail on both counts; the report shows both, and privacy still wins
    the urgency label."""
    _, genus = species
    co = _specimen(session, "JJPC-10009", genus)
    co.confidential = 1
    session.flush()
    decision = export_decision(co)
    assert len(decision.reasons) == 2
    assert decision.privacy_reasons == ("specimen is flagged confidential",)
    assert decision.withheld_for_privacy


# ── recorded_by_state (#170: the record_summary person_off badge's three sub-states) ──

def _specimen_with_recorder(session, catalog_number, taxon, *, confidential=False,
                             consent_approved=False):
    p = Person(full_name="J. Collector", created_at=_utcnow(), updated_at=_utcnow(),
               confidential=int(confidential), consent_approved=int(consent_approved))
    session.add(p)
    session.flush()
    ev = CollectingEvent(recorded_by_id=p.id, created_at=_utcnow(), updated_at=_utcnow())
    session.add(ev)
    session.flush()
    co = create_collection_object(
        session, collecting_event_id=ev.id, catalog_number=catalog_number,
        repository_id=ensure_repo(session, "JJPC"),
    )
    session.flush()
    create_determination(session, collection_object_id=co.id, taxon_id=taxon.id, is_current=1)
    session.flush()
    session.refresh(co)
    return co


def test_a_confidential_recorder_state_is_confidential(session, species):
    sp, _ = species
    co = _specimen_with_recorder(session, "JJPC-10010", sp, confidential=True)
    decision = export_decision(co)
    assert decision.recorded_by_state == "confidential"
    assert not decision.eligible


def test_an_unconsented_recorder_under_the_default_policy_is_redacted_not_withheld(
    session, species, monkeypatch,
):
    """The exact case flagged in live review: NOT confidential, NOT consented — under the
    default policy ("name_removed") this is NOT a withholding reason at all, only a
    redaction, so the specimen stays eligible and carries the grey/informational badge."""
    monkeypatch.setattr(config, "_instance", config.AppConfig(
        media_dir="unused", tw_export_nonconsent="name_removed"))
    sp, _ = species
    co = _specimen_with_recorder(session, "JJPC-10011", sp,
                                 confidential=False, consent_approved=False)
    decision = export_decision(co)
    assert decision.recorded_by_state == "redacted"
    assert decision.eligible          # NOT withheld — only the name is blanked
    assert decision.reasons == ()
    assert decision.blank_fields == ("recordedBy",)


def test_the_same_recorder_is_blocked_under_the_consented_only_policy(
    session, species, monkeypatch,
):
    """Same person, same flags — only the CONFIGURED POLICY differs, and that alone flips
    this from "exported, name redacted" to "withheld entirely"."""
    monkeypatch.setattr(config, "_instance", config.AppConfig(
        media_dir="unused", tw_export_nonconsent="consented_only"))
    sp, _ = species
    co = _specimen_with_recorder(session, "JJPC-10012", sp,
                                 confidential=False, consent_approved=False)
    decision = export_decision(co)
    assert decision.recorded_by_state == "blocked"
    assert not decision.eligible
    assert decision.withheld_for_privacy


def test_a_consenting_recorder_gets_no_state_at_all(session, species):
    sp, _ = species
    co = _specimen_with_recorder(session, "JJPC-10013", sp,
                                 confidential=False, consent_approved=True)
    decision = export_decision(co)
    assert decision.recorded_by_state == ""
    assert decision.eligible
    assert decision.blank_fields == ()


# ── determination=… (code review fix: avoid a lazy `co.determinations` load) ──────

def test_a_caller_supplied_determination_is_used_instead_of_looked_up(session, species):
    """`explore.py`'s query already outer-joins the current determination — passing it
    in must be equivalent to the lookup, not just accepted and ignored."""
    sp, _ = species
    co = _specimen(session, "JJPC-10015", sp)
    det = next(d for d in co.determinations if d.is_current == 1)
    looked_up = export_decision(co)
    supplied = export_decision(co, determination=det)
    assert supplied == looked_up


def test_none_means_genuinely_undetermined_not_unsupplied(session):
    """The sentinel distinction this fix depends on: `determination=None` must be
    treated as "no current identification", the same as an unsupplied lookup finding
    none — NOT as "caller didn't say, fall back to the (N+1) lookup"."""
    co = _specimen(session, "JJPC-10016")
    assert export_decision(co, determination=None) == export_decision(co)
    assert export_decision(co, determination=None).reasons == \
        ("no current identification",)


def test_supplying_the_determination_skips_the_co_determinations_lookup(
    session, species, monkeypatch,
):
    """The actual regression, tested directly against the fix rather than against
    SQLAlchemy internals: `_current_determination` (the function that lazily walks
    `co.determinations`) must never be called at all when the caller already supplied
    the determination."""
    import app.services.dwc_export as dwc_export_module

    sp, _ = species
    co = _specimen(session, "JJPC-10017", sp)
    det = next(d for d in co.determinations if d.is_current == 1)

    def _boom(_co):
        raise AssertionError("_current_determination was called despite being supplied")

    monkeypatch.setattr(dwc_export_module, "_current_determination", _boom)
    export_decision(co, determination=det)   # must not raise


def test_determination_reasons_is_the_curatorial_subset_of_reasons(session, species):
    """`determination_reasons` (#170's question-mark badge) must equal `reasons` minus
    whatever `privacy_reasons` already accounts for — never double-counted, never dropped."""
    _, genus = species
    co = _specimen(session, "JJPC-10014", genus)
    co.confidential = 1
    session.flush()
    decision = export_decision(co)
    assert decision.determination_reasons == \
        tuple(r for r in decision.reasons if r not in decision.privacy_reasons)
    assert any("not to species" in r for r in decision.determination_reasons)
