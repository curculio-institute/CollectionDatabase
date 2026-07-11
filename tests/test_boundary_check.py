"""#110 — the uncertainty-circle boundary check.

The bug was a category error: the centre came from Overpass **containment** and the four
perimeter samples from Photon **proximity**. The two answer different questions and report
different administrative tiers, so at a Peloponnese point the centre said `Peloponnese Region`
(the ISO region, GR-J, L5) while Photon said `Peloponnese, Western Greece and the Ionian` (the
L4 Decentralized Administration). Centre and perimeter could never agree → a false crossing on
every Greek lookup, and clicking the offered value wrote the wrong stateProvince into the record.

These tests pin the two halves of the fix without touching the network:
  1. every sample is answered by ONE Overpass request, attributed back by its stamped index;
  2. a failed request warns about nothing and claims nothing.
"""
import asyncio

import pytest

import app.ui.collecting_event_form as cef


def _adm(i: str, **tags) -> dict:
    """A `convert`ed admin relation as Overpass returns it (absent tags come back as '')."""
    base = {"i": i, "lvl": "", "nm": "", "en": "", "iso1": "", "iso2": ""}
    base.update(tags)
    return {"type": "adm", "tags": base}


# Greece: the state is identified by its ISO3166-2 tag (GR-J at L5), NOT by admin_level —
# the L4 above it is the Decentralized Administration, which is what Photon used to report.
_GREECE_CENTRE = [
    _adm("0", lvl="2", nm="Ελλάδα", en="Greece", iso1="GR"),
    _adm("0", lvl="4", nm="Peloponnese, Western Greece and the Ionian"),
    _adm("0", lvl="5", nm="Περιφέρεια Πελοποννήσου", en="Peloponnese Region", iso2="GR-J"),
    _adm("0", lvl="6", nm="Arcadia Regional Unit"),
    _adm("0", lvl="7", nm="Municipality of Tripoli"),
]


class TestOneRequestManySamples:
    def test_the_query_stamps_every_sample_so_rows_can_be_attributed_back(self, monkeypatch):
        """`is_in` yields AREAS, so relation(pivot.…) is required — rel.pN[…] silently matches
        nothing — and `convert` stamps the sample index."""
        seen = {}

        async def _fake_post(query, **kw):
            seen["q"] = query
            return [], None

        monkeypatch.setattr(cef, "_overpass_post", _fake_post)
        asyncio.run(cef._boundary_hierarchies([(1.0, 2.0), (3.0, 4.0), (5.0, 6.0)]))

        q = seen["q"]
        # ONE request, not one per point (the public instance grants ~2 concurrent slots).
        for i in range(3):
            assert f"is_in(" in q
            assert f"->.p{i};" in q
            assert f"relation(pivot.p{i})[boundary=administrative][name];" in q
            assert f'i="{i}"' in q
        assert q.count("is_in(") == 3

    def test_rows_are_attributed_to_their_own_sample(self, monkeypatch):
        """Two points in different countries must not bleed into each other's hierarchy."""
        async def _fake_post(query, **kw):
            return [
                _adm("0", lvl="2", nm="Germany", iso1="DE"),
                _adm("0", lvl="4", nm="Bavaria", iso2="DE-BY"),
                _adm("1", lvl="2", nm="France", iso1="FR"),
                _adm("1", lvl="4", nm="Grand Est", iso2="FR-GES"),
            ], None

        monkeypatch.setattr(cef, "_overpass_post", _fake_post)
        hiers = asyncio.run(cef._boundary_hierarchies([(0, 0), (1, 1)]))

        assert [h["country"] for h in hiers] == ["Germany", "France"]
        assert [h["state"] for h in hiers] == ["Bavaria", "Grand Est"]
        # The ISO 3166-2 code rides along — Photon never carried one, so a state picked from a
        # boundary warning used to be stored uncoded. (name, iso_code) is the vocab's identity.
        assert [h["state_code"] for h in hiers] == ["DE-BY", "FR-GES"]

    def test_an_unstamped_or_foreign_row_is_ignored(self, monkeypatch):
        async def _fake_post(query, **kw):
            return [
                _adm("0", lvl="2", nm="Germany", iso1="DE"),
                {"type": "relation", "tags": {"name": "not a converted row"}},
                _adm("", lvl="2", nm="No index"),
                _adm("9", lvl="2", nm="Out of range"),
            ], None

        monkeypatch.setattr(cef, "_overpass_post", _fake_post)
        hiers = asyncio.run(cef._boundary_hierarchies([(0, 0)]))
        assert len(hiers) == 1
        assert hiers[0]["country"] == "Germany"


class TestGreeceNoLongerDisagreesWithItself:
    def test_centre_and_perimeter_resolve_to_the_SAME_tier(self, monkeypatch):
        """#110's exact failure. Both are containment now, so both pick the ISO-tagged L5 —
        and the L4 Decentralized Administration (Photon's answer) is never chosen."""
        async def _fake_post(query, **kw):
            rows = list(_GREECE_CENTRE)
            for i in ("1", "2", "3", "4"):          # four perimeter samples, same areas
                rows += [dict(r, tags=dict(r["tags"], i=i)) for r in _GREECE_CENTRE]
            return rows, None

        monkeypatch.setattr(cef, "_overpass_post", _fake_post)
        hiers = asyncio.run(cef._boundary_hierarchies([(37.5089, 22.3745)] * 5))

        for h in hiers:
            assert h["state"] == "Peloponnese Region"
            assert h["state_code"] == "GR-J"
            assert h["state"] != "Peloponnese, Western Greece and the Ionian"
        # Every sample agrees with the centre → no crossing is reported.
        assert len({(h["country"], h["state"], h["county"], h["municipality"])
                    for h in hiers}) == 1


class TestFailureClaimsNothing:
    def test_a_failed_request_returns_None_rather_than_an_empty_hierarchy(self, monkeypatch):
        """An empty result would read as "this point lies in no administrative area" — a silent
        wrong answer (§2). The caller must warn about nothing AND show no ✓."""
        async def _fake_post(query, **kw):
            return None, "504 from Overpass"

        monkeypatch.setattr(cef, "_overpass_post", _fake_post)
        assert asyncio.run(cef._boundary_hierarchies([(0, 0), (1, 1)])) is None


class TestTierRulesAreSharedWithTheCentre:
    def test_the_state_is_found_by_its_ISO_tag_not_by_admin_level(self):
        """The level of the ISO state differs by country (DE-BY at L4, GR-J at L5), so a
        positional rule is wrong. _resolve_hierarchy is the single owner of this — the
        perimeter samples go through the very same function."""
        greece = cef._resolve_hierarchy([r["tags"] for r in _GREECE_CENTRE])
        assert greece["state"] == "Peloponnese Region"
        assert greece["state_code"] == "GR-J"

        germany = cef._resolve_hierarchy([
            {"lvl": "2", "nm": "Deutschland", "en": "Germany", "iso1": "DE", "iso2": ""},
            {"lvl": "4", "nm": "Bayern", "en": "Bavaria", "iso1": "", "iso2": "DE-BY"},
            {"lvl": "5", "nm": "Regierungsbezirk Schwaben", "en": "", "iso1": "", "iso2": ""},
            {"lvl": "6", "nm": "Augsburg", "en": "", "iso1": "", "iso2": ""},
        ])
        assert germany["state"] == "Bavaria" and germany["state_code"] == "DE-BY"
        # The L5 here is a Regierungsbezirk, not the state — it must not be mistaken for one.
        assert germany["region"] == "Regierungsbezirk Schwaben"
