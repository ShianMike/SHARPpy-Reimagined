//! Explicit user-consented GitHub repository star command (`ShianMike/SHARPpy-Reimagined`).

use std::future::Future;
use std::process::Stdio;
use std::time::Duration;

use serde::{Deserialize, Serialize};
use sharpmod_weather::github_star::{
    sanitize_token, star_repository_with_token, StarApiOutcome, GITHUB_REPO_URL,
};
use tokio::io::AsyncWriteExt;
use tokio::process::Command;

const CREDENTIAL_TIMEOUT: Duration = Duration::from_secs(2);
const BROWSER_OPEN_TIMEOUT: Duration = Duration::from_secs(2);
#[cfg(windows)]
const CREATE_NO_WINDOW: u32 = 0x0800_0000;

#[derive(Debug, Clone, Copy, PartialEq, Eq, Serialize, Deserialize)]
#[serde(rename_all = "camelCase")]
pub enum GitHubStarStatus {
    Starred,
    OpenedBrowser,
}

#[derive(Debug, Clone, PartialEq, Eq, Serialize, Deserialize)]
#[serde(rename_all = "camelCase")]
pub struct GitHubStarResult {
    pub status: GitHubStarStatus,
    pub url: &'static str,
}

fn configure_command(command: &mut Command) {
    command.kill_on_drop(true);
    #[cfg(windows)]
    command.creation_flags(CREATE_NO_WINDOW);
}

fn push_unique_token(tokens: &mut Vec<String>, candidate: &str) {
    if let Some(token) = sanitize_token(candidate) {
        if !tokens.iter().any(|existing| existing == &token) {
            tokens.push(token);
        }
    }
}

fn parse_git_credential_password(output: &str) -> Option<String> {
    for raw_line in output.lines() {
        let line = raw_line.trim_end_matches('\r');
        if line.is_empty() {
            break;
        }
        if let Some(password) = line.strip_prefix("password=") {
            return sanitize_token(password);
        }
    }
    None
}

async fn read_gh_cli_token() -> Option<String> {
    let mut command = Command::new("gh");
    configure_command(&mut command);
    command
        .args(["auth", "token", "--hostname", "github.com"])
        .env("GH_PROMPT_DISABLED", "1")
        .stdin(Stdio::null())
        .stdout(Stdio::piped())
        .stderr(Stdio::null());
    let output = tokio::time::timeout(CREDENTIAL_TIMEOUT, command.output())
        .await
        .ok()?
        .ok()?;
    if !output.status.success() {
        return None;
    }
    let text = String::from_utf8(output.stdout).ok()?;
    sanitize_token(&text)
}

async fn read_git_credential_token() -> Option<String> {
    let mut command = Command::new("git");
    configure_command(&mut command);
    command
        .args(["credential", "fill"])
        .env("GIT_TERMINAL_PROMPT", "0")
        .env("GCM_INTERACTIVE", "never")
        .stdin(Stdio::piped())
        .stdout(Stdio::piped())
        .stderr(Stdio::null());
    let mut child = command.spawn().ok()?;
    if let Some(mut stdin) = child.stdin.take() {
        let _ = stdin
            .write_all(b"protocol=https\nhost=github.com\n\n")
            .await;
    }
    let output = tokio::time::timeout(CREDENTIAL_TIMEOUT, child.wait_with_output())
        .await
        .ok()?
        .ok()?;
    if !output.status.success() {
        return None;
    }
    let text = String::from_utf8(output.stdout).ok()?;
    parse_git_credential_password(&text)
}

async fn discover_github_tokens() -> Vec<String> {
    let mut tokens = Vec::new();
    for var in ["GH_TOKEN", "GITHUB_TOKEN"] {
        if let Ok(value) = std::env::var(var) {
            push_unique_token(&mut tokens, &value);
        }
    }
    if let Some(token) = read_gh_cli_token().await {
        push_unique_token(&mut tokens, &token);
    }
    if let Some(token) = read_git_credential_token().await {
        push_unique_token(&mut tokens, &token);
    }
    tokens
}

async fn open_repository_in_browser() -> Result<(), String> {
    #[cfg(windows)]
    let mut command = {
        let mut cmd = Command::new("rundll32.exe");
        cmd.args(["url.dll,FileProtocolHandler", GITHUB_REPO_URL]);
        cmd
    };
    #[cfg(target_os = "macos")]
    let mut command = {
        let mut cmd = Command::new("/usr/bin/open");
        cmd.arg(GITHUB_REPO_URL);
        cmd
    };
    #[cfg(all(unix, not(target_os = "macos")))]
    let mut command = {
        let mut cmd = Command::new("xdg-open");
        cmd.arg(GITHUB_REPO_URL);
        cmd
    };
    #[cfg(windows)]
    command.creation_flags(CREATE_NO_WINDOW);
    command
        .stdin(Stdio::null())
        .stdout(Stdio::null())
        .stderr(Stdio::null());
    // Mirror updater.rs: launchers on Linux/Windows may stay alive after handing off the URL.
    if let Ok(result) = tokio::time::timeout(BROWSER_OPEN_TIMEOUT, command.status()).await {
        let status =
            result.map_err(|error| format!("Could not open GitHub repository ({error})."))?;
        if !status.success() {
            return Err(format!("Could not open GitHub repository ({status})."));
        }
    }
    Ok(())
}

async fn star_or_open<F, Fut, O, OFut>(
    tokens: Vec<String>,
    mut star_fn: F,
    open_browser: O,
) -> Result<GitHubStarResult, String>
where
    F: FnMut(String) -> Fut,
    Fut: Future<Output = StarApiOutcome>,
    O: FnOnce() -> OFut,
    OFut: Future<Output = Result<(), String>>,
{
    for token in tokens {
        match star_fn(token).await {
            StarApiOutcome::Starred => {
                log::info!("Starred {GITHUB_REPO_URL} via authorized GitHub credential");
                return Ok(GitHubStarResult {
                    status: GitHubStarStatus::Starred,
                    url: GITHUB_REPO_URL,
                });
            }
            StarApiOutcome::Unauthorized => {}
            StarApiOutcome::Failed(reason) => {
                log::warn!("GitHub star API attempt failed: {reason}");
            }
        }
    }
    open_browser().await?;
    Ok(GitHubStarResult {
        status: GitHubStarStatus::OpenedBrowser,
        url: GITHUB_REPO_URL,
    })
}

/// Explicitly stars the GitHub repository via local credentials or opens it in the browser.
#[tauri::command]
pub async fn star_github_repository() -> Result<GitHubStarResult, String> {
    let tokens = discover_github_tokens().await;
    star_or_open(
        tokens,
        |token| async move { star_repository_with_token(&token).await },
        open_repository_in_browser,
    )
    .await
}

#[cfg(test)]
mod tests {
    use super::*;
    use std::sync::{
        atomic::{AtomicBool, Ordering},
        Arc, Mutex,
    };

    #[test]
    fn parses_password_only_before_blank_line_and_deduplicates_tokens() {
        let out =
            "protocol=https\r\nhost=github.com\r\nusername=octocat\r\npassword=gho_secret\r\n\r\n";
        assert_eq!(
            parse_git_credential_password(out),
            Some("gho_secret".into())
        );
        assert_eq!(parse_git_credential_password("\npassword=ignored\n"), None);
        assert_eq!(parse_git_credential_password("password=   \n"), None);

        let mut list = Vec::new();
        push_unique_token(&mut list, " first ");
        push_unique_token(&mut list, "first");
        push_unique_token(&mut list, "bad\ntoken");
        push_unique_token(&mut list, "second");
        assert_eq!(list, vec!["first", "second"]);
    }

    #[tokio::test]
    async fn stars_with_first_authorized_token_without_opening_browser() {
        let tried = Arc::new(Mutex::new(Vec::new()));
        let opened = Arc::new(AtomicBool::new(false));
        let tried_clone = Arc::clone(&tried);
        let opened_clone = Arc::clone(&opened);
        let result = star_or_open(
            vec!["bad-token".into(), "good-token".into(), "unused".into()],
            move |token| {
                let tried = Arc::clone(&tried_clone);
                async move {
                    tried.lock().unwrap().push(token.clone());
                    if token == "good-token" {
                        StarApiOutcome::Starred
                    } else {
                        StarApiOutcome::Unauthorized
                    }
                }
            },
            move || async move {
                opened_clone.store(true, Ordering::SeqCst);
                Ok(())
            },
        )
        .await
        .unwrap();
        assert_eq!(
            result,
            GitHubStarResult {
                status: GitHubStarStatus::Starred,
                url: GITHUB_REPO_URL,
            }
        );
        assert_eq!(*tried.lock().unwrap(), vec!["bad-token", "good-token"]);
        assert!(!opened.load(Ordering::SeqCst));
    }

    #[tokio::test]
    async fn falls_back_to_browser_when_tokens_missing_or_rejected() {
        for tokens in [vec![], vec!["expired".into()], vec!["net-err".into()]] {
            let opened = Arc::new(AtomicBool::new(false));
            let opened_clone = Arc::clone(&opened);
            let result = star_or_open(
                tokens,
                |token| async move {
                    if token == "net-err" {
                        StarApiOutcome::Failed("timeout".into())
                    } else {
                        StarApiOutcome::Unauthorized
                    }
                },
                move || async move {
                    opened_clone.store(true, Ordering::SeqCst);
                    Ok(())
                },
            )
            .await
            .unwrap();
            assert_eq!(
                result,
                GitHubStarResult {
                    status: GitHubStarStatus::OpenedBrowser,
                    url: GITHUB_REPO_URL,
                }
            );
            assert!(opened.load(Ordering::SeqCst));
        }
    }

    #[tokio::test]
    async fn propagates_browser_open_error() {
        let err = star_or_open(
            vec![],
            |_| async { StarApiOutcome::Unauthorized },
            || async { Err("no launcher".into()) },
        )
        .await
        .unwrap_err();
        assert_eq!(err, "no launcher");
    }
}
