# Price-List Import API (AI scan): owner web app contract

Audience: owner web-app developers. You do not need to know which AI provider is
used; the backend hides that completely. The browser only talks to the Simame
API. **Never** add any AI/OCR key to the web app.

Base path: `/api/v1/laundries/dashboard/price-imports/`
Auth: the owner's normal session (Clerk/JWT). Customers get `403`.
The laundry is always the signed-in owner's own; never send a laundry id.

**Owners still onboarding (no laundry yet) can scan too.** Their scans belong to
the owner account, are visible only to them, and are used to *prefill the
onboarding form*; the wizard's normal final submit saves the prices. Confirming
such a scan through the API returns `409 NO_LAUNDRY` until the laundry exists.

Reference implementation: `Connect-Web-App/src/features/price-import/`.

Every response uses the standard envelope:

```json
{ "status": "success" | "error", "message": "…", "data": { … }, "code": "ONLY_ON_ERRORS" }
```

Show `message` to the owner as-is on errors: it is plain language and never
contains provider names or status codes. Branch on `code`.

---

## The flow

```
[Scan price list]  →  POST (photo)  →  job READY (drafts)  →  owner reviews/edits
        ↓ (not available / failed)                               ↓
   Manual entry (always available)                     POST confirm  →  services saved
```

Nothing is ever saved to the live menu until the owner presses **Confirm & Import**.

### 0. Should I show the "Scan price list" button?

`GET …/price-imports/availability/`

```json
{ "status": "success", "data": {
  "available": true, "reason": null,
  "daily_limit": 15, "used_last_24h": 2,
  "max_upload_mb": 10, "accepted_types": ["image/jpeg", "image/png", "image/webp"],
  "manual_entry_available": true } }
```

`reason` when unavailable: `DISABLED`, `NOT_ENABLED_FOR_LAUNDRY` (rollout allowlist: a laundry id, or an owner's user id / email for owners still onboarding), `NOT_CONFIGURED`.
When `available` is false, hide or disable the scan option and keep manual entry.

### 1. Upload

`POST …/price-imports/?async=1`, `multipart/form-data`, field **`source_image`**.

**Use `?async=1`.** The server answers at once with **`202`** and a `PROCESSING`
job, reads the list in the background, and you poll `GET …/{id}/` every ~2 s
until `READY` or `FAILED` (give up after ~100 s). Without `async` the request
blocks for the whole extraction (up to ~75 s), which proxies (e.g. Vercel
functions) and mobile connections may cut off. Keep uploads under ~4 MB: the web
app downscales photos to 2560 px before sending.

* Offer **[Take photo]** (`<input type="file" accept="image/*" capture="environment">`)
  and **[Choose image]** (`accept="image/jpeg,image/png,image/webp"`).
* Before upload, show framing tips: good lighting · whole list in view · avoid
  glare · hold the camera straight · prices must be readable.
* Show upload progress, then **"Reading your price list…"**. Extraction is
  synchronous and usually takes **10–40 s** (occasionally up to ~75 s). Use a
  request timeout of **at least 100 s**. Don't freeze the page; keep the spinner
  and "Enter services manually" link visible.

Responses:

| HTTP | Meaning |
|---|---|
| `202` | (`?async=1`) New job, `PROCESSING`: poll it. |
| `201` | (synchronous mode) New job. Check `data.status` (`READY` or `FAILED`). |
| `200` | Same photo already being reviewed: the existing job is returned (`data.deduplicated: true`). |
| `400/413/415` | File problem, see codes below. Nothing was created. |
| `403` `AI_IMPORT_NOT_AVAILABLE` | Feature off for this owner. Go to manual entry. |
| `409` `IMPORT_IN_PROGRESS` | Another scan is running for this laundry. |
| `429` `DAILY_LIMIT_REACHED` | Daily scan allowance used up. Manual entry. |
| `429` (no code, `Retry-After`) | Too many uploads in a short time. |
| `503` `SERVER_BUSY` | Scanner busy. Offer "Try again in a minute". |

**If the browser refreshes** mid-scan, the job survives server side: re-fetch it
with `GET …/{id}/` (store the id in `sessionStorage` when you get it).

### 2. The job

```json
{
  "id": "5b0c…", "status": "READY", "review_required": true,
  "provider": "gemini", "error": "", "error_code": "",
  "currency": "GHS",
  "document_warnings": ["EXTRA_PRICE"],
  "summary": { "total": 14, "looks_good": 11, "please_check": 2, "could_not_read": 1, "possible_matches": 1 },
  "served_from_cache": false, "deduplicated": false,
  "draft_items": [
    {
      "id": "d1…", "position": 0,
      "item_name": "Shirt – Wash & Iron",
      "raw_name": "SHIRT", "variant": "Wash & Iron", "category": "Tops",
      "pricing_method": "PER_ITEM",
      "suggested_price": "15.00", "price_per_kg": null,
      "surcharge_type": "", "surcharge_amount": null,
      "source_text": "SHIRT  10  15  20",
      "review_state": "LOOKS_GOOD",
      "warnings": [],
      "confidence": 0.95,
      "match_type": "NONE", "matched_item": null,
      "is_selected": true
    }
  ],
  "created_at": "…", "completed_at": "…", "confirmed_at": null
}
```

Statuses: `PROCESSING` → `READY` (= review required) → `CONFIRMED`; or `FAILED`,
`CANCELLED`. (`PENDING` is legacy and no longer produced.)

`provider` is informational only: don't show it to owners.

#### Showing each row

Use `review_state`, **not** `confidence` (provider confidence is not calibrated;
don't show percentages):

| `review_state` | Badge | Meaning |
|---|---|---|
| `LOOKS_GOOD` | green "Looks good" | Read clearly; checks agree. Still editable. |
| `CHECK` | yellow "Please check" | Something is ambiguous. Look at `warnings`. |
| `UNREADABLE` | red "Could not read" | Price or name missing. Owner must fill it in or remove. |

Show `source_text` as "Read from photo: …" so the owner can compare.
Rows with `is_selected: false` start unticked (duplicates, suspicious text).

Heading copy: **"We found {summary.total} possible services. Please review the
details before publishing."** Never imply the AI is always right.

Table columns: Include ☑ · Service · Category · Pricing (Per item / Per kg) ·
Price (GHS) · Status badge. Allow edit, delete, **add missing row**, and
changing Per item ↔ Per kg.

#### Row warning codes → owner-facing hints

| Code | Hint |
|---|---|
| `MISSING_PRICE` / `PRICE_UNREADABLE` | We couldn't read this price. |
| `MISSING_NAME` | We couldn't read this service name. |
| `PRICING_METHOD_UNKNOWN` / `PRICING_METHOD_CONFLICT` | Is this per item or per kg? (**must choose before import**) |
| `PRICE_PROVIDER_DISAGREEMENT` / `PRICE_NOT_CONFIRMED` / `PRICE_NOT_IN_SOURCE_TEXT` | Please double-check this price against your list. |
| `AMBIGUOUS_PAIRING` | We weren't sure which price belongs to this service. |
| `SERVICE_NAME_MISMATCH` | Please check the service name. |
| `SUSPICIOUS_PRICE` | This price is unusually high. |
| `ZERO_PRICE` | Price is 0. Is that right? |
| `AMBIGUOUS_DECIMAL_SEPARATOR` | Check the decimal point (e.g. 15.50). |
| `CROSSED_OUT_PRICE` | A crossed-out price was ignored; check the new one. |
| `DISCOUNTED_PRICE` | A promo price was shown; we used the regular price. |
| `PACKAGE_PRICE` | This looks like a bundle price. |
| `DUPLICATE_ROW` / `CONFLICTING_DUPLICATE` | This service appears more than once. |
| `MULTIPLE_PER_KG_RATES` | Your laundry can have one per-kg price. Keep one. |
| `EXISTING_ITEM_MATCH` / `POSSIBLE_MATCH` | You may already have this service (see below). |
| `SUSPICIOUS_TEXT` | This doesn't look like a service. (unticked) |
| `PARSED_FROM_OCR_TEXT`, `PRICING_METHOD_INFERRED`, `LOW_CONFIDENCE`, `PRICE_FIELD_MOVED`, `FOREIGN_CURRENCY` | Please check. |

Unknown codes: treat as "Please check".

#### Document warnings (`document_warnings`)

| Code | UI |
|---|---|
| `CURRENCY_UNCONFIRMED` | Show a checkbox **"These prices are in Ghana cedis (GHS)"**; send `currency_confirmed: true`. Required to confirm. |
| `FOREIGN_CURRENCY` | Same checkbox, stronger wording. |
| `EXTRA_PRICE` | "Some prices on your list may not have been matched to a service. Please check nothing is missing." |
| `FALLBACK_TEXT_PARSER_USED` | "We had trouble reading this list, so please check every row." |
| `BLURRY`, `GLARE`, `CROPPED`, `CONTAINS_INSTRUCTIONS`, `NOT_A_PRICE_LIST`, `CURRENCY_MISMATCH` | Generic "Please check carefully" banner. |

#### Existing-service matches

When `match_type` is `EXACT` or `POSSIBLE`, `matched_item` is populated:

```
Existing:  Shirt Wash & Iron — GHS 15.00
Detected:  Wash & Iron Shirt — GHS 18.00
[Update existing]  [Create new]  [Ignore]
```

Default the choice to **Update existing** for `EXACT`, and ask for `POSSIBLE`.
`Create new` with an identical name is skipped server side (never overwrites).

### 3. Confirm & Import

`POST …/price-imports/{id}/confirm/` with **every row the owner wants to act on**
(the server re-validates everything and ignores the original drafts' values):

```json
{
  "currency_confirmed": true,
  "items": [
    { "draft_id": "d1…", "action": "CREATE", "pricing_method": "PER_ITEM",
      "item_name": "Shirt – Wash & Iron", "category": "Tops", "unit_price": "15.00" },
    { "draft_id": "d2…", "action": "UPDATE", "pricing_method": "PER_ITEM",
      "item_name": "Shirt Wash & Iron", "unit_price": "18.00",
      "existing_item_id": "7c1e…" },
    { "draft_id": "d3…", "action": "CREATE", "pricing_method": "PER_KG", "price_per_kg": "18.00" },
    { "draft_id": "d4…", "action": "IGNORE" },
    { "action": "CREATE", "pricing_method": "PER_ITEM", "item_name": "Kaftan", "unit_price": "30" }
  ]
}
```

Rules enforced by the server:

* `pricing_method` must be `PER_ITEM` or `PER_KG` (not `UNKNOWN`).
* Prices: non-negative, max 2 decimals, ≤ 100 000. Send strings like `"15.00"`.
* `PER_KG` sets the laundry's single per-kg tariff. At most **one** per-kg row.
  If a per-kg price already exists, the row must use `"action": "UPDATE"`.
* `UPDATE` needs `existing_item_id` of one of **this** laundry's services.
* No duplicate names among `CREATE` rows.
* Rows without `draft_id` are owner-added rows.
* All-or-nothing: if any row is invalid, **nothing** is saved.
* Legacy body `{"items":[{"item_name","unit_price","category"}]}` still works
  (treated as CREATE / PER_ITEM).

Success:

```json
{ "status": "success", "message": "Imported 3 item(s), updated 1; skipped 0 duplicate(s).",
  "data": { "created": ["Shirt – Wash & Iron", "Kaftan", "Per-kg price"], "updated": ["Shirt Wash & Iron"],
            "skipped": [], "already_confirmed": false, "job": { … } } }
```

**Idempotent**: re-sending the same confirm (double click, network retry)
returns the original result with `already_confirmed: true` and creates nothing.

Validation error (`400`, `code: VALIDATION_FAILED`):

```json
{ "status": "error", "code": "VALIDATION_FAILED", "message": "Some rows need attention before importing.",
  "data": { "errors": [ { "row": 3, "field": "unit_price", "message": "Price cannot be negative." } ] } }
```

`row` is the index in your `items` array (`null` = whole request). Other codes:
`CURRENCY_CONFIRMATION_REQUIRED`, `NOTHING_TO_IMPORT`, `TOO_MANY_ROWS` (300 max),
`NOT_CONFIRMABLE` (409: job failed/cancelled/processing), `CONFLICT` (409: menu
changed meanwhile, so reload and retry), `NOT_FOUND` (404).

A pricing-catalogue version snapshot is taken before any change, so the owner
can roll back via the existing pricing-versions screen.

### 4. Cancel

`POST …/price-imports/{id}/cancel/` discards a READY/FAILED job's drafts.

---

## Upload / failure codes → UI

| `code` | Suggested UI |
|---|---|
| `NO_FILE` | "Please choose a photo of your price list." |
| `EMPTY_FILE`, `INVALID_IMAGE` | "This file is damaged or not a photo." [Choose another] |
| `UNSUPPORTED_FILE` (415) | Show `message` (explains JPEG/PNG/WebP; HEIC hint). |
| `FILE_TOO_LARGE` (413) | Show `message`. |
| `IMAGE_DIMENSIONS_TOO_LARGE`, `IMAGE_TOO_SMALL` | Show `message`. |
| job `FAILED` + `error_code: COULD_NOT_READ_IMAGE` | "We couldn't read this image clearly. Try taking another photo in better lighting." |
| job `FAILED` + `NO_PRICES_FOUND` | Show `error`. |
| job `FAILED` + `AI_TEMPORARILY_UNAVAILABLE` | Show `error`. |

For every failure offer three buttons: **Try again** · **Upload another image** ·
**Enter services manually**. Manual entry (`/dashboard/pricing-items/`,
`/dashboard/weight-pricing/`) never depends on the AI and must always stay
reachable, including during onboarding.

## Things the web app must not do

* Call Gemini, OCR.space or any AI API directly, or hold any AI key.
* Publish/save anything before the owner presses Confirm & Import.
* Show AI confidence percentages.
* Send a laundry id (the server ignores it and uses the owner's laundry).
