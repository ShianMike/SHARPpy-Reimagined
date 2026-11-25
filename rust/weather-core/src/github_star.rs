//! GitHub repository star helper for explicit user actions in desktop builds.

use std::time::Duration;

use reqwest::header;

pub const GITHUB_REPO_OWNER: &str = "ShianMike";
pub const GITHUB_REPO_NAME: &str = "SHARPpy-Reimagined";
pub const GITHUB_REPO_URL: &str = "https://github.com/ShianMike/SHARPpy-Reimagined";
const GITHUB_API_BASE: &str = "https://api.github.com";
const REQUEST_TIMEOUT: Duration = Duration::from_secs(10);

#[derive(Debug, Clone, PartialEq, Eq)]
pub enum StarApiOutcome {
    Starred,
    Unauthorized,
    Failed(String),
}

/// Validates and trims a bearer token, rejecting empty, whitespace-containing, or control inputs.
pub fn sanitize_token(raw: &str) -> Option<String> {
    let trimmed = raw.trim();
    if trimmed.is_empty() || trimmed.chars().any(|c| c.is_control() || c.is_whitespace()) {
        return None;
    }
    Some(trimmed.to_owned())
}

/// Stars `ShianMike/SHARPpy-Reimagined` on GitHub using an explicit bearer credential.
pub async fn star_repository_with_token(token: &str) -> StarApiOutcome {
    star_repository_against(GITHUB_API_BASE, token, REQUEST_TIMEOUT).await
}

/// Stars the repository against `api_base` (`https://api.github.com` in production, loopback in tests).
pub async fn star_repository_against(
    api_base: &str,
    token: &str,
    timeout: Duration,
) -> StarApiOutcome {
    let Some(token) = sanitize_token(token) else {
        return StarApiOutcome::Unauthorized;
    };
    let _ = rustls::crypto::ring::default_provider().install_default();
    let client = match reqwest::Client::builder()
        .timeout(timeout)
        .user_agent("SHARPpy-Reimagined")
        .redirect(reqwest::redirect::Policy::none())
        .build()
    {
        Ok(client) => client,
        Err(error) => return StarApiOutcome::Failed(error.to_string()),
    };
    let url = format!(
        "{}/user/starred/{GITHUB_REPO_OWNER}/{GITHUB_REPO_NAME}",
        api_base.trim_end_matches('/')
    );
    let response = match client
        .put(&url)
        .header(header::AUTHORIZATION, format!("Bearer {token}"))
        .header(header::ACCEPT, "application/vnd.github+json")
        .header("X-GitHub-Api-Version", "2022-11-28")
        .header(header::CONTENT_LENGTH, "0")
        .send()
        .await
    {
        Ok(response) => response,
        Err(error) => return StarApiOutcome::Failed(error.to_string()),
    };
    match response.status().as_u16() {
        200 | 204 => StarApiOutcome::Starred,
        401 | 403 | 404 => StarApiOutcome::Unauthorized,
        code => StarApiOutcome::Failed(format!("GitHub returned HTTP {code}")),
    }
}

#[cfg(test)]
mod tests {
    use super::*;
    use std::{
        collections::HashMap,
        io::{BufRead, BufReader, Write},
        net::TcpListener,
        thread,
        time::Duration,
    };

    type CapturedRequest = (String, HashMap<String, String>);

    fn serve_once(status_line: &str) -> (String, thread::JoinHandle<CapturedRequest>) {
        let listener = TcpListener::bind("127.0.0.1:0").unwrap();
        let addr = format!("http://{}", listener.local_addr().unwrap());
        let response =
            format!("HTTP/1.1 {status_line}\r\nContent-Length: 0\r\nConnection: close\r\n\r\n");
        let handle = thread::spawn(move || {
            let (mut stream, _) = listener.accept().unwrap();
            let mut reader = BufReader::new(stream.try_clone().unwrap());
            let mut request_line = String::new();
            reader.read_line(&mut request_line).unwrap();
            let mut headers = HashMap::new();
            let mut line = String::new();
            loop {
                line.clear();
                let n = reader.read_line(&mut line).unwrap();
                if n == 0 || line == "\r\n" || line == "\n" {
                    break;
                }
                if let Some((k, v)) = line.split_once(':') {
                    headers.insert(k.trim().to_ascii_lowercase(), v.trim().to_owned());
                }
            }
            stream.write_all(response.as_bytes()).unwrap();
            stream.flush().unwrap();
            (request_line.trim_end().to_owned(), headers)
        });
        (addr, handle)
    }

    #[test]
    fn sanitize_token_rejects_blank_control_and_internal_whitespace() {
        assert_eq!(sanitize_token(""), None);
        assert_eq!(sanitize_token("   \n\t "), None);
        assert_eq!(sanitize_token("bad\ntoken"), None);
        assert_eq!(sanitize_token("bad\rtoken"), None);
        assert_eq!(sanitize_token("bad token"), None);
        assert_eq!(
            sanitize_token("  ghp_valid123 \n"),
            Some("ghp_valid123".into())
        );
    }

    #[tokio::test]
    async fn star_repository_maps_204_and_sends_expected_headers() {
        let (base, server) = serve_once("204 No Content");
        let outcome =
            star_repository_against(&base, "  test-token-123 ", Duration::from_secs(2)).await;
        assert_eq!(outcome, StarApiOutcome::Starred);
        let (request_line, headers) = server.join().unwrap();
        assert_eq!(
            request_line,
            "PUT /user/starred/ShianMike/SHARPpy-Reimagined HTTP/1.1"
        );
        assert_eq!(
            headers.get("authorization").map(String::as_str),
            Some("Bearer test-token-123")
        );
        assert_eq!(
            headers.get("accept").map(String::as_str),
            Some("application/vnd.github+json")
        );
        assert_eq!(
            headers.get("x-github-api-version").map(String::as_str),
            Some("2022-11-28")
        );
        assert_eq!(headers.get("content-length").map(String::as_str), Some("0"));
    }

    #[tokio::test]
    async fn star_repository_maps_unauthorized_and_failures() {
        for status in ["401 Unauthorized", "403 Forbidden", "404 Not Found"] {
            let (base, server) = serve_once(status);
            let outcome = star_repository_against(&base, "token", Duration::from_secs(2)).await;
            assert_eq!(outcome, StarApiOutcome::Unauthorized, "{status}");
            let _ = server.join().unwrap();
        }
        let (base, server) = serve_once("500 Internal Server Error");
        let outcome = star_repository_against(&base, "token", Duration::from_secs(2)).await;
        assert!(matches!(outcome, StarApiOutcome::Failed(_)));
        let _ = server.join().unwrap();
    }

    #[tokio::test]
    async fn star_repository_rejects_invalid_token_before_connecting() {
        let listener = TcpListener::bind("127.0.0.1:0").unwrap();
        listener.set_nonblocking(true).unwrap();
        let base = format!("http://{}", listener.local_addr().unwrap());
        let outcome = star_repository_against(&base, "bad\ntoken", Duration::from_secs(2)).await;
        assert_eq!(outcome, StarApiOutcome::Unauthorized);
        assert!(listener.accept().is_err());
    }
}
