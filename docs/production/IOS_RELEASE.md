# Simame iOS TestFlight and App Store Runbook

Date accessed for source guidance: 2026-08-10.

## Official Sources Used

- Expo app config: https://docs.expo.dev/versions/v56.0.0/config/app/
- Clerk Expo production deployment: https://clerk.com/docs/guides/development/deployment/expo
- Apple internal TestFlight testers: https://developer.apple.com/help/app-store-connect/test-a-beta-version/add-internal-testers
- App Store Connect app information: https://developer.apple.com/help/app-store-connect/reference/app-information/app-information

## Required Values

- App name: Simame
- Full branding: Simame - Laundry Connect
- iOS bundle identifier currently configured: `com.connectlaundry.app`
- EAS production profile: `production`
- EAS channel: `production`

## Build and Submit

1. Confirm production EAS variables are live and do not contain test Clerk URLs or mock values.
2. Confirm App Store Connect app record uses the bundle identifier above.
3. Confirm push capability, App Attest environment, and notification entitlement match production.
4. Run `npm.cmd run validate:release:strict`.
5. Run `eas build --platform ios --profile production`.
6. Run `eas submit --platform ios --profile production` only after build review.
7. Create TestFlight groups: Internal QA, Founders, Field Beta.
8. Test physical iPhones for install, login, browsing, checkout, payment return, order tracking, push tap routing, logout, relaunch, slow network, and offline recovery.

## Manual App Store Metadata

Prepare app name, subtitle, description, keywords, category, support URL, privacy policy URL, screenshots, review contact, review notes, demo credentials if required, App Privacy answers, encryption/export compliance, copyright, and release method.

## Stop Conditions

Stop for any crash in auth, order creation, payment verification, notification routing, or session restore. Stop if production build points to test Clerk, mock API, staging database, or non-production Paystack.