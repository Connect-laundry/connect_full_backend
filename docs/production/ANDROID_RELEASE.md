# Simame Android Google Play Runbook

Date accessed for source guidance: 2026-08-10.

## Official Sources Used

- Google Play app setup and signing: https://support.google.com/googleplay/android-developer/answer/9859152
- Google Play internal, closed, and open testing: https://support.google.com/googleplay/android-developer/answer/9845334
- Google Play testing requirements for new personal accounts: https://support.google.com/googleplay/android-developer/answer/14151465
- Google Play Data safety: https://support.google.com/googleplay/android-developer/answer/10787469
- Clerk Expo production deployment: https://clerk.com/docs/guides/development/deployment/expo

## Required Values

- App name: Simame
- Android package currently configured: `com.connectlaundry.app`
- Artifact for Play production/testing: AAB, not local APK proof
- EAS production profile: `production`
- EAS channel: `production`

## Build and Testing Flow

1. Confirm Google Play app package matches `com.connectlaundry.app`.
2. Confirm Play App Signing and upload key are configured.
3. Download or record Play app signing SHA-256 and upload SHA-256 fingerprints.
4. Add Android package and production SHA-256 fingerprints to Clerk Native Applications and Google OAuth credentials.
5. Run `npm.cmd run validate:release:strict`.
6. Run `eas build --platform android --profile production`.
7. Upload AAB to internal testing or closed testing.
8. Test the Play-distributed build on physical Android devices. Do not certify using Expo Go.
9. If the Google Play developer account is a personal account created after 2023-11-13, run a closed test with at least 12 opted-in testers for 14 continuous days before applying for production access.

## Google Play Manual Metadata

Prepare Data Safety, privacy policy, app access, content rating, target audience, store listing, screenshots, app icon, feature graphic, support information, country availability, and release notes.