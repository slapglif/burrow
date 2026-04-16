use super::{BackendDescriptor, CaptureBackendDriver, CaptureBackendKind};
use crate::capture::DisplayInfo;

pub(crate) struct FallbackCaptureBackend {
    descriptor: BackendDescriptor,
}

impl FallbackCaptureBackend {
    pub(crate) fn new(note: String) -> Self {
        Self {
            descriptor: BackendDescriptor {
                kind: CaptureBackendKind::Stub,
                label: "stub",
                note,
                display: None,
                real_capture: false,
            },
        }
    }
}

impl CaptureBackendDriver for FallbackCaptureBackend {
    fn descriptor(&self) -> &BackendDescriptor {
        &self.descriptor
    }

    fn capture_png(&self, _display: &DisplayInfo) -> Result<Vec<u8>, ()> {
        Err(())
    }
}
