#[path = "capture_backend/mod.rs"]
mod capture_backend;
#[path = "frame_types.rs"]
mod frame_types;

use capture_backend::{BackendDescriptor, detect_backend};
#[cfg(test)]
use capture_backend::{
    CaptureBackendKind, DetectBackendInput, build_ffmpeg_capture_args, build_import_capture_args,
    detect_backend_from,
};
use frame_types::{CapturedFrame, FrameMetadata};
use regex::Regex;
use std::process::Command;

pub use frame_types::{CaptureInventory, CaptureSupport, DisplayInfo, SnapshotFrame};

#[derive(Debug, Default)]
pub struct CaptureService {
    sequence: u64,
}

impl CaptureService {
    pub fn new() -> Self {
        Self::default()
    }

    pub fn inventory(&self) -> CaptureInventory {
        detect_inventory()
    }

    pub fn displays(&self) -> Vec<DisplayInfo> {
        self.inventory().displays
    }

    pub fn capture_support(&self) -> CaptureSupport {
        detect_capture_support()
    }

    pub fn can_capture_display(&self, display: &DisplayInfo) -> bool {
        self.capture_support().real_capture
            && display.online
            && display.width > 0
            && display.height > 0
    }

    pub fn snapshot(&mut self, requested_display: Option<&str>) -> SnapshotFrame {
        self.sequence += 1;
        let inventory = self.inventory();
        let display = inventory
            .displays
            .iter()
            .find(|candidate| requested_display.is_none_or(|value| candidate.id == value))
            .cloned()
            .unwrap_or_else(stub_display);

        if let Some(frame) = capture_real_snapshot(self.sequence, &display) {
            return frame.into_snapshot();
        }

        build_stub_snapshot(self.sequence, &display, &inventory).into_snapshot()
    }
}

fn detect_inventory() -> CaptureInventory {
    let capture_support = detect_capture_support();

    #[cfg(target_os = "linux")]
    {
        if let Some(displays) = probe_xrandr_displays() {
            return CaptureInventory {
                displays,
                discovery_note: "display enumeration from xrandr, using a parser adapted from RustDesk linux display probing".to_string(),
                real_displays: true,
                capture_support,
            };
        }
    }

    CaptureInventory {
        displays: vec![stub_display()],
        discovery_note:
            "fallback stub display inventory; direct/native capture backend scaffold is ready but live display probing was unavailable"
                .to_string(),
        real_displays: false,
        capture_support,
    }
}

fn capture_real_snapshot(sequence: u64, display: &DisplayInfo) -> Option<CapturedFrame> {
    let backend = detect_backend();
    let descriptor = backend.descriptor().clone();
    if !descriptor.real_capture || !display.online || display.width == 0 || display.height == 0 {
        return None;
    }

    let png = backend.capture_png(display).ok()?;
    if png.is_empty() {
        return None;
    }

    Some(CapturedFrame::from_bytes(
        FrameMetadata::new(display, sequence, "image/png", "base64", descriptor.label),
        &png,
        false,
    ))
}

fn build_stub_snapshot(
    sequence: u64,
    display: &DisplayInfo,
    inventory: &CaptureInventory,
) -> CapturedFrame {
    let payload = format!(
        "burrow-rd-host stub frame:{}:{}:{}:{}:{}",
        display.id,
        sequence,
        display.width,
        inventory.discovery_note,
        inventory.capture_support.note
    );

    CapturedFrame::from_bytes(
        FrameMetadata::new(
            display,
            sequence,
            "application/octet-stream",
            "base64",
            "stub",
        ),
        payload.as_bytes(),
        true,
    )
}

fn detect_capture_support() -> CaptureSupport {
    let backend = detect_capture_backend();
    CaptureSupport {
        real_capture: backend.real_capture,
        backend: backend.label.to_string(),
        note: backend.note,
    }
}

fn detect_capture_backend() -> BackendDescriptor {
    detect_backend().descriptor().clone()
}

#[cfg(test)]
fn detect_capture_backend_from(input: DetectBackendInput) -> BackendDescriptor {
    detect_backend_from(input).descriptor().clone()
}

fn stub_display() -> DisplayInfo {
    DisplayInfo {
        id: "display-1".to_string(),
        name: "Stub Display".to_string(),
        width: 1280,
        height: 720,
        origin_x: 0,
        origin_y: 0,
        primary: true,
        online: true,
        backend: "stub".to_string(),
    }
}

#[cfg(target_os = "linux")]
fn probe_xrandr_displays() -> Option<Vec<DisplayInfo>> {
    let output = Command::new("xrandr").arg("--query").output().ok()?;
    if !output.status.success() {
        return None;
    }
    parse_xrandr_displays(&String::from_utf8_lossy(&output.stdout))
}

#[cfg(not(target_os = "linux"))]
fn probe_xrandr_displays() -> Option<Vec<DisplayInfo>> {
    None
}

// Adapted from RustDesk's Linux display parsing logic:
// - src/platform/linux.rs:get_xrandr_conn_pat/current_resolution
// - libs/scrap/src/wayland/display.rs:try_xrandr_primary/get_primary_monitor
// This keeps the first reuse slice small and honest: real display enumeration
// without claiming full scrap capture integration yet.
fn parse_xrandr_displays(output: &str) -> Option<Vec<DisplayInfo>> {
    let normalized = output.replace('\t', " ");
    let connected_re = Regex::new(
        r"(?m)^(?P<name>\S+)\s+connected(?:\s+primary)?(?:\s+(?P<width>\d+)x(?P<height>\d+)\+(?P<x>-?\d+)\+(?P<y>-?\d+))?",
    )
    .ok()?;

    let mut displays = Vec::new();
    for captures in connected_re.captures_iter(&normalized) {
        let name = captures.name("name")?.as_str().to_string();
        let width = captures
            .name("width")
            .and_then(|value| value.as_str().parse::<u32>().ok())
            .unwrap_or(0);
        let height = captures
            .name("height")
            .and_then(|value| value.as_str().parse::<u32>().ok())
            .unwrap_or(0);
        let origin_x = captures
            .name("x")
            .and_then(|value| value.as_str().parse::<i32>().ok())
            .unwrap_or(0);
        let origin_y = captures
            .name("y")
            .and_then(|value| value.as_str().parse::<i32>().ok())
            .unwrap_or(0);
        let full_line = captures.get(0)?.as_str();

        displays.push(DisplayInfo {
            id: format!("xrandr:{name}"),
            name,
            width,
            height,
            origin_x,
            origin_y,
            primary: full_line.contains(" primary "),
            online: true,
            backend: "xrandr".to_string(),
        });
    }

    if displays.is_empty() {
        return None;
    }

    if !displays.iter().any(|display| display.primary) {
        let primary_index = displays
            .iter()
            .position(|display| display.origin_x == 0 && display.origin_y == 0)
            .unwrap_or(0);
        if let Some(primary) = displays.get_mut(primary_index) {
            primary.primary = true;
        }
    }

    Some(displays)
}

#[cfg(test)]
mod tests {
    use super::{
        CaptureBackendKind, CaptureService, build_ffmpeg_capture_args, build_import_capture_args,
        detect_capture_backend_from, parse_xrandr_displays, stub_display,
    };
    use crate::capture::capture_backend::DetectBackendInput;
    use base64::{Engine as _, engine::general_purpose::STANDARD};

    #[test]
    fn parses_xrandr_output_with_explicit_primary() {
        let output = r#"
Screen 0: minimum 320 x 200, current 1920 x 1080, maximum 16384 x 16384
eDP-1 connected primary 1920x1080+0+0 (normal left inverted right x axis y axis) 344mm x 193mm
1920x1080     60.01*+  60.01    59.97
HDMI-1 connected 2560x1440+1920+0 (normal left inverted right x axis y axis) 600mm x 340mm
2560x1440     59.95*+
"#;

        let displays = parse_xrandr_displays(output).expect("parsed displays");
        assert_eq!(displays.len(), 2);
        assert_eq!(displays[0].id, "xrandr:eDP-1");
        assert_eq!(displays[0].width, 1920);
        assert!(displays[0].primary);
        assert!(!displays[1].primary);
    }

    #[test]
    fn falls_back_to_origin_primary_when_xrandr_omits_primary_marker() {
        let output = r#"
default connected 1920x1080+0+0 0mm x 0mm
1920x1080 10.00*
Virtual2 disconnected (normal left inverted right x axis y axis)
"#;

        let displays = parse_xrandr_displays(output).expect("parsed displays");
        assert_eq!(displays.len(), 1);
        assert_eq!(displays[0].name, "default");
        assert!(displays[0].primary);
    }

    #[test]
    fn snapshot_reflects_detected_display_shape_when_real_capture_is_unavailable() {
        let mut service = CaptureService::new();
        let first = service.snapshot(None);
        let second = service.snapshot(Some(&first.display_id));
        let decoded = STANDARD
            .decode(&first.data_base64)
            .expect("base64 snapshot payload");
        let decoded = String::from_utf8(decoded).expect("utf8 payload");

        if first.stubbed {
            assert!(decoded.contains("burrow-rd-host stub frame:"));
        } else {
            assert_eq!(first.mime_type, "image/png");
        }
        assert!(second.sequence > first.sequence);
        assert_eq!(second.display_id, first.display_id);
    }

    #[test]
    fn selects_imagemagick_backend_when_display_and_import_are_available() {
        let descriptor = detect_capture_backend_from(DetectBackendInput {
            display: Some(":0".to_string()),
            wayland_display: Some("wayland-0".to_string()),
            session_type: Some("wayland".to_string()),
            import_path: Some("/usr/bin/import".to_string()),
            ffmpeg_path: Some("/usr/bin/ffmpeg".to_string()),
        });

        assert_eq!(descriptor.kind, CaptureBackendKind::ImageMagickImport);
        assert!(descriptor.real_capture);
        assert_eq!(descriptor.label, "imagemagick-import");
    }

    #[test]
    fn reports_stub_fallback_when_no_live_backend_is_available() {
        let descriptor = detect_capture_backend_from(DetectBackendInput {
            display: None,
            wayland_display: Some("wayland-0".to_string()),
            session_type: Some("wayland".to_string()),
            import_path: None,
            ffmpeg_path: None,
        });

        assert_eq!(descriptor.kind, CaptureBackendKind::Stub);
        assert!(!descriptor.real_capture);
        assert!(descriptor.note.contains("capture fallback only"));
        assert!(
            descriptor
                .note
                .contains("Wayland-only sessions are not yet supported")
        );
    }

    #[test]
    fn builds_import_capture_command_for_display_bounds() {
        let display = stub_display();
        let args = build_import_capture_args(&display);

        assert_eq!(
            args,
            vec![
                "-silent",
                "-window",
                "root",
                "-crop",
                "1280x720+0+0",
                "+repage",
                "png:-",
            ]
        );
    }

    #[test]
    fn builds_ffmpeg_capture_command_for_display_bounds() {
        let mut display = stub_display();
        display.width = 1920;
        display.height = 1080;
        display.origin_x = 1920;
        let args = build_ffmpeg_capture_args(":0", &display);

        assert_eq!(
            args,
            vec![
                "-v",
                "error",
                "-f",
                "x11grab",
                "-video_size",
                "1920x1080",
                "-i",
                ":0+1920,0",
                "-frames:v",
                "1",
                "-f",
                "image2pipe",
                "-vcodec",
                "png",
                "-",
            ]
        );
    }

    #[test]
    fn snapshot_sequences_continue_across_stubbed_frames() {
        let mut service = CaptureService::new();
        let first = service.snapshot(Some("missing-display"));
        let second = service.snapshot(Some("missing-display"));

        assert!(first.stubbed);
        assert!(second.stubbed);
        assert_eq!(first.display_id, "display-1");
        assert_eq!(second.sequence, first.sequence + 1);
    }
}
