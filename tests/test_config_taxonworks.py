"""TaxonWorks connection settings: clearable fields, disk reload, the enabled gate.

The three connection fields are stored exactly as typed. A field the user cannot clear
silently re-saves the server they moved away from — which is how a token issued for one
TaxonWorks stayed pointed at another (see taxonworks.TaxonWorksUnreachable, and #149's
security note about OTU ids belonging to a *specific* instance).
"""
import asyncio
import json

import pytest

import app.config as config_mod
from app.config import AppConfig, get_config, reload_config, save_config


@pytest.fixture
def cfg_file(tmp_path, monkeypatch):
    """Point the config module at a throwaway config.json and clear its cache."""
    path = tmp_path / "config.json"
    monkeypatch.setattr(config_mod, "_CONFIG_PATH", path)
    monkeypatch.setattr(config_mod, "_instance", None)
    return path


def test_blank_fields_round_trip_as_blank(cfg_file):
    """Saving "" must persist "" — not fall back to the previous value."""
    save_config(AppConfig(tw_base="", tw_token="", taxonpages_base=""))
    stored = json.loads(cfg_file.read_text(encoding="utf-8"))
    assert stored["tw_base"] == ""
    assert stored["taxonpages_base"] == ""
    assert reload_config().taxonpages_base == ""


def test_reload_picks_up_an_external_edit(cfg_file):
    """get_config() caches for the process lifetime; reload_config() must re-read.

    Without this the settings dialog showed the value the process started with and, since
    save_config writes every field of the cached object, the next Save reverted the file.
    """
    save_config(AppConfig(tw_base="https://sfg.taxonworks.org/api/v1"))
    assert get_config().tw_base.startswith("https://sfg")

    edited = json.loads(cfg_file.read_text(encoding="utf-8"))
    edited["tw_base"] = "https://sandbox.taxonworks.org/api/v1"
    cfg_file.write_text(json.dumps(edited), encoding="utf-8")

    assert get_config().tw_base.startswith("https://sfg")      # still the cached value
    assert reload_config().tw_base == "https://sandbox.taxonworks.org/api/v1"
    assert get_config().tw_base == "https://sandbox.taxonworks.org/api/v1"


def test_taxonworks_enabled_needs_both_url_and_token():
    assert not AppConfig(tw_base="https://x/api/v1", tw_token="").taxonworks_enabled
    assert not AppConfig(tw_base="", tw_token="tok").taxonworks_enabled
    assert not AppConfig(tw_base="https://x/api/v1", tw_token="   ").taxonworks_enabled
    assert AppConfig(tw_base="https://x/api/v1", tw_token="tok").taxonworks_enabled


def test_enabled_is_not_persisted(cfg_file):
    """It is a property derived from the token — never a stored field that could drift."""
    save_config(AppConfig(tw_token="tok"))
    assert "taxonworks_enabled" not in json.loads(cfg_file.read_text(encoding="utf-8"))


def test_web_base_strips_the_api_tail(monkeypatch):
    """Deep links into the TW UI (#149) hang off the web root, derived from the API base."""
    import app.services.taxonworks as tw

    for base, expect in [
        ("https://sandbox.taxonworks.org/api/v1", "https://sandbox.taxonworks.org"),
        ("https://sandbox.taxonworks.org/api/v1/", "https://sandbox.taxonworks.org"),
        ("https://sfg.taxonworks.org/api", "https://sfg.taxonworks.org"),
        ("https://tw.example.org", "https://tw.example.org"),
    ]:
        monkeypatch.setattr(config_mod, "_instance", AppConfig(tw_base=base))
        assert tw.web_base() == expect


def test_unconfigured_calls_fail_with_a_reason(monkeypatch):
    """An empty base URL is now a reachable state; it must name the cause, not traceback.

    httpx raises InvalidURL (not an HTTPError) on a relative URL, so it escaped _explain
    and reached the UI as a raw exception instead of a sentence.
    """
    import app.services.taxonworks as tw

    monkeypatch.setattr(config_mod, "_instance", AppConfig(tw_base="", tw_token="tok"))
    with pytest.raises(tw.TaxonWorksUnreachable, match="base URL"):
        asyncio.run(tw.search_taxon_names("Otiorhynchus"))

    monkeypatch.setattr(config_mod, "_instance", AppConfig(tw_base="https://x/api/v1", tw_token=""))
    with pytest.raises(tw.TaxonWorksUnreachable, match="token"):
        asyncio.run(tw.fetch_taxon_name(1))
