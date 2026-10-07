"""Build (and optionally deploy) a preview site for one business.

Layout on disk::

    data/previews/<slug>/site/index.html   <- deployed (plus robots.txt, _headers)
    data/previews/<slug>/desktop.jpg        <- screenshots for the outreach email
    data/previews/<slug>/mobile.jpg
"""

from __future__ import annotations

import re
import unicodedata
from pathlib import Path
from typing import Any

from jinja2 import Environment, FileSystemLoader, select_autoescape

from leadengine.config import Settings
from leadengine.db.models import Business
from leadengine.enrich.website.render import WebsiteRenderer
from leadengine.http import HttpClient
from leadengine.llm import LLM
from leadengine.log import get_logger
from leadengine.preview import deploy as deployers
from leadengine.preview.content import gather_facts, make_copy

log = get_logger("preview")
STYLES = ("pro", "clean", "bold", "warm")      # pro = the full premium landing page (default)
_env = Environment(loader=FileSystemLoader(str(Path(__file__).parent / "templates")),
                   autoescape=select_autoescape(["html"]))

HEADERS = "/*\n  X-Robots-Tag: noindex, nofollow, noarchive\n  Referrer-Policy: no-referrer\n"
ROBOTS = "User-agent: *\nDisallow: /\n"


def slugify(text: str, max_len: int = 40) -> str:
    text = unicodedata.normalize("NFKD", text).encode("ascii", "ignore").decode().lower()
    text = re.sub(r"&", " and ", text)
    text = re.sub(r"[^a-z0-9]+", "-", text).strip("-")
    return text[:max_len].rstrip("-") or "business"


def pick_style(b: Business, requested: str | None) -> str:
    if requested in STYLES:
        return requested
    if requested == "rotate":
        return STYLES[1:][b.id % 3]       # the three simple one-page styles, spread across leads
    return "pro"


def schema_ld(facts, copy) -> str:
    """LocalBusiness + FAQPage JSON-LD (what a real site should have; also shows the owner we know SEO)."""
    import json

    biz = {"@context": "https://schema.org", "@type": "LocalBusiness", "name": facts.name,
           "telephone": facts.phone, "address": facts.address, "areaServed": facts.service_area or None}
    if facts.rating and facts.review_count:
        biz["aggregateRating"] = {"@type": "AggregateRating", "ratingValue": facts.rating,
                                  "reviewCount": facts.review_count}
    faq = {"@context": "https://schema.org", "@type": "FAQPage",
           "mainEntity": [{"@type": "Question", "name": q["q"], "acceptedAnswer": {"@type": "Answer", "text": q["a"]}}
                          for q in copy.faq]}
    data = [{k: v for k, v in biz.items() if v is not None}, faq]
    return json.dumps(data, ensure_ascii=False).replace("</", "<\\/")


def render_page(facts, copy, style: str, brand: dict[str, str], current_shot: str | None = None) -> str:
    if style == "pro":
        trade_title = re.sub(r"\s+(services?|company|companies|contractors?)$", "", facts.category, flags=re.I)
        return _env.get_template("pro.html").render(f=facts, c=copy, brand=brand, ld=schema_ld(facts, copy),
                                                    current_shot=current_shot, trade_title=trade_title)
    return _env.get_template("page.html").render(f=facts, c=copy, style=style, brand=brand)


class PreviewBuilder:
    def __init__(self, settings: Settings, http: HttpClient | None, *, llm: LLM | None = None,
                 renderer: WebsiteRenderer | None = None, out_dir: Path | None = None) -> None:
        self.settings = settings
        self.cfg = settings.section("preview")
        self.http = http
        self.llm = llm
        self.renderer = renderer
        self.out_dir = out_dir or settings.root / "data" / "previews"

    @property
    def brand(self) -> dict[str, str]:
        return {"name": self.cfg.get("brand_name") or "Your Agency", "url": self.cfg.get("brand_url", ""),
                "email": self.cfg.get("brand_email", "")}

    async def build(self, b: Business, activity: dict[str, Any] | None, *, style: str | None = None,
                    deploy: bool | None = None, extras: dict[str, Any] | None = None) -> dict[str, Any]:
        import shutil

        extras = extras or {}
        facts = gather_facts(b, activity, site_info=extras.get("site_info"), reviews_audit=extras.get("reviews"),
                             website=extras.get("website"), keyword=extras.get("keyword"))
        copy = await make_copy(facts, self.llm)
        style = pick_style(b, style or self.cfg.get("style"))
        slug = f"{slugify(b.name)}-{slugify(b.city or '', 20)}".strip("-") + f"-{b.id}"
        folder = self.out_dir / slug
        site = folder / "site"
        site.mkdir(parents=True, exist_ok=True)
        shot = None
        if style == "pro" and facts.current_screenshot and Path(facts.current_screenshot).is_file():
            shutil.copyfile(facts.current_screenshot, site / "current.jpg")    # "your current homepage" in the notes
            shot = "current.jpg"
        (site / "index.html").write_text(render_page(facts, copy, style, self.brand, shot), encoding="utf-8",
                                         newline="\n")
        (site / "robots.txt").write_text(ROBOTS, encoding="utf-8", newline="\n")
        (site / "_headers").write_text(HEADERS, encoding="utf-8", newline="\n")
        payload: dict[str, Any] = {"slug": slug, "style": style, "path": str(site / "index.html"),
                                   "copy_source": copy.source, "url": None, "provider": None,
                                   "headline": copy.headline}
        if self.renderer is not None:
            shot = await self.renderer.render((site / "index.html").resolve().as_uri(), folder / "desktop.jpg")
            payload["screenshot"] = shot.get("screenshot")
            payload["mobile_screenshot"] = shot.get("mobile_screenshot")
        deploy = self.cfg.get("deploy", "none") != "none" if deploy is None else deploy
        if deploy:
            payload.update(await self.publish(site, slug))
        return payload

    def subdomain(self, slug: str) -> str | None:
        base = (self.cfg.get("base_domain") or "").strip(".")
        return f"{slug}.{base}" if base else None

    async def publish(self, site: Path, slug: str) -> dict[str, Any]:
        target = self.cfg.get("deploy", "none")
        domain = self.subdomain(slug)
        if target == "netlify":
            url = await deployers.deploy_netlify(self.http, self.settings.deploy_keys.get("netlify", ""), site,
                                                 f"preview-{slug}"[:63], domain)
        elif target == "cloudflare":
            url = await deployers.deploy_cloudflare(site, f"preview-{slug}"[:58], custom_domain=domain,
                                                    api_token=self.settings.deploy_keys.get("cloudflare_token", ""),
                                                    account_id=self.settings.deploy_keys.get("cloudflare_account", ""))
        else:
            raise deployers.DeployError("set [preview] deploy = \"netlify\" or \"cloudflare\" in config.toml")
        return {"url": url, "provider": target}
