//! Pooled GRIB/index transfers with per-read timeouts, retries and cancellation.

use reqwest::{header, Client, Url};
use std::{future::Future, sync::OnceLock, time::Duration};
use time::{format_description::well_known::Rfc2822, OffsetDateTime};

#[derive(Debug, PartialEq)]
pub enum Error {
    Cancelled,
    Offline(String),
    Unavailable(u16),
    Http(u16),
    Budget,
    Invalid(String),
}

pub struct ModelClient {
    client: Result<Client, String>,
}

impl Default for ModelClient {
    fn default() -> Self {
        let _ = rustls::crypto::ring::default_provider().install_default();
        Self {
            client: Client::builder()
                .user_agent("sharpmod")
                .pool_max_idle_per_host(32)
                .redirect(reqwest::redirect::Policy::limited(30))
                .retry(reqwest::retry::never())
                .build()
                .map_err(|error| error.to_string()),
        }
    }
}

fn cancelled(cancel: &impl Fn() -> Result<bool, String>) -> Result<(), Error> {
    if cancel().map_err(Error::Invalid)? {
        Err(Error::Cancelled)
    } else {
        Ok(())
    }
}

async fn wait<T>(
    future: impl Future<Output = T>,
    timeout: Duration,
    cancel: &impl Fn() -> Result<bool, String>,
) -> Result<T, Error> {
    let deadline = tokio::time::Instant::now()
        .checked_add(timeout)
        .ok_or_else(|| Error::Invalid("model timeout is out of range".into()))?;
    tokio::pin!(future);
    loop {
        cancelled(cancel)?;
        tokio::select! {
            output = &mut future => return Ok(output),
            _ = tokio::time::sleep_until(deadline) => return Err(Error::Offline("request timed out".into())),
            _ = tokio::time::sleep(Duration::from_millis(50)) => {},
        }
    }
}

fn retry_after(value: Option<&header::HeaderValue>) -> Option<Duration> {
    let value = value?.to_str().ok()?.trim();
    if let Ok(seconds) = value.parse::<u64>() {
        return Some(Duration::from_secs(seconds));
    }
    let moment = OffsetDateTime::parse(value, &Rfc2822).ok()?;
    Some(
        (moment - OffsetDateTime::now_utc())
            .try_into()
            .unwrap_or(Duration::ZERO),
    )
}

async fn backoff(
    mut delay: Duration,
    cancel: &impl Fn() -> Result<bool, String>,
) -> Result<(), Error> {
    while !delay.is_zero() {
        cancelled(cancel)?;
        let step = delay.min(Duration::from_millis(50));
        tokio::time::sleep(step).await;
        delay -= step;
    }
    cancelled(cancel)
}

impl ModelClient {
    pub fn shared() -> &'static Self {
        static CLIENT: OnceLock<ModelClient> = OnceLock::new();
        CLIENT.get_or_init(Self::default)
    }

    pub async fn fetch(
        &self,
        url: &str,
        timeout: f64,
        limit: usize,
        byte_range: Option<(u64, u64)>,
        cancel: impl Fn() -> Result<bool, String>,
    ) -> Result<Vec<u8>, Error> {
        cancelled(&cancel)?;
        let url = Url::parse(url).map_err(|error| Error::Invalid(error.to_string()))?;
        if !matches!(url.scheme(), "http" | "https") || url.host_str().is_none() {
            return Err(Error::Invalid("model URL must use HTTP or HTTPS".into()));
        }
        let timeout = Duration::try_from_secs_f64(timeout)
            .ok()
            .filter(|value| !value.is_zero())
            .ok_or_else(|| Error::Invalid("invalid model timeout".into()))?;
        if byte_range.is_some_and(|(start, end)| end < start) {
            return Err(Error::Invalid("invalid model byte range".into()));
        }
        let client = self
            .client
            .as_ref()
            .map_err(|error| Error::Offline(error.clone()))?;
        let mut response = None;
        for attempt in 0..=2 {
            let mut request = client
                .get(url.clone())
                .header(header::ACCEPT_ENCODING, "identity");
            if let Some((start, end)) = byte_range {
                request = request.header(header::RANGE, format!("bytes={start}-{end}"));
            }
            let sent = wait(request.send(), timeout, &cancel).await;
            let reply = match sent {
                Ok(Ok(reply)) => reply,
                Err(Error::Cancelled) => return Err(Error::Cancelled),
                Err(Error::Invalid(message)) => return Err(Error::Invalid(message)),
                result => {
                    let message = match result {
                        Ok(Err(error)) => error.to_string(),
                        Err(Error::Offline(message)) => message,
                        _ => unreachable!("send returned only transport errors"),
                    };
                    if attempt == 2 {
                        return Err(Error::Offline(message));
                    }
                    backoff(
                        if attempt == 0 {
                            Duration::ZERO
                        } else {
                            Duration::from_millis(600)
                        },
                        &cancel,
                    )
                    .await?;
                    continue;
                }
            };
            let status = reply.status().as_u16();
            if matches!(status, 403 | 404) {
                return Err(Error::Unavailable(status));
            }
            let retry = matches!(status, 429 | 500 | 502 | 503 | 504)
                || (status == 413 && reply.headers().contains_key(header::RETRY_AFTER));
            if retry {
                if attempt == 2 {
                    return Err(Error::Offline(format!("HTTP {status} after two retries")));
                }
                let delay = retry_after(reply.headers().get(header::RETRY_AFTER)).unwrap_or(
                    if attempt == 0 {
                        Duration::ZERO
                    } else {
                        Duration::from_millis(600)
                    },
                );
                drop(reply);
                backoff(delay, &cancel).await?;
                continue;
            }
            if status >= 400 {
                return Err(Error::Http(status));
            }
            response = Some(reply);
            break;
        }
        let mut response = response.expect("an attempt returned a response or error");
        if response
            .content_length()
            .is_some_and(|length| length > limit as u64)
        {
            return Err(Error::Budget);
        }
        let mut output = Vec::new();
        loop {
            let chunk = wait(response.chunk(), timeout, &cancel)
                .await?
                .map_err(|error| Error::Offline(format!("while reading: {error}")))?;
            let Some(chunk) = chunk else { break };
            if chunk.len() > limit - output.len() {
                return Err(Error::Budget);
            }
            output.extend_from_slice(&chunk);
        }
        cancelled(&cancel)?;
        Ok(output)
    }
}

#[cfg(test)]
mod tests;
