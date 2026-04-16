mod direct_linux;
mod x11_xdotool;

pub(crate) use direct_linux::DirectLinuxBackend;
pub(crate) use x11_xdotool::XdotoolBackend;

use crate::input::{InputAck, InputAction};
use std::env;
use std::path::PathBuf;

pub(crate) trait NativeInputBackend {
    fn name(&self) -> &'static str;
    fn apply(&self, action: &InputAction) -> InputAck;
}

#[derive(Debug, Clone, PartialEq, Eq)]
pub(crate) struct UnsupportedBackend {
    reason: String,
}

impl UnsupportedBackend {
    pub(crate) fn new(reason: String) -> Self {
        Self { reason }
    }
}

impl NativeInputBackend for UnsupportedBackend {
    fn name(&self) -> &'static str {
        "unsupported"
    }

    fn apply(&self, _action: &InputAction) -> InputAck {
        InputAck {
            accepted: false,
            stubbed: false,
            note: self.reason.clone(),
        }
    }
}

pub(crate) fn detect_backend() -> Box<dyn NativeInputBackend> {
    #[cfg(target_os = "linux")]
    {
        let preferred = env::var("BURROW_RD_INPUT_BACKEND").ok();
        if preferred
            .as_deref()
            .is_some_and(|value| value.eq_ignore_ascii_case("direct"))
        {
            return Box::new(DirectLinuxBackend::detect());
        }

        let display = env::var("DISPLAY")
            .ok()
            .filter(|value| !value.trim().is_empty());
        if let Some(display) = display {
            if let Some(program) = which("xdotool") {
                return Box::new(XdotoolBackend::new(program, display));
            }
            return Box::new(UnsupportedBackend::new(
                "native input is unavailable: DISPLAY is set but xdotool is not installed; the direct Linux backend scaffold exists but is not wired yet"
                    .to_string(),
            ));
        }

        let wayland = env::var("WAYLAND_DISPLAY")
            .ok()
            .filter(|value| !value.trim().is_empty());
        if wayland.is_some() {
            return Box::new(DirectLinuxBackend::new(
                "Wayland was detected, but only the bounded X11 xdotool backend is currently functional".to_string(),
            ));
        }

        Box::new(UnsupportedBackend::new(
            "native input is unavailable: no DISPLAY was detected for the bounded X11 path, and the direct Linux backend scaffold is not wired yet"
                .to_string(),
        ))
    }
    #[cfg(not(target_os = "linux"))]
    {
        Box::new(UnsupportedBackend::new(
            "native input is only implemented for a bounded Linux X11 path right now".to_string(),
        ))
    }
}

fn which(binary: &str) -> Option<PathBuf> {
    let path = env::var_os("PATH")?;
    env::split_paths(&path)
        .map(|dir| dir.join(binary))
        .find(|candidate| candidate.is_file())
}
