"""Exception types. Messages are safe to show to the user (no API keys)."""

from __future__ import annotations


class LeadEngineError(Exception):
    """Base class for all engine errors."""


class NetworkError(LeadEngineError):
    """The request could not be completed after all retries."""


class ProviderError(LeadEngineError):
    """A data provider returned an error."""

    def __init__(self, provider: str, message: str) -> None:
        super().__init__(f"{provider}: {message}")
        self.provider = provider


class ProviderNotConfigured(ProviderError):
    """Missing API key or optional dependency."""


class ProviderAuthError(ProviderError):
    """The provider rejected the API key."""
