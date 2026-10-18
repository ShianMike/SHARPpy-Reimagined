//! Disk-cache totals. No filesystem path is exposed to, or accepted from, the webview.

use serde::{Deserialize, Serialize};

const MAX_SAFE: u64 = (1 << 53) - 1;
pub const CACHE_KEYS: [&str; 7] = [
    "models",
    "maps",
    "outlooks",
    "radar",
    "satellite",
    "observations",
    "alerts",
];

#[derive(Debug, Clone, PartialEq, Serialize, Deserialize)]
#[serde(
    rename_all(serialize = "camelCase", deserialize = "snake_case"),
    deny_unknown_fields
)]
pub struct CacheUsage {
    pub key: String,
    pub bytes: u64,
    pub files: u64,
    pub clearable_bytes: u64,
}

#[derive(Debug, Clone, PartialEq, Serialize, Deserialize)]
#[serde(deny_unknown_fields)]
pub struct StorageUsage {
    pub caches: Vec<CacheUsage>,
}

impl StorageUsage {
    pub fn validate(&self) -> Result<(), String> {
        if self.caches.len() != CACHE_KEYS.len()
            || CACHE_KEYS
                .iter()
                .any(|key| self.caches.iter().filter(|row| row.key == *key).count() != 1)
        {
            return Err("each cache category must appear once".into());
        }
        if self.caches.iter().any(|row| {
            row.bytes > MAX_SAFE || row.files > MAX_SAFE || row.clearable_bytes > row.bytes
        }) {
            return Err(
                "cache counts must be safe integers and clearable bytes must not exceed the size"
                    .into(),
            );
        }
        Ok(())
    }
}

#[derive(Debug, Clone, PartialEq, Serialize, Deserialize)]
#[serde(
    rename_all(serialize = "camelCase", deserialize = "snake_case"),
    deny_unknown_fields
)]
pub struct CacheClearResult {
    pub usage: StorageUsage,
    pub freed_bytes: u64,
    pub failed_files: u64,
}

impl CacheClearResult {
    pub fn validate(&self) -> Result<(), String> {
        self.usage.validate()?;
        if self.freed_bytes > MAX_SAFE || self.failed_files > MAX_SAFE {
            return Err("cache counts must be safe integers".into());
        }
        Ok(())
    }
}
