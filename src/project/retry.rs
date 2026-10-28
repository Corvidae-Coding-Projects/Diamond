//! Confirmed-abort retries constrained by request deadlines and a node-wide quota.
use super::{CheckedProject, input, rpc};
use crate::{
    authority::Effect,
    error::{Error, Result},
};
use serde::{Deserialize, Serialize};
use std::{
    sync::{
        Mutex,
        atomic::{AtomicU64, Ordering},
    },
    time::{Duration, Instant},
};

#[derive(Clone, Copy, Debug, Deserialize, Serialize)]
#[serde(default, deny_unknown_fields)]
pub(super) struct Config {
    pub max_retries: u32,
    pub window_ms: u64,
    pub backoff_ms: u64,
    pub max_backoff_ms: u64,
    pub jitter_ms: u64,
    pub budget_capacity: u32,
    pub budget_refill_per_second: u32,
    pub client_retries: u32,
    pub client_max_backoff_ms: u64,
    pub client_jitter_ms: u64,
    pub retry_after_seconds: u32,
}
impl Default for Config {
    fn default() -> Self {
        Self {
            max_retries: 2,
            window_ms: 2000,
            backoff_ms: 2,
            max_backoff_ms: 50,
            jitter_ms: 8,
            budget_capacity: 32,
            budget_refill_per_second: 8,
            client_retries: 2,
            client_max_backoff_ms: 4000,
            client_jitter_ms: 250,
            retry_after_seconds: 1,
        }
    }
}
impl Config {
    pub(super) fn checked(self) -> Result<()> {
        if self.max_retries > 5
            || !(1..=30_000).contains(&self.window_ms)
            || !(1..=1000).contains(&self.backoff_ms)
            || self.max_backoff_ms < self.backoff_ms
            || self.max_backoff_ms > 5000
            || self.jitter_ms > 1000
            || self.budget_capacity > 4096
            || self.budget_refill_per_second > 4096
            || self.client_retries > 5
            || !(100..=30_000).contains(&self.client_max_backoff_ms)
            || self.client_jitter_ms > 5000
            || !(1..=30).contains(&self.retry_after_seconds)
        {
            return Err(input("invalid runtime retry bounds"));
        }
        Ok(())
    }
}
#[derive(Debug)]
struct Tokens {
    tokens: f64,
    updated: Instant,
}
#[derive(Debug)]
pub(super) struct Budget {
    pub config: Config,
    tokens: Mutex<Tokens>,
    pub denied: AtomicU64,
}
impl Budget {
    pub(super) fn new(config: Config) -> Self {
        Self {
            config,
            tokens: Mutex::new(Tokens {
                tokens: f64::from(config.budget_capacity),
                updated: Instant::now(),
            }),
            denied: AtomicU64::new(0),
        }
    }
    fn take(&self) -> bool {
        let Ok(mut state) = self.tokens.lock() else {
            return false;
        };
        let now = Instant::now();
        state.tokens = (state.tokens
            + now.duration_since(state.updated).as_secs_f64()
                * f64::from(self.config.budget_refill_per_second))
        .min(f64::from(self.config.budget_capacity));
        state.updated = now;
        if state.tokens >= 1.0 {
            state.tokens -= 1.0;
            true
        } else {
            self.denied.fetch_add(1, Ordering::Relaxed);
            false
        }
    }
}
pub(super) fn eligible(project: &CheckedProject, operation: &rpc::Operation) -> bool {
    !operation.action
        && project.server.storage.is_some()
        && project.server.functions[&operation.function]
            .effects
            .iter()
            .all(|effect| matches!(effect, Effect::Read(_) | Effect::Write(_)))
}
pub(super) fn run<T>(
    eligible: bool,
    budget: &Budget,
    counter: &AtomicU64,
    mut attempt: impl FnMut() -> Result<T>,
) -> Result<T> {
    let until =
        crate::budget::clamp(Instant::now() + Duration::from_millis(budget.config.window_ms));
    for retry in 0..=budget.config.max_retries {
        crate::budget::check()?;
        match attempt() {
            Err(Error::Conflict) if eligible && retry < budget.config.max_retries => {
                let mut random = [0_u8; 8];
                getrandom::fill(&mut random).map_err(|_| Error::Random)?;
                let jitter = if budget.config.jitter_ms == 0 {
                    0
                } else {
                    u64::from_le_bytes(random) % budget.config.jitter_ms
                };
                let delay = Duration::from_millis(
                    (budget.config.backoff_ms.saturating_mul(1_u64 << retry) + jitter)
                        .min(budget.config.max_backoff_ms),
                );
                if Instant::now()
                    .checked_add(delay)
                    .is_none_or(|next| next >= until)
                    || !budget.take()
                {
                    return Err(Error::Conflict);
                }
                counter.fetch_add(1, Ordering::Relaxed);
                std::thread::sleep(delay);
            }
            result => return result,
        }
    }
    Err(Error::Conflict)
}
#[cfg(test)]
mod tests {
    use super::*;
    #[test]
    fn quota_is_shared_and_unknown_outcomes_never_replay() {
        let budget = Budget::new(Config {
            budget_capacity: 1,
            budget_refill_per_second: 0,
            ..Config::default()
        });
        let counter = AtomicU64::new(0);
        for expected in [2, 1] {
            let mut attempts = 0;
            let result: Result<()> = run(true, &budget, &counter, || {
                attempts += 1;
                Err(Error::Conflict)
            });
            assert!(matches!(result, Err(Error::Conflict)));
            assert_eq!(attempts, expected);
        }
        assert_eq!(counter.load(Ordering::Relaxed), 1);
        let mut attempts = 0;
        let result: Result<()> = run(true, &budget, &counter, || {
            attempts += 1;
            Err(Error::OutcomeUnknown(String::new()))
        });
        assert!(matches!(result, Err(Error::OutcomeUnknown(_))));
        assert_eq!(attempts, 1);
    }
}
