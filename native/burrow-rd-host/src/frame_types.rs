use base64::{Engine as _, engine::general_purpose::STANDARD};
use serde::{Deserialize, Serialize};

#[derive(Debug, Clone, Serialize, Deserialize, PartialEq, Eq)]
pub struct DisplayInfo {
    pub id: String,
    pub name: String,
    pub width: u32,
    pub height: u32,
    pub origin_x: i32,
    pub origin_y: i32,
    pub primary: bool,
    pub online: bool,
    pub backend: String,
}

#[derive(Debug, Clone, Serialize, Deserialize, PartialEq, Eq)]
pub struct SnapshotFrame {
    pub display_id: String,
    pub width: u32,
    pub height: u32,
    pub mime_type: String,
    pub encoding: String,
    pub data_base64: String,
    pub sequence: u64,
    pub stubbed: bool,
}

#[derive(Debug, Clone, PartialEq, Eq)]
pub struct CaptureInventory {
    pub displays: Vec<DisplayInfo>,
    pub discovery_note: String,
    pub real_displays: bool,
    pub capture_support: CaptureSupport,
}

#[derive(Debug, Clone, PartialEq, Eq)]
pub struct CaptureSupport {
    pub real_capture: bool,
    pub backend: String,
    pub note: String,
}

#[derive(Debug, Clone, PartialEq, Eq)]
pub struct FrameOrigin {
    pub x: i32,
    pub y: i32,
}

#[derive(Debug, Clone, PartialEq, Eq)]
pub struct FrameMetadata {
    pub display_id: String,
    pub width: u32,
    pub height: u32,
    pub mime_type: String,
    pub encoding: String,
    pub sequence: u64,
    pub backend: String,
    pub origin: FrameOrigin,
}

impl FrameMetadata {
    pub fn new(
        display: &DisplayInfo,
        sequence: u64,
        mime_type: impl Into<String>,
        encoding: impl Into<String>,
        backend: impl Into<String>,
    ) -> Self {
        Self {
            display_id: display.id.clone(),
            width: display.width,
            height: display.height,
            mime_type: mime_type.into(),
            encoding: encoding.into(),
            sequence,
            backend: backend.into(),
            origin: FrameOrigin {
                x: display.origin_x,
                y: display.origin_y,
            },
        }
    }
}

#[derive(Debug, Clone, PartialEq, Eq)]
pub struct CapturedFrame {
    pub metadata: FrameMetadata,
    pub data_base64: String,
    pub stubbed: bool,
}

impl CapturedFrame {
    pub fn from_bytes(metadata: FrameMetadata, bytes: &[u8], stubbed: bool) -> Self {
        Self {
            metadata,
            data_base64: STANDARD.encode(bytes),
            stubbed,
        }
    }

    pub fn into_snapshot(self) -> SnapshotFrame {
        SnapshotFrame {
            display_id: self.metadata.display_id,
            width: self.metadata.width,
            height: self.metadata.height,
            mime_type: self.metadata.mime_type,
            encoding: self.metadata.encoding,
            data_base64: self.data_base64,
            sequence: self.metadata.sequence,
            stubbed: self.stubbed,
        }
    }
}
