//! Streaming range transport; callers own retries, identity checks and persistence.

use reqwest::{header, Client};
use std::{collections::HashMap, future::Future, time::Duration};

pub struct RangeClient {
    client: Client,
}

pub struct RangeResponse {
    pub status: u16,
    pub headers: HashMap<String, String>,
    response: reqwest::Response,
}

impl RangeClient {
    pub fn new(connect_seconds: f64, read_seconds: f64) -> Result<Self, String> {
        let connect = Duration::try_from_secs_f64(connect_seconds)
            .ok()
            .filter(|timeout| !timeout.is_zero())
            .ok_or_else(|| "invalid connection timeout".to_string())?;
        let read = Duration::try_from_secs_f64(read_seconds)
            .ok()
            .filter(|timeout| !timeout.is_zero())
            .ok_or_else(|| "invalid read timeout".to_string())?;
        let _ = rustls::crypto::ring::default_provider().install_default();
        let client = Client::builder()
            .user_agent("sharpmod")
            .pool_max_idle_per_host(32)
            .connect_timeout(connect)
            .read_timeout(read)
            .redirect(reqwest::redirect::Policy::limited(30))
            .retry(reqwest::retry::never())
            .build()
            .map_err(|error| error.to_string())?;
        Ok(Self { client })
    }

    pub async fn get(
        &self,
        url: &str,
        headers: &[(String, String)],
        cancel: impl Fn() -> bool,
    ) -> Result<RangeResponse, String> {
        if cancel() {
            return Err("__cancelled__".into());
        }
        let mut request = self.client.get(url);
        for (name, value) in headers {
            request = request.header(name, value);
        }
        let mut request = request.build().map_err(|error| error.to_string())?;
        request.headers_mut().insert(
            header::ACCEPT_ENCODING,
            header::HeaderValue::from_static("identity"),
        );
        let response = cancellable(self.client.execute(request), &cancel).await?;
        let headers = ["content-range", "etag", "last-modified", "retry-after"]
            .into_iter()
            .filter_map(|name| {
                response
                    .headers()
                    .get(name)?
                    .to_str()
                    .ok()
                    .map(|value| (name.to_string(), value.to_string()))
            })
            .collect();
        Ok(RangeResponse {
            status: response.status().as_u16(),
            headers,
            response,
        })
    }
}

impl RangeResponse {
    pub async fn chunk(&mut self, cancel: impl Fn() -> bool) -> Result<Option<Vec<u8>>, String> {
        let chunk = cancellable(self.response.chunk(), &cancel).await?;
        Ok(chunk.map(|bytes| bytes.to_vec()))
    }
}

async fn cancellable<T>(
    future: impl Future<Output = Result<T, reqwest::Error>>,
    cancel: &impl Fn() -> bool,
) -> Result<T, String> {
    tokio::pin!(future);
    loop {
        if cancel() {
            return Err("__cancelled__".into());
        }
        tokio::select! {
            result = &mut future => {
                return if cancel() {
                    Err("__cancelled__".into())
                } else {
                    result.map_err(|error| error.to_string())
                };
            }
            _ = tokio::time::sleep(Duration::from_millis(50)) => {}
        }
    }
}

#[cfg(test)]
mod tests;
