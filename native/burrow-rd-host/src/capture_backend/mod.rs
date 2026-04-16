use crate::capture::DisplayInfo;
use std::env;

mod direct_linux;
mod fallback;

use self::fallback::FallbackCaptureBackend;

#[cfg(test)]
pub(crate) use direct_linux::{build_ffmpeg_capture_args, build_import_capture_args};

#[derive(Debug, Clone, PartialEq, Eq)]
pub(crate) struct BackendDescriptor {
    pub kind: CaptureBackendKind,
    pub label: &'static str,
    pub note: String,
    pub display: Option<String>,
    pub real_capture: bool,
}

#[derive(Debug, Clone, Copy, PartialEq, Eq)]
pub(crate) enum CaptureBackendKind {
    ImageMagickImport,
    FfmpegX11Grab,
    Stub,
}

#[derive(Debug, Clone, PartialEq, Eq)]
pub(crate) struct DetectBackendInput {
    pub display: Option<String>,
    pub wayland_display: Option<String>,
    pub session_type: Option<String>,
    pub import_path: Option<String>,
    pub ffmpeg_path: Option<String>,
}

pub(crate) trait CaptureBackendDriver: Send + Sync {
    fn descriptor(&self) -> &BackendDescriptor;
    fn capture_png(&self, display: &DisplayInfo) -> Result<Vec<u8>, ()>;
}

pub(crate) fn detect_backend() -> Box<dyn CaptureBackendDriver> {
    validate_backend(detect_backend_from(DetectBackendInput {
        display: env::var("DISPLAY").ok(),
        wayland_display: env::var("WAYLAND_DISPLAY").ok(),
        session_type: env::var("XDG_SESSION_TYPE").ok(),
        import_path: command_on_path("import"),
        ffmpeg_path: command_on_path("ffmpeg"),
    }))
}

pub(crate) fn detect_backend_from(input: DetectBackendInput) -> Box<dyn CaptureBackendDriver> {
    let display = input.display.filter(|value| !value.trim().is_empty());
    let session_type = input.session_type.filter(|value| !value.trim().is_empty());
    let wayland_display = input
        .wayland_display
        .filter(|value| !value.trim().is_empty());
    let import_path = input.import_path;
    let ffmpeg_path = input.ffmpeg_path;
    let via_xwayland = wayland_display.is_some() && display.is_some();

    if let (Some(command), Some(display_name)) = (import_path.clone(), display.clone()) {
        let route = if via_xwayland {
            "ImageMagick import through DISPLAY/Xwayland"
        } else {
            "ImageMagick import through DISPLAY"
        };
        return Box::new(direct_linux::DirectLinuxCaptureBackend::imagemagick(
            command,
            display_name,
            format!("real Linux capture enabled via {route}"),
        ));
    }

    if let (Some(command), Some(display_name)) = (ffmpeg_path.clone(), display.clone()) {
        let route = if via_xwayland {
            "ffmpeg x11grab through DISPLAY/Xwayland"
        } else {
            "ffmpeg x11grab through DISPLAY"
        };
        return Box::new(direct_linux::DirectLinuxCaptureBackend::ffmpeg(
            command,
            display_name,
            format!("real Linux capture enabled via {route}"),
        ));
    }

    let mut reasons = Vec::new();
    if display.is_none() {
        reasons.push("DISPLAY is not set".to_string());
    }
    if import_path.is_none() && ffmpeg_path.is_none() {
        reasons.push("neither `import` nor `ffmpeg` is available on PATH".to_string());
    }
    if wayland_display.is_some() && display.is_none() {
        reasons.push(
            "Wayland-only sessions are not yet supported for capture in this sidecar".to_string(),
        );
    }
    if matches!(session_type.as_deref(), Some("wayland")) && display.is_none() {
        reasons.push("Xwayland bridge was not detected".to_string());
    }
    if reasons.is_empty() {
        reasons.push("no supported live Linux capture backend detected".to_string());
    }

    Box::new(FallbackCaptureBackend::new(format!(
        "capture fallback only: {}",
        reasons.join("; ")
    )))
}

pub(crate) fn validate_backend(
    backend: Box<dyn CaptureBackendDriver>,
) -> Box<dyn CaptureBackendDriver> {
    if !backend.descriptor().real_capture {
        return backend;
    }

    let probe_display = DisplayInfo {
        id: "probe".to_string(),
        name: "Probe Display".to_string(),
        width: 1,
        height: 1,
        origin_x: 0,
        origin_y: 0,
        primary: true,
        online: true,
        backend: "probe".to_string(),
    };

    match backend.capture_png(&probe_display) {
        Ok(bytes) if bytes.starts_with(&[0x89, b'P', b'N', b'G']) => backend,
        Ok(_) => Box::new(FallbackCaptureBackend::new(format!(
            "capture fallback only: detected {} but runtime probe did not return a PNG frame",
            backend.descriptor().label
        ))),
        Err(()) => Box::new(FallbackCaptureBackend::new(format!(
            "capture fallback only: detected {} but runtime probe failed",
            backend.descriptor().label
        ))),
    }
}

fn command_on_path(command: &str) -> Option<String> {
    let path = env::var_os("PATH")?;
    env::split_paths(&path)
        .map(|dir| dir.join(command))
        .find(|candidate| candidate.is_file() && is_executable(candidate))
        .map(|candidate| candidate.to_string_lossy().into_owned())
}

fn is_executable(path: &std::path::Path) -> bool {
    #[cfg(unix)]
    {
        use std::os::unix::fs::PermissionsExt;
        path.metadata()
            .map(|meta| meta.permissions().mode() & 0o111 != 0)
            .unwrap_or(false)
    }

    #[cfg(not(unix))]
    {
        path.is_file()
    }
}
