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
STYLES = ("clean", "bold", "warm")
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
    return STYLES[b.id % len(STYLES)]  # spread styles across leads


def render_page(facts, copy, style: str, brand: dict[str, str]) -> str:
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
                    deploy: bool | None = None) -> dict[str, Any]:
        facts = gather_facts(b, activity)
        copy = await make_copy(facts, self.llm)
        style = pick_style(b, style or self.cfg.get("style"))
        slug = f"{slugify(b.name)}-{slugify(b.city or '', 20)}".strip("-") + f"-{b.id}"
        folder = self.out_dir / slug
        site = folder / "site"
        site.mkdir(parents=True, exist_ok=True)
        (site / "index.html").write_text(render_page(facts, copy, style, self.brand), encoding="utf-8", newline="\n")
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
