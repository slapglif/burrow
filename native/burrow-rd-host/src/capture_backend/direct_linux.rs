use super::{BackendDescriptor, CaptureBackendDriver, CaptureBackendKind};
use crate::capture::DisplayInfo;
use std::process::Command;

pub(crate) struct DirectLinuxCaptureBackend {
    descriptor: BackendDescriptor,
    command: String,
    display_name: String,
}

impl DirectLinuxCaptureBackend {
    pub(crate) fn imagemagick(command: String, display_name: String, note: String) -> Self {
        Self {
            descriptor: BackendDescriptor {
                kind: CaptureBackendKind::ImageMagickImport,
                label: "imagemagick-import",
                note,
                display: Some(display_name.clone()),
                real_capture: true,
            },
            command,
            display_name,
        }
    }

    pub(crate) fn ffmpeg(command: String, display_name: String, note: String) -> Self {
        Self {
            descriptor: BackendDescriptor {
                kind: CaptureBackendKind::FfmpegX11Grab,
                label: "ffmpeg-x11grab",
                note,
                display: Some(display_name.clone()),
                real_capture: true,
            },
            command,
            display_name,
        }
    }
}

impl CaptureBackendDriver for DirectLinuxCaptureBackend {
    fn descriptor(&self) -> &BackendDescriptor {
        &self.descriptor
    }

    fn capture_png(&self, display: &DisplayInfo) -> Result<Vec<u8>, ()> {
        let args = match self.descriptor.kind {
            CaptureBackendKind::ImageMagickImport => build_import_capture_args(display),
            CaptureBackendKind::FfmpegX11Grab => {
                build_ffmpeg_capture_args(&self.display_name, display)
            }
            CaptureBackendKind::Stub => return Err(()),
        };

        let output = Command::new(&self.command)
            .args(&args)
            .output()
            .map_err(|_| ())?;
        if !output.status.success() || output.stdout.is_empty() {
            return Err(());
        }
        Ok(output.stdout)
    }
}

pub(crate) fn build_import_capture_args(display: &DisplayInfo) -> Vec<String> {
    vec![
        "-silent".to_string(),
        "-window".to_string(),
        "root".to_string(),
        "-crop".to_string(),
        format!(
            "{}x{}+{}+{}",
            display.width, display.height, display.origin_x, display.origin_y
        ),
        "+repage".to_string(),
        "png:-".to_string(),
    ]
}

pub(crate) fn build_ffmpeg_capture_args(display_name: &str, display: &DisplayInfo) -> Vec<String> {
    vec![
        "-v".to_string(),
        "error".to_string(),
        "-f".to_string(),
        "x11grab".to_string(),
        "-video_size".to_string(),
        format!("{}x{}", display.width, display.height),
        "-i".to_string(),
        format!("{display_name}+{},{}", display.origin_x, display.origin_y),
        "-frames:v".to_string(),
        "1".to_string(),
        "-f".to_string(),
        "image2pipe".to_string(),
        "-vcodec".to_string(),
        "png".to_string(),
        "-".to_string(),
    ]
}
