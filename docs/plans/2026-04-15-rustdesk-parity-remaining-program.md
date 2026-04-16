# RustDesk-Parity Remote Desktop Remaining-Work Program

> For Hermes: execute as a deep program session. Delegate by concurrent track with strict file ownership. Do not claim parity until real native capture/input/clipboard are wired through Burrow and browser/CLI/MCP flows are validated end-to-end.

Goal: finish the remaining Burrow work needed to move from scaffold/prototype status to honest near-parity for the RustDesk-style remote desktop + computer-use slice, using RustDesk code/concepts where practical and Burrow as the control/rendezvous/relay plane.

Architecture: Burrow remains the orchestration/control layer. `native/burrow-rd-host` becomes the real host runtime boundary for display enumeration, capture, input, and clipboard. `burrow/desktop_bridge.py` and `burrow/desktop.py` become the Python/native boundary. `burrow/peer.py`, `burrow/mcp_server.py`, and `burrow/cli.py` expose one shared session model for humans and agents.

Tech stack: Python 3.11+, Rust sidecar, pytest, cargo test, stdio JSONL IPC, RustDesk-inspired capture/input/clipboard reuse, Burrow MCP + CLI.

---

## Current verified baseline

Verified now:
- `pytest -q` -> 364 passed
- `cargo test` in `native/` -> 8 passed

Already present:
- session/protocol/server groundwork
- temporary helper plus MCP/CLI affordances
- Python/native bridge scaffold
- native sidecar scaffold with real Linux display enumeration via xrandr

Still not done:
- real frame capture
- real input injection
- real clipboard behavior
- end-to-end native session flow as the default path
- session recovery/reconnect semantics
- multi-display targeting fully wired through runtime and UX
- honest integration docs for what is real vs stubbed

---

## Remaining objective definition

The remaining work is complete only when all of the following are true:
1. A native Burrow desktop session can open via the sidecar and expose real display inventory.
2. Snapshot/frame retrieval uses actual screen capture for at least one supported Linux path.
3. Input actions execute real native actions for at least one supported Linux path.
4. Clipboard actions have honest implemented behavior or explicit unsupported responses.
5. Burrow peer/MCP/CLI all route through the same native-capable session model.
6. Recovery/error handling is explicit and tested.
7. Docs precisely state supported platforms, supported paths, and remaining blockers.

---

## Remaining dependency graph

### Completed prerequisites
- Protocol/session scaffolding
- MCP/CLI basic desktop surface
- Sidecar bridge scaffold
- Native display discovery scaffold

### Remaining ordered dependencies
1. Track R1: native real capture path
   -> prerequisite for honest snapshot/frame behavior
2. Track R2: native real input + clipboard path
   -> prerequisite for honest control/clipboard behavior
3. Track R3: Python/runtime integration + recovery + UX polish
   -> depends on stable/native responses from R1/R2, but interface preparation can begin now
4. Track R4: final end-to-end verification and truthfulness pass
   -> depends on R1-R3

---

## Remaining blockers and risks

### External blockers
- Wayland capture/input may require compositor-specific tools/permissions.
- Direct RustDesk vendoring may still require decisions around `hbb_common` and code extraction boundaries.
- Host system tool availability may differ across peers.

### Internal blockers
- Avoid overlapping edits across Python control/runtime files.
- Avoid inventing pseudo-parity if native path remains stubbed.
- Keep the bridge protocol stable enough while native internals evolve.

### Concurrency rule
- No two tracks may edit the same file.
- If a track needs a file owned by another active track, document the blocker and stop rather than conflict.

---

## Concurrent remaining tracks

## Track R1 — Native real capture and display runtime

Objective: replace stub snapshot behavior with a real Linux capture path where feasible now, while improving multi-display/runtime metadata.

Own these files only:
- `native/burrow-rd-host/Cargo.toml`
- `native/burrow-rd-host/src/capture.rs`
- `native/burrow-rd-host/src/lib.rs`
- `native/README.md`

Subtasks:
1. Add a real capture implementation path for Linux when tools/backends are available.
   - Prefer bounded, honest implementation.
   - Accept fallback to external command capture if direct RustDesk/scrap vendoring is still too large for this slice.
2. Preserve current deterministic stub as fallback only.
3. Ensure session open/snapshot return real display dimensions and real image bytes when capture succeeds.
4. Improve display metadata if needed:
   - ids
   - primary marker
   - bounds/origin
   - backend source
5. Update native docs to distinguish:
   - real capture path
   - fallback/stub path
6. Add/update Rust tests.

Dependencies:
- none to start

Blockers:
- if no safe capture backend is available in-repo within scope, return explicit runtime capability flags instead of pretending.

Completion criteria:
- snapshot path can return a real image payload on supported Linux setups
- display metadata is truthful and tested
- cargo test passes

---

## Track R2 — Native real input and clipboard runtime

Objective: replace stubbed input/clipboard behavior with bounded real behavior where feasible now.

Own these files only:
- `native/burrow-rd-host/src/input.rs`
- `native/burrow-rd-host/src/clipboard.rs`
- `native/burrow-rd-host/src/ipc.rs`
- `native/burrow-rd-host/src/main.rs`
- `native/README.md`

Subtasks:
1. Implement real Linux input execution path where feasible now.
   - bounded set first: mouse move/click, key press, type text
   - preserve explicit unsupported errors for the rest
2. Implement honest clipboard operations.
   - start with write/paste or read/write if feasible
   - if true sync is not feasible, expose exact supported operations only
3. Update IPC contract if needed for capability flags and operation results.
4. Add/update Rust tests for real or capability-gated behavior.
5. Update native docs to mark what is implemented vs unsupported.

Dependencies:
- none to start

Blockers:
- if real input/clipboard require host tools not guaranteed present, expose capability-gated behavior rather than fake success.

Completion criteria:
- sidecar executes a bounded real input path on supported Linux setups
- clipboard responses are truthful and test-backed
- cargo test passes

---

## Track R3 — Python integration, session recovery, and user-facing truthfulness

Objective: make Burrow prefer the native path end-to-end, expose native capabilities clearly to humans/agents, and add recovery/error handling.

Own these files only:
- `burrow/desktop_bridge.py`
- `burrow/desktop.py`
- `burrow/peer.py`
- `burrow/desktop_session.py`
- `burrow/mcp_server.py`
- `burrow/cli.py`
- `burrow/computer_use.py`
- `README.md`
- `docs/plans/2026-04-15-rustdesk-parity-program.md`
- `tests/test_desktop.py`
- `tests/test_desktop_bridge.py`
- `tests/test_peer_desktop.py`
- `tests/test_mcp_desktop.py`
- `tests/test_computer_use.py`

Subtasks:
1. Prefer native path end-to-end when the sidecar is available.
2. Surface native capability flags to MCP/CLI.
3. Add display enumeration/listing UX where current interfaces allow.
4. Wire clipboard actions through the bridge with honest support/failure messaging.
5. Improve session recovery semantics:
   - bridge reset
   - close cleanup
   - stale session handling
   - better error propagation
6. Tighten action normalization and target/display metadata flow.
7. Update README/program docs with an honest support matrix.
8. Add/update focused Python tests.

Dependencies:
- can start now against the existing sidecar contract
- may need to adapt to new capability fields from R1/R2

Blockers:
- if R1/R2 extend IPC, adapt at boundary rather than rewriting whole flows

Completion criteria:
- MCP and CLI clearly show/display native capability state
- native session path is preferred when available
- recovery and cleanup behavior are test-backed
- docs are truthful
- pytest passes

Track R3 implementation note:
- Native session open no longer assumes a tunneled viewer port; control-plane-only native sessions are valid and should be reported honestly.
- Python should treat `unknown_session` / stale native-session responses as recovery events: clean local metadata, reset the bridge as needed, and tell the caller to reopen.
- Clipboard operations exposed from MCP/CLI remain honest shortcut/text-entry surfaces unless the sidecar explicitly reports richer clipboard support.

---

## Remaining subtasks not yet delegated (Wave R4)

These depend on R1-R3 finishing first:
1. End-to-end truth audit
2. Optional live smoke on this machine
3. Commit/issue reconciliation/docs closeout
4. Next-wave plan for streaming/audio/privacy mode if still out of scope

---

## Concurrency waves from here

### Wave R-now — start immediately in parallel
- Track R1
- Track R2
- Track R3

Rationale:
- R1 and R2 touch disjoint native files
- R3 touches only Python/docs/tests
- all can make bounded forward progress concurrently

### Wave R4 — after current wave lands
- final integration/truth audit
- next-wave planning if streaming/audio still remain

---

## Honest close criteria for this wave

This wave is successful when:
- one bounded real capture path exists or explicit capability gating is added
- one bounded real input path exists or explicit capability gating is added
- clipboard behavior is truthful
- Burrow prefers native path when available
- MCP/CLI/docs correctly describe support
- tests remain green

This wave is NOT successful if:
- stubs are relabeled as real support
- unsupported paths silently return success
- docs imply full RustDesk parity

---

## Immediate delegated packages

### Package R1
Implement Track R1 only. Do not edit Python files or R2-owned native files.

### Package R2
Implement Track R2 only. Do not edit Python files or R1-owned native files.

### Package R3
Implement Track R3 only. Do not edit anything under `native/`.
