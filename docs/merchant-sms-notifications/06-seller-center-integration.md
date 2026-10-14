# 06 — Seller Center Integration & Laundry Owner UI

## 1. Overview & Location

The Laundry Owner SMS Notification experience is integrated directly into SIMAME's Next.js Seller Center (`Connect-Web-App`):
- **Page Location**: `/settings` (authenticated merchant dashboard).
- **Tab Component**: `src/features/settings/components/NotificationsTab.tsx`.
- **Card Component**: `src/features/settings/components/SmsBookingAlertsCard.tsx`.
- **API Client**: `src/features/settings/api/sms-alerts.ts`.

---

## 2. Security & Authorization Model

1. **Role-Based Access Control**:
   - Every backend endpoint enforces `IsLaundryOwnerRole` (`request.user.role == 'OWNER'`).
   - The laundry instance is resolved strictly from `request.user` via `Laundry.objects.filter(owner=request.user).first()`.
   - No `laundry_id` is accepted from the client request body or query string, completely eliminating Insecure Direct Object References (IDOR).
2. **Credential Protection**:
   - No Arkesel API credentials or secret tokens are ever exposed to the client or browser.
3. **Data Masking**:
   - All phone numbers returned to the UI are masked (e.g. `+233 24 *** 1234`).
   - Full phone numbers and OTP secrets are never transmitted across client responses.

---

## 3. UI Features & User Interactions

### 3.1 Status & Verification Badge
- **Verified State**:
  - Displays green badge with check icon: `Verified`.
  - Shows masked phone number and method: `Verified via SMS verification code` or `Verified via sign-in account`.
  - Master SMS switch is enabled.
- **Action Required State**:
  - Displays amber badge: `Action Required`.
  - Helper message: `No verified phone configured. Add and verify your phone number to start receiving order alerts.`
  - Master SMS switch is disabled until verified.

### 3.2 Master SMS Toggle
- Toggles `sms_enabled` via `PATCH /api/v1/laundries/dashboard/sms-alerts/`.
- Optimistic feedback with Sonner toast:
  - Enabled: `"SMS booking alerts enabled for your laundry."`
  - Paused: `"SMS booking alerts paused."`

### 3.3 "Use Sign-in Phone" Quick Adoption
- When the owner logged into SIMAME via Clerk with a verified mobile number, and that number differs from the laundry notification setting, a button appears:
  - `Use Sign-in Phone (+233 50 *** 9999)`
- Clicking executes `POST /api/v1/laundries/dashboard/sms-alerts/use-account-phone/`, adopting the verified number instantly without requiring a separate OTP SMS.

### 3.4 Phone Number Verification Flow (Modal)
- Clicking `Set Up Number` or `Change Number` opens the verification modal:
  1. **Step 1: Phone Input**:
     - Owner enters mobile number (accepts `024...`, `+233...`, etc.).
     - Submitting calls `POST /api/v1/laundries/dashboard/sms-alerts/verify/start/`.
     - Generates 6-digit random code, calculates salted HMAC-SHA256 digest, sets 10-minute expiry, and texts the code via Arkesel.
  2. **Step 2: 6-Digit OTP Entry**:
     - Displays `InputOTP` slots with numeric formatting.
     - Submitting calls `POST /api/v1/laundries/dashboard/sms-alerts/verify/confirm/`.
     - Verifies digest, clears pending challenge, updates `phone_verified_at`, and activates SMS alerts.

### 3.5 "Send Test SMS" Feature
- Available only when phone is verified.
- Calls `POST /api/v1/laundries/dashboard/sms-alerts/test/`.
- Enforces strict abuse prevention:
  - Daily limit: 3 test SMS per laundry per calendar day.
  - Rate-limited attempts return HTTP 429 with user-friendly toast: `"Daily test limit reached (3/day)"`.
  - Dispatches a verified test alert to the owner's phone via Arkesel sandbox/live gateway.

### 3.6 Recent SMS Delivery Activity Table
- Displays the last 10 SMS notifications sent to the owner:
  - **Order No**: e.g., `#SM-1042`.
  - **Type**: e.g., `New booking`, `Cancelled`, `Changed`.
  - **Status Badge**:
    - `Delivered` (Green)
    - `Sent` (Blue)
    - `Sending` (Amber)
    - `Not sent` / `Failed` (Red, with failure reason tooltip)
  - **Sent Time**: Formatted localized timestamp.
