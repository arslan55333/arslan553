import asyncio
import json

import httpx
from fastapi.testclient import TestClient

from leadengine import settings_store as ss
from leadengine.config import Settings


def test_write_env_keeps_other_lines(tmp_path):
    (tmp_path / ".env").write_text("# my keys\nSERPAPI_API_KEY=old\nLOG_LEVEL=INFO\n", encoding="utf-8")
    ss.write_env(tmp_path, {"SERPAPI_API_KEY": "new123", "FIRECRAWL_API_KEY": "fc-1", "LOG_LEVEL": None})
    text = (tmp_path / ".env").read_text()
    assert text.startswith("# my keys\nSERPAPI_API_KEY=new123\n") and "FIRECRAWL_API_KEY=fc-1" in text
    assert "LOG_LEVEL" not in text
    ss.write_env(tmp_path, {"PROXIES": "1.2.3.4:80:u:p w,5.6.7.8:80"})
    assert ss.read_env(tmp_path)["PROXIES"] == "1.2.3.4:80:u:p w,5.6.7.8:80"
    assert ss.mask("abcdefgh12345678") == "••••••5678" and ss.mask("") == ""


def test_overrides_layer_over_config(settings):
    ss.write_overrides(settings.root, {"preview": {"brand_name": "Arslan Web"}, "ads": {"paid_fallback_max": 3}})
    fresh = Settings.load(settings.root, env={})
    assert fresh.section("preview")["brand_name"] == "Arslan Web" and fresh.section("ads")["paid_fallback_max"] == 3
    assert fresh.section("ads")["sweep_variations"] == 6                       # untouched config values stay


def test_settings_page_saves_and_reloads(settings):
    from leadengine.ui.app import create_app

    app = create_app(settings, start_runner=False)
    with TestClient(app) as c:
        page = c.get("/settings").text
        assert "API keys" in page and "SerpAPI" in page and "Proxies" in page and "Firecrawl" in page
        c.post("/settings/keys", data={"SERPAPI_API_KEY": "abcdef123456", "FIRECRAWL_API_KEY": "fc-999988887777"})
        assert settings.serpapi_api_key == "abcdef123456" and settings.firecrawl_api_key == "fc-999988887777"
        page = c.get("/settings").text
        assert "abcdef123456" not in page and "••••••3456" in page               # never shown back in full
        c.post("/settings/keys", data={"SERPAPI_API_KEY": "••••••3456"})          # untouched box keeps the key
        assert settings.serpapi_api_key == "abcdef123456"
        c.post("/settings/keys", data={"clear_FIRECRAWL_API_KEY": "1"})
        assert settings.firecrawl_api_key == ""
        c.post("/settings/options", data={"preview.brand_name": "Arslan Web Studio", "ads.serp_provider": "serpapi",
                                          "ads.paid_fallback_max": "4"})
        assert settings.section("preview")["brand_name"] == "Arslan Web Studio"
        assert settings.section("ads")["serp_provider"] == "serpapi" and settings.section("ads")["paid_fallback_max"] == 4
        assert json.loads((settings.root / "data" / "settings.json").read_text())["ads"]["paid_fallback_max"] == 4
        c.post("/settings/proxies", data={"proxies": "1.2.3.4:8080\n5.6.7.8:3128:user:pw"})
        assert settings.proxy_list == "1.2.3.4:8080,5.6.7.8:3128:user:pw"
        run = c.get("/run").text
        assert "SerpAPI (paid, no captchas)" in run and "Google Places API (paid) — add key in Settings" in run


def test_key_tests_use_free_endpoints():
    seen = []

    def handler(request):
        seen.append(str(request.url))
        if request.url.host == "serpapi.com":
            return httpx.Response(200, json={"plan_name": "Free Plan", "plan_searches_left": 233})
        if request.url.host == "api.firecrawl.dev":
            return httpx.Response(200, json={"success": True, "data": {"remaining_credits": 84849}})
        return httpx.Response(401, json={"error": "bad key"})

    async def go():
        async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as client:
            return [await ss.test_key("serpapi", "k", client), await ss.test_key("firecrawl", "k", client),
                    await ss.test_key("netlify", "k", client), await ss.test_key("serpapi", "", client)]
    out = asyncio.run(go())
    assert out[0] == (True, "Free Plan: 233 searches left this month") and out[1] == (True, "84849 credits left")
    assert out[2][0] is False and out[3] == (False, "no key saved yet")
    assert "account.json" in seen[0] and "credit-usage" in seen[1]
