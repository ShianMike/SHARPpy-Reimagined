//! Live received bytes for an active point-sounding request.
use serde::{Deserialize, Serialize};

#[derive(Debug, Clone, PartialEq, Serialize, Deserialize)]
#[serde(rename_all = "lowercase")]
pub enum DownloadStage {
    Waiting,
    Downloading,
    Processing,
}

#[derive(Debug, Clone, PartialEq, Serialize, Deserialize)]
#[serde(
    rename_all(serialize = "camelCase", deserialize = "snake_case"),
    deny_unknown_fields
)]
pub struct SoundingProgress {
    pub stage: DownloadStage,
    pub downloaded_bytes: u64,
    pub bytes_per_second: f64,
}

impl SoundingProgress {
    pub fn validate(&self) -> Result<(), String> {
        if self.downloaded_bytes > (1 << 53) - 1
            || !self.bytes_per_second.is_finite()
            || self.bytes_per_second < 0.0
            || self.bytes_per_second > ((1u64 << 53) - 1) as f64
            || (self.stage != DownloadStage::Downloading && self.bytes_per_second != 0.0)
        {
            return Err("invalid sounding download counters".into());
        }
        Ok(())
    }
}
