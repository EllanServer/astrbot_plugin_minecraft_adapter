//! Observation JSONL serialization and normalization.
//!
//! Rust port of `services/mine_sentinel/storage/codec.py`.
//! `dedupe_key` (blake2b) and `json_line` are run on every record during
//! both write (`add_batch`) and read (`read_jsonl_window` + `dedupe_key`),
//! so porting them to Rust removes two of the hottest per-record paths.

use blake2::digest::{Update, VariableOutput};
use blake2::Blake2bVar;
use pyo3::prelude::*;
use pyo3::types::PyDict;

/// Drop-in replacement for `ObservationRecordCodec`. Holds the small set of
/// config-derived limits needed by `normalize_record` / `record_to_json` /
/// `dedupe_key`. The Python `MineSentinelConfig` is unpacked once at
/// construction; per-record work never touches Python attribute access.
#[pyclass]
pub struct ObservationRecordCodec {
    max_content_length: usize,
    max_tags_per_record: usize,
    max_metric_fields: usize,
    max_raw_fields: usize,
    include_raw: bool,
    dedupe_window_seconds: i64,
}

#[pymethods]
impl ObservationRecordCodec {
    /// Construct from the unpacked config fields. The Python wrapper passes
    /// them directly so we never hold a reference to a Python object across
    /// calls.
    #[new]
    #[pyo3(signature = (
        max_content_length = 4000,
        max_tags_per_record = 8,
        max_metric_fields = 32,
        max_raw_fields = 16,
        include_raw = false,
        dedupe_window_seconds = 120,
    ))]
    #[allow(clippy::too_many_arguments)]
    pub fn new(
        max_content_length: usize,
        max_tags_per_record: usize,
        max_metric_fields: usize,
        max_raw_fields: usize,
        include_raw: bool,
        dedupe_window_seconds: i64,
    ) -> Self {
        Self {
            max_content_length,
            max_tags_per_record,
            max_metric_fields,
            max_raw_fields,
            include_raw,
            dedupe_window_seconds,
        }
    }

    /// Mutate the Python `ObservationRecord` in place: truncate content,
    /// slice + truncate tags, compact metrics/context/raw. Mirrors
    /// `ObservationRecordCodec.normalize_record`.
    pub fn normalize_record(&self, py: Python, record: &Bound<PyAny>) -> PyResult<()> {
        // content
        let content: String = record.getattr("content")?.extract()?;
        let truncated_content = truncate(&content, self.max_content_length);
        record.setattr("content", truncated_content)?;

        // tags
        let tags: Vec<String> = record.getattr("tags")?.extract()?;
        let limit = self.max_tags_per_record.min(tags.len());
        let mut new_tags: Vec<String> = Vec::with_capacity(limit);
        for tag in tags.into_iter().take(limit) {
            new_tags.push(truncate(&tag, self.max_content_length));
        }
        record.setattr("tags", new_tags)?;

        // metrics
        let metrics_binding = record.getattr("metrics")?;
        let metrics: Bound<PyDict> = metrics_binding.extract()?;
        let compacted_metrics = self.compact_dict(py, &metrics, self.max_metric_fields)?;
        record.setattr("metrics", compacted_metrics)?;

        // context
        let context_binding = record.getattr("context")?;
        let context: Bound<PyDict> = context_binding.extract()?;
        let compacted_context = self.compact_dict(py, &context, self.max_raw_fields)?;
        record.setattr("context", compacted_context)?;

        // raw
        if self.include_raw {
            let raw_binding = record.getattr("raw")?;
            let raw: Bound<PyDict> = raw_binding.extract()?;
            let compacted_raw = self.compact_dict(py, &raw, self.max_raw_fields)?;
            record.setattr("raw", compacted_raw)?;
        } else {
            record.setattr("raw", PyDict::new(py))?;
        }
        Ok(())
    }

    /// Build the JSONL-safe dict mirroring `record_to_json`. Returns a new
    /// Python dict; the input record is left untouched.
    pub fn record_to_json<'py>(
        &self,
        py: Python<'py>,
        record: &Bound<'py, PyAny>,
    ) -> PyResult<Bound<'py, PyDict>> {
        let event_id: String = record.getattr("event_id")?.extract()?;
        let kind: String = record.getattr("kind")?.extract()?;
        let timestamp: i64 = record.getattr("timestamp")?.extract()?;
        let server_id: String = record.getattr("server_id")?.extract()?;
        let server_name: String = record.getattr("server_name")?.extract()?;
        let backend_server: String = record.getattr("backend_server")?.extract()?;
        let proxy_id: String = record.getattr("proxy_id")?.extract()?;
        let player_name: String = record.getattr("player_name")?.extract()?;
        let player_uuid_hash: String = record.getattr("player_uuid_hash")?.extract()?;
        let content: String = record.getattr("content")?.extract()?;
        let tags: Vec<String> = record.getattr("tags")?.extract()?;
        let context = record.getattr("context")?;
        let metrics = record.getattr("metrics")?;
        let raw: Bound<PyAny> = if self.include_raw {
            record.getattr("raw")?
        } else {
            PyDict::new(py).into_any()
        };

        let out = PyDict::new(py);
        out.set_item("eventId", event_id)?;
        out.set_item("kind", kind)?;
        out.set_item("timestamp", timestamp)?;
        out.set_item("serverId", server_id)?;
        out.set_item("serverName", server_name)?;
        out.set_item("backendServer", backend_server)?;
        out.set_item("proxyId", proxy_id)?;
        let player = PyDict::new(py);
        player.set_item("name", player_name)?;
        player.set_item("uuidHash", player_uuid_hash)?;
        out.set_item("player", player)?;
        out.set_item("content", content)?;
        out.set_item("tags", tags)?;
        out.set_item("context", context)?;
        out.set_item("metrics", metrics)?;
        out.set_item("raw", raw)?;
        Ok(out)
    }

    /// Serialize record to a single JSONL line (compact, no ensure_ascii).
    /// Mirrors `ObservationRecordCodec.json_line`.
    pub fn json_line(&self, py: Python, record: &Bound<PyAny>) -> PyResult<String> {
        let dict = self.record_to_json(py, record)?;
        let json_module = py.import("json")?;
        let dumps = json_module.getattr("dumps")?;
        let kwargs = PyDict::new(py);
        kwargs.set_item("ensure_ascii", false)?;
        let separators = pyo3::types::PyTuple::new(py, [",", ":"])?;
        kwargs.set_item("separators", separators)?;
        let result: String = dumps.call((dict,), Some(&kwargs))?.extract()?;
        Ok(result)
    }

    /// Compute the dedupe key for a record. Mirrors `dedupe_key`:
    /// - if event_id non-empty → use it
    /// - else blake2b16 of `kind|server_id|identity|content_lower|bucket`
    pub fn dedupe_key(&self, record: &Bound<PyAny>) -> PyResult<String> {
        let event_id: String = record.getattr("event_id")?.extract()?;
        if !event_id.is_empty() {
            return Ok(event_id);
        }
        let kind: String = record.getattr("kind")?.extract()?;
        let server_id: String = record.getattr("server_id")?.extract()?;
        let identity: String = record
            .getattr("identity")?
            .extract()
            .unwrap_or_default();
        let content: String = record.getattr("content")?.extract()?;
        let timestamp: i64 = record.getattr("timestamp")?.extract()?;
        let bucket = timestamp / self.dedupe_window_seconds.max(1).saturating_mul(1000);
        let content_lower = normalize_ws_lower(&content);
        let raw = format!(
            "{}|{}|{}|{}|{}",
            kind, server_id, identity, content_lower, bucket
        );
        let mut hasher = Blake2bVar::new(16).expect("blake2b 16 bytes");
        hasher.update(raw.as_bytes());
        let mut out = [0u8; 16];
        hasher.finalize_variable(&mut out);
        Ok(format!("h:{}", hex_encode(&out)))
    }
}

impl ObservationRecordCodec {
    /// Mirror `compact_dict`. Takes a PyDict, returns a new bounded PyDict.
    /// `max_fields` is passed per-call to match the Python signature (metrics
    /// uses max_metric_fields, context/raw use max_raw_fields).
    fn compact_dict<'py>(
        &self,
        py: Python<'py>,
        data: &Bound<'py, PyDict>,
        max_fields: usize,
    ) -> PyResult<Bound<'py, PyDict>> {
        let compact = PyDict::new(py);
        let mut count = 0;
        for kv in data.iter() {
            if count >= max_fields {
                break;
            }
            let (key, value) = kv;
            let key_str: String = key.to_string();
            let compacted = self.compact_value(py, &value)?;
            compact.set_item(key_str, compacted)?;
            count += 1;
        }
        Ok(compact)
    }

    /// Mirror `compact_value`.
    fn compact_value<'py>(
        &self,
        py: Python<'py>,
        value: &Bound<'py, PyAny>,
    ) -> PyResult<Bound<'py, PyAny>> {
        if value.is_none() {
            return Ok(value.clone());
        }
        // bool/int/float passthrough: try numeric extractions.
        if value.extract::<bool>().is_ok()
            || value.extract::<i64>().is_ok()
            || value.extract::<f64>().is_ok()
        {
            return Ok(value.clone());
        }
        // str → truncate
        if let Ok(s) = value.extract::<String>() {
            return Ok(truncate(&s, self.max_content_length)
                .into_pyobject(py)?
                .into_any());
        }
        // fallback: json.dumps(value, ensure_ascii=False, default=str) then truncate
        let json_module = py.import("json")?;
        let dumps = json_module.getattr("dumps")?;
        let kwargs = PyDict::new(py);
        kwargs.set_item("ensure_ascii", false)?;
        // default=str: stringify unknown objects.
        let builtins = py.import("builtins")?;
        let str_fn = builtins.getattr("str")?;
        kwargs.set_item("default", str_fn)?;
        let text: String = dumps.call((value,), Some(&kwargs))?.extract()?;
        Ok(truncate(&text, self.max_content_length)
            .into_pyobject(py)?
            .into_any())
    }
}

/// Mirror Python `truncate`:
/// - `max_length <= 0` → empty
/// - `len(value) <= max_length` → unchanged
/// - `max_length <= 3` → first max_length chars
/// - else → first (max_length - 3) chars + "..."
pub fn truncate(value: &str, max_length: usize) -> String {
    if max_length == 0 {
        return String::new();
    }
    if value.chars().count() <= max_length {
        return value.to_string();
    }
    if max_length <= 3 {
        return value.chars().take(max_length).collect();
    }
    let take = max_length - 3;
    let mut out: String = value.chars().take(take).collect();
    out.push_str("...");
    out
}

/// Lowercase + collapse all whitespace runs to single space (no leading/trailing).
fn normalize_ws_lower(s: &str) -> String {
    let mut out = String::with_capacity(s.len());
    let mut prev_space = true;
    for c in s.chars() {
        let lc = c.to_lowercase().next().unwrap_or(c);
        if lc.is_whitespace() {
            if !prev_space {
                out.push(' ');
                prev_space = true;
            }
        } else {
            out.push(lc);
            prev_space = false;
        }
    }
    if out.ends_with(' ') {
        out.pop();
    }
    out
}

/// Tiny hex encoder (avoids pulling another crate just for 16 bytes).
fn hex_encode(bytes: &[u8]) -> String {
    const HEX: &[u8; 16] = b"0123456789abcdef";
    let mut s = String::with_capacity(bytes.len() * 2);
    for b in bytes {
        s.push(HEX[(b >> 4) as usize] as char);
        s.push(HEX[(b & 0x0f) as usize] as char);
    }
    s
}

pub fn register(parent: &Bound<PyModule>) -> PyResult<()> {
    parent.add_class::<ObservationRecordCodec>()?;
    Ok(())
}
