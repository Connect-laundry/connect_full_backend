"""Provider registry. Add a provider here; nothing else needs to change."""
from __future__ import annotations

from .base import PriceListExtractionProvider, ProviderResult
from .gemini import GeminiPriceListProvider
from .ocr_space import OCRSpaceProvider

PROVIDERS: dict[str, type[PriceListExtractionProvider]] = {
    GeminiPriceListProvider.name: GeminiPriceListProvider,
    OCRSpaceProvider.name: OCRSpaceProvider,
}


def get_provider(name: str | None) -> PriceListExtractionProvider | None:
    cls = PROVIDERS.get((name or '').strip().lower())
    return cls() if cls else None


__all__ = [
    'PROVIDERS', 'PriceListExtractionProvider', 'ProviderResult',
    'GeminiPriceListProvider', 'OCRSpaceProvider', 'get_provider',
]
