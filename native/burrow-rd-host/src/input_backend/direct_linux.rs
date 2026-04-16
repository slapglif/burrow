use super::NativeInputBackend;
use crate::input::{InputAck, InputAction};

#[derive(Debug, Clone, PartialEq, Eq)]
pub(crate) struct DirectLinuxBackend {
    reason: String,
}

impl DirectLinuxBackend {
    pub(crate) fn new(reason: String) -> Self {
        Self { reason }
    }

    pub(crate) fn detect() -> Self {
        Self::new(
            "direct Linux input backend selection is available for future work, but no native injection device path is wired in this sidecar yet"
                .to_string(),
        )
    }
}

impl NativeInputBackend for DirectLinuxBackend {
    fn name(&self) -> &'static str {
        "direct_linux"
    }

    fn apply(&self, _action: &InputAction) -> InputAck {
        InputAck {
            accepted: false,
            stubbed: false,
            note: format!(
                "direct Linux input backend is not available yet: {}",
                self.reason
            ),
        }
    }
}
