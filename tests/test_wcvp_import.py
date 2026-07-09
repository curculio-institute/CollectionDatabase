"""WCVP → local taxon rows (app/services/taxa.py::get_or_create_from_wcvp_data).

Drives the real extraction seam (`wcvp.fields_from_wcvp`) against a miniature index, so the
lineage rules and the DB writer are exercised together rather than against a hand-built dict.

The rule that matters most here is Epic #30's own-lineage parenting: a synonym is parented
under its OWN genus, never its accepted name's. Getting that wrong renames the synonym,
because `scientific_name` is composed from the parent chain — see docs/plant_names.md §5.
"""
import pytest

from app.models import Taxon
from app.services import wcvp
from app.services.taxa import get_or_create_from_wcvp_data

from tests.test_wcvp import _archive, _genus, _row


@pytest.fixture
def index(tmp_path):
    archive = _archive(tmp_path, [
        _genus("10", "Quercus", auth="L.", family="Fagaceae", ipni="77210-1"),
        _row("11", "Quercus robur", accepted="11", parent="10", ipni="304293-2"),
        _row("12", "Quercus robur subsp. brutia", rank="Subspecies", auth="(Ten.) O.Schwarz",
             accepted="12", parent="11", family="Fagaceae", genus="Quercus"),
        # Cytisus scoparius, accepted, and its synonym Sarothamnus scoparius in ANOTHER genus
        _genus("30", "Cytisus", auth="Desf.", family="Fabaceae"),
        _row("31", "Cytisus scoparius", auth="(L.) Link", accepted="31", parent="30",
             family="Fabaceae", genus="Cytisus"),
        _genus("20", "Sarothamnus", auth="Wimm.", family="Fabaceae", status="Synonym"),
        _row("21", "Sarothamnus scoparius", auth="(L.) Wimm.", status="Synonym",
             accepted="31", family="Fabaceae", genus="Sarothamnus"),
        # infraspecific synonym: no parent_id, species name only inside its own name
        _row("22", "Sarothamnus scoparius var. bicolor", rank="Variety", auth="Stubbe",
             status="Synonym", accepted="31", family="Fabaceae", genus="Sarothamnus"),
        # ambiguous genus (two non-accepted rows, same family) → no authorship may be invented
        _genus("40", "Ascyrum", auth="L.", family="Hypericaceae", status="Synonym"),
        _genus("41", "Ascyrum", auth="Mill.", family="Hypericaceae", status="Illegitimate"),
        _row("42", "Ascyrum plumieri", auth="Steud.", status="Synonym", accepted="31",
             family="Hypericaceae", genus="Ascyrum"),
        # refused
        _row("60", "Juglans gonroku", auth="Makino", status="Unplaced",
             family="Juglandaceae", genus="Juglans"),
        _row("61", "Quercus officinalis", auth="Thunb.", status="Misapplied",
             accepted="11", family="Fagaceae", genus="Quercus"),
        _row("62", "Paeonia corallina proles russoi", rank="proles", auth="N.Terracc.",
             accepted="11", family="Paeoniaceae", genus="Paeonia"),
        _row("70", "Quercus dangling", status="Synonym", accepted="99999",
             family="Fagaceae", genus="Quercus"),
    ])
    db_path = tmp_path / "wcvp.sqlite"
    wcvp.build_index(archive, db_path)
    return wcvp.open_index(db_path)


def _import(session, index, taxonid):
    fields = wcvp.fields_from_wcvp(index, wcvp.get(index, taxonid))
    return get_or_create_from_wcvp_data(session, fields)


def test_accepted_species_creates_family_genus_species(session, index):
    sp = _import(session, index, "11")
    assert sp.scientific_name == "Quercus robur"
    assert sp.taxon_rank == "species"
    assert sp.nomenclatural_code == "ICN"
    assert sp.accepted_name_usage_id is None
    genus = session.get(Taxon, sp.parent_name_usage_id)
    assert (genus.scientific_name, genus.taxon_rank, genus.scientific_name_authorship) == \
        ("Quercus", "genus", "L.")
    family = session.get(Taxon, genus.parent_name_usage_id)
    assert (family.scientific_name, family.taxon_rank) == ("Fagaceae", "family")
    # WCVP has no family rows, so a family authorship cannot be invented
    assert family.scientific_name_authorship is None
    assert family.nomenclatural_code == "ICN"


def test_ipni_id_is_captured(session, index):
    assert _import(session, index, "11").ipni_id == "304293-2"


def test_synonym_is_parented_under_its_own_genus(session, index):
    """Epic #30. The accepted name is Cytisus scoparius; the synonym must stay Sarothamnus."""
    syn = _import(session, index, "21")
    assert syn.scientific_name == "Sarothamnus scoparius"     # NOT "Cytisus scoparius"
    genus = session.get(Taxon, syn.parent_name_usage_id)
    assert genus.scientific_name == "Sarothamnus"
    accepted = session.get(Taxon, syn.accepted_name_usage_id)
    assert accepted.scientific_name == "Cytisus scoparius"
    assert session.get(Taxon, accepted.parent_name_usage_id).scientific_name == "Cytisus"


def test_synonym_does_not_merge_into_its_accepted_name(session, index):
    """Parenting under the accepted genus would compose 'Cytisus scoparius' and get_or_create
    would return the accepted row: the synonym vanishes, and its determinations with it."""
    syn = _import(session, index, "21")
    acc = session.get(Taxon, syn.accepted_name_usage_id)
    assert syn.id != acc.id
    assert session.query(Taxon).filter(Taxon.scientific_name == "Cytisus scoparius").count() == 1


def test_infraspecific_synonym_gets_its_species_parent_from_its_own_name(session, index):
    """WCVP gives synonyms no parent_id, so the species is cut out of the name string."""
    var = _import(session, index, "22")
    assert var.taxon_rank == "variety"
    assert var.scientific_name == "Sarothamnus scoparius var. bicolor"
    species = session.get(Taxon, var.parent_name_usage_id)
    assert (species.scientific_name, species.taxon_rank) == ("Sarothamnus scoparius", "species")


def test_accepted_infraspecific_uses_the_icn_connector(session, index):
    ssp = _import(session, index, "12")
    assert ssp.scientific_name == "Quercus robur subsp. brutia"


def test_ambiguous_genus_is_created_without_inventing_an_authorship(session, index):
    """Ascyrum L. (Synonym) vs Ascyrum Mill. (Illegitimate): the name is certain, the author
    is not. Silence asserts nothing false; picking one would."""
    sp = _import(session, index, "42")
    genus = session.get(Taxon, sp.parent_name_usage_id)
    assert genus.scientific_name == "Ascyrum"
    assert genus.scientific_name_authorship is None


def test_unplaced_is_refused(session, index):
    with pytest.raises(wcvp.NotImportable, match="no accepted placement"):
        _import(session, index, "60")


def test_misapplied_is_refused(session, index):
    with pytest.raises(wcvp.NotImportable, match="applied to Quercus robur"):
        _import(session, index, "61")


def test_unmodelled_rank_is_refused(session, index):
    with pytest.raises(wcvp.NotImportable, match="does not model the rank"):
        _import(session, index, "62")


def test_dangling_accepted_link_is_refused_not_invented(session, index):
    with pytest.raises(wcvp.NotImportable, match="not in the archive"):
        _import(session, index, "70")


def test_reimport_is_idempotent(session, index):
    first = _import(session, index, "11")
    session.flush()
    before = session.query(Taxon).count()
    second = _import(session, index, "11")
    assert first.id == second.id
    assert session.query(Taxon).count() == before


def test_local_values_win_over_the_import(session, index):
    """The DB is the source of truth: a conflicting non-NULL local value is reported, not
    overwritten."""
    sp = _import(session, index, "11")
    sp.scientific_name_authorship = "Linnaeus"
    session.flush()
    mismatches: list[str] = []
    fields = wcvp.fields_from_wcvp(index, wcvp.get(index, "11"))
    get_or_create_from_wcvp_data(session, fields, mismatches=mismatches)
    assert sp.scientific_name_authorship == "Linnaeus"
    assert any("authorship is 'Linnaeus' locally" in m for m in mismatches)


def test_importing_a_synonym_creates_the_accepted_name_too(session, index):
    _import(session, index, "21")
    names = {t.scientific_name for t in session.query(Taxon).all()}
    assert {"Sarothamnus scoparius", "Cytisus scoparius", "Sarothamnus", "Cytisus",
            "Fabaceae"} <= names


def test_a_synonym_can_never_become_its_own_accepted_name(session, index):
    """The backstop for the lineage bug this importer exists to avoid.

    Feed it the tempting-but-wrong lineage (genus taken from the accepted name) and it must
    refuse. Without the guard the synonym composes to 'Cytisus scoparius', get_or_create
    returns the accepted row, and the two merge: the synonym vanishes and its determinations
    silently become determinations of the accepted name. The accepted-is-terminal triggers do
    not catch it, because the self-link target is itself accepted.
    """
    fields = wcvp.fields_from_wcvp(index, wcvp.get(index, "21"))
    fields["genus"] = "Cytisus"                      # the bug, injected
    with pytest.raises(ValueError, match="its own accepted name"):
        get_or_create_from_wcvp_data(session, fields)


def test_genus_ipni_id_is_captured_on_the_ancestor_row(session, index):
    """resolve_genus() knows the genus row; its identifier must not be dropped on the way
    into the ancestor. The id can only be captured at import (#99)."""
    sp = _import(session, index, "11")
    genus = session.get(Taxon, sp.parent_name_usage_id)
    assert genus.scientific_name == "Quercus"
    assert genus.ipni_id == "77210-1"
