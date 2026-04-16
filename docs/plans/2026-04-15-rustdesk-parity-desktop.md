# RustDesk-Parity Remote Desktop + Computer-Use Plan

> For Hermes: use subagent-driven-development to execute this plan task-by-task. Treat current `burrow/desktop.py` as a temporary bootstrap, not the final architecture.

Goal: make Burrow deliver RustDesk-class remote desktop for both humans and agents, while reusing RustDesk host/runtime code where it is strongest and keeping Burrow as the rendezvous + relay + control plane.

Architecture: Burrow owns peer identity, permissions, session lifecycle, and relay/rendezvous. A Burrow-managed native host sidecar reuses RustDesk OS-facing code for capture, input, clipboard, and later audio. Human users may attach via tunneled viewer/runtime, while agents use the same session through a computer-use style observe/act API.

Tech stack: Python 3.11+ in Burrow core, Rust native sidecar crate vendoring/adapting RustDesk components (`libs/scrap`, `libs/enigo`, clipboard stack, selected server services), Burrow MCP tools + CLI for control, Burrow registry for relay/rendezvous.

---

## Reality check

This is a multi-phase product build, not a one-patch feature. The current repo state only provides a tunneled backend launcher. It is not honest to call that RustDesk parity.

Current temporary files already in flight:
- `burrow/desktop.py`
- `burrow/peer.py`
- `burrow/mcp_server.py`
- `burrow/cli.py`
- `tests/test_desktop.py`
- `tests/test_peer_desktop.py`

These should be reshaped into the phase-1 session model below, then eventually backed by a native Rust sidecar.

---

## Phase map

### Phase 1 — Shared Burrow desktop/computer-use session model
Deliver a first-class desktop session protocol in Burrow so human and agent use the same abstraction.

### Phase 2 — RustDesk-based native host sidecar
Reuse RustDesk code for capture/input/clipboard behind Burrow-controlled IPC.

### Phase 3 — Continuous media transport and richer parity
Add low-latency streaming, clipboard sync, multi-monitor, permissions, and better reconnection/QoS.

### Phase 4 — Hardening + edge-case parity
Audio, privacy mode, drag/drop, file clipboard, privileged desktop handling, direct-path optimization.

---

## RustDesk code reuse targets

Wrap/reuse where possible:
- Capture:
  - `/tmp/rustdesk/libs/scrap/`
  - `/tmp/rustdesk/src/server/video_service.rs`
- Input:
  - `/tmp/rustdesk/libs/enigo/`
  - `/tmp/rustdesk/src/server/input_service.rs`
  - `/tmp/rustdesk/src/server/uinput.rs`
- Clipboard:
  - `/tmp/rustdesk/libs/clipboard/`
  - `/tmp/rustdesk/src/server/clipboard_service.rs`
  - `/tmp/rustdesk/src/clipboard.rs`
- Later audio:
  - `/tmp/rustdesk/src/server/audio_service.rs`

Do NOT directly port as-is:
- RustDesk Flutter/Sciter connect UI
- RustDesk server product flow (`hbbs`/`hbbr`) as a mandatory dependency
- RustDesk connection/rendezvous protocol internals tightly coupled to `hbb_common`

---

# Phase 1 implementation plan

## Task 1: Add desktop protocol message types

Objective: create first-class Burrow protocol messages for desktop session lifecycle and computer-use actions.

Files:
- Modify: `burrow/protocol.py`
- Test: `tests/test_protocol.py`

Step 1: Write failing tests
- Add failing tests for builders:
  - `desktop_session_open()`
  - `desktop_session_ready()`
  - `desktop_session_close()`
  - `desktop_frame_request()`
  - `desktop_frame()`
  - `desktop_input()`
  - `desktop_permission()`

Step 2: Run test to verify failure
Run: `pytest tests/test_protocol.py -q`
Expected: FAIL with missing builder names/constants.

Step 3: Add protocol constants and builders
- Add message types:
  - `DESKTOP_SESSION_OPEN`
  - `DESKTOP_SESSION_READY`
  - `DESKTOP_SESSION_CLOSE`
  - `DESKTOP_SESSION_LIST`
  - `DESKTOP_FRAME_REQUEST`
  - `DESKTOP_FRAME`
  - `DESKTOP_INPUT`
  - `DESKTOP_PERMISSION`
- Add compact builder helpers matching existing protocol style.

Step 4: Run tests to verify pass
Run: `pytest tests/test_protocol.py -q`
Expected: PASS.

Step 5: Commit
`git commit -m "feat: add desktop session protocol messages"`

## Task 2: Add desktop session data model

Objective: define a shared internal model for desktop sessions and agent/human actions.

Files:
- Create: `burrow/desktop_session.py`
- Test: `tests/test_desktop_session.py`

Step 1: Write failing tests
- Test dataclass/object creation for:
  - `DesktopSession`
  - `DesktopTarget`
  - `DesktopFrame`
  - `PermissionState`
- Test serialization helpers to/from dict.

Step 2: Run test to verify failure
Run: `pytest tests/test_desktop_session.py -q`
Expected: FAIL because module doesn’t exist.

Step 3: Write minimal implementation
- Add small dataclasses and `to_dict()/from_dict()` helpers.
- Keep fields narrow:
  - `session_id`, `peer`, `backend`, `state`, `capabilities`, `viewer`, `computer_use`, `permissions`

Step 4: Run tests to verify pass
Run: `pytest tests/test_desktop_session.py -q`
Expected: PASS.

Step 5: Commit
`git commit -m "feat: add desktop session model"`

## Task 3: Add normalized computer-use action schema

Objective: define the exact shared observe/act contract used by both human tooling and agent tooling.

Files:
- Create: `burrow/computer_use.py`
- Test: `tests/test_computer_use.py`

Step 1: Write failing tests
- Add tests for action normalization:
  - click
  - double_click
  - move
  - drag
  - scroll
  - key
  - hotkey
  - type_text
  - snapshot_request
- Test invalid action validation.

Step 2: Run test to verify failure
Run: `pytest tests/test_computer_use.py -q`
Expected: FAIL due to missing module.

Step 3: Write minimal implementation
- Add parser/validator returning normalized dicts.
- Keep payloads JSON-serializable and explicit.

Step 4: Run tests to verify pass
Run: `pytest tests/test_computer_use.py -q`
Expected: PASS.

Step 5: Commit
`git commit -m "feat: add computer-use action schema"`

## Task 4: Relay desktop protocol in server

Objective: make the Burrow registry relay the new desktop messages.

Files:
- Modify: `burrow/server.py`
- Test: `tests/test_server.py`

Step 1: Write failing tests
- Add server integration tests proving desktop messages route between peers:
  - open
  - ready
  - frame request/frame
  - input
  - close

Step 2: Run test to verify failure
Run: `pytest tests/test_server.py -q`
Expected: FAIL because message types aren’t routed.

Step 3: Write minimal implementation
- Add new desktop message kinds to relay/routing paths.
- Keep server “dumb”: relay only, no desktop business logic.

Step 4: Run tests to verify pass
Run: `pytest tests/test_server.py -q`
Expected: PASS.

Step 5: Commit
`git commit -m "feat: relay desktop session messages through server"`

## Task 5: Refactor peer desktop flow into session manager

Objective: replace ad-hoc desktop bootstrap methods with a session-oriented peer API.

Files:
- Modify: `burrow/peer.py`
- Test: `tests/test_peer_desktop.py`

Step 1: Write failing tests
- Add/update tests for:
  - `open_desktop_session()`
  - `list_desktop_sessions()`
  - `close_desktop_session()`
  - `request_desktop_frame()`
  - `send_desktop_input()`
- Preserve current `start_desktop_session()` behavior only as a compatibility shim if needed.

Step 2: Run test to verify failure
Run: `pytest tests/test_peer_desktop.py -q`
Expected: FAIL with missing methods or mismatched behavior.

Step 3: Write minimal implementation
- Add `_desktop_sessions` as first-class session objects.
- `open_desktop_session()` should:
  - bootstrap remote helper if needed
  - store session metadata
  - tunnel viewer endpoint if available
  - expose same session to agent controls
- `request_desktop_frame()` and `send_desktop_input()` should use the new desktop protocol.

Step 4: Run tests to verify pass
Run: `pytest tests/test_peer_desktop.py -q`
Expected: PASS.

Step 5: Commit
`git commit -m "feat: convert peer desktop control to session manager"`

## Task 6: Expand desktop host helper to support snapshot and input

Objective: make the remote helper usable for computer-use style observe/act even before the Rust sidecar lands.

Files:
- Modify: `burrow/desktop.py`
- Test: `tests/test_desktop.py`

Step 1: Write failing tests
- Add tests for helper subcommands / functions:
  - `snapshot`
  - `input`
  - `list_sessions`
- Add tests for command selection priority:
  - X11 screenshot tools
  - Wayland screenshot tools
  - X11 input tools (`xdotool`)
  - Wayland input tools (`ydotool`, `wtype`)

Step 2: Run test to verify failure
Run: `pytest tests/test_desktop.py -q`
Expected: FAIL because helper lacks those functions.

Step 3: Write minimal implementation
- Add subcommands:
  - `snapshot --session-id <id>`
  - `input --session-id <id> --action-json <json>`
  - `list-sessions`
- Prefer host commands already present on system; return explicit capability errors if unavailable.
- Output JSON only.

Step 4: Run tests to verify pass
Run: `pytest tests/test_desktop.py -q`
Expected: PASS.

Step 5: Commit
`git commit -m "feat: add computer-use snapshot and input to desktop helper"`

## Task 7: Add MCP desktop/computer-use tools

Objective: let agents use the same session through first-class tools.

Files:
- Modify: `burrow/mcp_server.py`
- Create: `tests/test_mcp_desktop.py`

Step 1: Write failing tests
- Add tests for MCP-layer wrappers:
  - `burrow_desktop_open`
  - `burrow_desktop_list`
  - `burrow_desktop_snapshot`
  - `burrow_desktop_input`
  - `burrow_desktop_close`
  - keep `burrow_desktop_capabilities`

Step 2: Run test to verify failure
Run: `pytest tests/test_mcp_desktop.py -q`
Expected: FAIL because tools don’t exist.

Step 3: Write minimal implementation
- Replace narrow connect/stop-only UX with session tools.
- Input payload should be JSON so future UI/agents use the same contract.

Step 4: Run tests to verify pass
Run: `pytest tests/test_mcp_desktop.py -q`
Expected: PASS.

Step 5: Commit
`git commit -m "feat: add MCP computer-use desktop tools"`

## Task 8: Add CLI parity for manual use

Objective: let a human use the same control flow without a graphical connect UI.

Files:
- Modify: `burrow/cli.py`

Step 1: Add failing tests if CLI tests exist; otherwise document manual verification.

Step 2: Implement commands
- `/desktop-open <peer> [backend]`
- `/desktop-list`
- `/desktop-snap <peer> <session_id>`
- `/desktop-click <peer> <session_id> x y`
- `/desktop-type <peer> <session_id> text`
- `/desktop-key <peer> <session_id> key`
- `/desktop-close <peer> <session_id>`

Step 3: Manual verification
- Open session
- Request snapshot
- Send click
- Send key
- Close session

Step 4: Commit
`git commit -m "feat: add CLI computer-use desktop commands"`

## Task 9: Document phase-1 scope honestly

Objective: prevent README from overstating parity while making the path explicit.

Files:
- Modify: `README.md`
- Modify: `docs/plans/2026-04-15-rustdesk-parity-desktop.md`

Step 1: Add README section
- Explain:
  - Burrow is the control/rendezvous/relay plane
  - current backend helper is temporary
  - RustDesk code reuse lands in the native sidecar phase
  - humans and agents share the same session model

Step 2: Verification
- Read the rendered section and ensure there are no false claims of full parity yet.

Step 3: Commit
`git commit -m "docs: describe desktop parity roadmap and current scope"`

---

# Phase 2 plan — RustDesk native host sidecar

## Files to add
- `native/burrow-rd-host/Cargo.toml`
- `native/burrow-rd-host/src/main.rs`
- `native/burrow-rd-host/src/capture.rs`
- `native/burrow-rd-host/src/input.rs`
- `native/burrow-rd-host/src/clipboard.rs`
- `native/burrow-rd-host/src/ipc.rs`
- `burrow/desktop_bridge.py`
- `tests/test_desktop_bridge.py`

## Goals
- Wrap/reuse RustDesk code where it matters:
  - `libs/scrap`
  - `libs/enigo`
  - clipboard stack
- Provide simple IPC commands:
  - `capabilities`
  - `open_session`
  - `snapshot`
  - `frame_stream_start`
  - `input`
  - `clipboard_get/set`
  - `close_session`

## Verification
- Unit tests for bridge IPC framing
- Manual smoke tests on Linux first
- Keep fallback to Python helper when native sidecar unavailable

---

# Phase 3 plan — richer parity

Files likely to modify:
- `burrow/protocol.py`
- `burrow/server.py`
- `burrow/peer.py`
- `burrow/mcp_server.py`
- `tests/test_server.py`
- `tests/test_peer_desktop.py`

Deliver:
- continuous frames
- multi-monitor enumeration
- clipboard sync
- session permissions/consent
- reconnect/resubscribe
- optional audio transport after video/input stability

---

# Phase 4 plan — stretch parity items

Deliver later only after prior phases are stable:
- audio parity
- privacy mode / platform-specific elevated desktop handling
- file clipboard / drag-drop
- direct-path optimization / NAT traversal
- per-window or seamless app mode
- recording/auditing

---

# Verification checklist for the whole program

- [ ] Burrow has first-class desktop session protocol
- [ ] Human CLI and agent MCP tools use the same session/action model
- [ ] Snapshot + input work before native streaming lands
- [ ] RustDesk code is reused via sidecar rather than blindly ported into Python
- [ ] README is honest about phase and capability level
- [ ] Full test suite passes
- [ ] Desktop-specific tests cover protocol, peer, helper, server, and MCP layers

---

# Immediate next execution slice

If executing now, do this next:
1. add protocol message builders/tests
2. add `desktop_session.py`
3. add `computer_use.py`
4. refactor `peer.py` desktop API around sessions
5. extend `desktop.py` with snapshot/input subcommands
6. add MCP desktop session tools

That is the smallest honest path toward shared human/agent “computer use” while preserving room for real RustDesk-code reuse in phase 2.
