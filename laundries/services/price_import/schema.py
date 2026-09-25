"""Structured extraction schema.

``PriceListExtraction`` is sent to Gemini as the response schema (structured
output) and validated with Pydantic on the way back. Everything that can be
unreadable is nullable and pricing method has an explicit UNKNOWN, so the model
is never forced to fabricate a value to satisfy the schema.

Amounts are *strings copied as printed*; ``money.parse_amount`` turns them into
Decimals deterministically. Asking the model for floats invites silent
"corrections" (15.00 -> 1500) and float rounding.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from decimal import Decimal
from typing import Literal

from pydantic import BaseModel, Field

PricingMethodLiteral = Literal['PER_ITEM', 'PER_KG', 'UNKNOWN']


class ServiceCandidate(BaseModel):
    raw_name: str = Field(description='Service/garment name exactly as written in the image.')
    normalized_name: str | None = Field(
        default=None,
        description='Clean garment/service name in English title case, e.g. "Shirt". Null if unclear.',
    )
    category: str | None = Field(
        default=None,
        description='Section heading from the image if present (e.g. "Bedding"), else null.',
    )
    variant: str | None = Field(
        default=None,
        description='Service type or size for this price, e.g. "Wash & Iron", "Dry Clean", "King size". Null if none.',
    )
    pricing_method: PricingMethodLiteral = Field(
        description='PER_ITEM for a price per piece, PER_KG for a price per kilogram, UNKNOWN if not stated or unclear.',
    )
    price: str | None = Field(
        default=None,
        description='Per-item amount exactly as printed (digits and separators, e.g. "15", "15.00", "1,200"). Null if unreadable or absent.',
    )
    price_per_kg: str | None = Field(
        default=None,
        description='Per-kilogram amount exactly as printed. Null unless the image says per kg.',
    )
    surcharge_type: str | None = Field(
        default=None, description='e.g. "Express" if the image lists an extra charge for this item.',
    )
    surcharge_amount: str | None = Field(default=None, description='Surcharge amount as printed, or null.')
    source_text: str = Field(description='The exact text from the image this row was read from.')
    confidence: float = Field(ge=0, le=1, description='0..1: how clearly the name AND price were legible.')
    warnings: list[str] = Field(
        default_factory=list,
        description='Short codes such as CROSSED_OUT_PRICE, DISCOUNTED_PRICE, HANDWRITTEN, PARTIALLY_OBSCURED, AMBIGUOUS_PAIRING.',
    )


class PriceListExtraction(BaseModel):
    currency: Literal['GHS'] | None = Field(
        default=None,
        description='"GHS" only if the image shows a cedi marker (GH₵, GH¢, GHS, GHC, ₵, cedis). Null otherwise.',
    )
    business_name: str | None = None
    items: list[ServiceCandidate] = Field(default_factory=list)
    warnings: list[str] = Field(
        default_factory=list,
        description='Document-level codes, e.g. BLURRY, CROPPED, GLARE, NOT_A_PRICE_LIST, CONTAINS_INSTRUCTIONS.',
    )
    document_confidence: float = Field(ge=0, le=1)


@dataclass
class DraftCandidate:
    """A provider-agnostic candidate after deterministic normalisation."""

    raw_name: str
    item_name: str
    category: str = ''
    variant: str = ''
    pricing_method: str = 'UNKNOWN'
    price: Decimal | None = None
    price_per_kg: Decimal | None = None
    surcharge_type: str = ''
    surcharge_amount: Decimal | None = None
    source_text: str = ''
    confidence: float | None = None
    warnings: list[str] = field(default_factory=list)
    review_state: str = 'CHECK'
    is_selected: bool = True
    # Filled by matching.
    match_type: str = 'NONE'
    matched_item_id: str | None = None
    matched_item_name: str = ''
    matched_item_price: Decimal | None = None

    def add_warning(self, code: str) -> None:
        if code not in self.warnings:
            self.warnings.append(code)

    def to_json(self) -> dict:
        def d(v):
            return None if v is None else str(v)
        return {
            'raw_name': self.raw_name, 'item_name': self.item_name,
            'category': self.category, 'variant': self.variant,
            'pricing_method': self.pricing_method, 'price': d(self.price),
            'price_per_kg': d(self.price_per_kg), 'surcharge_type': self.surcharge_type,
            'surcharge_amount': d(self.surcharge_amount), 'source_text': self.source_text,
            'confidence': self.confidence, 'warnings': list(self.warnings),
            'review_state': self.review_state, 'is_selected': self.is_selected,
        }

    @classmethod
    def from_json(cls, data: dict) -> 'DraftCandidate':
        def dec(v):
            return None if v in (None, '') else Decimal(str(v))
        return cls(
            raw_name=data.get('raw_name', ''), item_name=data.get('item_name', ''),
            category=data.get('category', ''), variant=data.get('variant', ''),
            pricing_method=data.get('pricing_method', 'UNKNOWN'), price=dec(data.get('price')),
            price_per_kg=dec(data.get('price_per_kg')),
            surcharge_type=data.get('surcharge_type', ''),
            surcharge_amount=dec(data.get('surcharge_amount')),
            source_text=data.get('source_text', ''), confidence=data.get('confidence'),
            warnings=list(data.get('warnings') or []),
            review_state=data.get('review_state', 'CHECK'),
            is_selected=data.get('is_selected', True),
        )
