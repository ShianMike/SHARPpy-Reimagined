//! Source snapshots are produced and reopened by the shared Rust recovery module.
use super::{
    router::Router,
    sounding_ops::{invalid, rejected, sounding_id_ok},
};
use crate::{
    dto::soundings::SoundingSummary,
    error::{BackendError, ErrorContext, PYTHON_PROVIDER},
};
use serde::{Deserialize, Serialize};
use serde_json::json;
use sharpmod_weather::sounding_recovery::MAX_PAYLOAD_BYTES;
use std::time::Duration;

#[derive(Deserialize, Serialize)]
#[serde(
    rename_all(serialize = "camelCase", deserialize = "snake_case"),
    deny_unknown_fields
)]
pub struct SoundingSnapshot {
    pub sounding_id: String,
    pub payload: String,
}

impl Router {
    pub async fn sounding_snapshot(
        &self,
        sounding_id: &str,
    ) -> Result<SoundingSnapshot, BackendError> {
        let context = ErrorContext::new("get_sounding_snapshot", Some(PYTHON_PROVIDER), None);
        if !sounding_id_ok(sounding_id) {
            return Err(invalid(&context, "Invalid sounding ID."));
        }
        let result: SoundingSnapshot = self
            .call(
                &context,
                "soundings.snapshot",
                json!({"sounding_id":sounding_id}),
                Duration::from_secs(30),
            )
            .await?;
        if result.sounding_id != sounding_id
            || result.payload.is_empty()
            || result.payload.len() > MAX_PAYLOAD_BYTES
        {
            return Err(rejected(
                &context,
                "soundings.snapshot",
                "Invalid recovery source response.".into(),
            ));
        }
        Ok(result)
    }

    pub async fn restore_sounding_snapshot(
        &self,
        payload: &str,
    ) -> Result<SoundingSummary, BackendError> {
        let context = ErrorContext::new("restore_sounding_snapshot", Some(PYTHON_PROVIDER), None);
        if payload.is_empty() || payload.len() > MAX_PAYLOAD_BYTES {
            return Err(invalid(&context, "Invalid recovery source size."));
        }
        let summary: SoundingSummary = self
            .call(
                &context,
                "soundings.restore",
                json!({"payload":payload}),
                Duration::from_secs(90),
            )
            .await?;
        summary
            .validate()
            .map_err(|reason| rejected(&context, "soundings.restore", reason))?;
        Ok(summary)
    }
}
