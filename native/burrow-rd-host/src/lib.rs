pub mod capture;
pub mod clipboard;
pub mod input;
pub mod ipc;

use crate::capture::CaptureService;
use crate::clipboard::ClipboardService;
use crate::input::InputService;
use crate::ipc::{
    CapabilitiesResponse, ClipboardOperation, ClipboardRequest, ClipboardResponse,
    CloseSessionResponse, Command, ErrorPayload, InputRequest, InputResponse,
    MIN_COMPAT_PROTOCOL_VERSION, OpenSessionRequest, OpenSessionResponse, PROTOCOL_VERSION,
    PrivacyCapabilities, PrivacyMode, PrivacyRequest, PrivacyResponse, RecoveryCapabilities,
    RequestEnvelope, ResponseEnvelope, ResponsePayload, SessionStatusRequest,
    SessionStatusResponse, SnapshotRequest, SnapshotResponse, StreamOperation, StreamRequest,
    StreamResponse, StreamingCapabilities,
};
use std::collections::HashMap;
use thiserror::Error;

#[derive(Debug, Clone, PartialEq, Eq)]
struct SessionState {
    session_id: String,
    display_id: String,
    width: u32,
    height: u32,
    snapshot_stubbed: bool,
    stream_active: bool,
    stream_format: Option<String>,
    stream_fps: Option<u32>,
}

#[derive(Debug, Error)]
pub enum HostError {
    #[error("invalid request: {0}")]
    InvalidRequest(String),
    #[error("unknown session: {0}")]
    UnknownSession(String),
}

impl HostError {
    fn code(&self) -> &'static str {
        match self {
            Self::InvalidRequest(_) => "invalid_request",
            Self::UnknownSession(_) => "unknown_session",
        }
    }
}

#[derive(Debug)]
pub struct BurrowRdHost {
    capture: CaptureService,
    input: InputService,
    clipboard: ClipboardService,
    sessions: HashMap<String, SessionState>,
    next_session_id: u64,
}

impl Default for BurrowRdHost {
    fn default() -> Self {
        Self {
            capture: CaptureService::new(),
            input: InputService::new(),
            clipboard: ClipboardService::new(),
            sessions: HashMap::new(),
            next_session_id: 1,
        }
    }
}

impl BurrowRdHost {
    pub fn new() -> Self {
        Self::default()
    }

    pub fn handle_request(&mut self, request: RequestEnvelope) -> ResponseEnvelope {
        let id = request.id.clone();
        match self.execute(request.command) {
            Ok(result) => ResponseEnvelope {
                id,
                protocol_version: PROTOCOL_VERSION,
                min_compatible_protocol_version: MIN_COMPAT_PROTOCOL_VERSION,
                ok: true,
                result: Some(result),
                error: None,
                warnings: Vec::new(),
            },
            Err(error) => ResponseEnvelope {
                id,
                protocol_version: PROTOCOL_VERSION,
                min_compatible_protocol_version: MIN_COMPAT_PROTOCOL_VERSION,
                ok: false,
                result: None,
                error: Some(ErrorPayload {
                    code: error.code().to_string(),
                    message: error.to_string(),
                }),
                warnings: Vec::new(),
            },
        }
    }

    fn execute(&mut self, command: Command) -> Result<ResponsePayload, HostError> {
        match command {
            Command::Capabilities => Ok(ResponsePayload::Capabilities(self.capabilities())),
            Command::OpenSession(request) => {
                self.open_session(request).map(ResponsePayload::OpenSession)
            }
            Command::SessionStatus(request) => self
                .session_status(request)
                .map(ResponsePayload::SessionStatus),
            Command::Snapshot(request) => self.snapshot(request).map(ResponsePayload::Snapshot),
            Command::Input(request) => self.input(request).map(ResponsePayload::Input),
            Command::Clipboard(request) => self.clipboard(request).map(ResponsePayload::Clipboard),
            Command::Stream(request) => self.stream(request).map(ResponsePayload::Stream),
            Command::Privacy(request) => self.privacy(request).map(ResponsePayload::Privacy),
            Command::CloseSession(request) => self
                .close_session(request.session_id)
                .map(ResponsePayload::CloseSession),
        }
    }

    fn capabilities(&self) -> CapabilitiesResponse {
        let inventory = self.capture.inventory();
        let snapshot_formats = if inventory.capture_support.real_capture {
            vec!["image/png;base64".to_string()]
        } else {
            vec!["application/octet-stream;base64".to_string()]
        };

        let mut notes = vec![inventory.discovery_note, inventory.capture_support.note];
        if inventory.real_displays {
            notes.push(
                "Display ids, primary markers, bounds, and origin offsets come from live xrandr enumeration when available"
                    .to_string(),
            );
        } else {
            notes.push(
                "No live display inventory backend was detected, so display metadata and capture both fall back to stubs"
                    .to_string(),
            );
        }
        notes.push(
            "Input and clipboard remain separate capability-gated tracks; this sidecar does not claim RustDesk parity yet"
                .to_string(),
        );
        notes.push(
            "Prep-0 freezes placeholder command surfaces for clipboard, streaming, privacy, and session recovery so later tracks can implement them without reshaping stdio JSONL envelopes again"
                .to_string(),
        );

        CapabilitiesResponse {
            protocol_version: PROTOCOL_VERSION,
            min_compatible_protocol_version: MIN_COMPAT_PROTOCOL_VERSION,
            transport: "jsonl-stdio".to_string(),
            session_scoped: true,
            displays: inventory.displays,
            clipboard: self.clipboard.capabilities(),
            supported_commands: supported_commands(),
            input_actions: vec![
                "key_press".to_string(),
                "key_release".to_string(),
                "text".to_string(),
                "mouse_move".to_string(),
                "mouse_button".to_string(),
                "scroll".to_string(),
            ],
            snapshot_formats,
            recovery: RecoveryCapabilities {
                session_status_command: true,
                survives_sidecar_restart: false,
                stale_session_error_codes: vec!["unknown_session".to_string()],
                note: "Sessions are ephemeral to the running sidecar process. Track N1 callers should treat unknown_session as a stale-session recovery signal and reopen."
                    .to_string(),
            },
            streaming: StreamingCapabilities {
                available: true,
                configurable: true,
                active_default: false,
                formats: vec!["image/png;base64".to_string()],
                note: "streaming is available via Rust snapshot + polling loop. Start sets defaults for format/fps; Poll returns a fresh frame while active."
                    .to_string(),
            },
            privacy: PrivacyCapabilities {
                available: false,
                mode: "unsupported".to_string(),
                requires_consent: false,
                note: "Prep-0 reserves a privacy command surface only. No privacy curtain or blanking behavior is implemented yet."
                    .to_string(),
            },
            notes,
        }
    }

    fn open_session(
        &mut self,
        request: OpenSessionRequest,
    ) -> Result<OpenSessionResponse, HostError> {
        let displays = self.capture.displays();
        let display = if let Some(display_id) = request.display_id.as_deref() {
            displays
                .into_iter()
                .find(|candidate| candidate.id == display_id)
                .ok_or_else(|| {
                    HostError::InvalidRequest(format!("display not found: {display_id}"))
                })?
        } else {
            displays
                .into_iter()
                .find(|candidate| candidate.primary)
                .ok_or_else(|| {
                    HostError::InvalidRequest("no primary display available".to_string())
                })?
        };
        let capture_ready = self.capture.can_capture_display(&display);

        let session_id = format!("session-{}", self.next_session_id);
        self.next_session_id += 1;

        let state = SessionState {
            session_id: session_id.clone(),
            display_id: display.id.clone(),
            width: display.width,
            height: display.height,
            snapshot_stubbed: !capture_ready,
            stream_active: false,
            stream_format: None,
            stream_fps: None,
        };
        self.sessions.insert(session_id.clone(), state);

        Ok(OpenSessionResponse {
            session_id,
            display_id: display.id,
            width: display.width,
            height: display.height,
            stubbed: !capture_ready,
            available_commands: session_commands(),
            recovery_hint: recovery_hint().to_string(),
        })
    }

    fn session_status(
        &self,
        request: SessionStatusRequest,
    ) -> Result<SessionStatusResponse, HostError> {
        let session = self
            .sessions
            .get(&request.session_id)
            .ok_or_else(|| HostError::UnknownSession(request.session_id.clone()))?;

        Ok(SessionStatusResponse {
            session_id: session.session_id.clone(),
            display_id: session.display_id.clone(),
            width: session.width,
            height: session.height,
            snapshot_stubbed: session.snapshot_stubbed,
            active: true,
            available_commands: session_commands(),
            recovery_hint: recovery_hint().to_string(),
        })
    }

    fn snapshot(&mut self, request: SnapshotRequest) -> Result<SnapshotResponse, HostError> {
        let session = self
            .sessions
            .get(&request.session_id)
            .cloned()
            .ok_or_else(|| HostError::UnknownSession(request.session_id.clone()))?;

        let frame = self.capture.snapshot(Some(&session.display_id));
        Ok(SnapshotResponse {
            session_id: session.session_id,
            frame,
        })
    }

    fn input(&mut self, request: InputRequest) -> Result<InputResponse, HostError> {
        self.sessions
            .get(&request.session_id)
            .ok_or_else(|| HostError::UnknownSession(request.session_id.clone()))?;
        let ack = self.input.apply(request.action);
        Ok(InputResponse {
            session_id: request.session_id,
            accepted: ack.accepted,
            stubbed: ack.stubbed,
            note: ack.note,
        })
    }

    fn clipboard(&self, request: ClipboardRequest) -> Result<ClipboardResponse, HostError> {
        self.sessions
            .get(&request.session_id)
            .ok_or_else(|| HostError::UnknownSession(request.session_id.clone()))?;

        Ok(ClipboardResponse {
            session_id: request.session_id,
            operation: clipboard_operation_name(&request.operation).to_string(),
            supported: false,
            stubbed: true,
            note: "clipboard command is reserved for future native work. this sidecar tracks it as a no-op for now"
                .to_string(),
            text: None,
        })
    }

    fn stream(&mut self, request: StreamRequest) -> Result<StreamResponse, HostError> {
        let session = self
            .sessions
            .get_mut(&request.session_id)
            .ok_or_else(|| HostError::UnknownSession(request.session_id.clone()))?;

        match request.operation {
            StreamOperation::Start { format, target_fps } => {
                let requested_format = format.unwrap_or_else(|| "image/png;base64".to_string());
                let supported_formats = if self.capture.inventory().capture_support.real_capture {
                    vec!["image/png;base64".to_string()]
                } else {
                    vec!["application/octet-stream;base64".to_string()]
                };
                let is_supported = supported_formats
                    .iter()
                    .any(|value| value == &requested_format);

                if !is_supported {
                    return Ok(StreamResponse {
                        session_id: request.session_id,
                        operation: stream_operation_name(&StreamOperation::Start {
                            format: Some(requested_format.clone()),
                            target_fps,
                        })
                        .to_string(),
                        accepted: false,
                        active: false,
                        stubbed: false,
                        note: format!(
                            "snapshot polling handles delivery for unsupported stream formats for now. requested='{requested_format}', supported: {}",
                            supported_formats.join(", ")
                        ),
                    });
                }

                let fps = target_fps.unwrap_or(15).max(1);
                session.stream_active = true;
                session.stream_format = Some(requested_format.clone());
                session.stream_fps = Some(fps);

                Ok(StreamResponse {
                    session_id: request.session_id,
                    operation: stream_operation_name(&StreamOperation::Start {
                        format: Some(requested_format),
                        target_fps: Some(fps),
                    })
                    .to_string(),
                    accepted: true,
                    active: true,
                    stubbed: false,
                    note: format!("stream started at {fps} fps"),
                })
            }
            StreamOperation::Stop => {
                session.stream_active = false;
                session.stream_format = None;
                session.stream_fps = None;

                Ok(StreamResponse {
                    session_id: request.session_id,
                    operation: stream_operation_name(&StreamOperation::Stop).to_string(),
                    accepted: true,
                    active: false,
                    stubbed: false,
                    note: "stream stopped".to_string(),
                })
            }
            StreamOperation::Poll => {
                if !session.stream_active {
                    return Ok(StreamResponse {
                        session_id: request.session_id,
                        operation: stream_operation_name(&StreamOperation::Poll).to_string(),
                        accepted: false,
                        active: false,
                        stubbed: false,
                        note: "stream is not active. start stream first".to_string(),
                    });
                }

                let frame = self.capture.snapshot(Some(&session.display_id));
                let stubbed = frame.stubbed;

                Ok(StreamResponse {
                    session_id: request.session_id,
                    operation: stream_operation_name(&StreamOperation::Poll).to_string(),
                    accepted: true,
                    active: true,
                    stubbed,
                    note: format!(
                        "poll snapshot {}x{} mime_type={} sequence={}",
                        frame.width, frame.height, frame.mime_type, frame.sequence
                    ),
                })
            }
        }
    }

    fn privacy(&self, request: PrivacyRequest) -> Result<PrivacyResponse, HostError> {
        self.sessions
            .get(&request.session_id)
            .ok_or_else(|| HostError::UnknownSession(request.session_id.clone()))?;

        Ok(PrivacyResponse {
            session_id: request.session_id,
            mode: privacy_mode_name(&request.mode).to_string(),
            applied: false,
            stubbed: false,
            note: "privacy command reserved by Prep-0 only; the sidecar does not blank displays, block local input, or claim privacy-mode support yet"
                .to_string(),
        })
    }

    fn close_session(&mut self, session_id: String) -> Result<CloseSessionResponse, HostError> {
        let removed = self.sessions.remove(&session_id);
        if removed.is_none() {
            return Err(HostError::UnknownSession(session_id));
        }

        Ok(CloseSessionResponse {
            session_id,
            closed: true,
        })
    }
}

fn supported_commands() -> Vec<String> {
    vec![
        "capabilities".to_string(),
        "open_session".to_string(),
        "session_status".to_string(),
        "snapshot".to_string(),
        "input".to_string(),
        "clipboard".to_string(),
        "stream".to_string(),
        "privacy".to_string(),
        "close_session".to_string(),
    ]
}

fn session_commands() -> Vec<String> {
    vec![
        "session_status".to_string(),
        "snapshot".to_string(),
        "input".to_string(),
        "clipboard".to_string(),
        "stream".to_string(),
        "privacy".to_string(),
        "close_session".to_string(),
    ]
}

fn recovery_hint() -> &'static str {
    "Sessions survive only inside the current sidecar process. If a later request returns unknown_session, reset bridge state and reopen a new native session."
}

fn clipboard_operation_name(operation: &ClipboardOperation) -> &'static str {
    match operation {
        ClipboardOperation::ReadText => "read_text",
        ClipboardOperation::WriteText { .. } => "write_text",
        ClipboardOperation::Sync => "sync",
        ClipboardOperation::Paste => "paste",
        ClipboardOperation::Copy => "copy",
        ClipboardOperation::Cut => "cut",
        ClipboardOperation::Clear => "clear",
    }
}

fn stream_operation_name(operation: &StreamOperation) -> &'static str {
    match operation {
        StreamOperation::Start { .. } => "start",
        StreamOperation::Stop => "stop",
        StreamOperation::Poll => "poll",
    }
}

fn privacy_mode_name(mode: &PrivacyMode) -> &'static str {
    match mode {
        PrivacyMode::Query => "query",
        PrivacyMode::Set { enabled: true, .. } => "enable",
        PrivacyMode::Set { enabled: false, .. } => "disable",
    }
}

#[cfg(test)]
mod tests {
    use super::BurrowRdHost;
    use crate::input::InputAction;
    use crate::ipc::{
        ClipboardOperation, ClipboardRequest, CloseSessionRequest, Command, InputRequest,
        OpenSessionRequest, PrivacyMode, PrivacyRequest, RequestEnvelope, ResponsePayload,
        SessionStatusRequest, SnapshotRequest, StreamOperation, StreamRequest,
    };

    #[test]
    fn host_smoke_flow_open_snapshot_input_close() {
        let mut host = BurrowRdHost::new();

        let open = host.handle_request(RequestEnvelope {
            id: Some("1".to_string()),
            protocol_version: None,
            client: None,
            command: Command::OpenSession(OpenSessionRequest { display_id: None }),
        });
        assert!(open.ok);
        let session_id = match open.result.expect("open result") {
            ResponsePayload::OpenSession(payload) => payload.session_id,
            other => panic!("unexpected payload: {other:?}"),
        };

        let snapshot = host.handle_request(RequestEnvelope {
            id: Some("2".to_string()),
            protocol_version: None,
            client: None,
            command: Command::Snapshot(SnapshotRequest {
                session_id: session_id.clone(),
            }),
        });
        assert!(snapshot.ok);

        let input = host.handle_request(RequestEnvelope {
            id: Some("3".to_string()),
            protocol_version: None,
            client: None,
            command: Command::Input(InputRequest {
                session_id: session_id.clone(),
                action: InputAction::MouseMove { x: 100, y: 200 },
            }),
        });
        assert!(input.ok);

        let close = host.handle_request(RequestEnvelope {
            id: Some("4".to_string()),
            protocol_version: None,
            client: None,
            command: Command::CloseSession(CloseSessionRequest {
                session_id: session_id.clone(),
            }),
        });
        assert!(close.ok);

        let missing = host.handle_request(RequestEnvelope {
            id: Some("5".to_string()),
            protocol_version: None,
            client: None,
            command: Command::Snapshot(SnapshotRequest { session_id }),
        });
        assert!(!missing.ok);
        let error = missing.error.expect("missing error");
        assert_eq!(error.code, "unknown_session");
    }

    #[test]
    fn capabilities_note_and_formats_match_capture_state() {
        let mut host = BurrowRdHost::new();
        let response = host.handle_request(RequestEnvelope {
            id: Some("caps".to_string()),
            protocol_version: None,
            client: None,
            command: Command::Capabilities,
        });
        assert!(response.ok);

        let payload = match response.result.expect("capabilities payload") {
            ResponsePayload::Capabilities(payload) => payload,
            other => panic!("unexpected payload: {other:?}"),
        };

        assert_eq!(payload.protocol_version, 2);
        assert_eq!(payload.min_compatible_protocol_version, 1);
        assert!(
            payload
                .supported_commands
                .iter()
                .any(|value| value == "session_status")
        );
        assert!(!payload.snapshot_formats.is_empty());
        assert!(!payload.notes.is_empty());
        if payload
            .snapshot_formats
            .iter()
            .any(|value| value == "image/png;base64")
        {
            assert!(
                payload
                    .notes
                    .iter()
                    .any(|note| note.contains("real Linux capture enabled via"))
            );
        } else {
            assert!(
                payload
                    .notes
                    .iter()
                    .any(|note| note.contains("capture fallback only"))
            );
        }
    }

    #[test]
    fn session_status_reports_recovery_hint_and_reserved_commands() {
        let mut host = BurrowRdHost::new();
        let open = host.handle_request(RequestEnvelope {
            id: Some("open".to_string()),
            protocol_version: Some(2),
            client: None,
            command: Command::OpenSession(OpenSessionRequest { display_id: None }),
        });
        let session_id = match open.result.expect("open payload") {
            ResponsePayload::OpenSession(payload) => payload.session_id,
            other => panic!("unexpected payload: {other:?}"),
        };

        let status = host.handle_request(RequestEnvelope {
            id: Some("status".to_string()),
            protocol_version: Some(2),
            client: None,
            command: Command::SessionStatus(SessionStatusRequest {
                session_id: session_id.clone(),
            }),
        });

        assert!(status.ok);
        let payload = match status.result.expect("status payload") {
            ResponsePayload::SessionStatus(payload) => payload,
            other => panic!("unexpected payload: {other:?}"),
        };
        assert_eq!(payload.session_id, session_id);
        assert!(
            payload
                .available_commands
                .iter()
                .any(|value| value == "clipboard")
        );
        assert!(
            payload
                .available_commands
                .iter()
                .any(|value| value == "stream")
        );
        assert!(payload.recovery_hint.contains("unknown_session"));
    }

    #[test]
    fn reserved_clipboard_stream_and_privacy_commands_fail_honestly() {
        let mut host = BurrowRdHost::new();
        let open = host.handle_request(RequestEnvelope {
            id: Some("open".to_string()),
            protocol_version: Some(2),
            client: None,
            command: Command::OpenSession(OpenSessionRequest { display_id: None }),
        });
        let session_id = match open.result.expect("open payload") {
            ResponsePayload::OpenSession(payload) => payload.session_id,
            other => panic!("unexpected payload: {other:?}"),
        };

        let clipboard = host.handle_request(RequestEnvelope {
            id: Some("clip".to_string()),
            protocol_version: Some(2),
            client: None,
            command: Command::Clipboard(ClipboardRequest {
                session_id: session_id.clone(),
                operation: ClipboardOperation::WriteText {
                    text: "hello".to_string(),
                },
            }),
        });
        assert!(clipboard.ok);
        match clipboard.result.expect("clipboard payload") {
            ResponsePayload::Clipboard(payload) => {
                assert!(!payload.supported);
                assert!(payload.note.contains("reserved for future native work"));
            }
            other => panic!("unexpected payload: {other:?}"),
        }

        let stream = host.handle_request(RequestEnvelope {
            id: Some("stream".to_string()),
            protocol_version: Some(2),
            client: None,
            command: Command::Stream(StreamRequest {
                session_id: session_id.clone(),
                operation: StreamOperation::Start {
                    format: Some("image/png".to_string()),
                    target_fps: Some(15),
                },
            }),
        });
        assert!(stream.ok);
        match stream.result.expect("stream payload") {
            ResponsePayload::Stream(payload) => {
                assert!(!payload.accepted);
                assert!(payload.note.contains("snapshot polling"));
            }
            other => panic!("unexpected payload: {other:?}"),
        }

        let privacy = host.handle_request(RequestEnvelope {
            id: Some("privacy".to_string()),
            protocol_version: Some(2),
            client: None,
            command: Command::Privacy(PrivacyRequest {
                session_id,
                mode: PrivacyMode::Set {
                    enabled: true,
                    reason: Some("test".to_string()),
                },
            }),
        });
        assert!(privacy.ok);
        match privacy.result.expect("privacy payload") {
            ResponsePayload::Privacy(payload) => {
                assert!(!payload.applied);
                assert!(payload.note.contains("does not blank displays"));
            }
            other => panic!("unexpected payload: {other:?}"),
        }
    }
}
