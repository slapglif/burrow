use serde::{Deserialize, Serialize};

#[path = "input_backend/mod.rs"]
mod input_backend;

use input_backend::{NativeInputBackend, detect_backend};

#[derive(Debug, Clone, Serialize, Deserialize, PartialEq)]
#[serde(tag = "type", rename_all = "snake_case")]
pub enum InputAction {
    KeyPress { key: String },
    KeyRelease { key: String },
    Text { text: String },
    MouseMove { x: i32, y: i32 },
    MouseButton { button: String, pressed: bool },
    Scroll { delta_x: i32, delta_y: i32 },
}

#[derive(Debug, Clone, Serialize, Deserialize, PartialEq, Eq)]
pub struct InputAck {
    pub accepted: bool,
    pub stubbed: bool,
    pub note: String,
}

pub struct InputService {
    backend: Box<dyn NativeInputBackend>,
    actions: Vec<InputAction>,
}

impl Default for InputService {
    fn default() -> Self {
        Self {
            backend: detect_backend(),
            actions: Vec::new(),
        }
    }
}

impl std::fmt::Debug for InputService {
    fn fmt(&self, f: &mut std::fmt::Formatter<'_>) -> std::fmt::Result {
        f.debug_struct("InputService")
            .field("backend", &self.backend.name())
            .field("actions", &self.actions)
            .finish()
    }
}

impl InputService {
    pub fn new() -> Self {
        Self::default()
    }

    pub fn apply(&mut self, action: InputAction) -> InputAck {
        self.actions.push(action.clone());
        self.backend.apply(&action)
    }

    pub fn action_count(&self) -> usize {
        self.actions.len()
    }
}

#[cfg(test)]
mod tests {
    use super::{InputAction, InputService};
    use crate::input::input_backend::{
        DirectLinuxBackend, NativeInputBackend, UnsupportedBackend, XdotoolBackend,
    };
    use std::fs;
    use std::path::{Path, PathBuf};
    use std::time::{SystemTime, UNIX_EPOCH};

    #[test]
    fn xdotool_backend_executes_mouse_move() {
        let sandbox = unique_test_dir("input-xdotool-success");
        let log_path = sandbox.join("xdotool.log");
        let program = make_fake_xdotool(&sandbox, &log_path, 0);
        let mut service = InputService {
            backend: Box::new(XdotoolBackend::new(program, ":77".to_string())),
            actions: Vec::new(),
        };

        let ack = service.apply(InputAction::MouseMove { x: 10, y: 20 });

        assert!(ack.accepted);
        assert!(!ack.stubbed);
        assert_eq!(service.action_count(), 1);
        let logged = fs::read_to_string(&log_path).expect("read fake xdotool log");
        assert!(logged.contains("DISPLAY=:77"));
        assert!(logged.contains("mousemove 10 20"));
        remove_dir_if_present(&sandbox);
    }

    #[test]
    fn xdotool_backend_supports_scroll() {
        let sandbox = unique_test_dir("input-xdotool-scroll");
        let log_path = sandbox.join("xdotool.log");
        let program = make_fake_xdotool(&sandbox, &log_path, 0);
        let mut service = InputService {
            backend: Box::new(XdotoolBackend::new(program, ":88".to_string())),
            actions: Vec::new(),
        };

        let ack = service.apply(InputAction::Scroll {
            delta_x: 2,
            delta_y: -3,
        });

        assert!(ack.accepted);
        assert!(!ack.stubbed);
        assert!(ack.note.contains("scroll"));
        let logged = fs::read_to_string(&log_path).expect("read fake xdotool log");
        assert!(logged.contains("click --repeat 3 5"));
        assert!(logged.contains("click --repeat 2 7"));
        remove_dir_if_present(&sandbox);
    }

    #[test]
    fn direct_backend_reports_truthful_capability_gate() {
        let backend = DirectLinuxBackend::new("/dev/uinput is not wired yet".to_string());

        let ack = backend.apply(&InputAction::KeyPress {
            key: "a".to_string(),
        });

        assert!(!ack.accepted);
        assert!(!ack.stubbed);
        assert!(
            ack.note
                .contains("direct Linux input backend is not available")
        );
        assert!(ack.note.contains("/dev/uinput is not wired yet"));
    }

    #[test]
    fn unavailable_backend_returns_explicit_error() {
        let mut service = InputService {
            backend: Box::new(UnsupportedBackend::new(
                "native input unavailable for test".to_string(),
            )),
            actions: Vec::new(),
        };

        let ack = service.apply(InputAction::KeyPress {
            key: "a".to_string(),
        });

        assert!(!ack.accepted);
        assert!(!ack.stubbed);
        assert_eq!(ack.note, "native input unavailable for test");
        assert_eq!(service.action_count(), 1);
    }

    fn unique_test_dir(prefix: &str) -> PathBuf {
        let nonce = SystemTime::now()
            .duration_since(UNIX_EPOCH)
            .expect("time went backwards")
            .as_nanos();
        let dir = std::env::temp_dir().join(format!("{prefix}-{nonce}"));
        fs::create_dir_all(&dir).expect("create test dir");
        dir
    }

    fn make_fake_xdotool(dir: &Path, log_path: &Path, exit_code: i32) -> PathBuf {
        let program = dir.join("xdotool");
        let script = format!(
            "#!/usr/bin/env bash\nprintf 'DISPLAY=%s %s\\n' \"$DISPLAY\" \"$*\" >> '{}'\nexit {}\n",
            log_path.display(),
            exit_code
        );
        fs::write(&program, script).expect("write fake xdotool");
        #[cfg(unix)]
        {
            use std::os::unix::fs::PermissionsExt;
            let mut perms = fs::metadata(&program)
                .expect("stat fake xdotool")
                .permissions();
            perms.set_mode(0o755);
            fs::set_permissions(&program, perms).expect("chmod fake xdotool");
        }
        program
    }

    fn remove_dir_if_present(path: &Path) {
        let _ = fs::remove_dir_all(path);
    }
}
