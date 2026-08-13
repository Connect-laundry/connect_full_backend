# Mobile Dependency Security Assessment

Audit date: 2026-08-13

## Verdict

**CONDITIONAL / accepted build-tool risk, not a reason to force an unsupported upgrade.** `npm audit` reports 26 advisories: 10 high, 16 moderate, 0 critical. `npm audit --omit=dev` still reports 25 (10 high, 15 moderate) because Expo and React Native are production dependencies even though the vulnerable code paths are primarily CLI/Metro/Xcode build tooling. Expo dependency validation is green and Expo Doctor reports 18/18 checks passed.

No `npm audit fix --force` was run. Its proposals either downgrade to incompatible Expo 53 / React Native 0.72 combinations or require a major Expo SDK move. That would create a larger unverified native-runtime risk.

## Installed Platform

- App: `simame@1.0.0`
- Expo SDK: 54 (`expo` 54.x)
- React Native: Expo 54 supported version
- Runtime policy: fingerprint
- `npx expo install --check`: dependencies up to date
- `npx expo-doctor`: 18/18 checks passed
- Android target expectation: Expo SDK 54 compiles/targets API 36 per the official Expo SDK reference. An actual signed AAB must still be inspected before store submission.

Official references:

- [Expo SDK reference and Android target table](https://docs.expo.dev/versions/v55.0.0/)
- [Expo upgrade walkthrough](https://docs.expo.dev/workflow/upgrading-expo-sdk-walkthrough/)
- [Google Play target API requirements](https://support.google.com/googleplay/android-developer/answer/11926878)

## High Advisory Root Cause

All 10 high package entries collapse to one transitive advisory family rooted in `image-size` and propagated through Metro:

`expo -> @expo/metro -> metro -> metro-config / metro-transform-worker -> image-size@1.2.1`

Affected audit entries: `expo`, `@expo/cli`, `@expo/metro`, `@expo/metro-config`, `react-native`, `@react-native/community-cli-plugin`, `metro`, `metro-config`, `metro-transform-worker`, and `image-size`.

| Question | Assessment |
|---|---|
| Direct dependency? | `expo` and `react-native` are direct; the vulnerable parser and Metro packages are transitive. |
| Runtime bundled? | Metro/CLI/config packages execute on developer or CI build hosts. They are not shipped as callable application JS in the production bundle. |
| Reachable in Simame? | Not through a remote mobile user. Exploitation would require a malicious image/file to enter the trusted local/CI bundling process. |
| Supported in-place fix? | No fix compatible with the currently validated Expo 54 dependency graph was offered by npm. |
| Risk controls | Trusted repository inputs, protected CI, lockfile review, no untrusted assets during builds, and isolated build agents. |
| Resolution | Track Expo-supported dependency updates; reassess on every Expo 54 patch and before an SDK upgrade. Do not override Metro versions manually. |

This is an accepted build-pipeline risk for closed testing, subject to the controls above. It is not silently ignored.

## Moderate Advisory Root Cause

Most moderate entries are propagation from Expo configuration packages. `uuid@7.0.3` is reached through `expo -> @expo/config-plugins -> xcode -> uuid`; it is build tooling. Other affected names include `@expo/config`, `@expo/config-plugins`, `@expo/prebuild-config`, `expo-asset`, `expo-auth-session`, `expo-constants`, `expo-dev-client`, `expo-dev-launcher`, `expo-linking`, `expo-manifests`, `expo-notifications`, `expo-splash-screen`, `expo-updates`, `jest-expo`, and `xcode`.

The audit's automatic fix suggestions do not preserve the current supported Expo graph. These items must be tracked upstream and re-evaluated with a supported Expo patch/SDK release.

## Removed Backend Advisory

Backend `pip-audit` initially found `ecdsa==0.19.2` / `PYSEC-2026-1325` with no available fix. Repository search showed no runtime import, and the package was an unused direct requirement. It was removed. A second `pip-audit -r requirements.txt` returned **No known vulnerabilities found**.

## Evidence Commands

```text
npm.cmd audit --json
npm.cmd audit --omit=dev --json
npm.cmd explain image-size
npm.cmd explain uuid
npm.cmd ls
npx.cmd expo install --check
npx.cmd expo-doctor
pip-audit -r requirements.txt
```

## Release Conditions

1. Keep the lockfile immutable in CI (`npm ci`).
2. Do not process untrusted repository assets on privileged build hosts.
3. Re-run audit and Expo validation before each release candidate.
4. Inspect the actual AAB/IPA; this assessment does not replace artifact verification.
5. Upgrade Expo only through the official supported sequence with native regression tests.
6. Escalate to NO-GO if a runtime-reachable critical/high advisory appears or a supported compatible fix is ignored.