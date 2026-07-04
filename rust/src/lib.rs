//! `mine_sentinel_rs` — Rust core for the AstrBot minecraft_adapter mine_sentinel module.
//!
//! Exposes CPU-hot helpers (`RuleTermMatcher`, `ObservationRecordCodec`,
//! `observation_priority_score`) to Python via PyO3. The Python side keeps
//! the original module paths (`services/mine_sentinel/...`) and only replaces
//! the heavy function bodies with calls into this module — keeping the
//! AstrBot-facing API and import graph unchanged.

use pyo3::prelude::*;

mod codec;
mod dialogue_terms;
mod observation_priority;

/// Register the module. PyO3 picks up the module name from `pyproject.toml`.
#[pymodule]
fn mine_sentinel_rs(m: &Bound<PyModule>) -> PyResult<()> {
    // Submodules register their own classes + free functions.
    dialogue_terms::register(m)?;
    codec::register(m)?;
    observation_priority::register(m)?;
    m.add("__doc__", "Rust core for mine_sentinel (PyO3).")?;
    Ok(())
}
