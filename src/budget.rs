//! One absolute deadline propagated across async work and owned blocking tasks.
use crate::error::{Error, Result};
use std::{
    cell::Cell,
    future::Future,
    time::{Duration, Instant},
};
tokio::task_local! { static ASYNC_DEADLINE: Instant; }
thread_local! { static SYNC_DEADLINE: Cell<Option<Instant>> = const { Cell::new(None) }; }
thread_local! { static SYNC_CLEANUP_MS: Cell<u64> = const { Cell::new(200) }; }
pub(crate) fn cleanup_timeout() -> Duration {
    Duration::from_millis(SYNC_CLEANUP_MS.get())
}
pub(crate) fn current() -> Option<Instant> {
    ASYNC_DEADLINE
        .try_with(|value| *value)
        .ok()
        .or_else(|| SYNC_DEADLINE.get())
}
pub(crate) fn check() -> Result<()> {
    remaining(Duration::from_secs(1)).map(|_| ())
}
pub(crate) fn remaining(cap: Duration) -> Result<Duration> {
    match current() {
        Some(until) => until
            .checked_duration_since(Instant::now())
            .filter(|left| !left.is_zero())
            .map(|left| left.min(cap))
            .ok_or(Error::Limit("request deadline")),
        None => Ok(cap),
    }
}
pub(crate) fn clamp(until: Instant) -> Instant {
    current().map_or(until, |request| request.min(until))
}
pub(crate) fn until(cap: Duration) -> tokio::time::Instant {
    tokio::time::Instant::from_std(clamp(Instant::now() + cap))
}
pub(crate) async fn scope<T>(deadline: Instant, work: impl Future<Output = T>) -> T {
    ASYNC_DEADLINE.scope(deadline, work).await
}
pub(crate) fn sync<T>(deadline: Option<Instant>, cleanup_ms: u64, work: impl FnOnce() -> T) -> T {
    struct Restore(Option<Instant>, u64);
    impl Drop for Restore {
        fn drop(&mut self) {
            SYNC_DEADLINE.set(self.0);
            SYNC_CLEANUP_MS.set(self.1);
        }
    }
    let _restore = Restore(
        SYNC_DEADLINE.replace(deadline),
        SYNC_CLEANUP_MS.replace(cleanup_ms),
    );
    work()
}
pub(crate) fn response(
    response: axum::response::Response,
    deadline: Instant,
) -> axum::response::Response {
    use futures_util::StreamExt as _;
    let (parts, body) = response.into_parts();
    let stream = futures_util::stream::unfold(
        (body.into_data_stream(), false),
        move |(mut body, done)| async move {
            if done {
                return None;
            }
            // Timeout polls an immediately-ready future before its timer. Check
            // the absolute time too, so buffered output cannot bypass expiry.
            let next = if Instant::now() < deadline {
                tokio::time::timeout_at(tokio::time::Instant::from_std(deadline), body.next())
                    .await
                    .ok()
            } else {
                None
            };
            match next {
                Some(Some(Ok(bytes))) => Some((Ok(bytes), (body, false))),
                Some(Some(Err(error))) => Some((Err(std::io::Error::other(error)), (body, true))),
                Some(None) => None,
                None => Some((
                    Err(std::io::Error::new(
                        std::io::ErrorKind::TimedOut,
                        "response deadline",
                    )),
                    (body, true),
                )),
            }
        },
    );
    axum::response::Response::from_parts(parts, axum::body::Body::from_stream(stream))
}

#[cfg(test)]
mod tests {
    use super::*;
    use futures_util::StreamExt as _;

    #[tokio::test]
    async fn expired_stream_cannot_send_already_buffered_output() {
        let ready = axum::response::Response::new(axum::body::Body::from("buffered output"));
        let expired = response(ready, Instant::now());
        let mut stream = expired.into_body().into_data_stream();
        assert!(stream.next().await.is_some_and(|chunk| chunk.is_err()));
        assert!(stream.next().await.is_none());
    }
}
