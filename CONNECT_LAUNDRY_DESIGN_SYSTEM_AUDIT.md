# Connect Laundry — Design System Audit Report

**Audit date:** 2026-06-11
**Auditor role:** Senior Staff Frontend Engineer / Design Systems Architect / UI-UX Auditor
**Scope:** `connect-customer-mobile` (Expo / React Native) + `connect_new_backend` (Django) branding surfaces
**Mode:** Read-only. No application code was modified.

> Every color, font, and token in this report is traceable to source. Numeric counts come from `ripgrep` aggregation across `app/` and `src/` (mobile) and the backend templates/static. File:line references are given for material claims.

---

## 1. Executive Summary

Connect Laundry has the *skeleton* of a design system — a single, well-structured `ThemeContext` with 4 color themes × light/dark — but in practice the codebase runs **three competing color systems**, **a font that is never actually loaded**, and **zero centralization of non-color tokens** (spacing, radius, shadow, z-index). The backend admin/PWA/email surfaces use an **entirely different brand** (purple, not blue; Inter/Outfit/Segoe, not Poppins).

### Headline findings

| # | Severity | Finding |
|---|----------|---------|
| 1 | 🔴 Critical | **Poppins is never loaded.** 18 Poppins `.ttf` files ship in `assets/fonts/`, 67 `fontFamily:'Poppins'` references exist, but there is **no `useFonts`/`Font.loadAsync` anywhere**. Every "Poppins" string silently renders as the OS system font. |
| 2 | 🔴 Critical | **Three color systems coexist.** Theme tokens (`#0056F1`), a Tailwind palette (`#3B82F6`, `#64748B`…) isolated in auth screens, and an iOS/Bootstrap palette (`#007AFF`, `#0D6EFD`, `#34C759`) in maps + order statuses. |
| 3 | 🔴 Critical | **Backend brand ≠ mobile brand.** Admin/PWA primary is purple `#9333ea`; password-reset email button is Bootstrap blue `#007bff`; the 404 page is orange `#f97316`. Mobile brand is blue `#0056F1`. Nothing matches. |
| 4 | 🟠 High | **No non-color token centralization.** Spacing, border-radius, shadow, z-index, animation, and sizing are 100% inline across ~126 `StyleSheet.create` blocks. ~1,200+ inline token literals. |
| 5 | 🟠 High | **Auth flow ignores the theme entirely.** The 4 `authScreens/*` files never call `useTheme()` → no dark-mode support on the most critical user flow. |
| 6 | 🟡 Medium | **6 competing "primary blue" shades** and **4 competing "error red" shades** act as the same semantic role. |
| 7 | 🟡 Medium | **Dead assets**: leftover Expo starter images (`react-logo*.png`, `partial-react-logo.png`) and `SpaceMono-Regular.ttf` are shipped but unused. |

### Health scorecard

| Dimension | Status | Note |
|-----------|--------|------|
| Central color theme exists | 🟢 | `ThemeContext.tsx`, 4 themes × light/dark |
| Color theme adoption | 🟡 | 104 / 157 `.tsx` call `useTheme()`; only ~18% fully compliant |
| Font system | 🔴 | Declared everywhere, loaded nowhere |
| Typography centralization | 🔴 | 0 files; all inline |
| Spacing / radius / shadow tokens | 🔴 | 0 files; all inline |
| Cross-surface brand consistency | 🔴 | Mobile blue vs backend purple vs email blue vs 404 orange |

---

## 2. Overall Design System Architecture

```
connect-customer-mobile/
├── src/context/ThemeContext.tsx   ← ONLY centralized design artifact (colors only)
│        4 colorThemes: blue | green | purple | orange
│        × 2 modes: light | dark
│        → exposes useTheme() → { colors, isDarkMode, toggleTheme,
│                                 colorTheme, setColorTheme, themeType, setThemeType }
│
├── src/constants/ui.ts            ← business enums only (ADDRESS_TYPES, BENEFITS…), NO tokens
├── src/constants/discovery.ts     ← filter/sort labels, NO tokens
├── src/features/*/constants/*     ← feature data (orderStatuses colors, map config), NO UI tokens
│
├── assets/fonts/                  ← 18× Poppins + SpaceMono  ⚠️ never registered
└── (everything else)              ← inline StyleSheet.create with hardcoded hex + numbers
```

**Single source of truth:** `src/context/ThemeContext.tsx:10-198`. It defines, per theme:
`primary, onPrimary, secondary, onSecondary, background, onBackground, surface, onSurface, surfaceVariant, onSurfaceVariant, card, text, subText, border, outline, outlineVariant, icon, success, warning, error, info`.

**The gap:** the theme covers *colors* only. There is no typography module, no spacing scale, no radius/shadow/elevation tokens, no z-index layer map, and the semantic palette is missing **status colors**, **gold/rating accent**, and **overlay colors** — so those get hardcoded in components.

---

## 3. Master Color Palette

### 3.1 Theme-defined tokens (the canonical palette)

Source: `src/context/ThemeContext.tsx`. Semantic colors (`success/warning/error/info`) are **identical across all 4 themes and both modes**; only the brand `primary/secondary/background` shift per theme.

#### Brand primary/secondary per theme

| Theme | Mode | primary | secondary | background | surface | Source |
|-------|------|---------|-----------|------------|---------|--------|
| blue | light | `#0056F1` | `#3498db` | `#F5F8FF` | `#FFFFFF` | `ThemeContext.tsx:11-32` |
| blue | dark | `#3498db` | `#2980b9` | `#121212` | `#1E1E1E` | `ThemeContext.tsx:106-127` |
| green | light | `#27ae60` | `#2ecc71` | `#F5FFF8` | `#FFFFFF` | `ThemeContext.tsx:34-55` |
| green | dark | `#2ecc71` | `#27ae60` | `#121212` | `#1E1E1E` | `ThemeContext.tsx:129-150` |
| purple | light | `#8e44ad` | `#9b59b6` | `#F8F5FF` | `#FFFFFF` | `ThemeContext.tsx:57-78` |
| purple | dark | `#9b59b6` | `#8e44ad` | `#121212` | `#1E1E1E` | `ThemeContext.tsx:152-173` |
| orange | light | `#d35400` | `#e67e22` | `#FFF8F5` | `#FFFFFF` | `ThemeContext.tsx:80-101` |
| orange | dark | `#e67e22` | `#d35400` | `#121212` | `#1E1E1E` | `ThemeContext.tsx:175-196` |

> Note: the default `colorTheme` is `"blue"` (`ThemeContext.tsx:221`), so **`#0056F1` is the de-facto brand color**.

#### Shared semantic + neutral tokens

| Token | Light | Dark | RGB (light) | HSL (light) | Purpose |
|-------|-------|------|-------------|-------------|---------|
| success | `#4CAF50` | `#4CAF50` | 76,175,80 | 122°,39%,49% | Positive states |
| warning | `#FFC107` | `#FFC107` | 255,193,7 | 45°,100%,51% | Caution/pending |
| error | `#FF3B30` | `#FF3B30` | 255,59,48 | 4°,100%,59% | Errors/destructive |
| info | `#2196F3` | `#2196F3` | 33,150,243 | 207°,90%,54% | Informational |
| text | `#333333` | `#FFFFFF` | 51,51,51 | 0°,0%,20% | Primary text |
| subText | `#666666` | `#AAAAAA` | 102,102,102 | 0°,0%,40% | Secondary text |
| border | `#E0E0E0` | `#333333` | 224,224,224 | 0°,0%,88% | Borders |
| outline | `#CCCCCC` | `#444444` | 204,204,204 | 0°,0%,80% | Light strokes |
| icon | `#999999` | `#777777` | 153,153,153 | 0°,0%,60% | Icon tint |
| onPrimary | `#FFFFFF` | `#FFFFFF` | 255,255,255 | 0°,0%,100% | Text on primary |
| surfaceVariant | `#F0F2F5` (blue) | `#2C2C2C` | — | — | Subtle surface |

### 3.2 The reality: 138 unique hardcoded hex values

Aggregate counts (mobile `app/` + `src/`):

- **878** total hex literal usages
- **138** unique hex values
- **65** `rgba()/rgb()` usages

Top hardcoded colors by frequency:

| Hex | Count | Mapped role | In theme? |
|-----|------:|-------------|-----------|
| `#FFF` / `#FFFFFF` | 193 | white / onPrimary / surface | partly (`onPrimary`) |
| `#0056F1` | 62 | **brand primary (hardcoded)** | ✅ should be `colors.primary` |
| `#000` | 56 | shadowColor / black | no (shadow) |
| `#333` / `#333333` | 51 | text primary (hardcoded) | ✅ `colors.text` |
| `#1A1A1A` | 26 | near-black text/heading | ⚠️ off-token |
| `#666` / `#666666` | 35 | subtext (hardcoded) | ✅ `colors.subText` |
| `#F0F0F0` | 21 | placeholder bg | partial |
| `#3B82F6` | 20 | **competing blue (auth)** | ❌ conflict |
| `#FF3B30` | 19 | error (hardcoded, matches token) | ✅ `colors.error` |
| `#F5F5F5` | 19 | light bg | ✅ `colors.background` |
| `#007AFF` | 16 | **iOS blue (maps/status)** | ❌ conflict |
| `#FFD700` | 12 | gold rating star | ⚠️ missing token |
| `#DC2626` | 12 | **error red (auth Tailwind)** | ❌ conflict |
| `#64748B` | 12 | **slate subtext (auth)** | ❌ conflict |
| `#94A3B8` | 8 | **slate placeholder (auth)** | ❌ conflict |

### 3.3 Competing-shade catalogue (orphan & duplicate colors)

For each semantic role, the codebase contains multiple near-identical shades doing the same job:

| Role | Canonical (token) | Competing hardcoded shades | Where the competitors live |
|------|-------------------|----------------------------|----------------------------|
| **Brand / primary blue** | `#0056F1` | `#3B82F6` (20), `#007AFF` (16), `#2196F3` (8), `#0D6EFD` (2), `#007BFF` (1), `#0066FF` (2), `#2563EB` (1), `#1D4ED8` (3) | auth screens (Tailwind), maps (iOS), `orderStatuses.ts` (Bootstrap) |
| **Error / red** | `#FF3B30` | `#DC2626` (12), `#FF4444` (6), `#DC3545` (3), `#B02A37` | auth (Tailwind), `orderStatuses.ts` (Bootstrap) |
| **Success / green** | `#4CAF50` | `#28A745`, `#34C759`, `#1E7E34`, `#30D158`, `#2ECC71` | `orderStatuses.ts`, tracking |
| **Warning / amber** | `#FFC107` | `#FFB800` (4), `#FFCC00`, `#FF9800` | locator/driver rating stars |
| **Text primary** | `#333333` | `#1A1A1A` (26), `#111827`, `#1E293B` | headings, auth input text |
| **Text secondary** | `#666666` | `#64748B` (12), `#94A3B8` (8), `#999999` | auth labels/placeholders |
| **Border** | `#E0E0E0` | `#E2E8F0` (auth, ~10), `#E5E7EB` (5), `#EEE` | auth inputs |
| **Light background** | `#F5F5F5` | `#F8FAFC` (5), `#F0F7FF`, `#FAFAFA`, `#F0F0F0` | auth, placeholders |
| **Gold accent** | *(none — missing)* | `#FFD700` (12), `#FFB800` (4) | rating stars, featured badges |

### 3.4 The three color systems

| System | Where | Signature colors | Uses theme? |
|--------|-------|------------------|-------------|
| **Theme tokens** | booking, discovery, home, basket, laundry-details, orders header | `#0056F1`, `#4CAF50`, `#FF3B30`, `#333`, `#666` | ✅ yes (mostly) |
| **Tailwind palette** | `app/authScreens/{signIn,signUp,forgotPassword,resetPassword}.tsx` | `#3B82F6`, `#64748B`, `#1E293B`, `#94A3B8`, `#E2E8F0`, `#F8FAFC`, `#DC2626` | ❌ never imports `useTheme` |
| **iOS / Bootstrap** | `src/features/maps/*`, `src/features/tracking/*`, `orderStatuses.ts` | `#007AFF`, `#34C759`, `#FFB800`, `#0D6EFD`, `#007BFF`, `#28A745`, `#DC3545` | ⚠️ partial / constants |

---

## 4. Typography System

### 4.1 The critical font-loading defect

- `assets/fonts/` contains **18 Poppins variants** (Thin→Black + italics) + `SpaceMono-Regular.ttf`.
- **No font loading exists**: `useFonts`, `Font.loadAsync`, `expo-font` import, and `SplashScreen` font-gating all return **zero matches** across `app/` and `src/`.
- `expo-font@~14.0.10` is in `package.json` but never invoked.
- There is **no `react-native.config.js`** and no font-weight→filename map.

**Consequence:** all **67** `fontFamily:'Poppins'` declarations (52 single-quote + 15 double-quote) fall back to the platform system font (SF Pro on iOS, Roboto on Android). The app is **not actually rendering Poppins anywhere.** Furthermore, even if a bare `useFonts({Poppins})` were added, pairing bare `'Poppins'` with `fontWeight:'700'` would not select `Poppins-Bold.ttf` without an explicit weight map — so weights would still be wrong.

### 4.2 Font families found

| Family | Count | Notes |
|--------|------:|-------|
| `'Poppins'` (single quote) | 52 | bare family + numeric weight |
| `"Poppins"` (double quote) | 15 | same |
| `Platform.OS==='ios'?'System':'Roboto'` | 1 | `app/(tabs)/_layout.tsx:56` — the only honest declaration |

No weight-qualified names (`Poppins-Bold`, `Poppins-SemiBold`) are ever used.

### 4.3 Font weights (370 declarations) — inconsistent

| Weight | Count | | Weight | Count |
|--------|------:|--|--------|------:|
| `'600'` | 88 + 36 (dbl-quote) | | `'500'` | 28 + 17 |
| `'700'` | 71 + 11 | | `'800'` | 25 + 11 |
| `'bold'` | 54 | | `'400'` | 3 |
| | | | `'900'` | 1 |

**Inconsistencies:** mixing `'bold'` (54×) with `'700'` (82×) for the same visual weight; mixing single/double quote styles for identical values; `'400'` almost never used (regular weight avoided).

### 4.4 Font sizes (502 declarations) — de-facto scale

| Size | Count | Role | | Size | Count | Role |
|-----:|------:|------|--|-----:|------:|------|
| 14 | 132 | Body | | 20 | 13 | H3 |
| 16 | 102 | Body large / subtitle | | 11 | 13 | Mini |
| 12 | 62 | Caption | | 28 | 6 | Display |
| 18 | 55 | Title | | 22 | 6 | H2 |
| 13 | 44 | Label | | 24/26/17 | 2-3 | misc |
| 15 | 40 | Body alt | | 30/36/40/48 | 1 each | one-off display |
| 10 | 18 | Micro | | | | |

One-off display sizes (no scale): `48` (`app/index.jsx:263`), `40` (`OnboardingScreen.tsx:219`), `36` (`welcome.tsx:109`), `30` (`signIn.tsx:437`).

### 4.5 Line-height & letter-spacing — sparse

- `lineHeight`: 26 declarations total (~5% of text) — values 18,20,21,22,24,34.
- `letterSpacing`: 23 declarations total — values -0.5,-0.4,0.4,0.5,0.8,1.
- **95%+ of text has no explicit line-height** → relies on platform defaults → inconsistent vertical rhythm.

### 4.6 Reconstructed type scale (from observed clusters)

| Role | size / weight / line / spacing | Count | Example |
|------|--------------------------------|------:|---------|
| Display | 48 / 700 / — / 1 | 1 | `app/index.jsx:263` |
| Display XL | 40 / bold | 1 | `OnboardingScreen.tsx:219` |
| H1 | 28 / 800 / 34 | 6 | `signUp.tsx:515` |
| H2 | 22 / 700 / 22 | 6 | `CheckoutReviewScreen.tsx:821` |
| H3 | 20 / 800 / 24 | 13 | `CartDropdown.tsx:200` |
| Title | 18 / 700-800 / 24 | 55 | `RealTimeTrackingScreen.tsx:258` |
| Subtitle | 16 / 600-700 | 102 | `BookingScreen.tsx:260` |
| Body | 14 / 500-600 / 20 | 132 | `LaundryLocatorScreen.tsx:138` |
| Label | 13 / 600 | 44 | `signUp.tsx:539` |
| Caption | 12 / 600 | 62 | `DiscoveryLaundryCard.tsx:230` |
| Badge | 11 / 600 | 13 | `SchedulePickupScreen.tsx:267` |
| Micro | 10 / — | 18 | `DiscoveryLaundryCard.tsx:257` |

**There is no centralized typography module** (`Typography.ts`/`fonts.ts`/`text-styles.ts` — none exist). Every text style is inline.

---

## 5. Design Tokens Reference

All non-color tokens are **100% inline** — no `spacing.ts`, `radius.ts`, `shadow.ts`, or `layout.ts` exists. ~1,200+ inline literals.

### 5.1 Spacing (de-facto scale: 4 · 8 · 12 · 16 · 20 · 24)

`padding:` top values → 16 (53), 20 (44), 8 (26), 4 (16), 12 (14), 24 (7).
`margin*` top values → 8 (96), 16 (86), 12 (86), 4 (63), 20 (45), 24 (34).
`gap:` → 12 (24), 8 (23), 4 (18), 16 (9).

**Off-scale one-offs flagged:** 2, 3, 5, 6, 10, 14, 15, 17, 18, 23, 25, 30, 32. **Suspect values** in margins: `333`, `700`, `1000` (likely artifacts).

### 5.2 Border radius (scale: 8 · 12 · 16 · 20; pill 999/1000)

12 (78), 16 (39), 20 (36), 8 (16), 18 (13), 10 (11), 4 (10), 25 (9), 24 (7), 14 (7). Pills use **both** `999` (2) and `1000` (5) inconsistently. 24% of radius values are off-scale (1,2,3,5,6,9,15,17,22,23,30,40,45,48,50,60,80).

### 5.3 Shadow / elevation — 3 recurring recipes, all inline

| Recipe | shadowColor / offset / opacity / radius / elevation | Used for |
|--------|------------------------------------------------------|----------|
| Subtle card | `#000` / {0,4} / 0.05-0.1 / 4-8 / 2-4 | cards, tab bar |
| Medium | `#000` / {0,10} / 0.1-0.15 / 8-10 / 5-8 | form cards, modals |
| Brand glow | `#3B82F6` / {0,4} / 0.3 / 10 / 8 | primary buttons (auth) |

`shadowOpacity` distinct values: 0.1 (28), 0.05 (14), 0.3 (9), 0.2 (7), 0.25 (4), 0.15 (3). No centralization — duplicated across 30+ files (the 4 auth screens duplicate identical recipes).

### 5.4 Other tokens

| Token | Distinct values (count) |
|-------|-------------------------|
| `borderWidth` | 1 (62), 2 (13), 1.5 (10), 3 (2) — clean |
| `opacity` | 0.6 (9), 0.5 (4), 0.4 (2), 0.3 (2) |
| `zIndex` | 1,2,8,9,10,20,100,1000,2000,9999 — **no layer system** |
| Animation `duration` | 800 (10), 1000 (8), 600 (4), 1500 (4), 500 (3), 2000, 150 |
| Reanimated adoption | 7 of ~150 components (~5%) |
| Icon `size=` | 20 (84), 24 (55), 16 (32), 18 (31), 14 (22), 22 (16) |
| Avatar sizes | 36, 40, 48, 80 — no scale |
| Button height | 40 / 44 / 48 — 3 competing |
| `hitSlop` | 1 occurrence total (`LocationPickerModal.tsx:451`) |

---

## 6. Component Theme Map

Adoption: **104 / 157** `.tsx` files import `useTheme()`. But only ~18% are fully token-compliant.

| Component | useTheme? | Hardcoded colors | Font | Verdict |
|-----------|:--------:|------------------|------|---------|
| `shared/BrandedLoader.tsx` | ✅ | none | Poppins 600 | 🟢 centralized |
| `shared/EmptyState.tsx` | ✅ | none | — | 🟢 centralized |
| `shared/RatingStars.tsx` | ❌ | `#FFD700`, `#666` | — | 🔴 hardcoded |
| `shared/ServiceTags.tsx` | ❌ | `#E8F4FF`, `#0056F1` | — | 🔴 hardcoded brand |
| `CustomCountryPicker.jsx` | ❌ | `#111827,#E5E7EB,#F9FAFB,#6B7280…` | — | 🔴 hardcoded |
| `security/SecurityViolationScreen.tsx` | ❌ | `#07111f,#8b1e1e,#fff4f4,#f3b7b7…` | — | 🔴 fixed dark palette |
| `home/components/SearchBar.tsx` | ✅ | none | — | 🟢 centralized |
| `maps/components/MapSearchBar.tsx` | ✅ | none | — | 🟢 centralized |
| `orders/components/OrderCard.tsx` | ✅ | none | — | 🟢 centralized |
| `orders/components/OrdersHeader.tsx` | ✅ | none | — | 🟢 centralized |
| `laundry-details/components/BookButton.tsx` | ✅ | `#0066FF` (shadow+fallback) | — | 🟠 partial |
| `pickup/components/ConfirmButton.tsx` | ✅ | `rgba(0,0,0,0.05)` | Poppins 700 | 🟠 partial |
| `settings/components/MenuItem.tsx` | ✅ | `#FF4444` | — | 🟠 partial |
| `discovery/components/DiscoveryLaundryCard.tsx` | ✅ | `#FF3B30,#FFD700`, rgba | — | 🟠 partial |
| `locator/components/LaundryCard.tsx` | ✅ | `#FFB800,#34C759,#F0F0F0` | Poppins 600/700 | 🟠 partial |
| `basket/components/BasketItemCard.tsx` | ✅ | `#28A745,#DC3545,#FFD700` | — | 🟠 partial |
| `booking/components/LaundryItemCard.tsx` | ✅ | `#0056F1` ×3, `#1A1A1A,#F5F5F5,#F0F7FF` | — | 🟠 partial (brand) |
| `orders/components/OrderStatusBadge.tsx` | ✅(isDarkMode) | via `ORDER_STATUS_COLORS` const | — | 🟠 constants |
| `home/components/HomeHeader.tsx` | ✅ | `#FF3B30` badge | — | 🟠 partial |
| `orders/components/OrdersSkeleton.tsx` | ✅ | `#333,#E1E1E1` | — | 🟠 partial |
| `maps/components/LaundryMarker.tsx` | ❌ | `#eee,#FFD700,white` | — | 🔴 hardcoded |
| `schedule/components/AddAddressModal.tsx` | ❌ | `#0056F1` ×5, `#1a1a1a,#f5f5f5,#666…` | — | 🔴 hardcoded brand |
| `app/(tabs)/_layout.tsx` | ✅ | rgba backdrop | System/Roboto | 🟠 partial |
| `authScreens/{signIn,signUp,forgot,reset}.tsx` | ❌ | full Tailwind palette | — | 🔴 separate system |

**Brand-color violations (hardcode `#0056F1` instead of `colors.primary`):**
- `schedule/components/AddAddressModal.tsx` — lines 76, 105, 195, 236, 257 (×5)
- `booking/components/LaundryItemCard.tsx` — lines 178, 194, 254 (×3)
- `shared/ServiceTags.tsx:17` (default param)

**Bypass-theme-entirely list:** `RatingStars`, `ServiceTags`, `CustomCountryPicker`, `SecurityViolationScreen`, `LaundryMarker`, `AddAddressModal`, all 4 `authScreens/*`.

---

## 7. Backend Branding Audit

The Django backend (`connect_new_backend`) emits its own UI for **admins** (Unfold), **PWA**, **emails**, and **error pages** — and uses a **completely different brand**.

| Surface | File | Color / font | Matches mobile (`#0056F1` / Poppins)? |
|---------|------|--------------|---------------------------------------|
| Unfold admin primary | `config/settings.py:531-544` | purple `rgb(147,51,234)` = `#9333ea` | ❌ purple, not blue |
| PWA manifest theme_color | `templates/pwa/manifest.html:8` | `#9333ea`; bg `#18181b` | ❌ |
| PWA offline page | `templates/pwa/offline.html` | `--primary:#9333ea`; font **Inter** | ❌ |
| Admin Ops UI (⌘K + bell) | `marketplace/static/admin_ops/admin_ops.css` | `--aops-primary:#7e22ce`, `--aops-primary-600:#9333ea`, `--aops-danger:#dc2626`; font **Inter** | ❌ |
| Password-reset email | `users/templates/users/password_reset_email.html` | button `#007bff`, hover `#0056b3`, logo `#2c3e50`; font **Segoe UI/Tahoma** | ❌ Bootstrap blue, not brand |
| 404 page | `templates/404.html` | `--primary:#f97316` (orange); font **Outfit** (Google) | ❌ orange |
| Legal CMS | `marketplace/static/admin/legal_cms.css` | neutral grays only | n/a |
| PWA icons | `scripts/generate_pwa_icons.py:26` | maskable bg `#9333ea` | ❌ |
| PDF/receipts | — | none found (no reportlab/weasyprint) | n/a |

**Admin Ops design tokens** (`admin_ops.css`) — the one backend file with a real token block:
```
--aops-primary:#7e22ce  --aops-primary-600:#9333ea  --aops-danger:#dc2626
--aops-bg:#ffffff  --aops-fg:#111827  --aops-muted:#6b7280  --aops-border:#e5e7eb
.dark → --aops-bg:#18181b  --aops-fg:#f4f4f5  --aops-border:#3f3f46
font-family: Inter, system-ui, sans-serif
```

**Verdict:** the backend ships **four different brand expressions** (purple admin, Bootstrap-blue email, orange 404) and **three different fonts** (Inter, Outfit, Segoe UI) — none of which is the mobile brand (blue `#0056F1` / Poppins). This is the single largest cross-surface inconsistency.

---

## 8. Theme Consistency Report

| Issue | Evidence | Impact |
|-------|----------|--------|
| Auth flow has no dark mode | `authScreens/*` never call `useTheme()` | Sign-in/up stuck in light Tailwind palette |
| Order status colors off-brand | `orderStatuses.ts:3-12` Bootstrap hexes | status chips don't match theme; dark variants are iOS, not token |
| Status colors not in theme | no `statusColors` in `ThemeContext` | every status surface hardcodes |
| Gold accent not in theme | `#FFD700`/`#FFB800` in 6+ files | rating stars un-themeable |
| Overlay colors not in theme | `rgba(0,0,0,…)` scattered | backdrops inconsistent |
| Backend ≠ mobile brand | §7 | users see purple admin / blue app |
| Font never loads | §4.1 | brand typeface absent everywhere |
| 4 themes shipped, 1 surfaced | `setColorTheme` exists; is there UI? only `ThemeToggle` (light/dark) found | green/purple/orange themes likely dead config |

---

## 9. Hardcoded Style Violations (priority list)

**P0 — brand-color hardcoding (replace with `colors.primary`):**
- `schedule/components/AddAddressModal.tsx:76,105,195,236,257`
- `booking/components/LaundryItemCard.tsx:178,194,254`
- `shared/ServiceTags.tsx:17`

**P0 — auth screens → migrate to theme (4 files, ~25 hex each):**
- `signUp.tsx`: `#3B82F6`(517,586,591,618,623,650), `#DC2626`(286,531,570), `#64748B`(541,595,645), `#1E293B`(557,600), `#94A3B8`(82,97,615), `#F8FAFC`(550), `#E2E8F0`(552)
- same pattern in `signIn.tsx`, `forgotPassword.tsx`, `resetPassword.tsx`

**P1 — order status constants → semantic tokens:**
- `orderStatuses.ts`: `#0D6EFD`,`#007BFF` → `colors.primary`; `#28A745` → `colors.success`; `#DC3545` → `colors.error`; `#FFC107` → `colors.warning`

**P1 — error-red consolidation:** `#DC2626`, `#FF4444`, `#DC3545` → `colors.error` (`#FF3B30`).

**P2 — near-black text:** `#1A1A1A` (26×) → `colors.text`.

---

## 10. Dead / Unused Theme Assets

| Asset | Path | Status |
|-------|------|--------|
| `SpaceMono-Regular.ttf` | `assets/fonts/` | shipped, never referenced |
| 18 Poppins `.ttf` | `assets/fonts/` | shipped, never *loaded* (referenced by name only) |
| `react-logo.png`, `react-logo@2x.png`, `react-logo@3x.png` | `assets/images/` | Expo starter leftovers |
| `partial-react-logo.png` | `assets/images/` | Expo starter leftover |
| green / purple / orange themes | `ThemeContext.tsx` | defined; no surfaced switcher UI found (only light/dark `ThemeToggle`) |
| `colors.info` (`#2196F3`) | `ThemeContext.tsx` | defined; minimal component usage |

---

## 11. Recommendations — Centralizing the Design System

**Phase 1 — Make the foundation real (highest ROI):**
1. **Load fonts.** Add `useFonts({ 'Poppins-Regular':…, 'Poppins-Medium':…, 'Poppins-SemiBold':…, 'Poppins-Bold':… })` in `app/_layout.tsx`, gate splash on `fontsLoaded`, and create a weight→family helper (`fontFamily('600') → 'Poppins-SemiBold'`). Drop the unused `SpaceMono` if not needed.
2. **Create `src/theme/typography.ts`** exporting the §4.6 scale as named text styles (`textStyles.title`, `.body`, `.caption`…).
3. **Create `src/theme/tokens.ts`**: `spacing` (4/8/12/16/20/24/32), `radius` (xs4/sm8/md12/lg16/xl20/full999), `shadows` (subtle/medium/brand), `zLayers` (base/dropdown/overlay/modal/toast), `durations`.

**Phase 2 — Extend the color theme:**
4. Add `statusColors` (one entry per `OrderStatus`), `accent.gold`, `overlay.scrim`, and a true `iconMuted` to `ThemeContext`. Refactor `orderStatuses.ts` to read from it.
5. **Migrate auth screens** to `useTheme()` — kills the Tailwind system and unlocks dark mode on the critical flow.

**Phase 3 — Enforcement & cross-surface:**
6. Add an ESLint rule (`react-native/no-color-literals` or a custom rule) to fail CI on raw hex in `style` objects.
7. **Unify backend brand**: change Unfold primary, PWA `theme_color`, `admin_ops.css`, offline/404 pages, and the email button to `#0056F1`; standardize the web font (host Poppins or pick one fallback). Regenerate PWA icons on brand blue.
8. Decide the fate of green/purple/orange themes — wire a real switcher or delete the dead config.

---

## 12. Proposed Figma Design-Token Structure

```
Connect Laundry / Tokens
├── color/
│   ├── brand/{primary, primaryDark, secondary, onPrimary}
│   ├── semantic/{success, warning, error, info}
│   ├── status/{pending, confirmed, rejected, pickedUp, inProcess,
│   │            outForDelivery, delivered, completed, cancelled}
│   ├── accent/{gold}
│   ├── text/{primary, secondary, disabled, placeholder, inverse}
│   ├── surface/{background, surface, surfaceVariant, card}
│   ├── border/{default, outline, divider}
│   └── overlay/{scrim, glass}
│       └── each: { light, dark }  (alias mode tokens)
├── typography/
│   ├── fontFamily/{regular, medium, semibold, bold}  → Poppins-*
│   └── style/{displayXL, h1, h2, h3, title, subtitle, body,
│              bodySmall, label, caption, badge, button, input}
│       └── each: { size, weight, lineHeight, letterSpacing }
├── space/{0,1=4,2=8,3=12,4=16,5=20,6=24,8=32,10=40}
├── radius/{xs=4, sm=8, md=12, lg=16, xl=20, full=999}
├── elevation/{subtle, medium, brand}
├── size/{icon: sm16/md20/lg24, avatar: sm32/md48/lg80, control: 40/44/48}
├── zIndex/{base=1, dropdown=10, overlay=100, modal=1000, toast=2000}
└── motion/{fast=150, base=300, slow=600, easing.standard}
```

---

## 13. File-by-File Index (where colors & fonts are defined)

| Definition | Location |
|------------|----------|
| Color themes (all tokens) | `src/context/ThemeContext.tsx:10-198` |
| `useTheme()` hook | `src/context/ThemeContext.tsx:322-329` |
| Order status colors | `src/features/orders/constants/orderStatuses.ts:3-12` |
| Notification color (config) | `app.config.ts` (`expo-notifications` color `#0056F1`) |
| Splash background | `app.config.ts` (`#ffffff`) |
| Auth Tailwind palette | `app/authScreens/{signIn,signUp,forgotPassword,resetPassword}.tsx` |
| Gold rating star default | `src/components/shared/RatingStars.tsx:16` |
| Brand-blue hardcodes | `AddAddressModal.tsx`, `LaundryItemCard.tsx`, `ServiceTags.tsx` |
| Fonts (files) | `assets/fonts/Poppins-*.ttf`, `SpaceMono-Regular.ttf` |
| Font *loading* | **none** (no `useFonts`/`Font.loadAsync` in repo) |
| Tab bar font | `app/(tabs)/_layout.tsx:56` |
| Backend admin primary | `config/settings.py:531-544` (`#9333ea`) |
| Backend admin-ops tokens | `marketplace/static/admin_ops/admin_ops.css` |
| Backend PWA manifest | `templates/pwa/manifest.html:7-8` |
| Backend email styles | `users/templates/users/password_reset_email.html` |
| Backend 404 styles | `templates/404.html` |

---

## Validation — totals & second pass

| Metric | Value |
|--------|------:|
| Total unique hex colors (mobile) | **138** |
| Total hex usages (mobile) | **878** |
| Total `rgba()/rgb()` usages | **65** |
| Total unique font *families* declared | 2 (`Poppins`, system/Roboto) + 1 unused (`SpaceMono`) |
| Font families actually *loaded* | **0** |
| Total `fontFamily` declarations | 68 (67 Poppins) |
| Total `fontWeight` declarations | 370 |
| Total `fontSize` declarations | 502 (≈14 distinct sizes) |
| Reconstructed typography styles | 12 |
| Hardcoded colors outside theme (unique) | ~113 of 138 are not theme tokens |
| `StyleSheet.create` blocks | 126 |
| Components calling `useTheme()` | 104 / 157 `.tsx` |
| Components fully token-compliant | ~18% |
| Competing "primary blue" shades | 6–8 |
| Competing "error red" shades | 4 |
| Backend brand colors (off-mobile) | `#9333ea`, `#7e22ce`, `#007bff`, `#f97316` |
| Backend fonts (off-Poppins) | Inter, Outfit, Segoe UI |
| Dead asset files identified | 5 (`react-logo*`×4, `SpaceMono`) |

**Second verification pass confirms:**
- ✅ No colors missed — 138 unique hex enumerated from full-repo grep; top-60 manually purpose-mapped.
- ✅ No fonts missed — all 19 ttf files listed; loading absence verified by negative grep.
- ✅ Backend branding included — admin, PWA, email, 404, legal, icons all covered.
- ✅ Duplicates correctly identified — competing-shade tables in §3.3 cross-checked against `ThemeContext` tokens.

*End of audit. No application code was modified.*
