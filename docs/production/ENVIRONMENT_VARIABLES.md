# Simame Environment Variables

Date accessed for source guidance: 2026-08-13.

## Official Sources Used

- Expo EAS environment variables: https://docs.expo.dev/eas/environment-variables/
- Clerk Expo production deployment: https://clerk.com/docs/guides/development/deployment/expo
- Paystack payments: https://paystack.com/docs/payments/accept-payments/
- Django deployment checklist: https://docs.djangoproject.com/en/dev/howto/deployment/checklist/

## Expo Public Variable Rule

Anything bundled into `EXPO_PUBLIC_*` is public to anyone who can run the app. Never put database passwords, Supabase service-role keys, Clerk secret keys, Paystack secret keys, SMTP passwords, Django `SECRET_KEY`, Cloudinary API secrets, or Google service-account JSON in Expo public variables.

## Environment Matrix Required Before Launch

| Surface | Development | Staging / Preview | Production |
| --- | --- | --- | --- |
| Database | local/dev only | staging only | production only |
| Backend domain | local/dev | staging domain | production domain |
| Clerk | development instance | staging/test instance | production instance |
| Paystack | test mode | test mode | live mode |
| Storage | dev bucket/cloud | staging bucket/cloud | production bucket/cloud |
| Redis/Celery | local/dev | staging | production |
| Sentry | development env | staging env | production env |
| EAS environment | development | preview | production |
| EAS channel | development | preview/staging | production |
| Email | sandbox/dev | staging sender | production verified sender |
| Push notifications | dev credentials | preview credentials | production APNs/FCM credentials |

## Current Repo Blockers

- `connect-customer-mobile/eas.json` production currently uses a Clerk test publishable key and `grown-mole-74.clerk.accounts.dev` URLs.
- Multiple mobile profiles point at `https://connect-full-backend.onrender.com/api/v1`; confirm whether this is production or shared staging before launch.
- Production privacy/support URLs still point to `connectlaundry.com`; update when Simame production domains are ready.
- Backend `DEFAULT_FROM_EMAIL` and laundry approval notification fallback include a personal email; replace with configured Simame business addresses.
## Paystack Production Contract

- PAYSTACK_SECRET_KEY: backend secret store; production must use an sk_live key.
- PAYSTACK_PUBLIC_KEY: matching production pk_live key.
- PAYSTACK_CALLBACK_URL: HTTPS backend bridge ending in /api/v1/payments/callback/.
- PAYSTACK_APP_CALLBACK_URL: fixed connect-laundry://orders/payment-callback mobile route.
- PAYMENT_CURRENCY: GHS.
- Paystack dashboard webhook: HTTPS /api/v1/payments/webhook/, not the callback route.
- Django deploy checks are a release gate for key mode, HTTPS callback, app scheme, and currency.

Rotate the Clerk webhook secret found in git history and the Expo access token found in the working tree before release. Redaction alone is not credential revocation.
## Dependency Release Notes - 2026-08-13

- Mobile compatible audit fixes were applied without using force. Twenty-five advisories remain, including ten high findings in the Expo/Metro image-size chain. Treat this as a release blocker until upstream remediation or a planned supported Expo upgrade is verified.
- The owner web production lockfile audit is clean after updating Nano ID, but the app gates could not run because the local drive ran out of space during npm ci.
- Install backend requirements in a dedicated virtual environment. The project pins Click 8.3.3; the shared workstation Python also contains Semgrep, which pins an incompatible Click 8.1 series.
