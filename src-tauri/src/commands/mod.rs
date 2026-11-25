//! Tauri commands, grouped by responsibility. Each returns a public DTO or a
//! `BackendError`, which Tauri serializes as the rejection value.

pub mod animation;
pub mod backend;
pub mod clipboard;
pub mod github;
pub mod map;
pub mod soundings;
pub mod stations;
pub mod updater;
