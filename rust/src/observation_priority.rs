//! Lightweight observation priority scoring used before full report analysis.
//!
//! Rust port of `services/mine_sentinel/observation_priority.py`. The
//! `observation_priority_score` runs once per record (up to 50k records per
//! report window) inside `RecentWindowBuilder.add`, so moving it off the
//! Python interpreter is high-value.

use crate::dialogue_terms::{RuleTermMatcher};
use pyo3::prelude::*;
use pyo3::types::{PyDict, PyList, PyTuple};

/// Score records that should survive bounded-memory report sampling.
/// Mirrors `observation_priority.observation_priority_score`.
///
/// `record` is the Python `ObservationRecord` object; `matcher` is an
/// optional Python-side `RuleTermMatcher` (default ruleset). We accept the
/// pre-built matcher so callers can reuse a process-wide cache (mirrors
/// `_DEFAULT_MATCHER`); the Python wrapper handles caching.
#[pyfunction]
#[pyo3(signature = (record, matcher=None))]
pub fn observation_priority_score(
    py: Python,
    record: &Bound<PyAny>,
    matcher: Option<&Bound<PyAny>>,
) -> PyResult<f64> {
    let kind: String = record.getattr("kind")?.extract()?;
    let content: String = record.getattr("content")?.extract()?;
    let tags: Vec<String> = record.getattr("tags")?.extract()?;

    // text = normalize_text(f"{content} {' '.join(tags)}")
    let mut combined = content;
    combined.push(' ');
    let mut first_tag = true;
    for t in &tags {
        if !first_tag {
            combined.push(' ');
        }
        combined.push_str(t);
        first_tag = false;
    }
    let text = crate::dialogue_terms::normalize_text(&combined);

    let mut score: f64 = 0.0;

    match kind.as_str() {
        "CHAT" => {
            score += 1.0;
            if let Some(m) = matcher {
                // scan returns dict[rule, (kw_list, ug_list)]
                let hits = m.call_method1("scan", (text.as_str(),))?;
                let hits_dict = hits.downcast::<PyDict>()?;
                for item in hits_dict.items() {
                    let tup = item?.downcast::<PyTuple>()?;
                    let rule_obj = tup.get_item(0)?;
                    let lists = tup.get_item(1)?.downcast::<PyTuple>()?;
                    let kw_list = lists.get_item(0)?.downcast::<PyList>()?;
                    let ug_list = lists.get_item(1)?.downcast::<PyList>()?;
                    let kw_count = kw_list.len();
                    if kw_count == 0 {
                        continue;
                    }
                    score += 4.0 + (kw_count.min(3)) as f64;
                    if ug_list.len() > 0 {
                        score += 2.0;
                    }
                    let base_severity: String = rule_obj
                        .getattr("base_severity")?
                        .extract()
                        .unwrap_or_default();
                    if base_severity == "high" || base_severity == "critical" {
                        score += 1.0;
                    }
                }
            }
        }
        "PLUGIN_ERROR" => score += 5.0,
        "SERVER_SWITCH" => score += 2.0,
        "SERVER_METRICS" => {
            score += metrics_priority(record)?;
        }
        _ => {}
    }

    Ok(score)
}

/// Mirror `_metrics_priority`: tps/memory based scoring.
fn metrics_priority(record: &Bound<PyAny>) -> PyResult<f64> {
    let metrics = record.getattr("metrics")?.downcast::<PyDict>()?;
    let tps = metrics
        .get_item("tps1m")
        .or_else(|| metrics.get_item("tps"))?
        .map(|v| v.extract::<f64>())
        .transpose()?
        .unwrap_or(20.0);
    let tps = if tps == 0.0 && metrics.get_item("tps1m")?.is_none() && metrics.get_item("tps")?.is_none() {
        20.0
    } else {
        tps
    };
    let memory = memory_usage_percent(metrics)?;
    let mut score = 0.0_f64;
    if tps < 18.0 {
        score += 3.0;
    }
    if tps < 15.0 {
        score += 2.0;
    }
    if memory >= 90.0 {
        score += 2.0;
    }
    Ok(score)
}

/// Mirror `metrics_context.memory_usage_percent(metrics)`:
/// returns the percentage (0-100) or None. Looks up common metric keys.
fn memory_usage_percent(metrics: &Bound<PyDict>) -> PyResult<f64> {
    // keys tried in order: usedMemoryPercent, memoryUsagePercent, memoryPercent,
    // then (usedMemory / maxMemory) * 100.
    for key in ["usedMemoryPercent", "memoryUsagePercent", "memoryPercent"] {
        if let Some(v) = metrics.get_item(key)? {
            if let Ok(p) = v.extract::<f64>() {
                return Ok(p);
            }
        }
    }
    let used = metrics.get_item("usedMemory")?.and_then(|v| v.extract::<f64>().ok());
    let max = metrics.get_item("maxMemory")?.and_then(|v| v.extract::<f64>().ok());
    if let (Some(u), Some(m)) = (used, max) {
        if m > 0.0 {
            return Ok((u / m) * 100.0);
        }
    }
    Ok(0.0)
}

pub fn register(parent: &Bound<PyModule>) -> PyResult<()> {
    parent.add_function(wrap_pyfunction!(observation_priority_score, parent)?)?;
    // Re-export RuleTermMatcher here too so callers can grab it from either module.
    let _ = RuleTermMatcher::register;
    Ok(())
}
