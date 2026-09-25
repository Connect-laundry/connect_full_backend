"""Provider interface.

A provider turns a prepared image into a ``ProviderResult``. Views and the
orchestrator only ever talk to this interface; nothing outside ``providers/``
knows about Gemini or OCR.space specifics.
"""
from __future__ import annotations

from dataclasses import dataclass, field

from ..image import PreparedImage
from ..schema import PriceListExtraction


@dataclass
class ProviderResult:
    provider: str
    model: str = ''
    # Structured extraction (Gemini) or one built by the deterministic text
    # parser (OCR). Always schema-valid.
    extraction: PriceListExtraction | None = None
    # Plain transcription, when the provider has one (OCR). Used as the second
    # source of evidence in the cross-check.
    raw_text: str | None = None
    latency_ms: int = 0
    usage: dict = field(default_factory=dict)
    structured: bool = True


class PriceListExtractionProvider:
    name = 'base'
    # True if the provider understands layout/pairing itself (LLM); False for
    # plain transcription plus a deterministic parser (lower trust).
    structured = True

    def is_configured(self) -> bool:
        raise NotImplementedError

    def is_available(self) -> bool:
        """Configured, enabled and not quota-blocked (circuit checked separately)."""
        return self.is_configured()

    def extract(self, image: PreparedImage, *, timeout: float) -> ProviderResult:
        """Raise ``ProviderError`` on any failure. ``timeout`` is in seconds."""
        raise NotImplementedError
