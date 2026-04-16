# Burrow Remote (Android)

This folder contains a clean-room Android client for Burrow desktop sessions.
It is isolated in the `android/` workspace and does not modify Python
runtime behavior.

## What is implemented
 - WebSocket transport to Burrow registry using existing desktop command schema.
 - Peer discovery and session orchestration:
   - register / peers
   - desktop_session_open
   - desktop_frame_request
   - desktop_input
   - desktop_privacy
   - desktop_session_close
   - desktop_frame
 - Live frame polling and image decoding on the client.
 - Mouse and text interaction using native Burrow actions:
   - `move`, `click`, `mouse_button`, `scroll`, `type_text`
   - gesture-based tap/double-tap/long-press + drag
 - Clipboard helpers for common `clipboard_*` actions (`copy`, `cut`, `select_all`) and system clipboard paste for `clipboard_paste_text`.
 - Stream telemetry UX: frame sequence count, last-frame age, and stubbed/liveness indicator.
- Stream control UX improvements:
  - adaptive frame request pacing driven by observed frame gaps and jitter
  - stream stability monitor + stale recovery requests when telemetry ages out
  - smoothed/EMA jitter metrics exposed for live UI feedback
- Input transport hardening:
  - coalescing/batching pipeline for mouse move and scroll events
  - bounded burst queue for key/clipboard actions to reduce send noise
 - Session controls improved for mobile usability:
   - keep-screen-on behavior
   - basic quick-action keyboard cluster and clipboard toolbar
 - connection hardening:
   - websocket heartbeat (ping/pong keepalive) for long sessions
   - background-safe frame decoding path
 - permission/event visibility in session status


## Layout
- `app/src/main/java/com/nila/burrow/remote/MainActivity.kt`
- `app/src/main/java/com/nila/burrow/remote/RemoteViewModel.kt`
- `app/src/main/java/com/nila/burrow/remote/network/BurrowDesktopClient.kt`
- `app/src/main/java/com/nila/burrow/remote/model/ProtocolModels.kt`
- `app/src/main/java/com/nila/burrow/remote/ui/BurrowRemoteScreen.kt`

## Build / run note
- This workspace includes a checked-in Gradle wrapper in `android/gradle/wrapper/`.
- Use the wrapper consistently:
  - `./gradlew :app:assembleDebug`
  - `./gradlew :app:tasks`
- For a full Android build, use a modern Android toolchain in CI/local environment:
  - JDK 17+
  - Android SDK with API 35
  - Recent Gradle/AGP compatible with this module.

## Command examples
- Connect to local registry and build once available:
  - `cd android`
  - `./gradlew :app:assembleDebug`
