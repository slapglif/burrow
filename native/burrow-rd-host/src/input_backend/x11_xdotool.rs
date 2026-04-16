use super::NativeInputBackend;
use crate::input::{InputAck, InputAction};
use std::path::{Path, PathBuf};
use std::process::Command;

#[derive(Debug, Clone, PartialEq, Eq)]
pub(crate) struct XdotoolBackend {
    program: PathBuf,
    display: String,
}

impl XdotoolBackend {
    pub(crate) fn new(program: PathBuf, display: String) -> Self {
        Self { program, display }
    }
}

impl NativeInputBackend for XdotoolBackend {
    fn name(&self) -> &'static str {
        "x11_xdotool"
    }

    fn apply(&self, action: &InputAction) -> InputAck {
        let result = match action {
            InputAction::MouseMove { x, y } => {
                let args = vec!["mousemove".to_string(), x.to_string(), y.to_string()];
                run_command(self.program.as_path(), &self.display, &args)
            }
            InputAction::MouseButton { button, pressed } => match map_mouse_button(button) {
                Some(mapped) => {
                    let verb = if *pressed { "mousedown" } else { "mouseup" };
                    let args = vec![verb.to_string(), mapped.to_string()];
                    run_command(self.program.as_path(), &self.display, &args)
                }
                None => Err(format!(
                    "unsupported mouse button '{button}'; supported buttons: left, middle, right"
                )),
            },
            InputAction::KeyPress { key } => match map_key(key) {
                Some(mapped) => {
                    let args = vec!["keydown".to_string(), mapped.to_string()];
                    run_command(self.program.as_path(), &self.display, &args)
                }
                None => Err(format!("unsupported key '{key}' for xdotool backend")),
            },
            InputAction::KeyRelease { key } => match map_key(key) {
                Some(mapped) => {
                    let args = vec!["keyup".to_string(), mapped.to_string()];
                    run_command(self.program.as_path(), &self.display, &args)
                }
                None => Err(format!("unsupported key '{key}' for xdotool backend")),
            },
            InputAction::Text { text } => {
                if text.is_empty() {
                    Ok("ignored empty text input".to_string())
                } else {
                    let args = vec![
                        "type".to_string(),
                        "--delay".to_string(),
                        "0".to_string(),
                        text.clone(),
                    ];
                    run_command(self.program.as_path(), &self.display, &args)
                }
            }
            InputAction::Scroll { delta_x, delta_y } => {
                run_scroll(self.program.as_path(), &self.display, *delta_x, *delta_y)
            }
        };

        match result {
            Ok(note) => InputAck {
                accepted: true,
                stubbed: false,
                note,
            },
            Err(note) => InputAck {
                accepted: false,
                stubbed: false,
                note,
            },
        }
    }
}

fn run_scroll(program: &Path, display: &str, delta_x: i32, delta_y: i32) -> Result<String, String> {
    if delta_x == 0 && delta_y == 0 {
        return Ok("ignored zero scroll input".to_string());
    }

    let mut segments = Vec::new();
    if delta_y != 0 {
        let button = if delta_y > 0 { "4" } else { "5" };
        let repeat = delta_y.unsigned_abs();
        let args = vec![
            "click".to_string(),
            "--repeat".to_string(),
            repeat.to_string(),
            button.to_string(),
        ];
        run_command(program, display, &args)?;
        segments.push(format!("vertical={delta_y}"));
    }
    if delta_x != 0 {
        let button = if delta_x > 0 { "7" } else { "6" };
        let repeat = delta_x.unsigned_abs();
        let args = vec![
            "click".to_string(),
            "--repeat".to_string(),
            repeat.to_string(),
            button.to_string(),
        ];
        run_command(program, display, &args)?;
        segments.push(format!("horizontal={delta_x}"));
    }

    Ok(format!(
        "executed native scroll via {} on DISPLAY {} ({})",
        program.display(),
        display,
        segments.join(", ")
    ))
}

fn run_command(program: &Path, display: &str, args: &[String]) -> Result<String, String> {
    let output = Command::new(program)
        .env("DISPLAY", display)
        .args(args)
        .output()
        .map_err(|error| format!("failed to launch {}: {error}", program.display()))?;

    if output.status.success() {
        Ok(format!(
            "executed native input via {} on DISPLAY {}",
            program.display(),
            display
        ))
    } else {
        let stderr = String::from_utf8_lossy(&output.stderr);
        let detail = stderr.trim();
        Err(if detail.is_empty() {
            format!("{} exited with status {}", program.display(), output.status)
        } else {
            format!("{} failed: {}", program.display(), detail)
        })
    }
}

fn map_mouse_button(button: &str) -> Option<&'static str> {
    if button.eq_ignore_ascii_case("left") {
        Some("1")
    } else if button.eq_ignore_ascii_case("middle") {
        Some("2")
    } else if button.eq_ignore_ascii_case("right") {
        Some("3")
    } else {
        None
    }
}

fn map_key(key: &str) -> Option<&str> {
    match key {
        " " => Some("space"),
        key if key.eq_ignore_ascii_case("enter") => Some("Return"),
        key if key.eq_ignore_ascii_case("tab") => Some("Tab"),
        key if key.eq_ignore_ascii_case("backspace") => Some("BackSpace"),
        key if key.eq_ignore_ascii_case("escape") || key.eq_ignore_ascii_case("esc") => {
            Some("Escape")
        }
        key if key.eq_ignore_ascii_case("delete") => Some("Delete"),
        key if key.eq_ignore_ascii_case("home") => Some("Home"),
        key if key.eq_ignore_ascii_case("end") => Some("End"),
        key if key.eq_ignore_ascii_case("page_up") || key.eq_ignore_ascii_case("pageup") => {
            Some("Page_Up")
        }
        key if key.eq_ignore_ascii_case("page_down") || key.eq_ignore_ascii_case("pagedown") => {
            Some("Page_Down")
        }
        key if key.eq_ignore_ascii_case("up") || key.eq_ignore_ascii_case("arrow_up") => Some("Up"),
        key if key.eq_ignore_ascii_case("down") || key.eq_ignore_ascii_case("arrow_down") => {
            Some("Down")
        }
        key if key.eq_ignore_ascii_case("left") || key.eq_ignore_ascii_case("arrow_left") => {
            Some("Left")
        }
        key if key.eq_ignore_ascii_case("right") || key.eq_ignore_ascii_case("arrow_right") => {
            Some("Right")
        }
        key if key.eq_ignore_ascii_case("shift") => Some("Shift_L"),
        key if key.eq_ignore_ascii_case("ctrl") || key.eq_ignore_ascii_case("control") => {
            Some("Control_L")
        }
        key if key.eq_ignore_ascii_case("alt") => Some("Alt_L"),
        key if key.eq_ignore_ascii_case("meta")
            || key.eq_ignore_ascii_case("super")
            || key.eq_ignore_ascii_case("win") =>
        {
            Some("Super_L")
        }
        key if is_simple_key(key) => Some(key),
        _ => None,
    }
}

fn is_simple_key(key: &str) -> bool {
    key.len() == 1 && key.is_ascii()
}
