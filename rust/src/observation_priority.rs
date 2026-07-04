//! Lightweight observation priority scoring used before full report analysis.
//!
//! Rust port of `services/mine_sentinel/observation_priority.py`. The
//! `observation_priority_score` runs once per record (up to 50k records per
//! report window) inside `RecentWindowBuilder.add`, so moving it off the
//! Python interpreter is high-value.

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
    matcher: Option<Bound<PyAny>>,
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
            if let Some(m) = matcher.as_ref() {
                // scan returns dict[rule, (kw_list, ug_list)]
                let hits = m.call_method1("scan", (text.as_str(),))?;
                let hits_dict: Bound<PyDict> = hits.extract()?;
                for (rule_obj, lists_bound) in hits_dict.iter() {
                    let lists: Bound<PyTuple> = lists_bound.extract()?;
                    let kw_list: Bound<PyList> = lists.get_item(0)?.extract()?;
                    let ug_list: Bound<PyList> = lists.get_item(1)?.extract()?;
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

    // Silence unused-warn if py ever unused (it isn't, but be safe).
    let _ = py;

    Ok(score)
}

/// Mirror `_metrics_priority`: tps/memory based scoring.
fn metrics_priority(record: &Bound<PyAny>) -> PyResult<f64> {
    let metrics_binding = record.getattr("metrics")?;
    let metrics: Bound<PyDict> = metrics_binding.extract()?;
    const TPS_KEYS: &[&str] = &["tps1m", "tps", "tps_1m", "oneMinuteTps", "one_minute_tps"];
    let mut tps: f64 = 20.0;
    let mut found = false;
    for key in TPS_KEYS {
        if let Some(v) = metrics.get_item(*key)? {
            if let Ok(p) = v.extract::<f64>() {
                tps = p;
                found = true;
                break;
            }
        }
    }
    if !found {
        tps = 20.0;
    }
    let memory = memory_usage_percent(&metrics)?;
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
/// returns the percentage (0-100) or 0.0 if unknown. Mirrors the full
/// MEMORY_PERCENT_KEYS + MEMORY_PAIR_KEYS tables from metrics_context.py so
/// behavior stays in sync with the Python implementation.
fn memory_usage_percent(metrics: &Bound<PyDict>) -> PyResult<f64> {
    // Percent keys (any one suffices). Python normalizes 0..1 to 0..100.
    const PERCENT_KEYS: &[&str] = &[
        "memoryUsagePercent",
        "memory_usage_percent",
        "memoryPercent",
        "memory_percent",
        "heapUsagePercent",
        "heap_usage_percent",
        "usedMemoryPercent",
        "used_memory_percent",
        "ramUsagePercent",
        "ram_usage_percent",
    ];
    for key in PERCENT_KEYS {
        if let Some(v) = metrics.get_item(key)? {
            if let Ok(p) = v.extract::<f64>() {
                let normalized = if (0.0..=1.0).contains(&p) { p * 100.0 } else { p };
                return Ok(normalized);
            }
        }
    }
    // Pair keys: (used, max). First matching pair wins.
    const PAIR_KEYS: &[(&str, &str)] = &[
        ("memoryUsed", "memoryMax"),
        ("memoryUsedMb", "memoryMaxMb"),
        ("memoryUsedMB", "memoryMaxMB"),
        ("memory_used_mb", "memory_max_mb"),
        ("memory_used", "memory_max"),
        ("heapUsed", "heapMax"),
        ("heapUsedMb", "heapMaxMb"),
        ("heap_used_mb", "heap_max_mb"),
        ("heap_used", "heap_max"),
        ("usedMemory", "maxMemory"),
        ("usedMemoryMb", "maxMemoryMb"),
        ("used_memory_mb", "max_memory_mb"),
        ("used_memory", "max_memory"),
    ];
    for (used_key, max_key) in PAIR_KEYS {
        let used = metrics
            .get_item(*used_key)?
            .and_then(|v| v.extract::<f64>().ok());
        let max = metrics
            .get_item(*max_key)?
            .and_then(|v| v.extract::<f64>().ok());
        if let (Some(u), Some(m)) = (used, max) {
            if m > 0.0 {
                let pct = (u / m) * 100.0;
                return Ok(pct.max(0.0).min(100.0));
            }
        }
    }
    Ok(0.0)
}

pub fn register(parent: &Bound<PyModule>) -> PyResult<()> {
    parent.add_function(wrap_pyfunction!(observation_priority_score, parent)?)?;
    Ok(())
}
