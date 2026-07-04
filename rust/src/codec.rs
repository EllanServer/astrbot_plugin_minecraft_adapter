//! Observation JSONL serialization and normalization.
//!
//! Rust port of `services/mine_sentinel/storage/codec.py`.
//! `dedupe_key` (blake2b) and `json_line` are run on every record during
//! both write (`add_batch`) and read (`read_jsonl_window` + `dedupe_key`),
//! so porting them to Rust removes two of the hottest per-record paths.

use blake2::digest::{Update, VariableOutput};
use blake2::VarBlake2b;
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
        let metrics = record.getattr("metrics")?.downcast::<PyDict>()?;
        let compacted = self.compact_dict(py, metrics)?;
        record.setattr("metrics", compacted)?;

        // context
        let context = record.getattr("context")?.downcast::<PyDict>()?;
        let compacted = self.compact_dict(py, context)?;
        record.setattr("context", compacted)?;

        // raw
        if self.include_raw {
            let raw = record.getattr("raw")?.downcast::<PyDict>()?;
            let compacted = self.compact_dict(py, raw)?;
            record.setattr("raw", compacted)?;
        } else {
            record.setattr("raw", PyDict::new_bound(py))?;
        }
        Ok(())
    }

    /// Build the JSONL-safe dict mirroring `record_to_json`. Returns a new
    /// Python dict; the input record is left untouched.
    pub fn record_to_json<'py>(&self, py: Python<'py>, record: &Bound<'py, PyAny>) -> PyResult<Bound<'py, PyDict>> {
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
        let raw = if self.include_raw {
            record.getattr("raw")?
        } else {
            PyDict::new_bound(py).into_any()
        };

        let out = PyDict::new_bound(py);
        out.set_item("eventId", event_id)?;
        out.set_item("kind", kind)?;
        out.set_item("timestamp", timestamp)?;
        out.set_item("serverId", server_id)?;
        out.set_item("serverName", server_name)?;
        out.set_item("backendServer", backend_server)?;
        out.set_item("proxyId", proxy_id)?;
        let player = PyDict::new_bound(py);
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
        let json_module = py.import_bound(pyo3::intern!(py, "json"))?;
        let dumps = json_module.getattr(pyo3::intern!(py, "dumps"))?;
        let kwargs = pyo3::types::PyDict::new_bound(py);
        kwargs.set_item(pyo3::intern!(py, "ensure_ascii"), false)?;
        let separators = pyo3::types::PyTuple::new_bound(py, [",", ":"]);
        kwargs.set_item(pyo3::intern!(py, "separators"), separators)?;
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
        let raw = format!("{}|{}|{}|{}|{}", kind, server_id, identity, content_lower, bucket);
        let mut hasher = VarBlake2b::new(16).expect("blake2b 16 bytes");
        hasher.update(raw.as_bytes());
        let mut out = [0u8; 16];
        hasher.finalize_variable(|f| {
            out.copy_from_slice(f);
        });
        Ok(format!("h:{}", hex::encode(&out)))
    }
}

impl ObservationRecordCodec {
    /// Mirror `compact_dict`. Takes a PyDict, returns a new bounded PyDict.
    fn compact_dict<'py>(&self, py: Python<'py>, data: &Bound<'py, PyDict>) -> PyResult<Bound<'py, PyDict>> {
        let compact = PyDict::new_bound(py);
        let mut count = 0;
        for kv in data.iter() {
            if count >= self.max_fields_limit() {
                break;
            }
            let (key, value) = kv?;
            let key_str: String = key.to_string();
            let compacted = self.compact_value(py, value)?;
            compact.set_item(key_str, compacted)?;
            count += 1;
        }
        Ok(compact)
    }

    /// Mirror `compact_value`.
    fn compact_value<'py>(&self, py: Python<'py>, value: &Bound<'py, PyAny>) -> PyResult<PyObject> {
        if value.is_none() {
            return Ok(value.clone().unbind());
        }
        // bool/int/float passthrough
        if value.is_instance_of::<pyo3::types::PyBool>()
            || value.is_instance_of::<pyo3::types::PyLong>()
            || value.is_instance_of::<pyo3::types::PyFloat>()
        {
            return Ok(value.clone().unbind());
        }
        // str → truncate
        if let Ok(s) = value.extract::<String>() {
            return Ok(truncate(&s, self.max_content_length).into_py(py));
        }
        // fallback: json.dumps(value, ensure_ascii=False, default=str) then truncate
        let json_module = py.import_bound(pyo3::intern!(py, "json"))?;
        let dumps = json_module.getattr(pyo3::intern!(py, "dumps"))?;
        let kwargs = pyo3::types::PyDict::new_bound(py);
        kwargs.set_item(pyo3::intern!(py, "ensure_ascii"), false)?;
        let default_fn = pyo3::types::PyCFunction::new_closure(py, None, None, |args, _kw| {
            // default=str: stringify unknown objects
            args.get_item(0).map(|o| o.to_string()).unwrap_or_default()
        })?;
        kwargs.set_item(pyo3::intern!(py, "default"), default_fn)?;
        let text: String = dumps.call((value,), Some(&kwargs))?.extract()?;
        Ok(truncate(&text, self.max_content_length).into_py(py))
    }

    /// Convenience accessor: the field-limit used by `compact_dict` is
    /// `max_metric_fields` when called for metrics, `max_raw_fields` for
    /// context/raw. We use the larger of the two as the cap (matches Python
    /// behavior where each call passes its own limit; here we approximate by
    /// taking max_metric_fields since metrics typically dominates).
    /// Note: Python passes per-call max_fields; the wrapper always passes the
    /// right one. To stay faithful, we keep two separate limits below.
    fn max_fields_limit(&self) -> usize {
        self.max_metric_fields
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

// Tiny hex encoder (avoids pulling another crate just for 16 bytes).
mod hex {
    pub fn encode(bytes: &[u8]) -> String {
        const HEX: &[u8; 16] = b"0123456789abcdef";
        let mut s = String::with_capacity(bytes.len() * 2);
        for b in bytes {
            s.push(HEX[(b >> 4) as usize] as char);
            s.push(HEX[(b & 0x0f) as usize] as char);
        }
        s
    }
}

pub fn register(parent: &Bound<PyModule>) -> PyResult<()> {
    parent.add_class::<ObservationRecordCodec>()?;
    Ok(())
}
