//! Real public-API consumer for session lifecycle and typed route URL behavior.

use diamond::{Error, SessionStore};
use std::time::Duration;

fn require(condition: bool, message: &str) -> Result<(), Box<dyn std::error::Error>> {
    if condition {
        Ok(())
    } else {
        Err(message.into())
    }
}

fn invalid_inputs() -> Result<(), Box<dyn std::error::Error>> {
    let mut sessions = SessionStore::default();
    let lifetime = Duration::from_mins(1);
    let invalid = [
        "",
        "has space",
        "slash/name",
        "雪",
        "comma,name",
        "line\nname",
    ];
    for identity in invalid {
        require(
            sessions.issue(identity, &[], lifetime).is_err(),
            "invalid identity accepted",
        )?;
        require(
            sessions.issue("alice", &[identity], lifetime).is_err(),
            "invalid role accepted",
        )?;
    }
    require(
        sessions.issue(&"a".repeat(129), &[], lifetime).is_err(),
        "overlong identity accepted",
    )?;
    require(
        sessions
            .issue("alice", &[&"r".repeat(129)], lifetime)
            .is_err(),
        "overlong role accepted",
    )?;
    for rejected in [Duration::ZERO, Duration::from_secs(86_401), Duration::MAX] {
        require(
            sessions.issue("alice", &[], rejected).is_err(),
            "invalid lifetime accepted",
        )?;
    }
    require(
        sessions
            .issue(
                &"a".repeat(128),
                &[&"r".repeat(128)],
                Duration::from_hours(24),
            )
            .is_ok(),
        "maximum legal identity/role/lifetime rejected",
    )?;
    Ok(())
}

fn lifecycle() -> Result<(), Box<dyn std::error::Error>> {
    let mut sessions = SessionStore::default();
    let lifetime = Duration::from_mins(1);
    let (alice, alice_csrf) =
        sessions.issue("alice-01", &["editor", "editor", "member_2"], lifetime)?;
    let (bob, bob_csrf) = sessions.issue("bob", &[], lifetime)?;
    require(
        alice.cookie_value() != bob.cookie_value(),
        "distinct sessions share token",
    )?;
    require(alice_csrf != bob_csrf, "distinct sessions share CSRF")?;
    require(
        format!("{alice:?}") == "SessionToken([REDACTED])",
        "debug exposes token",
    )?;
    require(
        sessions.is_valid(alice.cookie_value()),
        "valid token rejected",
    )?;
    require(
        sessions.check_csrf(alice.cookie_value(), &alice_csrf),
        "correct CSRF rejected",
    )?;
    require(
        !sessions.check_csrf(alice.cookie_value(), &bob_csrf),
        "CSRF crosses sessions",
    )?;
    require(
        !sessions.check_csrf(bob.cookie_value(), &alice_csrf),
        "CSRF crosses sessions in reverse",
    )?;
    for token in [
        String::new(),
        "g".repeat(64),
        "a".repeat(63),
        "a".repeat(65),
        "a".repeat(64),
        "雪".repeat(64),
    ] {
        require(!sessions.is_valid(&token), "unissued token accepted")?;
        require(
            !sessions.check_csrf(&token, &alice_csrf),
            "invalid session CSRF accepted",
        )?;
        require(
            !sessions.check_csrf(alice.cookie_value(), &token),
            "invalid CSRF accepted",
        )?;
        sessions.revoke(&token);
    }
    sessions.revoke(alice.cookie_value());
    require(
        !sessions.is_valid(alice.cookie_value()),
        "revoked token valid",
    )?;
    require(
        !sessions.check_csrf(alice.cookie_value(), &alice_csrf),
        "revoked CSRF valid",
    )?;
    require(
        sessions.is_valid(bob.cookie_value()),
        "revocation affected another session",
    )?;
    let (rotated, rotated_csrf) = sessions.issue("alice-01", &["editor"], lifetime)?;
    require(
        rotated.cookie_value() != alice.cookie_value(),
        "reissued token was not fresh",
    )?;
    require(
        !sessions.check_csrf(rotated.cookie_value(), &alice_csrf),
        "reissued session accepts old CSRF",
    )?;
    require(
        sessions.check_csrf(rotated.cookie_value(), &rotated_csrf),
        "reissued CSRF invalid",
    )?;

    let (short, short_csrf) = sessions.issue("short", &[], Duration::from_millis(2))?;
    std::thread::sleep(Duration::from_millis(15));
    require(
        !sessions.is_valid(short.cookie_value()),
        "expired token valid",
    )?;
    require(
        !sessions.check_csrf(short.cookie_value(), &short_csrf),
        "expired CSRF valid",
    )?;
    Ok(())
}

fn capacity() -> Result<(), Box<dyn std::error::Error>> {
    let lifetime = Duration::from_mins(1);
    let mut full = SessionStore::default();
    let mut retained = Vec::with_capacity(4096);
    for _ in 0..4096 {
        retained.push(full.issue("capacity", &[], lifetime)?);
    }
    require(
        matches!(full.issue("capacity", &[], lifetime), Err(Error::Limit(_))),
        "capacity not enforced",
    )?;
    full.revoke(retained[113].0.cookie_value());
    require(
        full.issue("replacement", &[], lifetime).is_ok(),
        "revoked capacity not reclaimed",
    )?;
    require(
        matches!(full.issue("capacity", &[], lifetime), Err(Error::Limit(_))),
        "capacity escaped after replacement",
    )?;
    full.revoke(retained[271].0.cookie_value());
    let (expiring, _) = full.issue("expiring", &[], Duration::from_millis(1))?;
    std::thread::sleep(Duration::from_millis(10));
    require(
        !full.is_valid(expiring.cookie_value()),
        "capacity session did not expire",
    )?;
    require(
        full.issue("reclaimed", &[], lifetime).is_ok(),
        "expired capacity not reclaimed",
    )?;
    require(
        matches!(full.issue("capacity", &[], lifetime), Err(Error::Limit(_))),
        "reclaimed capacity escaped bound",
    )?;
    Ok(())
}

fn typed_urls() -> Result<(), Box<dyn std::error::Error>> {
    let source = "type ItemId = Id<\"item\">\npublic route GET \"/items/{id:ItemId}\" as Item:\n  \"item\"\nfn main() -> Html:\n  html:\n    <a href={route_url(Item, ItemId(\"a-b_7\"))}>item</a>\n";
    let rendered = diamond::compile(source)?.evaluate()?.display_text()?;
    require(
        rendered.trim() == "<a href=\"/items/a-b_7\">item</a>",
        "typed URL render differs",
    )?;
    Ok(())
}

fn main() -> Result<(), Box<dyn std::error::Error>> {
    invalid_inputs()?;
    lifecycle()?;
    capacity()?;
    typed_urls()?;
    println!(
        "{{\"status\":\"PASS\",\"invalid_identity_cases\":14,\"invalid_lifetimes\":3,\"malformed_token_families\":6,\"session_capacity\":4096,\"real_expiry_checks\":2,\"typed_route_urls\":1}}"
    );
    Ok(())
}
