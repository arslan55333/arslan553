"""Provider registry."""

from __future__ import annotations

from leadengine.config import Settings
from leadengine.credits import CreditTracker
from leadengine.errors import LeadEngineError
from leadengine.http import HttpClient
from leadengine.proxy import ProxyPool
from leadengine.providers.base import Provider, ProviderResult
from leadengine.providers.google_places import GooglePlacesProvider
from leadengine.providers.osm import OsmProvider
from leadengine.providers.playwright_maps import PlaywrightMapsProvider
from leadengine.providers.selenium_maps import SeleniumMapsProvider
from leadengine.providers.serpapi import SerpApiProvider

PROVIDERS: dict[str, type[Provider]] = {
    cls.name: cls
    for cls in (PlaywrightMapsProvider, SerpApiProvider, GooglePlacesProvider, OsmProvider, SeleniumMapsProvider)
}


def build_provider(
    name: str,
    settings: Settings,
    http: HttpClient,
    credits: CreditTracker | None = None,
    proxies: ProxyPool | None = None,
) -> Provider:
    try:
        cls = PROVIDERS[name]
    except KeyError:
        raise LeadEngineError(f"Unknown provider {name!r}. Choose from: {', '.join(PROVIDERS)}") from None
    return cls(settings, http, credits, proxies)


__all__ = ["PROVIDERS", "Provider", "ProviderResult", "build_provider"]
