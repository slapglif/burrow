# Native sidecars

This directory hosts Burrow native sidecars that complement the Python control plane.

## `burrow-rd-host`

`burrow-rd-host` is the native Burrow desktop host sidecar slice for early RustDesk-aligned desktop hosting. It exposes a narrow JSON-over-stdio IPC contract intended for bridge integration.

Implemented contract surface:
- `capabilities`
- `open_session`
- `snapshot`
- `input`
- `close_session`

Current state:
- Linux display enumeration attempts a real `xrandr --query` probe using a small parser adapted from RustDesk's display probing logic.
- Linux snapshot capture now has a bounded real path when an X11/Xwayland `DISPLAY` is reachable and either ImageMagick `import` or `ffmpeg` `x11grab` is available.
- The real snapshot path captures one cropped PNG for the selected display bounds and returns it as base64.
- Snapshot responses still fall back to a deterministic stub payload when no supported live capture backend is available or the runtime capture attempt fails.
- Input now has a bounded real Linux/X11 execution path when `DISPLAY` and `xdotool` are available.
- Clipboard now reports honest detected backend metadata, but the current IPC surface still gates clipboard operations as unsupported.
- Sessions are in-memory and process-local.

Truthful support boundaries:
- Real today:
  - Linux display ids, primary markers, bounds, origins, and online inventory via `xrandr` when available
  - Single-frame PNG snapshot capture via external X11/Xwayland tools when the host runtime exposes the required command/backend
  - Bounded Linux/X11 native input via `xdotool` for `mouse_move`, `mouse_button`, `key_press`, `key_release`, and `text`
- Still stubbed or incomplete:
  - direct RustDesk `libs/scrap` integration
  - Wayland-native capture without an X11/Xwayland bridge
  - streaming/delta capture
  - Wayland/native non-X11 input paths and `scroll`
  - clipboard sync/read/write IPC commands
  - session recovery/auth/transport hardening

Planned next steps for RustDesk parity:
- replace shell-backed display/capture paths with direct RustDesk `libs/scrap` integration where build scope permits
- wrap RustDesk `libs/enigo` for keyboard and mouse injection
- wrap RustDesk `libs/clipboard` for clipboard sync
- align IPC payloads with the Python bridge and Burrow desktop session model
- add transport/auth/session recovery once later tracks land

Run locally:

```bash
cd native
cargo check
cargo test
printf '{"id":"1","command":"capabilities"}\n' | cargo run -p burrow-rd-host
```
