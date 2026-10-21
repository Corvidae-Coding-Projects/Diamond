//! Public session consumer observing a child-local realtime fault and real elapsed time.

use diamond::SessionStore;
use std::path::PathBuf;
use std::time::{Duration, Instant, SystemTime};

fn signed_elapsed(before: SystemTime, after: SystemTime) -> f64 {
    match after.duration_since(before) {
        Ok(duration) => duration.as_secs_f64(),
        Err(error) => -error.duration().as_secs_f64(),
    }
}

fn main() -> Result<(), Box<dyn std::error::Error>> {
    let mut arguments = std::env::args_os().skip(1);
    let mode = arguments.next().ok_or("missing mode")?;
    let marker = PathBuf::from(arguments.next().ok_or("missing marker")?);
    let backwards = match mode.to_str() {
        Some("forward") => false,
        Some("backward") => true,
        _ => return Err("unknown mode".into()),
    };
    if marker.exists() {
        return Err("fault marker already exists".into());
    }
    let mut sessions = SessionStore::default();
    let wall_before = SystemTime::now();
    let elapsed_before = Instant::now();
    let (token, csrf) = sessions.issue("clock-observer", &[], Duration::from_millis(80))?;
    let issued_valid = sessions.is_valid(token.cookie_value());
    if backwards {
        std::thread::sleep(Duration::from_millis(120));
    }
    let before_fault_valid = sessions.is_valid(token.cookie_value());
    std::fs::write(&marker, b"arm this child's clock wrapper")?;
    let wall_after = SystemTime::now();
    let elapsed_seconds = elapsed_before.elapsed().as_secs_f64();
    let after_fault_valid = sessions.is_valid(token.cookie_value());
    let after_fault_csrf = sessions.check_csrf(token.cookie_value(), &csrf);
    println!(
        "{{\"issued_valid\":{issued_valid},\"before_fault_valid\":{before_fault_valid},\"after_fault_valid\":{after_fault_valid},\"after_fault_csrf\":{after_fault_csrf},\"wall_elapsed_seconds\":{},\"monotonic_elapsed_seconds\":{elapsed_seconds}}}",
        signed_elapsed(wall_before, wall_after)
    );
    Ok(())
}
