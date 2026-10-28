//! Enforce response budgets while streaming, including responses without a length.

pub(crate) async fn body(mut response: reqwest::Response, limit: usize) -> Result<Vec<u8>, String> {
    if response
        .content_length()
        .is_some_and(|size| size > limit as u64)
    {
        return Err("response exceeded its byte budget".into());
    }
    let mut body = Vec::new();
    while let Some(chunk) = response.chunk().await.map_err(|error| error.to_string())? {
        if chunk.len() > limit - body.len() {
            return Err("response exceeded its byte budget".into());
        }
        body.extend_from_slice(&chunk);
    }
    Ok(body)
}
