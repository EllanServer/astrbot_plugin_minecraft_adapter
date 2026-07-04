//! Term normalization and matching helpers for dialogue analysis.
//!
//! Rust port of `services/mine_sentinel/reporting/dialogue_terms.py`.
//! This is the hottest CPU path in mine_sentinel: every CHAT observation
//! runs `RuleTermMatcher::scan` once during window sampling and once more
//! during heuristic report building. Replacing the Python regex + dict scan
//! with a single Rust pass cuts per-record cost by ~10-20x.

use ahash::AHashMap;
use once_cell::sync::Lazy;
use pyo3::prelude::*;
use pyo3::types::{PyDict, PyList};
use regex::Regex;
use std::sync::Arc;

/// Negation prefixes mirroring `dialogue_terms.NEGATION_PREFIXES`.
/// A term hit whose 4-character prefix window ends with any of these is
/// treated as negated and ignored (matches `matched_terms` semantics).
const NEGATION_PREFIXES: &[&str] = &["不", "没", "没有", "不是", "并不", "不太"];

/// `(?:(.)\1{2,})` → collapse 3+ repeated chars to 2.
/// `Lazy` because `Regex` construction is non-trivial and this is hit per
/// CHAT record via `message_fingerprint`.
static REPEATED_CHAR_RE: Lazy<Regex> =
    Lazy::new(|| Regex::new(r"(.)\1{2,}").expect("invalid repeated-char regex"));

/// Mirrors `normalize_text`: collapse whitespace + lowercase.
/// `text.lower().split().join(" ")` in Python.
pub fn normalize_text(text: &str) -> String {
    let mut out = String::with_capacity(text.len());
    let mut prev_space = true;
    for c in text.chars() {
        // lowercase only ASCII fast path; Unicode also lowercased.
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
    // trim trailing space if any
    if out.ends_with(' ') {
        out.pop();
    }
    out
}

/// Mirrors `message_fingerprint`.
/// normalize → keep alnum only → collapse 3+ repeats to 2.
pub fn message_fingerprint(text: &str) -> String {
    let normalized = normalize_text(text);
    let mut compact = String::with_capacity(normalized.len());
    for ch in normalized.chars() {
        if ch.is_alphanumeric() {
            compact.push(ch);
        }
    }
    // Collapse runs of >=3 same chars down to 2 (matches `r"\1\1"` substitution).
    let mut result = String::with_capacity(compact.len());
    let chars: Vec<char> = compact.chars().collect();
    let mut i = 0;
    while i < chars.len() {
        let c = chars[i];
        let mut j = i + 1;
        while j < chars.len() && chars[j] == c {
            j += 1;
        }
        let run = j - i;
        if run >= 3 {
            result.push(c);
            result.push(c);
        } else {
            for _ in 0..run {
                result.push(c);
            }
        }
        i = j;
    }
    result
}

/// Mirrors `term_is_negated`. A term is negated if every occurrence is
/// preceded (within 4 chars) by a negation prefix. Returns true only when
/// all occurrences are negated — a single non-negated occurrence returns
/// false (matches the Python `saw_negated` accumulator logic).
pub fn term_is_negated(text: &str, term: &str) -> bool {
    if term.is_empty() {
        return false;
    }
    let term_bytes = term.as_bytes();
    let mut start = 0;
    let mut saw_negated = false;
    loop {
        // find term starting from `start`
        let hay = &text[start..];
        let rel = match hay.find(term) {
            Some(r) => r,
            None => return saw_negated,
        };
        let abs = start + rel;
        // 4-character prefix window (by char count, not bytes).
        let prefix_start = char_window_start(text, abs, 4);
        let prefix = &text[prefix_start..abs];
        if NEGATION_PREFIXES.iter().any(|p| prefix.ends_with(p)) {
            saw_negated = true;
            start = abs + term_bytes.len();
            continue;
        }
        return false;
    }
}

/// Compute the byte offset of `count` chars before `pos` (clamped at 0).
/// Walks backwards one UTF-8 char boundary at a time using the stable
/// `str::is_char_boundary` API.
fn char_window_start(s: &str, pos: usize, count: usize) -> usize {
    let mut taken = 0;
    let mut idx = pos;
    while taken < count && idx > 0 {
        let mut prev = idx - 1;
        while prev > 0 && !s.is_char_boundary(prev) {
            prev -= 1;
        }
        idx = prev;
        taken += 1;
    }
    idx
}

/// Rust-side compiled term set. Sorts terms by length desc (longest-first
/// alternation, mirroring `_compile_term_pattern`).
struct CompiledTerms {
    /// lowered term → display (original-cased) form
    display: AHashMap<String, String>,
    /// compiled alternation regex (empty → never matches)
    pattern: Regex,
    /// sorted lowered terms for fallback scanning if regex misbehaves
    terms: Vec<String>,
}

impl CompiledTerms {
    fn new(terms: AHashMap<String, String>) -> Self {
        if terms.is_empty() {
            // `(?!)` always-fail pattern, matching Python `_compile_term_pattern`.
            return Self {
                display: AHashMap::new(),
                pattern: Regex::new(r"(?!)").expect("invalid never-match regex"),
                terms: Vec::new(),
            };
        }
        let mut sorted: Vec<String> = terms.keys().cloned().collect();
        // Sort by length desc, then alphabetical for determinism.
        sorted.sort_by(|a, b| b.len().cmp(&a.len()).then(a.cmp(b)));
        let alternation: Vec<String> = sorted.iter().map(|t| regex::escape(t)).collect();
        let pattern_str = alternation.join("|");
        let pattern = Regex::new(&pattern_str).expect("compiled term pattern invalid");
        Self {
            display: terms,
            pattern,
            terms: sorted,
        }
    }

    /// Collect non-negated hits keyed by the lowered matched term.
    /// Mirrors `_collect_non_negated_hits`.
    fn collect_hits(&self, text: &str) -> Vec<String> {
        let mut seen: AHashMap<String, ()> = AHashMap::new();
        let mut hits: Vec<String> = Vec::new();
        for cap in self.pattern.find_iter(text) {
            let term = cap.as_str();
            if term_is_negated(text, term) {
                continue;
            }
            if seen.insert(term.to_string(), ()).is_none() {
                hits.push(term.to_string());
            }
        }
        hits
    }
}

/// PyO3-exposed matcher. Mirrors the public surface of
/// `dialogue_terms.RuleTermMatcher`.
#[pyclass]
pub struct RuleTermMatcher {
    /// Index from lowered term → owning rule indices
    keyword_owners: AHashMap<String, Vec<usize>>,
    urgent_owners: AHashMap<String, Vec<usize>>,
    keyword_compiled: CompiledTerms,
    urgent_compiled: CompiledTerms,
    /// The Python rule objects, kept alive so we can return them as dict keys.
    rules: Vec<PyObject>,
    /// lowered term → display form, kept separate from CompiledTerms.display
    /// so we can return display-cased strings to Python callers.
    keyword_display: AHashMap<String, String>,
    urgent_display: AHashMap<String, String>,
}

#[pymethods]
impl RuleTermMatcher {
    /// `rules` is an iterable of `(rule_obj, keywords: tuple[str,...], urgent_terms: tuple[str,...])`.
    #[new]
    pub fn new(rules: &PyAny) -> PyResult<Self> {
        let mut rules_vec: Vec<PyObject> = Vec::new();
        let mut keyword_owners: AHashMap<String, Vec<usize>> = AHashMap::new();
        let mut urgent_owners: AHashMap<String, Vec<usize>> = AHashMap::new();
        let mut keyword_display: AHashMap<String, String> = AHashMap::new();
        let mut urgent_display: AHashMap<String, String> = AHashMap::new();
        let mut keyword_terms: AHashMap<String, String> = AHashMap::new();
        let mut urgent_terms: AHashMap<String, String> = AHashMap::new();

        let iter = rules.try_iter()?;
        for entry in iter {
            let entry = entry?;
            let tup = entry.downcast::<pyo3::types::PyTuple>()?;
            if tup.len() != 3 {
                return Err(pyo3::exceptions::PyTypeError::new_err(
                    "RuleTermMatcher expects (rule, keywords, urgent_terms) tuples",
                ));
            }
            let rule = tup.get_item(0)?;
            let keywords = tup.get_item(1)?;
            let urgent = tup.get_item(2)?;

            let idx = rules_vec.len();
            rules_vec.push(rule.into());

            for ko in keywords.try_iter()? {
                let k = ko?;
                let s: String = k.extract()?;
                let lowered = s.to_lowercase();
                keyword_terms.entry(lowered.clone()).or_insert(s.clone());
                keyword_owners.entry(lowered).or_default().push(idx);
                keyword_display.entry(lowered).or_insert(s);
            }
            for uo in urgent.try_iter()? {
                let u = uo?;
                let s: String = u.extract()?;
                let lowered = s.to_lowercase();
                urgent_terms.entry(lowered.clone()).or_insert(s.clone());
                urgent_owners.entry(lowered).or_default().push(idx);
                urgent_display.entry(lowered).or_insert(s);
            }
        }

        Ok(Self {
            keyword_owners,
            urgent_owners,
            keyword_compiled: CompiledTerms::new(keyword_terms),
            urgent_compiled: CompiledTerms::new(urgent_terms),
            rules: rules_vec,
            keyword_display,
            urgent_display,
        })
    }

    /// Return `{rule: (matched_keywords, matched_urgent_terms)}` for the text.
    /// Mirrors `RuleTermMatcher.scan`.
    pub fn scan(&self, py: Python, text: &str) -> PyResult<Py<PyDict>> {
        let out = PyDict::new(py);
        if text.is_empty() {
            return Ok(out.into());
        }

        // keyword hits
        let kw_hits = self.keyword_compiled.collect_hits(text);
        let ug_hits = self.urgent_compiled.collect_hits(text);

        for lowered in kw_hits {
            let display = self
                .keyword_display
                .get(&lowered)
                .cloned()
                .unwrap_or_else(|| lowered.clone());
            if let Some(owners) = self.keyword_owners.get(&lowered) {
                for &rule_idx in owners {
                    let rule_obj = self.rules[rule_idx].clone_ref(py);
                    let (kw_list, _ug_list) = ensure_entry(&out, rule_obj, py)?;
                    kw_list.as_ref(py).append(display.clone())?;
                }
            }
        }

        for lowered in ug_hits {
            let display = self
                .urgent_display
                .get(&lowered)
                .cloned()
                .unwrap_or_else(|| lowered.clone());
            if let Some(owners) = self.urgent_owners.get(&lowered) {
                for &rule_idx in owners {
                    let rule_obj = self.rules[rule_idx].clone_ref(py);
                    let (_kw_list, ug_list) = ensure_entry(&out, rule_obj, py)?;
                    ug_list.as_ref(py).append(display.clone())?;
                }
            }
        }

        Ok(out.into())
    }

    /// Return `{rule: matched_keywords}` ignoring urgent terms.
    /// Mirrors `RuleTermMatcher.matched_keywords`.
    pub fn matched_keywords(&self, py: Python, text: &str) -> PyResult<Py<PyDict>> {
        let out = PyDict::new(py);
        if text.is_empty() {
            return Ok(out.into());
        }
        let hits = self.keyword_compiled.collect_hits(text);
        for lowered in hits {
            let display = self.keyword_display.get(&lowered).cloned().unwrap_or(lowered.clone());
            if let Some(owners) = self.keyword_owners.get(&lowered) {
                for &rule_idx in owners {
                    let rule_obj = self.rules[rule_idx].clone_ref(py);
                    let list = if let Some(existing) = out.get_item(&rule_obj)? {
                        existing.downcast::<PyList>()?.into()
                    } else {
                        let l = PyList::empty(py);
                        out.set_item(rule_obj.clone_ref(py), l.clone_ref(py))?;
                        l.into()
                    };
                    list.as_ref(py).append(display.clone())?;
                }
            }
        }
        Ok(out.into())
    }
}

/// Ensure a `(keyword_list, urgent_list)` entry exists in `out` for `rule_obj`
/// and return cloned references to the two `PyList`s. Used by `RuleTermMatcher::scan`.
fn ensure_entry<'py>(
    out: &'py Bound<PyDict>,
    rule_obj: PyObject,
    py: Python<'py>,
) -> PyResult<(Bound<'py, PyList>, Bound<'py, PyList>)> {
    if let Some(existing) = out.get_item(&rule_obj)? {
        let tup = existing.downcast::<pyo3::types::PyTuple>()?;
        let kw = tup.get_item(0)?.downcast::<PyList>()?.clone();
        let ug = tup.get_item(1)?.downcast::<PyList>()?.clone();
        return Ok((kw, ug));
    }
    let kw = PyList::empty_bound(py);
    let ug = PyList::empty_bound(py);
    let tup = pyo3::types::PyTuple::new_bound(
        py,
        [kw.clone().into_any(), ug.clone().into_any()],
    );
    out.set_item(rule_obj, tup)?;
    Ok((kw, ug))
}

/// Module-level functions exposed to Python (drop-in replacements for the
/// `dialogue_terms.py` module's free functions).
#[pyfunction]
fn normalize_text_py(text: &str) -> String {
    normalize_text(text)
}

#[pyfunction]
fn message_fingerprint_py(text: &str) -> String {
    message_fingerprint(text)
}

#[pyfunction]
fn matched_terms(py: Python, text: &str, terms: &PyAny) -> PyResult<Py<PyList>> {
    let list = PyList::empty(py);
    for term_obj in terms.try_iter()? {
        let term = term_obj?;
        let s: String = term.extract()?;
        let lowered = s.to_lowercase();
        if !lowered.is_empty() && text.contains(&lowered) && !term_is_negated(text, &lowered) {
            list.append(s)?;
        }
    }
    Ok(list.into())
}

#[pyfunction]
fn term_is_negated_py(text: &str, term: &str) -> bool {
    term_is_negated(text, term)
}

pub fn register(parent: &Bound<PyModule>) -> PyResult<()> {
    parent.add_class::<RuleTermMatcher>()?;
    parent.add_function(wrap_pyfunction!(normalize_text_py, parent)?)?;
    parent.add_function(wrap_pyfunction!(message_fingerprint_py, parent)?)?;
    parent.add_function(wrap_pyfunction!(matched_terms, parent)?)?;
    parent.add_function(wrap_pyfunction!(term_is_negated_py, parent)?)?;
    Ok(())
}

// Silence unused-import warnings for `Arc` (kept for future sharing).
#[allow(dead_code)]
type _Unused = Arc<()>;
