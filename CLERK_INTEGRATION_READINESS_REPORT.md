# CONNECT Laundry Clerk Integration Readiness Report

Date: 2026-06-12

## Scope Completed

- Added Clerk identity support to the Django/DRF backend without replacing the local `users.User` model.
- Added secure Clerk session JWT verification via JWKS, issuer, audience, signature, expiration, and required-claim validation.
- Added Clerk-to-local-user synchronization with duplicate prevention and local role preservation.
- Added `/api/v1/auth/social-login/` and `/api/v1/auth/session/`.
- Kept existing internal SimpleJWT access/refresh session lifecycle, device-session tracking, refresh rotation, and logout revocation.
- Integrated `@clerk/expo` into the Expo customer app with `ClerkProvider`, secure token cache, Google/Facebook SSO buttons, backend sync, and Clerk sign-out on logout.
- Disabled the public legacy mobile email/password sign-up route by redirecting it to social sign-in.

## Files Changed

Backend:

- `connect_new_backend/config/settings.py`
- `connect_new_backend/config/test_settings.py`
- `connect_new_backend/users/models.py`
- `connect_new_backend/users/admin.py`
- `connect_new_backend/users/auth/authentication.py`
- `connect_new_backend/users/services/clerk_service.py`
- `connect_new_backend/users/serializers/profile.py`
- `connect_new_backend/users/serializers/social.py`
- `connect_new_backend/users/views/social.py`
- `connect_new_backend/users/urls.py`
- `connect_new_backend/users/migrations/0012_user_clerk_social_identity.py`
- `connect_new_backend/tests/test_clerk_auth.py`
- `connect_new_backend/.env.example`

Mobile:

- `connect-customer-mobile/package.json`
- `connect-customer-mobile/package-lock.json`
- `connect-customer-mobile/app.config.ts`
- `connect-customer-mobile/app/_layout.tsx`
- `connect-customer-mobile/app/authScreens/signIn.tsx`
- `connect-customer-mobile/app/authScreens/signUp.tsx`
- `connect-customer-mobile/app/onboardingScreens/welcome.tsx`
- `connect-customer-mobile/src/api/endpoints.ts`
- `connect-customer-mobile/src/api/types.ts`
- `connect-customer-mobile/src/config/env.ts`
- `connect-customer-mobile/src/context/UserContext.tsx`
- `connect-customer-mobile/src/features/settings/hooks/useLogout.ts`
- `connect-customer-mobile/.env.example`

## New Environment Variables

Backend only:

- `CLERK_APPLICATION_ID`
- `CLERK_API_BASE_URL`
- `CLERK_ISSUER`
- `CLERK_JWKS_URL`
- `CLERK_JWT_AUDIENCE`
- `CLERK_SECRET_KEY`
- `CLERK_JWT_LEEWAY_SECONDS`
- `CLERK_API_TIMEOUT_SECONDS`

Mobile public config:

- `EXPO_PUBLIC_CLERK_PUBLISHABLE_KEY`
- `EXPO_PUBLIC_CLERK_SIGN_IN_URL`
- `EXPO_PUBLIC_CLERK_SIGN_UP_URL`
- `EXPO_PUBLIC_CLERK_UNAUTHORIZED_SIGN_IN_URL`
- `EXPO_PUBLIC_CLERK_USER_PROFILE_URL`

Never expose `CLERK_SECRET_KEY` to Expo, React web, EAS public env, or bundled frontend code.

## Database Migration

Added migration:

- `users/migrations/0012_user_clerk_social_identity.py`

Schema changes:

- `users_user.phone` becomes nullable/blankable so Google/Facebook users can be created without fake phone data.
- Adds `clerk_user_id` as a unique nullable identity mapping.
- Adds `social_provider`.
- Adds `social_profile_image_url`.
- Adds `last_social_login_at`.

Production deployment command:

```bash
python manage.py migrate
```

## Manual Deployment Steps

1. Configure Clerk Google and Facebook OAuth providers in the Clerk dashboard.
2. Configure Clerk redirect/deep-link URLs for `connect-laundry://auth/clerk-callback`.
3. Set backend env vars, especially `CLERK_ISSUER`, `CLERK_JWT_AUDIENCE`, and `CLERK_SECRET_KEY`.
4. Set `EXPO_PUBLIC_CLERK_PUBLISHABLE_KEY` in EAS/mobile build environment.
5. Deploy backend and run migrations.
6. Build/release the Expo app with the new Clerk config.
7. Perform real-device OAuth checks for Google and Facebook.

## Validation Results

Backend:

- `python manage.py check` passed.
- `python -m pytest tests/test_clerk_auth.py -q` passed: 7 passed.
- `python -m pytest tests/test_auth_sessions.py tests/test_registration_role.py laundries/tests/test_my_laundry.py tests/test_payments.py marketplace/tests/test_notifications.py ordering/tests/test_permissions.py -q` passed: 51 passed.
- `python -m pytest -q` passed: 176 passed, 1 third-party `pythonjsonlogger` deprecation warning.

Mobile:

- `npm.cmd run typecheck` passed.
- `npm.cmd run lint` passed.
- `npm.cmd run test:ci` passed: 7 suites, 19 tests.

## Remaining Risks

- Real Clerk issuer/JWKS/audience values must be configured from the actual Clerk dashboard before production traffic.
- The owner React web app is not present in this workspace, so only the backend OWNER role support and owner API regression tests were validated here.
- Real end-to-end OAuth, payment, notification, and multi-device testing requires deployed Clerk credentials plus real devices or production-like simulators.
- Existing local password backend endpoints remain for backward compatibility; public mobile email/password sign-up is no longer linked.

## Rollback Plan

1. Revert mobile release to the previous build or route users back to existing backend auth screens.
2. Remove `@clerk/expo` usage from the mobile build if rolling back the client.
3. Revert backend auth class to `rest_framework_simplejwt.authentication.JWTAuthentication`.
4. Remove `/auth/social-login/` traffic at the API gateway or deploy the previous backend release.
5. If the database migration was applied and must be rolled back, run:

```bash
python manage.py migrate users 0011
```

Only run the migration rollback if no production social users depend on `clerk_user_id`; otherwise keep columns in place and disable traffic instead.

## Launch Verdict

Backend and customer mobile code are ready for staging with real Clerk credentials. Production launch should wait until deployed E2E OAuth tests pass for Google, Facebook, logout/re-login, owner registration, booking, payment, notifications, and multi-device session behavior.
