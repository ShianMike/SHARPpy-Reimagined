//! Storage operations use the cache owners in Python, including their active-file leases.

use crate::backend::map_ops::context;
use crate::backend::router::Router;
use crate::backend::sounding_ops::{invalid, rejected};
use crate::dto::storage::{CacheClearResult, StorageUsage, CACHE_KEYS};
use crate::error::BackendError;
use serde_json::json;
use std::time::Duration;

impl Router {
    pub async fn storage_usage(&self) -> Result<StorageUsage, BackendError> {
        let context = context("get_storage_usage", "cached weather data");
        let result: StorageUsage = self
            .call(
                &context,
                "storage.usage",
                json!({}),
                Duration::from_secs(60),
            )
            .await?;
        result
            .validate()
            .map_err(|reason| rejected(&context, "storage.usage", reason))?;
        Ok(result)
    }

    pub async fn clear_cache(&self, cache: &str) -> Result<CacheClearResult, BackendError> {
        let context = context("clear_cache", "cached weather data");
        if cache != "all" && !CACHE_KEYS.contains(&cache) {
            return Err(invalid(
                &context,
                "cache must be models, maps, outlooks or all",
            ));
        }
        let result: CacheClearResult = self
            .call(
                &context,
                "storage.clear",
                json!({"cache": cache}),
                Duration::from_secs(120),
            )
            .await?;
        result
            .validate()
            .map_err(|reason| rejected(&context, "storage.clear", reason))?;
        Ok(result)
    }
}
