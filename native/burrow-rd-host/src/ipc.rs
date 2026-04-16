use crate::capture::DisplayInfo;
use crate::clipboard::ClipboardCapabilities;
use crate::input::InputAction;
use serde::{Deserialize, Serialize};

pub const PROTOCOL_VERSION: u32 = 2;
pub const MIN_COMPAT_PROTOCOL_VERSION: u32 = 1;

#[derive(Debug, Clone, Serialize, Deserialize, PartialEq)]
pub struct RequestEnvelope {
    pub id: Option<String>,
    #[serde(skip_serializing_if = "Option::is_none")]
    pub protocol_version: Option<u32>,
    #[serde(skip_serializing_if = "Option::is_none")]
    pub client: Option<ClientMetadata>,
    #[serde(flatten)]
    pub command: Command,
}

#[derive(Debug, Clone, Serialize, Deserialize, PartialEq, Eq)]
pub struct ClientMetadata {
    pub name: String,
    #[serde(skip_serializing_if = "Option::is_none")]
    pub version: Option<String>,
}

#[derive(Debug, Clone, Serialize, Deserialize, PartialEq)]
#[serde(tag = "command", rename_all = "snake_case")]
pub enum Command {
    Capabilities,
    OpenSession(OpenSessionRequest),
    SessionStatus(SessionStatusRequest),
    Snapshot(SnapshotRequest),
    Input(InputRequest),
    Clipboard(ClipboardRequest),
    Stream(StreamRequest),
    Privacy(PrivacyRequest),
    CloseSession(CloseSessionRequest),
}

#[derive(Debug, Clone, Serialize, Deserialize, PartialEq, Eq)]
pub struct OpenSessionRequest {
    pub display_id: Option<String>,
}

#[derive(Debug, Clone, Serialize, Deserialize, PartialEq, Eq)]
pub struct SnapshotRequest {
    pub session_id: String,
}

#[derive(Debug, Clone, Serialize, Deserialize, PartialEq, Eq)]
pub struct SessionStatusRequest {
    pub session_id: String,
}

#[derive(Debug, Clone, Serialize, Deserialize, PartialEq)]
pub struct InputRequest {
    pub session_id: String,
    pub action: InputAction,
}

#[derive(Debug, Clone, Serialize, Deserialize, PartialEq, Eq)]
pub struct ClipboardRequest {
    pub session_id: String,
    pub operation: ClipboardOperation,
}

#[derive(Debug, Clone, Serialize, Deserialize, PartialEq, Eq)]
#[serde(tag = "type", rename_all = "snake_case")]
pub enum ClipboardOperation {
    ReadText,
    WriteText { text: String },
    Sync,
    Paste,
    Copy,
    Cut,
    Clear,
}

#[derive(Debug, Clone, Serialize, Deserialize, PartialEq, Eq)]
pub struct StreamRequest {
    pub session_id: String,
    pub operation: StreamOperation,
}

#[derive(Debug, Clone, Serialize, Deserialize, PartialEq, Eq)]
#[serde(tag = "type", rename_all = "snake_case")]
pub enum StreamOperation {
    Start {
        #[serde(skip_serializing_if = "Option::is_none")]
        format: Option<String>,
        #[serde(skip_serializing_if = "Option::is_none")]
        target_fps: Option<u32>,
    },
    Stop,
    Poll,
}

#[derive(Debug, Clone, Serialize, Deserialize, PartialEq, Eq)]
pub struct PrivacyRequest {
    pub session_id: String,
    pub mode: PrivacyMode,
}

#[derive(Debug, Clone, Serialize, Deserialize, PartialEq, Eq)]
#[serde(tag = "type", rename_all = "snake_case")]
pub enum PrivacyMode {
    Query,
    Set {
        enabled: bool,
        #[serde(skip_serializing_if = "Option::is_none")]
        reason: Option<String>,
    },
}

#[derive(Debug, Clone, Serialize, Deserialize, PartialEq, Eq)]
pub struct CloseSessionRequest {
    pub session_id: String,
}

#[derive(Debug, Clone, Serialize, Deserialize, PartialEq)]
pub struct ResponseEnvelope {
    pub id: Option<String>,
    pub protocol_version: u32,
    pub min_compatible_protocol_version: u32,
    pub ok: bool,
    #[serde(skip_serializing_if = "Option::is_none")]
    pub result: Option<ResponsePayload>,
    #[serde(skip_serializing_if = "Option::is_none")]
    pub error: Option<ErrorPayload>,
    #[serde(default, skip_serializing_if = "Vec::is_empty")]
    pub warnings: Vec<String>,
}

#[derive(Debug, Clone, Serialize, Deserialize, PartialEq)]
#[serde(tag = "type", rename_all = "snake_case")]
pub enum ResponsePayload {
    Capabilities(CapabilitiesResponse),
    OpenSession(OpenSessionResponse),
    SessionStatus(SessionStatusResponse),
    Snapshot(SnapshotResponse),
    Input(InputResponse),
    Clipboard(ClipboardResponse),
    Stream(StreamResponse),
    Privacy(PrivacyResponse),
    CloseSession(CloseSessionResponse),
}

#[derive(Debug, Clone, Serialize, Deserialize, PartialEq, Eq)]
pub struct ErrorPayload {
    pub code: String,
    pub message: String,
}

#[derive(Debug, Clone, Serialize, Deserialize, PartialEq, Eq)]
pub struct CapabilitiesResponse {
    pub protocol_version: u32,
    pub min_compatible_protocol_version: u32,
    pub transport: String,
    pub session_scoped: bool,
    pub displays: Vec<DisplayInfo>,
    pub clipboard: ClipboardCapabilities,
    pub supported_commands: Vec<String>,
    pub input_actions: Vec<String>,
    pub snapshot_formats: Vec<String>,
    pub recovery: RecoveryCapabilities,
    pub streaming: StreamingCapabilities,
    pub privacy: PrivacyCapabilities,
    pub notes: Vec<String>,
}

#[derive(Debug, Clone, Serialize, Deserialize, PartialEq, Eq)]
pub struct RecoveryCapabilities {
    pub session_status_command: bool,
    pub survives_sidecar_restart: bool,
    pub stale_session_error_codes: Vec<String>,
    pub note: String,
}

#[derive(Debug, Clone, Serialize, Deserialize, PartialEq, Eq)]
pub struct StreamingCapabilities {
    pub available: bool,
    pub configurable: bool,
    pub active_default: bool,
    pub formats: Vec<String>,
    pub note: String,
}

#[derive(Debug, Clone, Serialize, Deserialize, PartialEq, Eq)]
pub struct PrivacyCapabilities {
    pub available: bool,
    pub mode: String,
    pub requires_consent: bool,
    pub note: String,
}

#[derive(Debug, Clone, Serialize, Deserialize, PartialEq, Eq)]
pub struct OpenSessionResponse {
    pub session_id: String,
    pub display_id: String,
    pub width: u32,
    pub height: u32,
    pub stubbed: bool,
    pub available_commands: Vec<String>,
    pub recovery_hint: String,
}

#[derive(Debug, Clone, Serialize, Deserialize, PartialEq, Eq)]
pub struct SessionStatusResponse {
    pub session_id: String,
    pub display_id: String,
    pub width: u32,
    pub height: u32,
    pub snapshot_stubbed: bool,
    pub active: bool,
    pub available_commands: Vec<String>,
    pub recovery_hint: String,
}

#[derive(Debug, Clone, Serialize, Deserialize, PartialEq, Eq)]
pub struct SnapshotResponse {
    pub session_id: String,
    pub frame: crate::capture::SnapshotFrame,
}

#[derive(Debug, Clone, Serialize, Deserialize, PartialEq, Eq)]
pub struct InputResponse {
    pub session_id: String,
    pub accepted: bool,
    pub stubbed: bool,
    pub note: String,
}

#[derive(Debug, Clone, Serialize, Deserialize, PartialEq, Eq)]
pub struct ClipboardResponse {
    pub session_id: String,
    pub operation: String,
    pub supported: bool,
    pub stubbed: bool,
    pub note: String,
    #[serde(skip_serializing_if = "Option::is_none")]
    pub text: Option<String>,
}

#[derive(Debug, Clone, Serialize, Deserialize, PartialEq, Eq)]
pub struct StreamResponse {
    pub session_id: String,
    pub operation: String,
    pub accepted: bool,
    pub active: bool,
    pub stubbed: bool,
    pub note: String,
}

#[derive(Debug, Clone, Serialize, Deserialize, PartialEq, Eq)]
pub struct PrivacyResponse {
    pub session_id: String,
    pub mode: String,
    pub applied: bool,
    pub stubbed: bool,
    pub note: String,
}

#[derive(Debug, Clone, Serialize, Deserialize, PartialEq, Eq)]
pub struct CloseSessionResponse {
    pub session_id: String,
    pub closed: bool,
}

#[cfg(test)]
mod tests {
    use super::{
        CapabilitiesResponse, ClientMetadata, ClipboardOperation, ClipboardRequest, Command,
        PROTOCOL_VERSION, PrivacyCapabilities, RecoveryCapabilities, RequestEnvelope,
        ResponseEnvelope, ResponsePayload, StreamingCapabilities,
    };
    use crate::clipboard::ClipboardCapabilities;

    #[test]
    fn serde_round_trip_for_capabilities_command() {
        let request = RequestEnvelope {
            id: Some("req-1".to_string()),
            protocol_version: None,
            client: None,
            command: Command::Capabilities,
        };

        let json = serde_json::to_string(&request).expect("serialize request");
        let parsed: RequestEnvelope = serde_json::from_str(&json).expect("deserialize request");

        assert_eq!(parsed, request);
    }

    #[test]
    fn response_omits_absent_error_and_result_fields() {
        let response = ResponseEnvelope {
            id: Some("req-1".to_string()),
            protocol_version: PROTOCOL_VERSION,
            min_compatible_protocol_version: 1,
            ok: true,
            result: Some(ResponsePayload::CloseSession(super::CloseSessionResponse {
                session_id: "session-1".to_string(),
                closed: true,
            })),
            error: None,
            warnings: Vec::new(),
        };

        let json = serde_json::to_string(&response).expect("serialize response");
        assert!(json.contains("\"ok\":true"));
        assert!(!json.contains("\"error\""));
        assert!(json.contains("\"protocol_version\":2"));
        assert!(!json.contains("\"warnings\""));
    }

    #[test]
    fn capabilities_response_serializes_extended_contract_metadata() {
        let response = CapabilitiesResponse {
            protocol_version: 2,
            min_compatible_protocol_version: 1,
            transport: "jsonl-stdio".to_string(),
            session_scoped: true,
            displays: Vec::new(),
            clipboard: ClipboardCapabilities {
                available: false,
                direction: "none".to_string(),
                stubbed: false,
                backend: Some("xclip".to_string()),
                supported_operations: Vec::new(),
                note: "clipboard intentionally gated".to_string(),
            },
            supported_commands: vec!["capabilities".to_string(), "clipboard".to_string()],
            input_actions: vec!["mouse_move".to_string()],
            snapshot_formats: vec!["application/octet-stream;base64".to_string()],
            recovery: RecoveryCapabilities {
                session_status_command: true,
                survives_sidecar_restart: false,
                stale_session_error_codes: vec!["unknown_session".to_string()],
                note: "sessions are ephemeral".to_string(),
            },
            streaming: StreamingCapabilities {
                available: false,
                configurable: true,
                active_default: false,
                formats: vec!["image/png".to_string()],
                note: "status only".to_string(),
            },
            privacy: PrivacyCapabilities {
                available: false,
                mode: "unsupported".to_string(),
                requires_consent: false,
                note: "privacy mode not implemented".to_string(),
            },
            notes: vec!["test".to_string()],
        };

        let json = serde_json::to_string(&response).expect("serialize capabilities");
        assert!(json.contains("\"backend\":\"xclip\""));
        assert!(json.contains("\"supported_operations\":[]"));
        assert!(json.contains("\"note\":\"clipboard intentionally gated\""));
        assert!(json.contains("\"supported_commands\":[\"capabilities\",\"clipboard\"]"));
        assert!(json.contains("\"session_status_command\":true"));
        assert!(json.contains("\"mode\":\"unsupported\""));
    }

    #[test]
    fn clipboard_request_round_trips_with_client_metadata() {
        let request = RequestEnvelope {
            id: Some("req-2".to_string()),
            protocol_version: Some(2),
            client: Some(ClientMetadata {
                name: "desktop-bridge".to_string(),
                version: Some("0.2.0".to_string()),
            }),
            command: Command::Clipboard(ClipboardRequest {
                session_id: "session-9".to_string(),
                operation: ClipboardOperation::WriteText {
                    text: "hello".to_string(),
                },
            }),
        };

        let json = serde_json::to_string(&request).expect("serialize clipboard request");
        assert!(json.contains("\"command\":\"clipboard\""));
        assert!(json.contains("\"protocol_version\":2"));

        let parsed: RequestEnvelope = serde_json::from_str(&json).expect("deserialize request");
        assert_eq!(parsed, request);
    }
}
