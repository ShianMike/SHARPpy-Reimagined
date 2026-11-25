mod policy;
mod proxy;
mod status;

use worker::{
    Context, Env, Method, Request, Response, Result, ScheduleContext, ScheduledEvent, event,
};

#[event(fetch)]
async fn fetch(req: Request, env: Env, ctx: Context) -> Result<Response> {
    let url = req.url()?;
    let method = req.method();
    match (method, url.path()) {
        (Method::Get, "/__edge/config" | "/__edge/status" | "/__edge/health") => {
            if let Some(response) = proxy::limit(&req, &env, "READ_LIMIT").await? {
                return proxy::public_headers(response);
            }
            let mut response = match url.path() {
                "/__edge/config" => status::config(&env).await?,
                "/__edge/status" => status::status(&env).await?,
                _ => status::health(&env).await?,
            };
            response.headers_mut().set("Cache-Control", "no-store")?;
            proxy::public_headers(response)
        }
        (Method::Post, "/__edge/backend") => proxy::backend(req, &env, &ctx, url).await,
        (Method::Get | Method::Head, path)
            if path.starts_with("/__edge/public/")
                || policy::public_file(path.trim_start_matches('/')).is_some() =>
        {
            proxy::public_file(req, &env, &ctx, url).await
        }
        _ => proxy::error(404, "Unknown edge route.", true),
    }
}

#[event(scheduled)]
async fn scheduled(_: ScheduledEvent, env: Env, _: ScheduleContext) {
    if let Err(error) = status::refresh(&env).await {
        worker::console_error!("SHARPpy edge monitoring could not publish its snapshot: {error}");
    }
}
