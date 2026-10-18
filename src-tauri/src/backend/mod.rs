//! Semantic operations: the router decides who answers each one, and
//! `convert` turns bridge payloads into public DTOs.

pub mod alert_ops;
pub mod convert;
pub mod map_ops;
pub mod radar_ops;
pub mod reports_ops;
pub mod router;
pub mod satellite_ops;
pub mod sounding_ops;
pub mod storage_ops;
pub mod surface_ops;
pub mod thresholds_ops;
