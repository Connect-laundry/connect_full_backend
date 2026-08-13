# Simame Physical Device Acceptance Test

Release candidate: __________  Build IDs: Android __________ / iOS __________
Tester/date: __________  Backend release: __________

A simulator, Expo Go, or debug bundle is not acceptance evidence. Use signed development/release candidates connected to the intended non-production environment first. Record screenshots, order IDs and payment references without exposing credentials or full personal/payment data.

## Device Matrix

| Platform | Minimum supported OS/device | Current common OS/device | Newest OS/device | Result/evidence |
|---|---|---|---|---|
| Android | __________ | __________ | Android 16 / API 36 device or emulator | __________ |
| iOS | __________ | __________ | newest supported iOS/iPhone | __________ |

Test at least one lower-memory Android device and one physical iPhone. Repeat critical paths on Wi-Fi and mobile data.

## Install, Identity And Session

- [ ] Install cleanly with no prior app data; first screen says Simame and splash/icon are correct.
- [ ] First launch does not show development menus, mock data, stale Connect customer branding or secrets.
- [ ] Complete onboarding; kill/restart and verify onboarding does not repeat incorrectly.
- [ ] Sign up with email/password; validate duplicate email, weak password and malformed phone handling.
- [ ] Complete email/phone verification if enabled in the production Clerk policy.
- [ ] Complete Google/Apple/other configured OAuth on cold and warm app starts.
- [ ] Log in, background/foreground, kill/restart and confirm session restoration.
- [ ] Expire/revoke the backend session; confirm one controlled refresh and a clean sign-in redirect, not a retry loop.
- [ ] Log out; verify protected data is cleared and back navigation cannot reopen it.
- [ ] Request forgotten credentials and complete reset from the production email/link flow.
- [ ] Edit profile and avatar; test camera/photo denial and upload failure.
- [ ] Open Settings -> Account Settings -> Delete Account; cancel once, then confirm on a disposable user.
- [ ] After deletion, verify local logout, API rejection, Clerk sign-in failure, revoked sessions and inability to restore the old session.
- [ ] Confirm retained historical order/payment records show an anonymized customer in operations tools.

## Permissions And Location

- [ ] Deny location on first request; discovery remains usable with manual/default location and a clear recovery action.
- [ ] Grant approximate location; nearby laundries and distance behavior remain coherent.
- [ ] Grant precise location; map/current-location behavior works and does not request background access.
- [ ] Revoke permission in OS settings while app is backgrounded; resume without crash or blocked screen.
- [ ] Verify there is no microphone prompt.
- [ ] Deny notification permission; inbox still works in-app and settings explain how to enable later.
- [ ] Grant notifications; verify token registration and no duplicate device registration.

## Discovery, Pricing And Booking

- [ ] Laundry list loading, empty, pagination, retry and offline states.
- [ ] Nearby/map discovery, filters, favorites and unfavorite persistence.
- [ ] Laundry details, services, item pricing, weight pricing and unavailable/suspended laundry behavior.
- [ ] Review submission only for an eligible completed order; duplicate/invalid review is rejected gracefully.
- [ ] Build by-item, by-weight and hybrid bookings where supported.
- [ ] Verify server estimate equals checkout totals; test stale price/change response before order creation.
- [ ] Validate coupon success, invalid, expired, wrong-laundry and minimum-spend cases.
- [ ] Deny/out-of-zone address, unsupported schedule and unavailable service produce recoverable validation.
- [ ] Submit once under slow network; verify no duplicate order from repeated taps/retries.

## Paystack Test Mode

Use Paystack's official test credentials/cards only. Do not record card details in evidence.

- [ ] Backend initializes transaction; mobile receives HTTPS Paystack authorization URL and exact reference.
- [ ] Success returns through `connect-laundry://` callback and refreshes order to `SUCCESS` with receipt available.
- [ ] Failed card shows `FAILED`, no paid order state and a controlled retry.
- [ ] Abandon browser/close app; order shows `PENDING`/abandoned accurately and can resume the same authorization session.
- [ ] Expired transaction shows `EXPIRED` and permits a safe new initialization according to backend rules.
- [ ] Repeat Pay immediately; verify no duplicate reference/charge and no overwritten successful payment.
- [ ] Replay callback and webhook in test tooling; verify idempotent state and one settlement.
- [ ] Underpayment, overpayment, wrong currency, wrong metadata/order and foreign-user reference are rejected.
- [ ] `REFUND_PENDING` and `REFUNDED` appear accurately using controlled backend test fixtures/admin actions.
- [ ] A paid non-cash order cannot be customer-cancelled where backend policy forbids it.
- [ ] Cash order shows cash due, not Paystack paid.
- [ ] Custom quote order shows awaiting invoice/payment until a payment exists.

## Paystack Live Mode (Owner-Controlled)

Run only after live keys/webhook/domain configuration is approved. Use the smallest permitted real amount and authorized tester/card. Never run from Codex automation.

- [ ] One live successful charge; compare Paystack dashboard, backend Payment, settlement/order state and customer receipt.
- [ ] One live failed/abandoned attempt; verify no false success.
- [ ] Confirm live webhook signature, delivery and retry behavior.
- [ ] Perform one owner-approved refund; verify `REFUND_PENDING` -> `REFUNDED`, Paystack dashboard and customer UI.
- [ ] Reconcile gross amount, fees, expected settlement and ledger/reporting.

## Orders, Owner Actions And Tracking

- [ ] New order appears in owner web without manual refresh delay beyond expected polling/realtime behavior.
- [ ] Owner accept/reject/quote transitions appear correctly on customer device.
- [ ] Pickup, washed, out-for-delivery, delivered and complete follow only allowed transitions.
- [ ] Invalid/stale owner or customer transition is rejected and UI refreshes to server truth.
- [ ] Customer cancellation works only in allowed order/payment states and shows resulting status/refund policy.
- [ ] Tracking timeline and map survive background/foreground, app kill/restart and temporary location loss.
- [ ] Receipt totals, currency, payment method/state, laundry and line items match backend.

## Notifications And Deep Links

Test foreground, background and killed app for each relevant event.

- [ ] Order status push reaches the correct user/device only.
- [ ] Notification tap opens the correct order and handles unauthenticated sign-in then resumes safely.
- [ ] Invalid/missing order ID opens a safe fallback, not arbitrary navigation.
- [ ] Payment callback handles valid, missing, expired and malicious parameters.
- [ ] OAuth/password redirects handle valid, expired and tampered state.
- [ ] Referral/order links, if distributed, handle cold/warm/background states.
- [ ] Mark read, read all, unread badge, preferences and token unregister persist server-side.
- [ ] Notification payload/lock-screen text does not expose unnecessary payment or address data.

## Network And Resilience

- [ ] Airplane mode at launch, login, discovery, checkout, payment return and tracking.
- [ ] 2G/high latency/packet loss during create and verify; no duplicate side effects.
- [ ] Backend 401, 403, 404, 409, 429, 500 and timeout produce suitable messages/retry controls.
- [ ] App background/foreground and kill/restart during authentication, checkout and payment verification.
- [ ] Render worker/Redis interruption in a non-production test environment; synchronous core order/payment truth remains consistent and jobs retry safely.
- [ ] OTA update check failure does not block app launch; signed update behavior is verified on a matching runtime.

## Sign-Off

| Area | Android | iOS | Evidence link / issue |
|---|---|---|---|
| Identity/account deletion |  |  |  |
| Permissions/location |  |  |  |
| Discovery/booking |  |  |  |
| Paystack test mode |  |  |  |
| Paystack live mode |  |  |  |
| Orders/owner parity |  |  |  |
| Notifications/deep links |  |  |  |
| Resilience |  |  |  |
| Store artifact inspection |  |  |  |

Release owner approval: __________  Date: __________

Any unresolved P0/P1, production test credential, false payment state, cross-user data exposure, failed account deletion or unsigned/uninspected artifact blocks release.