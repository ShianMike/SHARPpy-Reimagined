//! State managed by Tauri and shared by the commands.

use std::sync::Arc;

use crate::backend::router::Router;

pub struct AppState {
    pub router: Arc<Router>,
}

impl AppState {
    pub fn new(router: Router) -> Self {
        Self {
            router: Arc::new(router),
        }
    }
}
