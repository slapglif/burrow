# RustDesk-Parity Remote Desktop Program Plan

> For Hermes: use subagent-driven-development. Execute by concurrent track, with strict file ownership per track to avoid merge conflicts. Treat the current Python helper stack as transitional. Do not claim parity until the native host sidecar is wired and end-to-end validated.

Goal: deliver Burrow-managed remote desktop and computer-use with RustDesk-class capability for both human and agent use, reusing RustDesk code where it is strongest while keeping Burrow as the rendezvous, relay, identity, policy, and orchestration plane.

Architecture: Burrow owns session lifecycle, peer identity, policy, routing, and agent-facing tools. A Burrow-managed native Rust host sidecar wraps RustDesk capture/input/clipboard components. Human users and agents share one session model: open session, inspect capabilities, observe frames/snapshots, send normalized input actions, sync clipboard, and close/recover sessions.

Tech Stack: Python 3.11+ Burrow core, Rust sidecar crate(s), reused RustDesk modules (`libs/scrap`, `libs/enigo`, `libs/clipboard`, selected host-service logic), pytest, cargo test, MCP tools, CLI.

---

## Current state snapshot

Already landed or in-flight:
- desktop protocol constants/builders in `burrow/protocol.py`
- desktop relay wiring in `burrow/server.py`
- desktop session scaffolding in `burrow/peer.py`
- transitional desktop helper in `burrow/desktop.py`
- desktop/computer-use models in:
  - `burrow/desktop_session.py`
  - `burrow/computer_use.py`
- tests for protocol/server/desktop helper/session model/computer-use
- architecture notes in:
  - `docs/plans/2026-04-15-rustdesk-parity-desktop.md`

Not done:
- first-class session APIs exposed consistently in peer + MCP + CLI
- snapshot/input observe-act flow wired through Burrow protocol
- native Rust sidecar scaffold
- RustDesk code vendoring/wrapping
- clipboard sync
- continuous frame streaming
- multi-monitor, permissions, reconnect/recovery, audio, privacy mode

---

## Program objective definition

“Parity” for this program means:
1. Human can connect/control/view desktop through Burrow without RustDesk connect UI.
2. Agent can use the same desktop through a computer-use workflow.
3. Burrow registry can serve rendezvous/relay role.
4. RustDesk host/runtime code is reused where sensible instead of reinvented.
5. Feature set approaches RustDesk for the remote desktop/control slice:
   - capture/view
   - keyboard/mouse control
   - clipboard
   - session recovery
   - multiple displays
   - permissions/control modes
   - later audio

Non-goals for now:
- RustDesk’s full user-facing connect UI
- RustDesk account/address-book ecosystem
- exact network/protobuf compatibility with RustDesk hbbs/hbbr

Current truthfulness checkpoint after Track R3:
- Python prefers the native sidecar path when it is installed and responds to capability probes.
- MCP/CLI can now surface native capability state, enumerated displays, and clipboard truthfulness metadata.
- Clipboard actions exposed from Python are still action-oriented only; they do not imply control-plane clipboard read/write unless the sidecar/backend eventually reports that explicitly.
- Session recovery is limited to bounded cleanup/reset/stale-session handling in Python; full native reconnect continuity remains a later parity item.

---

## Dependency graph

### Foundation already available
- `burrow/protocol.py`
- `burrow/server.py`
- `burrow/peer.py`
- `burrow/desktop.py`
- `burrow/desktop_session.py`
- `burrow/computer_use.py`

### Required dependency ordering
1. Track A: session/control-plane completion
   -> prerequisite for everything user-visible
2. Track B: computer-use tool surface
   -> depends on Track A interfaces stabilizing, but helper-side pieces can start now
3. Track C: native sidecar scaffold
   -> can start now in parallel if it avoids touching Track A/B files
4. Track D: bridge integration between Python and sidecar
   -> depends on Track C scaffold and Track A session model
5. Track E: clipboard + multi-display + recovery
   -> depends on A + C + D
6. Track F: continuous streaming/perf/audio
   -> depends on D and partly E

---

## Known blockers and risks

### External blockers
- RustDesk shared protocol internals live in `hbb_common`, which may need submodule/vendor handling.
- Native sidecar build/distribution story for Burrow is not established yet.
- Wayland input/capture parity may require elevated/device-specific handling.

### Internal blockers
- Avoid parallel edits to the same Python files across tracks.
- Current desktop helper is transitional and should not accumulate too much permanent logic.
- Need an honest compatibility layer before claiming parity.

### Practical concurrency rule
- No two concurrent tracks may modify the same file.
- If a track discovers it needs a file owned by another active track, it must stop and document the dependency rather than push conflicting edits.

---

## Execution tracks

## Track A — Session and protocol completion

Objective: make Burrow desktop a first-class session system rather than a launcher shim.

Own these files:
- `burrow/peer.py`
- `burrow/protocol.py`
- `burrow/server.py`
- `burrow/desktop_session.py`
- `tests/test_peer_desktop.py`
- `tests/test_protocol.py`
- `tests/test_server.py`

Subtasks:
1. Add/finish peer session APIs:
   - `open_desktop_session()`
   - `list_desktop_sessions()`
   - `close_desktop_session()`
   - `request_desktop_frame()`
   - `send_desktop_input()`
2. Wire new desktop protocol paths through peer receive loop.
3. Keep backward-compatible shims only if low-cost.
4. Strengthen session metadata ownership and lifecycle.
5. Add rollback/recovery tests.
6. Ensure full test coverage for desktop session messages.

Dependencies:
- none to start

Blockers:
- if snapshot/input semantics require helper changes beyond stubs, coordinate through Track B via documented interface only.

Completion criteria:
- peer-level desktop session flow is test-backed
- no orphan remote session on local tunnel failure
- server relays all needed desktop messages

---

## Track B — Computer-use helper + MCP + CLI surface

Objective: let human and agent use the same observe/act model now, even before the native sidecar lands.

Own these files:
- `burrow/desktop.py`
- `burrow/computer_use.py`
- `burrow/mcp_server.py`
- `burrow/cli.py`
- `tests/test_desktop.py`
- `tests/test_computer_use.py`
- `tests/test_mcp_desktop.py`

Subtasks:
1. Expand helper subcommands:
   - `capabilities`
   - `start`
   - `stop`
   - `snapshot`
   - `input`
   - `list-sessions`
2. Normalize action payloads with `burrow/computer_use.py`.
3. Add MCP tools:
   - `burrow_desktop_open`
   - `burrow_desktop_list`
   - `burrow_desktop_snapshot`
   - `burrow_desktop_input`
   - `burrow_desktop_close`
   - preserve `burrow_desktop_capabilities`
4. Add CLI commands for manual use.
5. Add/expand tests for helper and MCP layer.

Dependencies:
- may start now using current helper/session contract
- should align final naming with Track A before finishing

Blockers:
- if peer API names shift in Track A, adapt at the integration boundary only

Completion criteria:
- agent can open session, request snapshot, send action, close session
- human can do the same from CLI
- tests cover happy path and failure path

---

## Track C — Native Rust sidecar scaffold

Objective: create the in-repo native host sidecar skeleton that will eventually wrap RustDesk code.

Own these files:
- `native/burrow-rd-host/Cargo.toml`
- `native/burrow-rd-host/src/main.rs`
- `native/burrow-rd-host/src/ipc.rs`
- `native/burrow-rd-host/src/capture.rs`
- `native/burrow-rd-host/src/input.rs`
- `native/burrow-rd-host/src/clipboard.rs`
- `native/README.md` or equivalent sidecar docs

Subtasks:
1. Scaffold cargo crate.
2. Define a narrow JSON/stdin-stdout or socket IPC contract:
   - `capabilities`
   - `open_session`
   - `snapshot`
   - `input`
   - `close_session`
3. Stub capture/input/clipboard modules with no-op or mock implementations.
4. Add unit tests or smoke tests in Rust where practical.
5. Document where RustDesk code will be imported/wrapped next.

Dependencies:
- none to start

Blockers:
- may need vendoring strategy decision for RustDesk code, but scaffold can proceed without it

Completion criteria:
- sidecar builds
- exposes IPC contract stub
- ready for Track D integration later

---

## Track D — Python bridge to native sidecar

Objective: replace direct helper logic with a Python bridge once sidecar scaffold exists.

Own these files:
- `burrow/desktop_bridge.py`
- `tests/test_desktop_bridge.py`
- later selected edits in `burrow/desktop.py` after Track B is merged

Dependencies:
- Track C complete
- session interface from Track A sufficiently stable

Current blocker:
- cannot safely start full integration concurrently if it would touch Track B-owned files

Completion criteria:
- Python can invoke sidecar IPC for capabilities/open/snapshot/input/close

---

## Track E — Clipboard, display selection, recovery

Objective: move beyond minimal control into practical parity features.

Own these files later:
- `burrow/peer.py`
- `burrow/mcp_server.py`
- `burrow/cli.py`
- `burrow/desktop_bridge.py`
- related tests

Dependencies:
- Tracks A, C, D

Current blockers:
- no sidecar integration yet

Subtasks later:
1. clipboard get/set/sync
2. display enumeration/select target
3. reconnect/recover desktop sessions
4. readonly/control permission states

---

## Track F — Streaming/performance/audio

Objective: close the gap from snapshot-based control toward RustDesk-class live session quality.

Dependencies:
- Tracks C, D, E

Current blockers:
- native capture path not yet integrated

Subtasks later:
1. continuous frame stream
2. frame subscription API in Burrow
3. transport/QoS/backpressure improvements
4. optional audio capture/playback

---

## Concurrency waves

### Wave 1 — Start immediately in parallel
- Track A
- Track B
- Track C

Rationale:
- minimal file overlap if enforced strictly
- maximizes progress on control-plane, tool-plane, and native scaffold at once

### Wave 2 — Start after Wave 1 partial completion
- Track D once C is merged enough for bridge work
- Track E planning once A/B stable

### Wave 3 — Start after sidecar bridge is real
- Track E implementation
- Track F implementation

---

## Honest closure criteria

The feature is NOT done when:
- only xpra/x11vnc/wayvnc launching works
- only CLI or only MCP works
- agent and human flows differ materially
- native sidecar is absent
- clipboard/recovery/display selection are missing but parity is claimed

The current phase may be honestly called complete when:
- Burrow exposes a stable desktop session model
- human and agent both use the same observe/act flow
- native sidecar scaffold exists and builds
- documentation clearly marks what is done vs deferred

---

## Immediate delegated work package definitions

### Package A1
Implement Track A only. Do not touch helper/MCP/CLI files.

### Package B1
Implement Track B only. Do not touch peer/protocol/server files except through existing public interfaces.

### Package C1
Implement Track C only under `native/`. Do not touch Python runtime files.

---

## Verification plan

Per track:
- run only the relevant focused tests first
- then run `pytest -q` if Python touched
- run `cargo test` or `cargo check` for Rust sidecar when added

Program-level:
- `pytest -q`
- Rust sidecar build/test
- manual CLI smoke
- MCP tool smoke
- documented gaps list

---

## Deliverables expected after Wave 1

1. Updated session/control-plane implementation
2. Working computer-use tool surface for agent + human
3. Native sidecar scaffold committed and buildable
4. Updated docs with blockers/dependencies/gaps

---

## Next controller action

Dispatch Wave 1 concurrently now:
- Track A / session protocol
- Track B / helper + MCP + CLI
- Track C / native sidecar scaffold
