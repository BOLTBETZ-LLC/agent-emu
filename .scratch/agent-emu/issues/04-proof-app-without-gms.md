# Proof app without GMS

Type: research
Status: resolved
Blocked by:

## Question

Does the proof app (EAS build `183b566c`, v2-React-Native commit `09ef72912b9edf91410b5055aa3a321b3b577d1b`) reach its first screen on an Android image with no Google Play Services? Answer these:

- Which dependencies touch GMS at startup and could crash or hang before the first screen: `@react-native-firebase/app` and `/messaging`, Plaid, Sentry, Amplitude, expo-updates, react-native-permissions, others? Read the source at that commit.
- Does the first screen need the network, a WebView or a browser?
- Which ABIs does the APK contain (is x86_64 in it)? Use the artifact URL in the map Notes.
- What would have to be faked or stubbed for the first screen to render?

## Answer

- **Yes, very likely.** Logged out on a fresh install, nothing on the cold-start path needs GMS. This is from reading the source only. It has not run on a no-GMS device.
- **First screen = login-v2 `Start`, not legacy `Onboarding`.** `.env.staging:25` sets `LOGIN_V2_FORCE=true`. `android/app/build.gradle:23-24` maps the staging variants to `.env.staging`. `useLoginV2Flag.ts:59-62,86` returns true when forced. `config.ts:22` allow-lists the `staging` flavor. `navigation/index.tsx` selects `AuthV2Flow`. `initialRoute.ts:67-74` sends a no-credentials launch to `Start`.
- **The APK has x86_64.** It also has arm64-v8a, and nothing else. Each ABI has 35 libs: x86_64 is 51,182,984 B and arm64 is 47,915,376 B. The JS is embedded as `assets/index.android.bundle`, 8,961,164 B, stored uncompressed. I re-read the zip directory with HTTP range requests (full size 145,343,072 B).
- **FCM (`/messaging`) is off the logged-out path.** `getToken` runs only in `MainTabs` (after login), the tutorial and logout cleanup. All three catch errors.
- **RNFirebase app, Sentry, ML Kit and Plaid only touch startup through manifest providers or nothing at all.** None of them waits on GMS.
- **Amplitude is off in this build.** `.env.staging:20` sets an empty key, and `amplitude.ts:52-55` returns early. This means its GMS probes never run.
- **expo-updates:** it needs the network, not GMS. Offline, the splash holds up to 10 s and the watchdog fires at 15 s. Online, it may fetch a newer `staging` OTA.
- **No WebView or browser is needed for `Start`.** A Custom Tabs browser is needed later, for Auth0 web login.
- **The network is optional, but it changes what is on screen.** Offline: NoInternetModal opens over `Start`. Online: the version check (8 s timeout, fails open) can swap in UpdateGate if staging says an update is required.
- **The splash has no overall time limit.** If AsyncStorage fails in `hydrateLocale`, `isLocaleReady` never becomes true and the splash stays up. This is not caused by GMS, but a trimmed image must keep normal app storage working.
- **Nothing must be faked or stubbed for the first screen.**
- For a deterministic run: do a fresh install or `pm clear`. Block `u.expo.dev` (or log the update ID) so the embedded JS is what runs. Give the Device internet, or accept the offline modal.
- **Still unverified:** that native init on a no-GMS API 36 image throws nothing before JS mounts. Check it with one launch and logcat.

| Dependency | Touches GMS at startup? | Failure without GMS | Evidence |
|---|---|---|---|
| `@react-native-firebase/app` 23.4.1 | Only through passive init providers | Reads resources only. The availability check returns a status and does not throw. No crash expected (unverified on device). | Merged manifest `FirebaseInitProvider`, `ReactNativeFirebaseAppInitProvider`; `ReactNativeFirebaseUtilsModule.java:117-145` |
| `@react-native-firebase/messaging` 23.4.1 | No (logged out) | `getToken` rejects; every caller catches it | `MainTabs.tsx:36`; `Tutorial/Notification.tsx:25`; `authSlice.ts:732-794,827-828` |
| Amplitude 1.8.0 | No (key empty, init skipped) | None. With a key, the GMS probes are caught. The App Set ID wait has no timeout. | `.env.staging:20`; `amplitude.ts:52-55`; `AndroidContextProvider.kt:154-270` |
| Sentry 7.4.0 | No | Queues while offline | `App.tsx:47`; `sentry.ts:120`; sentry-android-core 8.23.0 has no GMS dependency |
| expo-updates 57.0.15 | No (network only) | Offline: splash held for up to 10 s | `AndroidManifest.xml:10-15`; `launchUpdate.ts:23,79-96`; `useLaunchUpdateGate.ts:14,40-66` |
| Plaid 12.4.0 | No | n/a (screen comes later) | `AuthStack.tsx:82`; `Plaid/index.tsx:56-83` |
| react-native-permissions 5.4.2 | No | n/a (not on the startup path) | `RNPermissionsModuleImpl.kt:106-201` |
| react-native-auth0 5.11.1 | No | Later: `a0.browser_not_available` if there is no browser | `auth0Client.ts:220-228`; `initialRoute.ts:74` |
| react-native-keychain 10.0.0 | No | A read error leaves the user logged out | `App.tsx:116-153`; `authSlice.ts:297-321` |
| expo-store-review 57.0.2 | No (only counts opens; request threshold is 3) | Later: isAvailable returns false | `useAppOpenReviewPrompt.ts:11-14`; `reviewPromptConfig.ts:9` |
| VisionCamera + ML Kit barcode | No (scan screen only) | Model is bundled, so it needs no GMS | `lib/x86_64/libbarhopper_v3.so` in the APK |
| NetInfo 11.4.1 | No | Offline modal over `Start` | `NoInternetModal.tsx:10-73` |

### Disagreements

- **First screen.** A said v2 `Start`. B said `Onboarding` or `Start`, unverified, because it read no env files. **A wins.** I checked `.env.staging:25`, `build.gradle:23-24`, `config.ts:22` and `useLoginV2Flag.ts:59-62,86` at 09ef729.
- **ABIs in the APK.** A said x86_64 is present. B said unverified, because it could not fetch the URL. **A wins.** My range read of the zip directory gave the same counts and sizes as A.
- **Amplitude touches GMS at startup.** B said it probes GMS and the App Set ID wait has no bound. A said it is off in this build. **A wins for this build.** `.env.staging:20` is empty and `amplitude.ts:52-55` returns early. react-native-config 1.5.5 `dotenv.gradle:52-55,71` writes empty values as `""`, not undefined, so the non-empty `DEFAULT_API_KEY` (`amplitude.ts:21,48`) is not used. B's point still holds for any build that has a key.
- **Is the splash bounded?** A said auth and the update gate always settle. B said there is no overall watchdog and a locale failure can hold the splash. **B wins.** In `App.tsx:131-153`, a throw in `hydrateLocale` (`localePreference.ts:23` reads AsyncStorage, `:30` rethrows) skips `setIsLocaleReady(true)`. That leaves the splash up forever. This risk has nothing to do with GMS.
- **Native SDK init before JS.** A said these are passive providers and no crash is expected. B said an autolinked SDK (FCM auto-init, Firebase providers) could fail before JS mounts. **Unresolved.** Neither pass ran the APK. To settle it: install it on a no-GMS API 36 x86_64 image and launch it fresh. Look for `FATAL EXCEPTION` or a GMS `SecurityException` in logcat before `Start` renders.

Context: Pass A (Claude) is `C:/dev/worktrees/agent-emu--research-proof-app-no-gms/.scratch/agent-emu/research/04-proof-app-without-gms.md` on branch `research/proof-app-no-gms`. Pass B (GPT-6.1 Sol) is `C:/dev/worktrees/agent-emu--codex-proof-app-without-gms/.scratch/agent-emu/research/04-proof-app-without-gms.codex.md` on branch `research/codex-proof-app-without-gms`, not yet committed.
