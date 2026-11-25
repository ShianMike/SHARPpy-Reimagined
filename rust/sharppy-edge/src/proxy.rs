use crate::policy::{self, MAX_RPC_BYTES, ReadRequest};
use futures_util::TryStreamExt;
use serde_json::json;
use sha2::{Digest, Sha256};
use worker::{
    Cache, Context, Env, Fetch, Headers, Method, Request, RequestInit, RequestRedirect, Response,
    Result, Url, js_sys::Uint8Array,
};

const CACHE_HEADER: &str = "X-Sharppy-Edge-Cache";
const ROUTE_HEADER: &str = "X-Sharppy-Edge-Route";

pub fn error(http: u16, message: &str, not_forwarded: bool) -> Result<Response> {
    let mut response = Response::from_json(&json!({
        "code": "BRIDGE_UNAVAILABLE",
        "message": message,
        "retryable": http >= 500 && not_forwarded,
    }))?
    .with_status(http);
    let headers = response.headers_mut();
    headers.set("Cache-Control", "no-store")?;
    if not_forwarded {
        headers.set(ROUTE_HEADER, "not-forwarded")?;
    }
    if http == 429 {
        headers.set("Retry-After", "60")?;
    }
    Ok(response)
}

pub fn public_headers(mut response: Response) -> Result<Response> {
    response
        .headers_mut()
        .set("Access-Control-Allow-Origin", "*")?;
    response
        .headers_mut()
        .set("X-Content-Type-Options", "nosniff")?;
    Ok(response)
}

pub async fn limit(req: &Request, env: &Env, binding: &str) -> Result<Option<Response>> {
    // Cloudflare supplies this header. Unknown callers share a single bucket.
    let visitor = req
        .headers()
        .get("CF-Connecting-IP")?
        .unwrap_or_else(|| "unknown".into());
    let limiter = match env.rate_limiter(binding) {
        Ok(limiter) => limiter,
        Err(_) => {
            return Ok(Some(error(
                503,
                "Edge rate limiting is unavailable.",
                true,
            )?));
        }
    };
    Ok(match limiter.limit(visitor).await {
        Ok(outcome) if outcome.success => None,
        Ok(_) => Some(error(
            429,
            "Too many edge requests. Try again in a minute.",
            true,
        )?),
        Err(_) => Some(error(503, "Edge rate limiting is unavailable.", true)?),
    })
}

fn origin(env: &Env) -> Option<Url> {
    policy::origin_url(&env.var("OVH_ORIGIN").ok()?.to_string())
}

fn anonymous(req: &Request) -> bool {
    ![
        "Cookie",
        "Authorization",
        "Range",
        "If-None-Match",
        "If-Modified-Since",
    ]
    .iter()
    .any(|name| req.headers().has(name).unwrap_or(true))
}

async fn rpc_body(req: &mut Request) -> Result<Option<Vec<u8>>> {
    if req
        .headers()
        .get("Content-Length")?
        .and_then(|size| size.parse::<usize>().ok())
        .is_some_and(|size| size > MAX_RPC_BYTES)
    {
        return Ok(None);
    }
    let mut bytes = Vec::new();
    let mut stream = req.stream()?;
    while let Some(chunk) = stream.try_next().await? {
        if chunk.len() > MAX_RPC_BYTES - bytes.len() {
            return Ok(None);
        }
        bytes.extend_from_slice(&chunk);
    }
    Ok(Some(bytes))
}

pub async fn backend(mut req: Request, env: &Env, ctx: &Context, url: Url) -> Result<Response> {
    if url.query().is_some() {
        return error(400, "Edge RPC requests do not take query parameters.", true);
    }
    if req
        .headers()
        .get("Origin")?
        .is_some_and(|value| value != url.origin().ascii_serialization())
    {
        return error(403, "Edge RPC requests must use the app origin.", true);
    }
    if !req.headers().get("Content-Type")?.is_some_and(|value| {
        value
            .split(';')
            .next()
            .is_some_and(|media| media.trim().eq_ignore_ascii_case("application/json"))
    }) {
        return error(415, "Send a JSON request envelope.", true);
    }
    if let Some(response) = limit(&req, env, "API_LIMIT").await? {
        return Ok(response);
    }
    let Some(mut origin) = origin(env) else {
        return error(503, "The edge origin is unavailable.", true);
    };
    let local = origin.scheme() == "http"
        && url.scheme() == "http"
        && matches!(url.host_str(), Some("localhost" | "127.0.0.1" | "[::1]"));
    if !local && origin.origin() != url.origin() {
        return error(
            404,
            "Edge RPC routing is unavailable on this hostname.",
            true,
        );
    }
    let Some(body) = rpc_body(&mut req).await? else {
        return error(413, "The edge request envelope is too large.", true);
    };
    let Ok(read) = serde_json::from_slice::<ReadRequest>(&body) else {
        return error(400, "Invalid edge request envelope.", true);
    };
    let Some(ttl) = policy::read_ttl(&read.method) else {
        return error(404, "This RPC is outside the edge read routes.", true);
    };
    origin.set_path("/__backend");
    let candidate = anonymous(&req).then(|| CacheCandidate {
        key: cache_key(
            &url,
            &origin,
            &serde_json::to_vec(&read).expect("the request serializes"),
        ),
        ttl,
        kind: CacheKind::Rpc,
    });
    forward(&req, ctx, origin, Some(&body), candidate).await
}

pub async fn public_file(req: Request, env: &Env, ctx: &Context, url: Url) -> Result<Response> {
    let name = url
        .path()
        .strip_prefix("/__edge/public/")
        .unwrap_or_else(|| url.path().trim_start_matches('/'));
    let Some(name) = policy::public_file(name) else {
        return error(404, "Unknown public edge resource.", true);
    };
    if let Some(response) = limit(&req, env, "READ_LIMIT").await? {
        return public_headers(response);
    }
    let Some(mut origin) = origin(env) else {
        return public_headers(error(503, "The edge origin is unavailable.", true)?);
    };
    origin.set_path(&format!("/{name}"));
    origin.set_query(url.query());
    // Query versions are passed through. A random query is never a cache fill.
    let versioned = url.query().is_none_or(|query| {
        query.strip_prefix("v=").is_some_and(|value| {
            !value.is_empty()
                && value.len() <= 12
                && value.bytes().all(|byte| byte.is_ascii_digit())
        })
    });
    let candidate =
        (req.method() == Method::Get && anonymous(&req) && versioned).then(|| CacheCandidate {
            key: cache_key(&url, &origin, b""),
            ttl: 86_400,
            kind: CacheKind::PublicFile,
        });
    public_headers(forward(&req, ctx, origin, None, candidate).await?)
}

enum CacheKind {
    PublicFile,
    Rpc,
}

struct CacheCandidate {
    key: String,
    ttl: u32,
    kind: CacheKind,
}

impl CacheCandidate {
    fn response_ttl(&self, response: &Response) -> Result<Option<u32>> {
        let headers = response.headers();
        let content_type = headers.get("Content-Type")?.unwrap_or_default();
        let media = content_type.split(';').next().unwrap_or_default().trim();
        let matches = match self.kind {
            CacheKind::PublicFile => media.starts_with("image/"),
            CacheKind::Rpc => matches!(media, "application/json" | "application/octet-stream"),
        };
        if !matches {
            return Ok(None);
        }
        let length = headers
            .get("Content-Length")?
            .and_then(|raw| raw.parse().ok());
        let control = headers.get("Cache-Control")?.unwrap_or_default();
        let vary = headers.get("Vary")?.unwrap_or_default();
        let age = headers
            .get("Age")?
            .and_then(|raw| raw.parse::<u32>().ok())
            .unwrap_or(0);
        Ok(policy::cacheable_response(
            response.status_code(),
            headers.has("Set-Cookie")?,
            &vary,
            &control,
            length,
            self.ttl,
        )
        .map(|ttl| ttl.saturating_sub(age))
        .filter(|ttl| *ttl > 0))
    }
}

fn cache_key(worker: &Url, origin: &Url, body: &[u8]) -> String {
    let mut hash = Sha256::new();
    hash.update(origin.as_str());
    hash.update(b"\0");
    hash.update(body);
    format!(
        "{}/__edge/cache/{}/{:x}",
        worker.origin().ascii_serialization(),
        env!("CARGO_PKG_VERSION"),
        hash.finalize()
    )
}

fn forwarded_headers(req: &Request, origin: &Url, send_credentials: bool) -> Result<Headers> {
    let headers = Headers::new();
    for name in [
        "Accept",
        "Content-Type",
        "Range",
        "If-None-Match",
        "If-Modified-Since",
    ] {
        if let Some(value) = req.headers().get(name)? {
            headers.set(name, &value)?;
        }
    }
    if send_credentials {
        for name in ["Authorization", "Cookie"] {
            if let Some(value) = req.headers().get(name)? {
                headers.set(name, &value)?;
            }
        }
        if req.headers().has("Origin")? {
            headers.set("Origin", &origin.origin().ascii_serialization())?;
        }
    }
    Ok(headers)
}

fn tagged(response: Response, cache_status: &str) -> Result<Response> {
    let headers = Headers::new();
    for (name, value) in response.headers().entries() {
        if !name.eq_ignore_ascii_case("Set-Cookie") {
            headers.append(&name, &value)?;
        }
    }
    for value in response.headers().get_all("Set-Cookie")? {
        headers.append("Set-Cookie", &value)?;
    }
    headers.set(CACHE_HEADER, cache_status)?;
    headers.set(ROUTE_HEADER, "routed")?;
    Ok(response.with_headers(headers))
}

async fn forward(
    req: &Request,
    ctx: &Context,
    origin: Url,
    body: Option<&[u8]>,
    candidate: Option<CacheCandidate>,
) -> Result<Response> {
    if let Some(candidate) = &candidate
        && let Ok(Some(response)) = Cache::default().get(candidate.key.clone(), false).await
    {
        return tagged(response, "HIT");
    }
    let mut init = RequestInit::new();
    init.with_method(req.method())
        .with_headers(forwarded_headers(req, &origin, body.is_some())?)
        .with_redirect(RequestRedirect::Manual);
    if let Some(body) = body {
        init.with_body(Some(Uint8Array::from(body).into()));
    }
    let request = Request::new_with_init(origin.as_str(), &init)?;
    let Ok(mut response) = Fetch::Request(request).send().await else {
        // The origin may already have accepted a job, so do not signal a safe retry.
        return error(502, "The OVH origin could not return a response.", false);
    };
    if let Some(candidate) = candidate
        && let Some(ttl) = candidate.response_ttl(&response).ok().flatten()
        && let Ok(clone) = response.cloned()
        && let Ok(mut stored) = tagged(clone, "HIT")
        && stored
            .headers_mut()
            .set("Cache-Control", &format!("public, max-age={ttl}"))
            .is_ok()
    {
        ctx.wait_until(async move {
            if Cache::default().put(candidate.key, stored).await.is_err() {
                worker::console_warn!("SHARPpy edge cache could not store a public response.");
            }
        });
        return tagged(response, "MISS");
    }
    tagged(response, "BYPASS")
}
