import pytest

from leadengine.config import Settings
from leadengine.normalize import (
    dedupe_key,
    normalize_domain,
    normalize_keyword,
    normalize_phone,
    normalize_zip,
    parse_us_address,
)


@pytest.mark.parametrize(
    "raw, expected",
    [
        ("(214) 555-0199", "2145550199"),
        ("+1 214-555-0199", "2145550199"),
        ("12345", None),
        (None, None),
    ],
)
def test_normalize_phone(raw, expected):
    assert normalize_phone(raw) == expected


@pytest.mark.parametrize(
    "raw, expected",
    [
        ("https://www.Example.com/contact", "example.com"),
        ("example.com", "example.com"),
        ("http://sub.biz.co:8080/x", "sub.biz.co"),
        ("not a url", None),
        ("", None),
    ],
)
def test_normalize_domain(raw, expected):
    assert normalize_domain(raw) == expected


def test_dedupe_key_ignores_shared_domains():
    assert dedupe_key("214-555-0199", "https://www.acme.com") == "2145550199|acme.com"
    # Facebook-only businesses must not all collapse into one record
    assert dedupe_key(None, "https://facebook.com/acme") is None
    assert dedupe_key("214-555-0199", "https://m.facebook.com/acme") == "2145550199|"


def test_keyword_and_zip():
    assert normalize_keyword("  Dumpster   RENTAL ") == "dumpster rental"
    assert normalize_zip("75201-1234") == "75201"
    with pytest.raises(ValueError):
        normalize_zip("7520")


def test_parse_us_address():
    assert parse_us_address("1201 Elm St, Dallas, TX 75270, United States") == ("Dallas", "TX", "75270")
    assert parse_us_address("500 Main St, Fort Worth, TX 76102") == ("Fort Worth", "TX", "76102")
    assert parse_us_address("Somewhere") == (None, None, None)


def test_settings_load(tmp_path):
    (tmp_path / "config.toml").write_text(
        '[general]\ndefault_provider = "osm"\n[cache]\nsearch = 3\n'
        '[providers.serpapi]\ncost_per_call_usd = 0.02\nmonthly_free_calls = 250\n',
        encoding="utf-8",
    )
    s = Settings.load(root=tmp_path, env={"SERPAPI_API_KEY": "k", "LOG_LEVEL": "debug", "EMPTY": ""})
    assert s.default_provider == "osm"
    assert s.ttl("search") == 3
    assert s.ttl("website") == 30  # default kept
    assert s.provider("serpapi").cost_per_call_usd == 0.02
    assert s.provider("unknown").cost_per_call_usd == 0
    assert s.serpapi_api_key == "k" and s.google_places_api_key == ""
    assert s.log_level == "DEBUG"
    assert s.database_url.startswith("sqlite:///") and s.database_url.endswith("data/leadengine.db")


def test_secrets_never_in_config_files():
    """Guard against committing keys: config.toml and .env.example must hold no values for secrets."""
    from pathlib import Path

    root = Path(__file__).resolve().parent.parent
    for line in (root / ".env.example").read_text("utf-8").splitlines():
        if line.strip() and not line.startswith("#") and "KEY" in line:
            assert line.split("=", 1)[1].strip() == "", line
    assert "api_key" not in (root / "config.toml").read_text("utf-8").lower()
