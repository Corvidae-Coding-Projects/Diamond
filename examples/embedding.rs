//! A real native consumer of the compiler, evaluator, and session APIs.

use anyhow::{Context, Result, ensure};
use std::time::Duration;

fn main() -> Result<()> {
    let Err(diamond::Error::Diagnostic { code, location, .. }) =
        diamond::compile("fn main() -> Int:\n  1__2\n")
    else {
        anyhow::bail!("invalid integer did not produce a source diagnostic");
    };
    ensure!(code == "D004", "unexpected diagnostic category: {code}");
    ensure!(
        (location.line(), location.column()) == (2, 4),
        "unexpected public source location: {location}"
    );
    let program = diamond::compile("fn main() -> Int:\n  (7 + 5) * 3\n")
        .context("compile embedded source")?;
    let answer = program
        .evaluate()
        .context("evaluate checked program")?
        .display_text()
        .context("display result")?;
    ensure!(answer == "36", "incorrect evaluation: {answer}");
    let mut sessions = diamond::SessionStore::default();
    let (token, csrf) = sessions
        .issue("alice", &["editor"], Duration::from_mins(1))
        .context("issue trusted-host session")?;
    ensure!(
        sessions.is_valid(token.cookie_value()),
        "issued session is invalid"
    );
    ensure!(
        sessions.check_csrf(token.cookie_value(), &csrf),
        "session CSRF binding failed"
    );
    ensure!(
        !sessions.check_csrf(token.cookie_value(), "invalid"),
        "invalid CSRF accepted"
    );
    ensure!(
        format!("{token:?}") == "SessionToken([REDACTED])",
        "session token debug disclosure"
    );
    sessions.revoke(token.cookie_value());
    ensure!(
        !sessions.is_valid(token.cookie_value()),
        "revoked session remains valid"
    );
    let (expiring, expiring_csrf) = sessions
        .issue("alice", &[], Duration::from_millis(1))
        .context("issue short-lived session")?;
    std::thread::sleep(Duration::from_millis(5));
    ensure!(
        !sessions.is_valid(expiring.cookie_value()),
        "expired session remains valid"
    );
    ensure!(
        !sessions.check_csrf(expiring.cookie_value(), &expiring_csrf),
        "expired session CSRF remains valid"
    );
    println!(
        "embedding: diagnostic spans, checked evaluation, session issuance, CSRF binding, redaction, revocation, and expiration passed"
    );
    Ok(())
}
