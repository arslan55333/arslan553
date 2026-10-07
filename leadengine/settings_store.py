"""The dashboard's Settings page: API keys and proxies go to ``.env`` (secrets never leave your PC),
everything else to ``data/settings.json`` (layered over config.toml). Each key has a free "Test" call."""

from __future__ import annotations

import json
import re
from pathlib import Path
from typing import Any

import httpx

from leadengine.config import Settings

# env name, label, what it unlocks, where to get it, test id
KEYS: list[dict[str, str]] = [
    {"env": "SERPAPI_API_KEY", "label": "SerpAPI", "test": "serpapi", "url": "https://serpapi.com/manage-api-key",
     "help": "Google Maps + Google search results without captchas (250 free searches/month). Used as a source and "
             "as the captcha fallback for ad checks."},
    {"env": "GOOGLE_PLACES_API_KEY", "label": "Google Places API (New)", "test": "places",
     "url": "https://console.cloud.google.com/google/maps-apis/credentials",
     "help": "Official Google business data (phone, website, rating, hours). Enable 'Places API (New)'."},
    {"env": "PAGESPEED_API_KEY", "label": "Google PageSpeed", "test": "pagespeed",
     "url": "https://developers.google.com/speed/docs/insights/v5/get-started#APIKey",
     "help": "Real mobile speed score for websites and ad landing pages (free; without a key Google rate-limits)."},
    {"env": "FIRECRAWL_API_KEY", "label": "Firecrawl", "test": "firecrawl", "url": "https://www.firecrawl.dev/app/api-keys",
     "help": "Reads any website (JavaScript, protected): better emails, full-site SEO audit, services & owner "
             "details, competitor page counts."},
    {"env": "ANTHROPIC_API_KEY", "label": "Claude (AI)", "test": "anthropic", "url": "https://console.anthropic.com/settings/keys",
     "help": "AI-written email drafts and preview copy (best quality)."},
    {"env": "GEMINI_API_KEY", "label": "Gemini (AI)", "test": "gemini", "url": "https://aistudio.google.com/app/apikey",
     "help": "Alternative AI provider (has a free tier)."},
    {"env": "GROQ_API_KEY", "label": "Groq (AI)", "test": "groq", "url": "https://console.groq.com/keys",
     "help": "Alternative AI provider (free tier, text only)."},
    {"env": "OPENPAGERANK_API_KEY", "label": "Open PageRank", "test": "openpagerank",
     "url": "https://www.domcop.com/openpagerank/", "help": "Free domain authority (0-10) for the Local SEO score."},
    {"env": "NETLIFY_TOKEN", "label": "Netlify", "test": "netlify", "url": "https://app.netlify.com/user/applications#personal-access-tokens",
     "help": "Publish preview sites and audit reports as links you can email."},
    {"env": "OUTREACH_SMTP_USER", "label": "Outreach mailbox user", "test": "", "url": "",
     "help": "Mailbox for 'save drafts to Gmail' / reply checks / opt-in sending (separate outreach domain)."},
    {"env": "OUTREACH_SMTP_PASSWORD", "label": "Outreach mailbox app password", "test": "", "url": "",
     "help": "App password, never your normal password."},
    {"env": "ALERT_WEBHOOK_URL", "label": "Alert webhook URL", "test": "", "url": "",
     "help": "n8n / Make / Zapier / Slack URL that receives 'new advertiser' alerts."},
]
SECRET_ENVS = {k["env"] for k in KEYS} | {"PROXIES", "OUTREACH_WEBHOOK_URL", "CONTACT_EMAIL"}

# non-secret settings shown on the page: (section, key, label, kind, choices/help)
OPTIONS: list[tuple[str, str, str, str, Any]] = [
    ("preview", "brand_name", "Your agency name", "text", "Shown on previews, reports and emails"),
    ("preview", "brand_url", "Your website", "text", ""),
    ("preview", "brand_email", "Your email", "text", ""),
    ("outreach", "sender_name", "Your name (email sign-off)", "text", ""),
    ("outreach", "physical_address", "Postal address (required by US law in cold emails)", "text", ""),
    ("outreach", "offer", "One line about what you offer", "text", "e.g. I build fast, mobile-first sites for local trades."),
    ("discovery", "provider", "Default source for New scan", "choice",
     [("playwright", "Browser (free)"), ("serpapi", "SerpAPI (paid, no captchas)"), ("google_places", "Google Places API (paid)")]),
    ("ads", "serp_provider", "Google Ads check", "choice",
     [("auto", "Auto: free browser, SerpAPI only on captcha"), ("playwright", "Free browser only"),
      ("serpapi", "SerpAPI only"), ("none", "Off")]),
    ("ads", "paid_fallback_max", "Max SerpAPI credits per run (captcha fallback)", "number", ""),
    ("discovery", "max_checks", "Deep checks per ZIP (best-reviewed first)", "number", ""),
    ("discovery", "shortlist_min_reviews", "Deep-check only businesses with at least … reviews", "number", ""),
    ("firecrawl", "mode", "Firecrawl", "choice",
     [("off", "Off"), ("fallback", "Only when a site can't be read normally"), ("smart", "Smart: fallback + site map for SEO (recommended)"),
      ("full", "Full: also read services/owner/years with AI (~5 credits per site)")]),
    ("money", "ad_budget", "Assumed monthly Google Ads budget of a local business ($) — for the 'ad waste' estimate", "number", ""),
    ("money", "job_value", "Average job value ($) — leave empty to use the niche average", "number", ""),
    ("reviews", "scope", "Reviews audit during scans", "choice",
     [("targets", "Only for My targets (recommended)"), ("shortlist", "Every shortlisted business"), ("off", "Off")]),
    ("llm", "provider", "AI provider", "choice", [("claude", "Claude"), ("gemini", "Gemini"), ("groq", "Groq"), ("ollama", "Ollama (local)")]),
    ("preview", "deploy", "Publish previews/reports to", "choice", [("none", "Don't publish"), ("netlify", "Netlify"), ("cloudflare", "Cloudflare Pages")]),
]


def mask(value: str | None) -> str:
    if not value:
        return ""
    return ("•" * 6) + value[-4:] if len(value) > 8 else "•" * len(value)


# ── .env ─────────────────────────────────────────────────────────────
def read_env(root: Path) -> dict[str, str]:
    from dotenv import dotenv_values
    path = root / ".env"
    return {k: v for k, v in dotenv_values(path).items() if v} if path.exists() else {}


def write_env(root: Path, updates: dict[str, str | None]) -> None:
    """Set / remove keys in .env, keeping every other line (and comments) as it was."""
    path = root / ".env"
    lines = path.read_text("utf-8").splitlines() if path.exists() else []
    done = set()
    out = []
    for line in lines:
        m = re.match(r"\s*([A-Z0-9_]+)\s*=", line)
        if m and m.group(1) in updates:
            key = m.group(1)
            done.add(key)
            if updates[key] is not None:
                out.append(f"{key}={_quote(updates[key])}")
            continue
        out.append(line)
    for key, val in updates.items():
        if key not in done and val is not None:
            out.append(f"{key}={_quote(val)}")
    path.write_text("\n".join(out).rstrip() + "\n", encoding="utf-8", newline="\n")
    try:
        path.chmod(0o600)
    except OSError:
        pass


def _quote(v: str) -> str:
    v = v.replace("\n", ",").strip()
    return f'"{v}"' if re.search(r"[\s#'\"]", v) else v


# ── data/settings.json ──────────────────────────────────────────────
def read_overrides(root: Path) -> dict[str, Any]:
    p = root / "data" / "settings.json"
    try:
        return json.loads(p.read_text("utf-8")) if p.exists() else {}
    except ValueError:
        return {}


def write_overrides(root: Path, values: dict[str, dict[str, Any]]) -> None:
    current = read_overrides(root)
    for section, kv in values.items():
        current.setdefault(section, {}).update(kv)
    p = root / "data" / "settings.json"
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text(json.dumps(current, indent=2), encoding="utf-8")


def reload_into(settings: Settings) -> Settings:
    """Re-read .env + config + overrides into the SAME object, so running jobs and pages see new keys."""
    import os
    new = Settings.load(settings.root, env={**read_env(settings.root),
                                            **{k: v for k, v in os.environ.items() if k not in SECRET_ENVS}})
    settings.__dict__.update(new.__dict__)
    return settings


# ── free "Test" calls ────────────────────────────────────────────────
async def test_key(name: str, value: str, client: httpx.AsyncClient | None = None) -> tuple[bool, str]:
    """(ok, message). Every test uses a free endpoint (no paid credits)."""
    if not value:
        return False, "no key saved yet"
    own = client is None
    client = client or httpx.AsyncClient(timeout=40)
    try:
        if name == "serpapi":
            r = await client.get("https://serpapi.com/account.json", params={"api_key": value})
            d = r.json() if r.status_code == 200 else {}
            if d.get("plan_searches_left") is not None:
                return True, f"{d.get('plan_name', 'plan')}: {d['plan_searches_left']} searches left this month"
            return False, d.get("error") or f"HTTP {r.status_code}"
        if name == "places":
            r = await client.post("https://places.googleapis.com/v1/places:searchText",
                                  headers={"X-Goog-Api-Key": value, "X-Goog-FieldMask": "places.id"},
                                  json={"textQuery": "plumber in Dallas", "pageSize": 1})
            if r.status_code == 200:
                return True, "key works (Places API New enabled)"
            msg = (r.json().get("error", {}) or {}).get("message", "") if "json" in r.headers.get("content-type", "") else ""
            return False, f"HTTP {r.status_code}: {msg[:160]}"
        if name == "pagespeed":
            r = await client.get("https://www.googleapis.com/pagespeedonline/v5/runPagespeed",
                                 params={"url": "https://example.com", "key": value, "category": "performance",
                                         "strategy": "mobile"}, timeout=90)
            if r.status_code == 200:
                return True, "key works"
            return False, f"HTTP {r.status_code}: {r.text[:160]}"
        if name == "firecrawl":
            r = await client.get("https://api.firecrawl.dev/v1/team/credit-usage", headers={"Authorization": f"Bearer {value}"})
            d = r.json() if r.status_code == 200 else {}
            if d.get("success"):
                return True, f"{d['data'].get('remaining_credits')} credits left"
            return False, d.get("error") or f"HTTP {r.status_code}"
        if name == "anthropic":
            import anthropic
            try:
                await anthropic.AsyncAnthropic(api_key=value, max_retries=0).models.list(limit=1)
                return True, "key works"
            except anthropic.APIError as exc:
                return False, str(exc)[:160]
        if name == "gemini":
            r = await client.get("https://generativelanguage.googleapis.com/v1beta/models", params={"key": value})
            return (r.status_code == 200), ("key works" if r.status_code == 200 else f"HTTP {r.status_code}")
        if name == "groq":
            r = await client.get("https://api.groq.com/openai/v1/models", headers={"Authorization": f"Bearer {value}"})
            return (r.status_code == 200), ("key works" if r.status_code == 200 else f"HTTP {r.status_code}")
        if name == "openpagerank":
            r = await client.get("https://openpagerank.com/api/v1.0/getPageRank", params={"domains[]": "google.com"},
                                 headers={"API-OPR": value})
            return (r.status_code == 200), ("key works" if r.status_code == 200 else f"HTTP {r.status_code}")
        if name == "netlify":
            r = await client.get("https://api.netlify.com/api/v1/user", headers={"Authorization": f"Bearer {value}"})
            return (r.status_code == 200), (f"signed in as {r.json().get('email')}" if r.status_code == 200 else f"HTTP {r.status_code}")
        return False, "no test for this one"
    except Exception as exc:
        return False, f"{type(exc).__name__}: {str(exc)[:120]}"
    finally:
        if own:
            await client.aclose()


async def test_proxies(lines: list[str]) -> list[tuple[str, bool, str]]:
    """Each proxy: can it open Google? (free)"""
    import asyncio

    from leadengine.proxy import parse_proxy

    async def one(line: str):
        url = parse_proxy(line)
        if not url:
            return line, False, "not a proxy line"
        try:
            async with httpx.AsyncClient(proxy=url, timeout=20) as c:
                r = await c.get("https://www.google.com/generate_204")
                return line, r.status_code in (200, 204), f"HTTP {r.status_code}"
        except Exception as exc:
            return line, False, type(exc).__name__
    return list(await asyncio.gather(*(one(x) for x in lines if x.strip() and not x.strip().startswith("#"))))
