use burrow_rd_host::BurrowRdHost;
use burrow_rd_host::ipc::{
    MIN_COMPAT_PROTOCOL_VERSION, PROTOCOL_VERSION, RequestEnvelope, ResponseEnvelope,
};
use std::io::{self, BufRead, Write};

fn main() {
    if let Err(error) = run() {
        let _ = writeln!(io::stderr(), "burrow-rd-host error: {error}");
        std::process::exit(1);
    }
}

fn run() -> Result<(), Box<dyn std::error::Error>> {
    let stdin = io::stdin();
    let mut stdout = io::stdout().lock();
    let mut host = BurrowRdHost::new();

    for line in stdin.lock().lines() {
        let line = line?;
        if line.trim().is_empty() {
            continue;
        }

        let response = match serde_json::from_str::<RequestEnvelope>(&line) {
            Ok(request) => host.handle_request(request),
            Err(error) => ResponseEnvelope {
                id: None,
                protocol_version: PROTOCOL_VERSION,
                min_compatible_protocol_version: MIN_COMPAT_PROTOCOL_VERSION,
                ok: false,
                result: None,
                error: Some(burrow_rd_host::ipc::ErrorPayload {
                    code: "invalid_json".to_string(),
                    message: error.to_string(),
                }),
                warnings: Vec::new(),
            },
        };

        serde_json::to_writer(&mut stdout, &response)?;
        stdout.write_all(b"\n")?;
        stdout.flush()?;
    }

    Ok(())
}
