//! Tauri commands, grouped by responsibility. Each returns a public DTO or a
//! `BackendError`, which Tauri serializes as the rejection value.

pub mod backend;
pub mod map;
pub mod soundings;
pub mod stations;
