use crate::ipc::ClipboardOperation;
use serde::{Deserialize, Serialize};
use std::env;
use std::io::Write;
use std::path::PathBuf;
use std::process::{Command, Stdio};

#[derive(Debug, Clone, Serialize, Deserialize, PartialEq, Eq)]
pub struct ClipboardCapabilities {
    pub available: bool,
    pub direction: String,
    pub stubbed: bool,
    #[serde(skip_serializing_if = "Option::is_none")]
    pub backend: Option<String>,
    pub supported_operations: Vec<String>,
    pub note: String,
}

#[derive(Debug, Clone, PartialEq, Eq)]
enum ClipboardBackend {
    Xclip(PathBuf),
    Xsel(PathBuf),
    WaylandWlClipboard { copy: PathBuf, paste: PathBuf },
    Unsupported { reason: String },
}

#[derive(Debug, Clone, PartialEq, Eq)]
pub struct ClipboardResult {
    pub supported: bool,
    pub text: Option<String>,
    pub stubbed: bool,
    pub note: String,
}

#[derive(Debug)]
pub struct ClipboardService {
    backend: ClipboardBackend,
}

impl Default for ClipboardService {
    fn default() -> Self {
        Self {
            backend: detect_backend(),
        }
    }
}

impl ClipboardService {
    pub fn new() -> Self {
        Self::default()
    }

    pub fn capabilities(&self) -> ClipboardCapabilities {
        match &self.backend {
            ClipboardBackend::Xclip(_) => ClipboardCapabilities {
                available: true,
                direction: "bidirectional".to_string(),
                stubbed: false,
                backend: Some("xclip".to_string()),
                supported_operations: vec![
                    "read_text".to_string(),
                    "write_text".to_string(),
                    "copy".to_string(),
                    "cut".to_string(),
                    "paste".to_string(),
                    "clear".to_string(),
                    "sync".to_string(),
                ],
                note: "xclip is available for X11 clipboard reads and writes.".to_string(),
            },
            ClipboardBackend::Xsel(_) => ClipboardCapabilities {
                available: true,
                direction: "bidirectional".to_string(),
                stubbed: false,
                backend: Some("xsel".to_string()),
                supported_operations: vec![
                    "read_text".to_string(),
                    "write_text".to_string(),
                    "copy".to_string(),
                    "cut".to_string(),
                    "paste".to_string(),
                    "clear".to_string(),
                    "sync".to_string(),
                ],
                note: "xsel is available for X11 clipboard reads and writes.".to_string(),
            },
            ClipboardBackend::WaylandWlClipboard { .. } => ClipboardCapabilities {
                available: true,
                direction: "bidirectional".to_string(),
                stubbed: false,
                backend: Some("wl-clipboard".to_string()),
                supported_operations: vec![
                    "read_text".to_string(),
                    "write_text".to_string(),
                    "clear".to_string(),
                    "sync".to_string(),
                ],
                note: "wl-copy/wl-paste are available for Wayland clipboard reads and writes.".to_string(),
            },
            ClipboardBackend::Unsupported { reason } => ClipboardCapabilities {
                available: false,
                direction: "none".to_string(),
                stubbed: false,
                backend: None,
                supported_operations: Vec::new(),
                note: reason.clone(),
            },
        }
    }

    pub fn execute(&self, operation: ClipboardOperation) -> ClipboardResult {
        if !self.supports_operation(&operation) {
            return ClipboardResult {
                supported: false,
                text: None,
                stubbed: false,
                note: "clipboard operation is not supported by the detected backend".to_string(),
            };
        }

        match operation {
            ClipboardOperation::ReadText => match self.read_text() {
                Ok(text) => ClipboardResult {
                    supported: true,
                    text: Some(text),
                    stubbed: false,
                    note: "read_text completed".to_string(),
                },
                Err(message) => ClipboardResult {
                    supported: false,
                    text: None,
                    stubbed: false,
                    note: message,
                },
            },
            ClipboardOperation::WriteText { text } => match self.write_text(&text) {
                Ok(()) => ClipboardResult {
                    supported: true,
                    text: None,
                    stubbed: false,
                    note: "write_text completed".to_string(),
                },
                Err(message) => ClipboardResult {
                    supported: false,
                    text: None,
                    stubbed: false,
                    note: message,
                },
            },
            ClipboardOperation::Sync => ClipboardResult {
                supported: true,
                text: None,
                stubbed: false,
                note: "sync completed".to_string(),
            },
            ClipboardOperation::Clear => match self.clear() {
                Ok(()) => ClipboardResult {
                    supported: true,
                    text: None,
                    stubbed: false,
                    note: "clear completed".to_string(),
                },
                Err(message) => ClipboardResult {
                    supported: false,
                    text: None,
                    stubbed: false,
                    note: message,
                },
            },
            ClipboardOperation::Copy | ClipboardOperation::Cut | ClipboardOperation::Paste => ClipboardResult {
                supported: false,
                text: None,
                stubbed: false,
                note: "clipboard action is reserved for input/key shortcut paths in higher layers".to_string(),
            },
        }
    }

    pub fn supports_operation(&self, operation: &ClipboardOperation) -> bool {
        self.capabilities()
            .supported_operations
            .iter()
            .any(|supported| supported == self.operation_name(operation))
    }

    fn operation_name(&self, operation: &ClipboardOperation) -> &str {
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

    fn read_text(&self) -> Result<String, String> {
        match &self.backend {
            ClipboardBackend::Xclip(path) => {
                run_stdout_text("xclip", path, &["-selection", "clipboard", "-out"])
            }
            ClipboardBackend::Xsel(path) => {
                run_stdout_text("xsel", path, &["--clipboard", "--output"])
            }
            ClipboardBackend::WaylandWlClipboard { copy: _, paste } => {
                run_stdout_text("wl-paste", paste, &[])
            }
            ClipboardBackend::Unsupported { reason } => Err(reason.clone()),
        }
    }

    fn write_text(&self, text: &str) -> Result<(), String> {
        match &self.backend {
            ClipboardBackend::Xclip(path) => write_stdin_text(
                "xclip",
                path,
                &["-selection", "clipboard", "-in"],
                text,
            ),
            ClipboardBackend::Xsel(path) => {
                write_stdin_text("xsel", path, &["--clipboard", "--input"], text)
            }
            ClipboardBackend::WaylandWlClipboard { copy, .. } => {
                write_stdin_text("wl-copy", copy, &[], text)
            }
            ClipboardBackend::Unsupported { reason } => Err(reason.clone()),
        }
    }

    fn clear(&self) -> Result<(), String> {
        match &self.backend {
            ClipboardBackend::Xclip(path) => write_stdin_text("xclip", path, &["-selection", "clipboard", "-in"], ""),
            ClipboardBackend::Xsel(path) => run_status("xsel", path, &["--clipboard", "--clear"]),
            ClipboardBackend::WaylandWlClipboard { copy, .. } => run_status("wl-copy", copy, &["--clear"]),
            ClipboardBackend::Unsupported { reason } => Err(reason.clone()),
        }
    }
}

fn detect_backend() -> ClipboardBackend {
    #[cfg(target_os = "linux")]
    {
        let display = env::var("DISPLAY")
            .ok()
            .filter(|value| !value.trim().is_empty());
        if display.is_some() {
            if which("xclip").is_some() {
                return ClipboardBackend::Xclip(which("xclip").unwrap());
            }
            if which("xsel").is_some() {
                return ClipboardBackend::Xsel(which("xsel").unwrap());
            }
            return ClipboardBackend::Unsupported {
                reason: "clipboard is unavailable: DISPLAY is set but neither xclip nor xsel is installed"
                    .to_string(),
            };
        }

        let wayland = env::var("WAYLAND_DISPLAY")
            .ok()
            .filter(|value| !value.trim().is_empty());
        if wayland.is_some() {
            if let (Some(copy), Some(paste)) = (which("wl-copy"), which("wl-paste")) {
                return ClipboardBackend::WaylandWlClipboard { copy, paste };
            }
            return ClipboardBackend::Unsupported {
                reason: "clipboard is unavailable: WAYLAND_DISPLAY is set but wl-copy/wl-paste are not both installed"
                    .to_string(),
            };
        }

        ClipboardBackend::Unsupported {
            reason: "clipboard is unavailable: no supported X11 or Wayland clipboard backend was detected"
                .to_string(),
        }
    }
    #[cfg(not(target_os = "linux"))]
    {
        ClipboardBackend::Unsupported {
            reason: "clipboard is only probed on Linux in this sidecar right now".to_string(),
        }
    }
}

fn which(binary: &str) -> Option<PathBuf> {
    let path = env::var_os("PATH")?;
    env::split_paths(&path)
        .map(|dir| dir.join(binary))
        .find(|candidate| candidate.is_file())
}

fn run_stdout_text(bin_name: &str, path: &PathBuf, args: &[&str]) -> Result<String, String> {
    let output = Command::new(path)
        .args(args)
        .output()
        .map_err(|error| format!("{bin_name} failed to execute: {error}"))?;

    if !output.status.success() {
        return Err(format!(
            "{bin_name} failed with status {}",
            output.status.code().unwrap_or(-1)
        ));
    }

    String::from_utf8(output.stdout).map_err(|error| {
        format!("{bin_name} returned non-UTF8 clipboard bytes: {error}")
    })
}

fn run_status(bin_name: &str, path: &PathBuf, args: &[&str]) -> Result<(), String> {
    let output = Command::new(path)
        .args(args)
        .output()
        .map_err(|error| format!("{bin_name} failed to execute: {error}"))?;

    if !output.status.success() {
        return Err(format!(
            "{bin_name} failed with status {}",
            output.status.code().unwrap_or(-1)
        ));
    }

    Ok(())
}

fn write_stdin_text(bin_name: &str, path: &PathBuf, args: &[&str], text: &str) -> Result<(), String> {
    let mut child = Command::new(path)
        .args(args)
        .stdin(Stdio::piped())
        .stdout(Stdio::null())
        .stderr(Stdio::null())
        .spawn()
        .map_err(|error| format!("{bin_name} failed to execute: {error}"))?;

    if let Some(handle) = child.stdin.as_mut() {
        handle
            .write_all(text.as_bytes())
            .map_err(|error| format!("{bin_name} stdin write failed: {error}"))?;
    }

    let status = child
        .wait()
        .map_err(|error| format!("{bin_name} wait failed: {error}"))?;

    if !status.success() {
        return Err(format!("{bin_name} failed with status {}", status.code().unwrap_or(-1)));
    }

    Ok(())
}

#[cfg(test)]
mod tests {
    use std::path::PathBuf;

    use super::{ClipboardBackend, ClipboardOperation, ClipboardResult, ClipboardService};

    #[test]
    fn clipboard_detection_reports_truthful_unsupported_state() {
        let service = ClipboardService {
            backend: ClipboardBackend::Unsupported {
                reason: "clipboard unavailable for test".to_string(),
            },
        };
        let capabilities = service.capabilities();

        assert!(!capabilities.available);
        assert_eq!(capabilities.direction, "none");
        assert!(!capabilities.stubbed);
        assert!(capabilities.supported_operations.is_empty());
        assert_eq!(capabilities.note, "clipboard unavailable for test");
    }

    #[test]
    fn detected_backend_is_reported_with_realistic_capabilities() {
        let service = ClipboardService {
            backend: ClipboardBackend::Xclip(PathBuf::from("/usr/bin/xclip")),
        };
        let capabilities = service.capabilities();

        assert!(capabilities.available);
        assert_eq!(capabilities.backend.as_deref(), Some("xclip"));
        assert_eq!(capabilities.direction, "bidirectional");
        assert!(!capabilities.stubbed);
        assert!(capabilities.supported_operations.iter().any(|item| item == "read_text"));
        assert!(capabilities.supported_operations.iter().any(|item| item == "write_text"));
        assert!(capabilities.note.contains("xclip is available"));
    }

    #[test]
    fn execute_reports_copy_as_higher_layer_shortcut() {
        let service = ClipboardService {
            backend: ClipboardBackend::Xclip(PathBuf::from("/usr/bin/xclip")),
        };
        let result = service.execute(ClipboardOperation::Copy);

        assert_eq!(
            result,
            ClipboardResult {
                supported: false,
                text: None,
                stubbed: false,
                note: "clipboard action is reserved for input/key shortcut paths in higher layers".to_string(),
            }
        );
    }

    #[test]
    fn execute_reports_unsupported_when_no_backend_is_present() {
        let service = ClipboardService {
            backend: ClipboardBackend::Unsupported {
                reason: "clipboard unavailable".to_string(),
            },
        };
        let result = service.execute(ClipboardOperation::Sync);

        assert_eq!(
            result,
            ClipboardResult {
                supported: false,
                text: None,
                stubbed: false,
                note: "clipboard operation is not supported by the detected backend".to_string(),
            }
        );
    }
}
