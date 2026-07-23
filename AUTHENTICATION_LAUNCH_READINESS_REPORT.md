# CONNECT Laundry Authentication Launch Readiness Report

Date: 2026-06-12

## 1. Authentication Architecture Report

Backend source of truth:

- Django/DRF exposes auth under `/api/v1/auth/*` in `connect_new_backend/users/urls.py`.
- Email auth uses `RegisterView`, `LoginView`, `CustomTokenRefreshView`, `LogoutView`, `ProfileView`, password reset views, device session views, and account deletion.
- Clerk social auth uses `SocialLoginView` for Clerk token to backend JWT exchange and `SessionView` for authenticated session validation.
- DRF authentication uses `ClerkOrJWTAuthentication`, accepting internal SimpleJWT tokens first and Clerk session JWTs as fallback.
- SimpleJWT policy is 10-minute access tokens, 14-day refresh tokens, refresh rotation enabled, and blacklist-after-rotation enabled.
- Device/session tracking is bound to `X-Device-ID`, `X-Client-Platform`, and `X-Client-Version` headers.

Mobile implementation:

- `connect-customer-mobile/src/services/auth.service.ts` is the auth boundary.
- Tokens are stored only in `expo-secure-store`.
- `connect-customer-mobile/src/services/http.ts` attaches backend JWTs, device headers, request IDs, idempotency keys, and manages one-at-a-time refresh rotation.
- `connect-customer-mobile/src/context/UserContext.tsx` owns authenticated user state and SecureStore-backed PII cache.
- Clerk OAuth flows in `app/authScreens/signIn.tsx` and `app/authScreens/signUp.tsx` call Clerk, then exchange the Clerk token through `/auth/social-login/`.

## 2. Backend to Frontend API Contract Report

Resolved mismatch:

- Backend login/register/social/refresh responses are raw DRF payloads, not always `{ status, message, data }` envelopes.
- Mobile previously expected `response.data.data` for login/register/social login, which would fail against production.
- Mobile now accepts the current raw backend payload and still tolerates the older envelope shape for transitional compatibility.

Auth contract:

| Flow | Method | Endpoint | Backend request | Backend response consumed by mobile |
| --- | --- | --- | --- | --- |
| Email signup | POST | `/auth/register/` | `email`, `phone`, `first_name`, `last_name`, `password`, `password_confirm`, optional `role` | `accessToken`, `refreshToken`, `user` |
| Email login | POST | `/auth/login/` | `email`, `password` | `accessToken`, `refreshToken`, `user` |
| Clerk social login/signup | POST | `/auth/social-login/` | `clerk_token`, optional `role` | `accessToken`, `refreshToken`, `user` |
| Refresh | POST | `/auth/token/refresh/` | `refresh` | `accessToken`, `refreshToken` |
| Current profile | GET/PATCH | `/auth/me/` | Bearer token, profile fields on PATCH | `{ user }` |
| Logout | POST | `/auth/logout/` | `refresh` | `{ detail }` |
| Active sessions | GET | `/auth/sessions/` | Bearer token | `{ sessions }` |
| Revoke current session | POST | `/auth/sessions/revoke-current/` | `refresh` | `{ detail }` |
| Logout all devices | POST | `/auth/sessions/revoke-all/` | Bearer token | `{ detail }` |
| Delete account | DELETE | `/auth/account/` | optional `reason` | `status`, `message`, `data.deleted` |
| Forgot password | POST | `/auth/forgot-password/` | `email` | `{ message }` |
| Reset password | POST | `/auth/reset-password/` | `reset_id`, `token`, `new_password`, `confirm_password` | `{ message }` |

## 3. Authentication UI/UX Audit Report

Implemented:

- Email/password sign-in is visible again; it was previously hidden behind a disabled render branch.
- Signup is now a real backend-backed registration screen instead of redirecting to sign-in.
- Google and Facebook buttons exist on both sign-in and sign-up screens and use the Clerk to backend session exchange.
- Auth forms include loading states, disabled states, keyboard navigation, field-level errors, sanitized general errors, and client-side rate limiting.
- Remembered email is stored with encrypted SecurePrefs instead of plaintext AsyncStorage.

Remaining UI QA:

- Needs physical-device review on small iPhone, large iPhone, common Android sizes, and dark/light modes.
- Current auth screens still use existing rounded card visual language. It is functional and polished enough for submission, but a later design pass could further reduce decorative weight.

## 4. Security Audit Report

Implemented:

- Auth tokens remain in SecureStore only.
- Refresh token rotation is queued to prevent parallel refresh storms.
- Refresh persistence checks the expected refresh token before committing rotated tokens, preventing stale refresh races after logout.
- Logout makes a best-effort backend blacklist call and always clears local tokens.
- All mobile HTTP requests now reject cleartext HTTP URLs.
- Production API validation requires `connect-full-backend.onrender.com`.
- `.env.example` no longer contains Clerk test publishable keys or `clerk.accounts.dev` URLs.
- Backend `CLERK_APPLICATION_ID` no longer has a hardcoded fallback.
- Sentry config redacts request headers/body and user PII.

Documented findings:

- `eas.json` keeps mock mode only for `development`; `preview` and `production` set `EXPO_PUBLIC_USE_MOCK=false`, and `validate:release` enforces production false.
- Plain AsyncStorage remains only for non-sensitive UI preferences such as theme and notification count.

## 5. Clerk Production Integration Report

Verified in code:

- Mobile uses `@clerk/expo`, `ClerkProvider`, Clerk token cache, `useSSO`, and `getToken({ skipCache: true })`.
- Google/Facebook OAuth strategies call Clerk first, then POST the Clerk JWT to `/auth/social-login/`.
- Backend verifies Clerk JWT issuer/JWKS/audience, syncs local users, supports webhooks, and rejects unverified or unsupported provider data.
- Backend production system checks require Clerk secret, publishable key, JWKS/issuer, and webhook secret.

Manual production action still required:

- Set real production Clerk values in EAS secrets and Render env: publishable key, secret key, issuer/JWKS, application ID, audience, webhook secret, and Clerk callback/deep-link URLs.

## 6. Production Environment Verification Report

Verified:

- Mobile production API base defaults to `https://connect-full-backend.onrender.com/api/v1`.
- Production validation warns if API host is not `connect-full-backend.onrender.com`.
- Live Render health check returned `{"status":"healthy"}` after cold-start delay.
- Release config validation passed.

Environment scan:

- Remaining frontend hits are `EXPO_PUBLIC_USE_MOCK` declarations only.
- Production and preview profiles set mock mode to `false`; development sets it to `true`.

## 7. End-to-End Test Report

Automated checks run:

- `npm.cmd run typecheck` passed.
- `npm.cmd run test:ci` passed: 8 suites, 24 tests.
- `npx.cmd eslint . --no-cache` passed with 0 errors and 11 pre-existing warnings in test/setup files.
- `npm.cmd run validate:release` passed with warnings for env values not present in the local shell.
- `python -m pytest tests/test_auth_sessions.py tests/test_clerk_auth.py tests/test_forgot_password.py` passed: 33 tests.
- Live Render `/health/` returned healthy.

Credentialed live E2E not completed:

- Email signup/login, Google signup/login, Facebook signup/login, password reset completion, profile update, address update, legal acceptance, push registration, logout-all-devices, account deletion, revoked user, deleted account, and invalid Clerk session require production-safe test credentials and live Clerk/OAuth sessions.

Manual verification checklist:

- Create a production test customer with email/password and verify backend session appears in `/auth/sessions/`.
- Complete Google and Facebook OAuth on iOS and Android builds and confirm `/auth/social-login/` returns backend JWTs.
- Restart the app and verify SecureStore session restoration calls `/auth/me/`.
- Force access token expiry and verify one refresh call rotates tokens and replays queued requests.
- Logout current device and verify the refresh token can no longer rotate.
- Run logout-all-devices from Account Settings and verify every session is revoked.
- Request password reset, complete with `reset_id` plus token, and verify old sessions are revoked.
- Register push token after login and verify duplicate registration is idempotent.
- Fetch latest legal docs, accept latest versions, restart, and verify acceptance persists.
- Delete the account and verify local tokens, cached PII, addresses, and backend active sessions are cleared.

## 8. Final Launch Readiness Score

Score: 86 / 100

Ready for internal production candidate build after EAS/Render/Clerk secret review.

Blocking before App Store / Play Store submission:

- Complete credentialed live E2E on production-safe test accounts.
- Confirm production Clerk OAuth redirect URLs for `connect-laundry://auth/clerk-callback`.
- Confirm restricted Google Maps/Mapbox keys, Sentry DSN, privacy/contact URLs, Expo update signing config, and SSL pin set are loaded through EAS secrets.

Backend issues discovered and fixed:

- Removed hardcoded backend `CLERK_APPLICATION_ID` fallback from `config/settings.py`.

Remaining backend issue:

- None requiring code changes from this pass. Operational production env values still need confirmation.
