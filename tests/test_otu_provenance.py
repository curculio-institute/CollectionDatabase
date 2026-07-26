"""The OTU-id provenance stamp: easy to distrust, hard to trust (#149, 2026-07-26).

`AppConfig.tw_otu_instance` records the host the stored `taxon.taxonworksOtuID` values
were captured from; `otu_instance_state()` reports them trustworthy only while it matches
the configured instance. **The same integer denotes a different entity — or nothing — on
another server**, so that stamp is the only thing standing between a stored id and a
silently wrong claim.

Two rules keep it honest, and they are deliberately asymmetric:

* **Setting it requires DB-wide evidence** — `reconcile_otu_ids` stamps only when no
  taxon *anywhere* still holds an id it could not vouch for.
* **Clearing it takes one counterexample** — `invalidate_otu_provenance` retracts it as
  soon as any check finds a stored id this instance does not recognise.

This is not hypothetical. An earlier `reconcile_otu_ids` stamped after a *partial*
reconcile; the resulting false stamp claimed all 39 stored ids belonged to
sandbox.taxonworks.org when 28 were sfg ids resolving to nothing there — with the tab's
warning suppressed precisely because the stamp said all was well.
"""
import pytest

import app.config as config_mod
from app.config import AppConfig
from app.models import Taxon
from app.models.base import _utcnow
from app.services.tw_sync import (
    NameCheck, TwCandidate, invalidate_otu_provenance, reconcile_otu_ids,
    set_stored_otu_id, taxa_with_stored_otu_ids,
)


@pytest.fixture
def cfg(tmp_path, monkeypatch):
    """An isolated config file — these tests write provenance, never the real one."""
    monkeypatch.setattr(config_mod, "_CONFIG_PATH", tmp_path / "config.json")
    monkeypatch.setattr(config_mod, "_instance", None)
    cfg = AppConfig(tw_base="https://sandbox.taxonworks.org/api/v1",
                    tw_otu_instance="sandbox.taxonworks.org")
    config_mod.save_config(cfg)
    return cfg


def _check(verdict: str, taxon_id: int = 1, stored: int | None = 42,
           status: str = "match", otu_id: int | None = None,
           candidates: tuple = ()) -> NameCheck:
    return NameCheck(
        taxon_id=taxon_id, local_name="Otiorhynchus", local_authorship=None,
        local_rank="genus", status=status, matched=None, candidates=candidates,
        notes=(), stored_otu_id=stored, otu_id=otu_id, stored_otu_verdict=verdict,
    )


def _candidate(name: str, authorship: str) -> TwCandidate:
    return TwCandidate(taxon_name_id=1, cached=name, authorship=authorship,
                       rank="tribe", is_valid=True, valid_taxon_name_id=None,
                       nomenclatural_code="ICZN")


def _taxon(session, element, rank, otu_id=None):
    tx = Taxon(name_element=element, scientific_name=element, taxon_rank=rank,
               nomenclatural_code="ICZN", taxonworks_otu_id=otu_id,
               created_at=_utcnow(), updated_at=_utcnow())
    session.add(tx)
    session.flush()
    return tx


# ── invalidate_otu_provenance ───────────────────────────────────────────────────

@pytest.mark.parametrize("verdict", ["absent", "mismatch"])
def test_a_single_counterexample_retracts_the_stamp(cfg, verdict):
    """`absent` = the id denotes nothing here; `mismatch` = it denotes something else.
    Either one proves the stamp wrong for at least one row, and the stamp is a claim
    about every row."""
    assert invalidate_otu_provenance([_check("confirmed"), _check(verdict)]) is True
    assert config_mod.get_config().tw_otu_instance == ""


def test_all_confirmed_leaves_the_stamp_alone(cfg):
    assert invalidate_otu_provenance([_check("confirmed"), _check("confirmed")]) is False
    assert config_mod.get_config().tw_otu_instance == "sandbox.taxonworks.org"


def test_unchecked_is_not_evidence(cfg):
    """No stored id, or a lookup that failed — absence of evidence is not evidence."""
    assert invalidate_otu_provenance([_check("unchecked", stored=None)]) is False
    assert config_mod.get_config().tw_otu_instance == "sandbox.taxonworks.org"


def test_it_never_sets_the_stamp_only_clears_it(cfg):
    """The asymmetry: trust must be earned DB-wide by `reconcile_otu_ids`, never as a
    side effect of a check that happened to find everything confirmed."""
    cfg2 = config_mod.get_config()
    cfg2.tw_otu_instance = ""
    config_mod.save_config(cfg2)
    assert invalidate_otu_provenance([_check("confirmed")]) is False
    assert config_mod.get_config().tw_otu_instance == ""


def test_no_checks_at_all_changes_nothing(cfg):
    assert invalidate_otu_provenance([]) is False
    assert config_mod.get_config().tw_otu_instance == "sandbox.taxonworks.org"


# ── taxa_with_stored_otu_ids ────────────────────────────────────────────────────

def test_it_returns_exactly_the_rows_the_stamp_speaks_for(session):
    """Every taxon carrying an id — including the ancestor rows a chain import stamps,
    which no export ever names and which the old reconcile scope could not reach."""
    _taxon(session, "Polyphaga", "suborder", otu_id=708183)
    _taxon(session, "Curculionidae", "family", otu_id=708970)
    _taxon(session, "Nowhere", "genus")            # no id — not the stamp's business
    got = taxa_with_stored_otu_ids(session)
    assert [t.name_element for t in got] == ["Curculionidae", "Polyphaga"]


def test_a_database_with_no_stored_ids_yields_nothing(session):
    _taxon(session, "Unstamped", "genus")
    assert taxa_with_stored_otu_ids(session) == []


def test_ancestors_are_included_even_though_no_export_names_them(session):
    """The regression this scope exists for: ids live on ranks an export never carries,
    so a reconcile scoped to export names left `unvouched` permanently non-zero."""
    _taxon(session, "Entiminae", "subfamily", otu_id=712818)
    assert [t.taxon_rank for t in taxa_with_stored_otu_ids(session)] == ["subfamily"]


# ── the unvouched list: a count you can act on ──────────────────────────────────

def test_unvouched_rows_say_which_taxon_and_why(session, cfg):
    """A bare count reports a problem and offers no way to act on it. Each row names
    the taxon, what it still stores, and why the automatic match refused."""
    amb = _taxon(session, "Otiorhynchini", "tribe", otu_id=999999)
    gone = _taxon(session, "Phellos", "species", otu_id=736378)
    res = reconcile_otu_ids(session, [
        _check("absent", taxon_id=amb.id, stored=999999, status="ambiguous",
               candidates=(_candidate("Otiorhynchini", "Schoenherr, 1826"),
                           _candidate("Otiorhynchini", "Schönherr, 1826"))),
        _check("absent", taxon_id=gone.id, stored=736378, status="missing"),
    ])
    assert res.unvouched == 2 and res.provenance_recorded is False
    rows = {r.name: r for r in res.unvouched_rows}
    assert rows["Otiorhynchini"].reason == "ambiguous"
    assert rows["Otiorhynchini"].stored_otu_id == 999999
    # The candidates ride along: choosing between homonyms is the user's judgement,
    # which is exactly why check_names refused to make it.
    assert [c.authorship for c in rows["Otiorhynchini"].candidates] == [
        "Schoenherr, 1826", "Schönherr, 1826"]
    assert rows["Phellos"].reason == "missing"
    assert rows["Phellos"].candidates == ()


def test_a_taxon_outside_the_checked_set_says_so(session, cfg):
    """Never imply TaxonWorks answered something about a name nobody asked about."""
    _taxon(session, "Unasked", "genus", otu_id=708183)
    res = reconcile_otu_ids(session, [])
    assert [(r.name, r.reason) for r in res.unvouched_rows] == [("Unasked", "not checked")]


def test_a_confirmed_stored_id_is_vouched_and_absent_from_the_list(session, cfg):
    tx = _taxon(session, "Fine", "genus", otu_id=1285369)
    res = reconcile_otu_ids(session, [_check("confirmed", taxon_id=tx.id,
                                             stored=1285369)])
    assert res.unvouched == 0 and res.unvouched_rows == ()
    # A clean sweep is the only thing that earns the stamp.
    assert res.provenance_recorded is True
    assert config_mod.get_config().tw_otu_instance == "sandbox.taxonworks.org"


# ── set_stored_otu_id — the manual escape hatch ─────────────────────────────────

def test_setting_an_id_by_hand(session):
    tx = _taxon(session, "Otiorhynchini", "tribe", otu_id=999999)
    set_stored_otu_id(session, tx.id, 1299727)
    assert session.get(Taxon, tx.id).taxonworks_otu_id == 1299727


def test_clearing_is_a_real_fix_not_a_cop_out(session):
    """When a name is not on this instance, an id captured elsewhere is a false claim
    about this server; an empty column claims nothing."""
    tx = _taxon(session, "Phellos", "species", otu_id=736378)
    set_stored_otu_id(session, tx.id, None)
    assert session.get(Taxon, tx.id).taxonworks_otu_id is None


def test_an_unknown_taxon_is_refused_loudly(session):
    with pytest.raises(ValueError, match="no taxon with id"):
        set_stored_otu_id(session, 999999, 1)


def test_a_manual_fix_never_grants_provenance(session, cfg):
    """Trust is a DB-wide judgement; one hand-set row cannot confer it."""
    cfg2 = config_mod.get_config()
    cfg2.tw_otu_instance = ""
    config_mod.save_config(cfg2)
    tx = _taxon(session, "Otiorhynchini", "tribe", otu_id=999999)
    set_stored_otu_id(session, tx.id, 1299727)
    assert config_mod.get_config().tw_otu_instance == ""
