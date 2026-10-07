# Proof app without GMS

Type: research
Status: open
Blocked by:

## Question

Does the proof app (EAS build `183b566c`, v2-React-Native commit `09ef72912b9edf91410b5055aa3a321b3b577d1b`) reach its first screen on an Android image with no Google Play Services? Answer these:

- Which dependencies touch GMS at startup and could crash or hang before the first screen: `@react-native-firebase/app` and `/messaging`, Plaid, Sentry, Amplitude, expo-updates, react-native-permissions, others? Read the source at that commit.
- Does the first screen need the network, a WebView or a browser?
- Which ABIs does the APK contain (is x86_64 in it)? Use the artifact URL in the map Notes.
- What would have to be faked or stubbed for the first screen to render?
