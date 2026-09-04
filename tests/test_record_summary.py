"""The single record-summary renderer (app/ui/record_summary.py).

Every browse surface used to invent its own string, so nothing looked the same twice and the
rules (italics, authorship, confidentiality) were re-decided per surface — and got them wrong.
"""
import app.ui.record_summary as rs


class TestIdentityLine:
    def test_the_name_is_italic_by_rank_and_the_author_is_not(self):
        html = rs.specimen_html(catalog="JJPC-00021", name="Otiorhynchus armadillo",
                                rank="species", authorship="(Rossi, 1792)")
        assert "<i>Otiorhynchus armadillo</i>" in html
        assert "(Rossi, 1792)" in html and "<i>(Rossi" not in html

    def test_a_family_is_not_italicised(self):
        html = rs.specimen_html(catalog="X", name="Curculionidae", rank="family",
                                authorship="Latreille, 1802")
        assert "<i>" not in html

    def test_an_undetermined_specimen_says_so(self):
        html = rs.specimen_html(catalog="X", name="")
        assert "no identification" in html

    def test_a_doubt_qualifier_prefixes_the_whole_name(self):
        """cf./aff./nr./? go BEFORE the full name — "Genus cf. species" would wrongly
        imply the genus is certain and only the species tentative."""
        html = rs.specimen_html(catalog="X", name="Otiorhynchus armadillo", rank="species",
                                authorship="(Rossi, 1792)", qualifier="cf.")
        assert "cf. <i>Otiorhynchus armadillo</i> (Rossi, 1792)" in html

    def test_a_scope_qualifier_suffixes_the_name(self):
        html = rs.specimen_html(catalog="X", name="Otiorhynchus", rank="genus",
                                qualifier="sp.")
        assert "<i>Otiorhynchus</i> sp." in html

    def test_the_qualifier_reaches_the_plain_twin(self):
        plain = rs.specimen_plain(catalog="X", name="Otiorhynchus armadillo",
                                  authorship="(Rossi, 1792)", qualifier="cf.")
        assert "cf. Otiorhynchus armadillo" in plain
        assert "<" not in plain


class TestHostCarriesItsRelationship:
    """A plant name beside a beetle says nothing about how they met."""

    def test_the_relationship_is_shown(self):
        html = rs.hosts_html([("collected from", "Quercus robur", "species")])
        assert "collected from" in html
        assert "<i>Quercus robur</i>" in html

    def test_several_associations_collapse_to_the_first_plus_a_count(self):
        html = rs.hosts_html([("collected from", "Quercus robur", "species"),
                              ("feeds on", "Betula", "genus")])
        assert "Quercus robur" in html and "+1" in html
        assert "Betula" not in html          # the summary stays one line

    def test_no_association_renders_nothing(self):
        assert rs.hosts_html([]) == ""
        assert rs.hosts_html(None) == ""

    def test_it_reaches_the_specimen_row(self):
        html = rs.specimen_html(catalog="X", name="Curculio glandium", rank="species",
                                hosts=[("collected from", "Quercus robur", "species")])
        assert "collected from <i>Quercus robur</i>" in html

    def test_a_host_qualifier_is_placed_in_the_host_name(self):
        """The host tuple's optional 4th element is the host determination's qualifier."""
        html = rs.hosts_html([("collected from", "Quercus robur", "species", "cf.")])
        assert "collected from cf. <i>Quercus robur</i>" in html


class TestConfidentiality:
    """A closed amber padlock at the end of the line — and NOTHING when the record is public.
    An open padlock on every public record would be clutter on the 99% case."""

    def test_a_public_record_shows_no_icon_at_all(self):
        assert rs.lock_html() == ""
        assert "lock" not in rs.specimen_html(catalog="X", name="Curculio", rank="genus")

    def test_the_specimens_own_flag_shows_the_padlock(self):
        html = rs.specimen_html(catalog="X", name="Curculio", rank="genus", confidential=True)
        assert ">lock<" in html
        assert "this specimen is flagged confidential" in html

    def test_it_is_INHERITED_from_a_confidential_event(self):
        """A confidential event withholds every specimen collected at it — so the specimen is
        withheld even though its own flag is clear."""
        html = rs.specimen_html(catalog="X", name="Curculio", rank="genus",
                                event_confidential=True)
        assert ">lock<" in html
        assert "collecting event is confidential" in html

    def test_both_flags_are_reported_together(self):
        reason = rs.confidential_reason(own=True, from_event=True)
        assert "specimen is flagged" in reason and "collecting event is too" in reason

    def test_one_glyph_means_one_thing(self):
        """Own vs inherited are the same STATE (withheld); only the tooltip differs."""
        a = rs.lock_html(own=True)
        b = rs.lock_html(from_event=True)
        assert ">lock<" in a and ">lock<" in b


class TestConsentBadge:
    """The recordedBy person's export handling (#170) — a `person_off` glyph, distinct from
    the padlock, in one of three colours per `export_decision`'s three sub-states."""

    def test_no_state_shows_no_icon(self):
        assert rs.consent_badge_html() == ""
        assert rs.consent_badge_html("") == ""
        assert "person_off" not in rs.specimen_html(catalog="X", name="Curculio", rank="genus")

    def test_confidential_is_red(self):
        html = rs.consent_badge_html("confidential")
        assert ">person_off<" in html
        assert rs._PERSON_RED in html
        # Names the collector as the reason and the whole-record consequence — not a bare
        # "Confidential", which said neither (the most severe state had the least useful tip).
        assert "the collector (recordedBy) is flagged confidential" in html
        assert "withholds the whole record" in html

    def test_blocked_is_amber(self):
        html = rs.consent_badge_html("blocked")
        assert ">person_off<" in html
        assert rs._LOCK_AMBER in html
        assert "has not consented" in html

    def test_redacted_is_grey_and_not_phrased_as_withheld(self):
        """Live review: this specimen IS still exported, just with the name removed — the
        tooltip must not say "withheld", and the colour is informational, not a warning."""
        html = rs.consent_badge_html("redacted")
        assert ">person_off<" in html
        assert "var(--tp-base-soft)" in html
        assert "Will be exported with the collector’s name removed" in html
        assert "Withheld" not in html

    def test_it_reaches_the_specimen_row(self):
        html = rs.specimen_html(catalog="X", name="Curculio", rank="genus",
                                recorded_by_state="confidential")
        assert ">person_off<" in html

    def test_an_unrecognised_state_renders_nothing_rather_than_raising(self):
        """Code review fix: every caller sources `state` from `export_decision`'s
        closed set today, but `_render_specimens`/`_render_events` build a whole
        results panel's worth of rows in one loop — an unhandled KeyError from one
        bad value would take out every OTHER row's render with it. A missing badge
        is the safe degradation; a page-killing exception is not."""
        assert rs.consent_badge_html("something_new") == ""


class TestIdentificationDoubtBadge:
    """Why the CURRENT identification isn't publishable (#170) — an orange question mark,
    tooltip listing whichever of the curatorial reasons apply."""

    def test_no_reasons_shows_no_icon(self):
        assert rs.identification_doubt_badge_html(()) == ""
        assert "question_mark" not in rs.specimen_html(catalog="X", name="Curculio",
                                                        rank="genus")

    def test_one_reason_is_shown_in_the_tooltip(self):
        html = rs.identification_doubt_badge_html(("identified only to genus — not to species",))
        assert ">question_mark<" in html
        assert rs._LOCK_AMBER in html
        assert "identified only to genus" in html

    def test_several_reasons_join_in_one_badge_not_several(self):
        """Same convention as the padlock's own/from_event: multiple sub-reasons collapse into
        ONE icon whose tooltip lists all of them, not one icon per reason."""
        html = rs.identification_doubt_badge_html((
            "identification is qualified 'cf.' — expresses doubt",
            "identified only to genus — not to species",
        ))
        assert html.count("question_mark") == 1
        assert "qualified" in html and "not to species" in html

    def test_it_reaches_the_specimen_row(self):
        html = rs.specimen_html(catalog="X", name="Curculio", rank="genus",
                                determination_reasons=("no current identification",))
        assert ">question_mark<" in html


class TestThePlainTextTwin:
    """A q-select filters against the plain label and echoes it into its input — it cannot hold
    markup. It must still carry every searchable datum."""

    def test_it_carries_everything_searchable(self):
        plain = rs.specimen_plain(
            catalog="JJPC-00021", name="Otiorhynchus armadillo", authorship="(Rossi, 1792)",
            hosts=[("collected from", "Quercus robur", "species")],
            sex="male", count=3, locality="Bodenmöser, Germany", event_date="2026-06-13",
            recorded_by="J. Jilg", identified_by="A. Meyer")
        for token in ("JJPC-00021", "Otiorhynchus armadillo", "(Rossi, 1792)", "Quercus robur",
                      "male", "3×", "Bodenmöser", "2026-06-13", "leg. J. Jilg", "det. A. Meyer"):
            assert token in plain, token

    def test_it_contains_no_markup(self):
        plain = rs.specimen_plain(catalog="X", name="Curculio glandium",
                                  hosts=[("collected from", "Quercus robur", "species")])
        assert "<" not in plain and ">" not in plain


class TestEventRow:
    def test_a_confidential_event_shows_the_padlock(self):
        assert ">lock<" in rs.event_html(summary="Bodenmöser", confidential=True)

    def test_a_public_event_does_not(self):
        assert "lock" not in rs.event_html(summary="Bodenmöser", n_specimens=3)


class TestEscaping:
    def test_text_is_escaped(self):
        html = rs.specimen_html(catalog="<script>", name="Curculio", rank="genus",
                                locality="a & b")
        assert "&lt;script&gt;" in html and "a &amp; b" in html


class TestAnIconIsNotText:
    """The padlock was an <i> — and `.rs-row i { font-style: italic }` (the rule that italicises
    the NAME) reached it, so the glyph rendered skewed. An icon is not text and is never italic.
    """

    def test_the_lock_is_not_an_i_element(self):
        html = rs.lock_html(own=True)
        assert "<i " not in html and "<i>" not in html
        assert "material-icons" in html

    def test_the_css_forbids_an_italic_icon_outright(self):
        """Belt and braces: an icon may land inside any of the app's italics rules
        (.rs-row i, .tw-result i, .rank-genus …), so the guard is global and !important."""
        assert ".material-icons" in rs.CSS
        assert "font-style:normal !important" in rs.CSS
