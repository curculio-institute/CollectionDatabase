"""Which named OSM features may become a collecting locality (decided 2026-07-12).

Two questions, two mechanisms, and the tests keep them apart:

  * MAY it be shown?      → _locality_eligible(): allowlist of KEYS, blocklist of VALUES inside
    them, so OSM's long tail comes along for free and a new key can never surprise us.
  * MAY it be auto-filled? → _LOCALITY_KV: an allowlist of (key, value) with a priority. A tag
    nobody ranked is offered but never written (CLAUDE.md §2 — no silent wrong value).
"""
import pytest

from app.ui.collecting_event_form import (
    _LOCALITY_KV, _MAX_LOCALITY_POINTS, _admin_name, _locality_eligible, _resolve_hierarchy,
)


class TestEligibility:
    @pytest.mark.parametrize("key,value", [
        ("natural", "heath"), ("natural", "peak"), ("natural", "wetland"),
        ("boundary", "protected_area"), ("leisure", "nature_reserve"),
        ("landuse", "forest"), ("landuse", "wood"), ("landuse", "meadow"),
        ("place", "locality"), ("place", "hamlet"), ("place", "island"),
        ("water", "pond"), ("waterway", "stream"),
    ])
    def test_places_are_eligible(self, key, value):
        assert _locality_eligible(key, value)

    @pytest.mark.parametrize("key,value", [
        # The noise that flooded the menu — every one of these was measured at a real point.
        ("building", "dormitory"),      # 10 of them at the Flugplatzheide
        ("information", "board"),       # 4 info panels, all named "Flugplatzheide"
        ("highway", "secondary"), ("highway", "primary"), ("highway", "pedestrian"),
        ("shop", "supermarket"), ("shop", "curtain"),
        ("amenity", "restaurant"), ("amenity", "fuel"), ("amenity", "charging_station"),
        ("tourism", "hotel"), ("tourism", "museum"),
        ("railway", "tram_stop"), ("aerialway", "cable_car"), ("man_made", "tower"),
    ])
    def test_non_places_are_never_shown(self, key, value):
        assert not _locality_eligible(key, value)

    @pytest.mark.parametrize("value", ["tree", "tree_row", "tree_group", "tree_stump", "shrub"])
    def test_individual_plants_are_not_places(self, value):
        """natural=tree alone is ~34 M objects — by far the biggest noise source Photon has."""
        assert not _locality_eligible("natural", value)

    def test_a_coastline_is_a_line_not_a_place(self):
        assert not _locality_eligible("natural", "coastline")

    @pytest.mark.parametrize("value", ["residential", "industrial", "retail", "farmyard",
                                       "recreation_ground", "winter_sports"])
    def test_landuse_is_places_only_for_natural_cover(self, value):
        """A named landuse polygon carries the toponym of the wood/meadow ("Bodener Wald");
        everything else under the key is settlement or sports."""
        assert not _locality_eligible("landuse", value)

    @pytest.mark.parametrize("value", ["county", "municipality", "state", "region", "country",
                                       "square", "city_block", "plot"])
    def test_administrative_place_values_are_not_localities(self, value):
        """Those tiers already have their own fields."""
        assert not _locality_eligible("place", value)

    def test_an_unknown_key_is_never_a_locality(self):
        """The point of allowing by KEY: a key OSM invents tomorrow cannot surprise us with a
        supermarket."""
        assert not _locality_eligible("brand_new_key_2027", "whatever")

    def test_the_long_tail_of_an_allowed_key_comes_along_for_free(self):
        """The reason eligibility is a key allowlist and not a value allowlist: rare natural
        features must not be dropped silently just because nobody listed them."""
        for value in ("blowhole", "arch", "geyser", "reef", "crevasse", "isthmus"):
            assert _locality_eligible("natural", value)


class TestAutoFillIsNarrowerThanEligibility:
    def test_a_ranked_tag_can_be_auto_filled(self):
        assert _LOCALITY_KV[("natural", "heath")] == 4
        assert _LOCALITY_KV[("boundary", "protected_area")] == 5

    def test_an_eligible_but_unranked_tag_is_offered_yet_never_auto_filled(self):
        """Deciding which name silently lands in the record is exactly the 'silent wrong value'
        of §2. natural=blowhole is a real place — show it, but let the user pick it."""
        assert _locality_eligible("natural", "blowhole")
        assert ("natural", "blowhole") not in _LOCALITY_KV

    def test_everything_ranked_is_also_eligible(self):
        """A tag that can be auto-filled but would not be shown would be incoherent."""
        for (key, value) in _LOCALITY_KV:
            assert _locality_eligible(key, value), f"{key}={value} is ranked but not eligible"

    def test_the_point_being_INSIDE_a_protected_place_outranks_cover(self):
        assert _LOCALITY_KV[("leisure", "nature_reserve")] > _LOCALITY_KV[("landuse", "forest")]
        assert _LOCALITY_KV[("natural", "peak")] > _LOCALITY_KV[("landuse", "meadow")]


class TestFilterBeforeCap:
    """The bug the Flugplatzheide exposed: the code cut to the nearest N *before* applying the
    allowlist, so noise could evict the real locality from the candidate budget entirely."""

    def _rank(self, feats):
        """Mirror of _fill_photon's selection: filter → cap → rank."""
        eligible = [p for p in feats if _locality_eligible(p["osm_key"], p["osm_value"])]
        near = sorted(eligible, key=lambda p: p["_dist"])[:_MAX_LOCALITY_POINTS]
        ranked = sorted(((_LOCALITY_KV.get((p["osm_key"], p["osm_value"]), -1), -p["_dist"], p)
                         for p in near), key=lambda t: (t[0], t[1]), reverse=True)
        best = next((p for pri, _, p in ranked if pri > 0), None)
        return best, near

    def test_noise_cannot_evict_the_real_locality(self):
        """Ten dorm blocks nearer than the heath must not consume the whole budget."""
        feats = [{"name": f"C{i}", "osm_key": "building", "osm_value": "dormitory",
                  "_dist": 10 + i} for i in range(_MAX_LOCALITY_POINTS + 2)]
        feats.append({"name": "Flugplatzheide", "osm_key": "natural", "osm_value": "heath",
                      "_dist": 200})
        best, near = self._rank(feats)
        assert best is not None and best["name"] == "Flugplatzheide"
        assert [p["name"] for p in near] == ["Flugplatzheide"]

    def test_priority_wins_and_distance_only_breaks_ties(self):
        feats = [
            {"name": "Wiese", "osm_key": "landuse", "osm_value": "meadow", "_dist": 5},
            {"name": "Naturschutzgebiet", "osm_key": "leisure", "osm_value": "nature_reserve",
             "_dist": 400},
        ]
        best, _ = self._rank(feats)
        assert best["name"] == "Naturschutzgebiet"      # pri 5 beats pri 2, despite being farther

    def test_nothing_eligible_leaves_the_field_empty(self):
        """An empty locality is the correct answer when only a guidepost is nearby — better than
        writing a bus stop into the record."""
        feats = [{"name": "Naturpfad", "osm_key": "information", "osm_value": "guidepost",
                  "_dist": 3}]
        best, near = self._rank(feats)
        assert best is None and near == []


class TestAdminLanguagePolicy:
    """CLAUDE.md: country + stateProvince in ENGLISH, everything below in the LOCAL name.
    _admin_name preferred name:en for every tier, so Augsburg's Regierungsbezirk came out as
    'Swabia' — a tier with no English exonym any German label would use."""

    ROWS = [
        {"lvl": "2", "nm": "Deutschland", "en": "Germany", "iso1": "DE", "iso2": ""},
        {"lvl": "4", "nm": "Bayern", "en": "Bavaria", "iso1": "", "iso2": "DE-BY"},
        {"lvl": "5", "nm": "Regierungsbezirk Schwaben", "en": "Swabia", "iso1": "", "iso2": ""},
        {"lvl": "6", "nm": "Augsburg", "en": "", "iso1": "", "iso2": ""},
    ]

    def test_country_and_state_are_english(self):
        h = _resolve_hierarchy(self.ROWS)
        assert h["country"] == "Germany" and h["country_code"] == "DE"
        assert h["state"] == "Bavaria" and h["state_code"] == "DE-BY"

    def test_the_regierungsbezirk_stays_local(self):
        h = _resolve_hierarchy(self.ROWS)
        assert h["region"] == "Regierungsbezirk Schwaben"
        assert h["region"] != "Swabia"

    def test_admin_name_falls_back_when_the_asked_for_language_is_missing(self):
        assert _admin_name({"nm": "Augsburg", "en": ""}, english=True) == "Augsburg"
        assert _admin_name({"nm": "", "en": "Swabia"}) == "Swabia"
