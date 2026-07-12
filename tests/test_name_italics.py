"""Scientific names are italic only where the nomenclatural codes say so.

Every UI surface used to roll its own: taxon_search wrapped the WHOLE label — authorship
included — in <i>, and did so regardless of rank, so every family and tribe in the dropdown was
italicised along with its author. `taxa.scientific_name_html` is now the single owner.
"""
import pytest

from app.services.taxa import rank_is_italic, scientific_name_html as html


class TestOnlyTheGenusGroupAndBelowIsItalic:
    @pytest.mark.parametrize("rank", ["genus", "subgenus", "section", "subsection",
                                      "species", "subspecies", "variety", "form"])
    def test_genus_group_and_below(self, rank):
        assert rank_is_italic(rank)

    @pytest.mark.parametrize("rank", ["family", "subfamily", "tribe", "subtribe", "superfamily",
                                      "order", "suborder", "class", "phylum", "kingdom"])
    def test_above_the_genus_group_is_roman(self, rank):
        assert not rank_is_italic(rank)

    def test_a_family_is_not_italicised(self):
        assert html("Curculionidae", "family") == "Curculionidae"
        assert "<i>" not in html("Otiorhynchini", "tribe")

    def test_a_species_is(self):
        assert html("Otiorhynchus armadillo", "species") == "<i>Otiorhynchus armadillo</i>"

    def test_an_unknown_rank_asserts_no_convention(self):
        """A row can carry a rank outside our vocabulary (#96). Don't italicise on a guess."""
        assert "<i>" not in html("Something", "spec.")


class TestAuthorshipIsNeverItalic:
    def test_on_a_species(self):
        out = html("Otiorhynchus (Otiorhynchus) armadillo", "species", "(Rossi, 1792)")
        assert out == "<i>Otiorhynchus (Otiorhynchus) armadillo</i> (Rossi, 1792)"

    def test_on_a_family_nothing_is_italic_at_all(self):
        assert html("Curculionidae", "family", "Latreille, 1802") == \
            "Curculionidae Latreille, 1802"


class TestConnectorsAndQualifiersStayRoman:
    def test_open_nomenclature_qualifier(self):
        assert html("Otiorhynchus cf. forticollis", "species") == \
            "<i>Otiorhynchus</i> cf. <i>forticollis</i>"

    def test_sp_on_a_genus_row(self):
        assert html("Otiorhynchus sp.", "genus") == "<i>Otiorhynchus</i> sp."

    def test_icn_genus_group_connector(self):
        assert html("Taraxacum sect. Ruderalia", "section") == \
            "<i>Taraxacum</i> sect. <i>Ruderalia</i>"

    def test_icn_infraspecific_connector_with_author(self):
        assert html("Achillea millefolium var. alpina", "variety", "L.") == \
            "<i>Achillea millefolium</i> var. <i>alpina</i> L."


class TestSafety:
    def test_the_name_is_escaped(self):
        assert "&lt;" in html("Quercus <robur>", "species")

    def test_empty_is_empty(self):
        assert html("", "species") == ""
        assert html(None, "species") == ""
