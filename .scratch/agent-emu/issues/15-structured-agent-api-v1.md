# Structured agent API v1

Type: grilling
Status: resolved
Blocked by:

## Question

Which structured layers ship in v1 beyond computer use, and in what order? Candidates:

- UI tree
- app JS/Hermes state through CDP
- network capture and mocking
- app storage, keychain and files
- logs
- clock control
- push injection
- deep links and intents
- GPS, sensors and camera image injection

## Answer

Grilled with Aaron on 2026-10-07. Computer use (Computer-use API v1) and the UI tree ship first. All four structured layers below are in v1, built in this order:

1. **Logs and crash events:** logcat streamed and filtered to the app, plus pushed crash and ANR events.
2. **Device controls:**
   - deep links and intents;
   - GPS;
   - camera image injection (QR codes for the machine flow);
   - clock freeze and advance;
   - permission grants;
   - app install and clear-data.
3. **App JS state through CDP:** the Hermes Chrome DevTools Protocol for console, JS errors, evaluate, and reading the Redux store. It works without Metro through the in-app inspector (to be checked in the spike).
4. **Network capture and mocking:** the off-by-default capture from Device networking, exposed as calls (list requests, mock, fail). It needs the test CA plus a host proxy, so it comes last.

- Each layer is a set of calls on the binary API with matching MCP tools that take a device id, like the computer-use calls.
- Not decided: exact call names and schemas per layer. That's builder-level detail; the spec gives the list above.
