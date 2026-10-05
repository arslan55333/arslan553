"""Settings: non-secret options from ``config.toml``, secrets from ``.env``.

Real environment variables win over ``.env`` so a server can inject keys
without a file on disk.
"""

from __future__ import annotations

import os
import tomllib
from dataclasses import dataclass, field
from functools import lru_cache
from pathlib import Path
from typing import Any, Mapping

from dotenv import dotenv_values

PROJECT_ROOT = Path(__file__).resolve().parent.parent

DEFAULT_TTL_DAYS: dict[str, float] = {
    "search": 14,
    "website": 30,
    "emails": 30,
    "ads": 7,
    "geocode": 365,
    "activity": 14,
    "paid_place": 30,
    "domain": 30,
    "default": 30,
}


@dataclass(frozen=True)
class HttpSettings:
    """Network behaviour shared by every provider."""

    timeout_seconds: float = 20.0
    max_retries: int = 3
    backoff_base_seconds: float = 1.0
    concurrency: int = 5
    delay_seconds: float = 0.0


@dataclass(frozen=True)
class ProviderSettings:
    """Per-provider pricing estimate plus any provider-specific options."""

    cost_per_call_usd: float = 0.0
    monthly_free_calls: int = 0
    extra: dict[str, Any] = field(default_factory=dict)


@dataclass(frozen=True)
class Settings:
    """Everything the engine needs to run. Build it with :meth:`load`."""

    root: Path
    database_url: str
    log_level: str
    log_dir: Path
    default_provider: str
    contact_email: str
    serpapi_api_key: str
    google_places_api_key: str
    ttl_days: dict[str, float]
    http: HttpSettings
    providers: dict[str, ProviderSettings]
    sections: dict[str, dict[str, Any]] = field(default_factory=dict)  # other config.toml tables
    proxy_list: str = ""
    proxy_file: str = ""
    reacher_secret: str = ""
    llm_keys: dict[str, str] = field(default_factory=dict)
    pagespeed_api_key: str = ""
    deploy_keys: dict[str, str] = field(default_factory=dict)
    outreach_keys: dict[str, str] = field(default_factory=dict)

    def ttl(self, kind: str) -> float:
        """Freshness window in days for a cached data kind."""
        return self.ttl_days.get(kind, self.ttl_days.get("default", 30))

    def provider(self, name: str) -> ProviderSettings:
        return self.providers.get(name, ProviderSettings())

    def section(self, name: str) -> dict[str, Any]:
        """A plain config.toml table such as ``[discovery]`` or ``[proxy]``."""
        return self.sections.get(name, {})

    @property
    def user_agent(self) -> str:
        contact = f" ({self.contact_email})" if self.contact_email else ""
        return f"LeadEngine/0.1{contact}"

    @classmethod
    def load(cls, root: Path | str | None = None, env: Mapping[str, str | None] | None = None) -> "Settings":
        """Read ``config.toml`` and ``.env`` from ``root`` (default: project folder)."""
        root = Path(root or os.environ.get("LEADENGINE_HOME") or PROJECT_ROOT)
        if env is None:
            env = {**dotenv_values(root / ".env"), **os.environ}
        env = {k: v for k, v in env.items() if v not in (None, "")}

        cfg_path = Path(env.get("LEADENGINE_CONFIG") or root / "config.toml")
        cfg: dict[str, Any] = tomllib.loads(cfg_path.read_text("utf-8")) if cfg_path.exists() else {}

        http_cfg = cfg.get("http", {})
        http = HttpSettings(
            timeout_seconds=float(http_cfg.get("timeout_seconds", 20)),
            max_retries=int(http_cfg.get("max_retries", 3)),
            backoff_base_seconds=float(http_cfg.get("backoff_base_seconds", 1.0)),
            concurrency=max(1, int(http_cfg.get("concurrency", 5))),
            delay_seconds=float(http_cfg.get("delay_seconds", 0.0)),
        )

        providers: dict[str, ProviderSettings] = {}
        for name, pcfg in cfg.get("providers", {}).items():
            pcfg = dict(pcfg)
            providers[name] = ProviderSettings(
                cost_per_call_usd=float(pcfg.pop("cost_per_call_usd", 0.0)),
                monthly_free_calls=int(pcfg.pop("monthly_free_calls", 0)),
                extra=pcfg,
            )

        default_db = f"sqlite:///{(root / 'data' / 'leadengine.db').as_posix()}"
        return cls(
            root=root,
            database_url=env.get("DATABASE_URL", default_db),
            log_level=env.get("LOG_LEVEL", "INFO").upper(),
            log_dir=root / "logs",
            default_provider=cfg.get("general", {}).get("default_provider", "serpapi"),
            contact_email=env.get("CONTACT_EMAIL", ""),
            serpapi_api_key=env.get("SERPAPI_API_KEY", ""),
            google_places_api_key=env.get("GOOGLE_PLACES_API_KEY", ""),
            ttl_days={**DEFAULT_TTL_DAYS, **{k: float(v) for k, v in cfg.get("cache", {}).items()}},
            http=http,
            providers=providers,
            sections={k: v for k, v in cfg.items()
                      if isinstance(v, dict) and k not in ("http", "providers", "cache", "general")},
            proxy_list=env.get("PROXIES", ""),
            proxy_file=env.get("PROXY_FILE", ""),
            reacher_secret=env.get("REACHER_SECRET", ""),
            llm_keys={"anthropic": env.get("ANTHROPIC_API_KEY", ""), "gemini": env.get("GEMINI_API_KEY", ""),
                      "groq": env.get("GROQ_API_KEY", ""), "ollama_url": env.get("OLLAMA_URL", "")},
            pagespeed_api_key=env.get("PAGESPEED_API_KEY", ""),
            deploy_keys={"netlify": env.get("NETLIFY_TOKEN", ""), "cloudflare_token": env.get("CLOUDFLARE_API_TOKEN", ""),
                         "cloudflare_account": env.get("CLOUDFLARE_ACCOUNT_ID", "")},
            outreach_keys={"smtp_user": env.get("OUTREACH_SMTP_USER", ""),
                           "smtp_password": env.get("OUTREACH_SMTP_PASSWORD", ""),
                           "webhook_url": env.get("OUTREACH_WEBHOOK_URL", "")},
        )


@lru_cache(maxsize=1)
def get_settings() -> Settings:
    """Process-wide settings, loaded once."""
    return Settings.load()
