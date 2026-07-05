//! CPU-hot single-line runtime log hints.
//!
//! This module intentionally returns facts, not report decisions. Python still
//! owns category gating, context assembly, and administrator-facing wording.

use pyo3::prelude::*;
use pyo3::types::PyDict;
use regex::Regex;
use sha1::{Digest, Sha1};
use std::sync::OnceLock;

#[pyfunction]
#[pyo3(signature = (line, max_line_length = 1000))]
pub fn runtime_log_hints<'py>(
    py: Python<'py>,
    line: &str,
    max_line_length: usize,
) -> PyResult<Bound<'py, PyDict>> {
    let content = sanitize_line(&truncate_chars(line, max_line_length));
    let level = detect_level(&content);
    let fingerprint = fingerprint(&content);
    let out = PyDict::new(py);
    out.set_item("content", &content)?;
    out.set_item("level", level)?;
    out.set_item("fingerprint", fingerprint)?;
    if let Some((player, message)) = detect_chat_message(&content) {
        let meaningless = detect_meaningless_message(&message);
        out.set_item("chatPlayer", player)?;
        out.set_item("chatMessage", message)?;
        out.set_item("chatMeaningless", meaningless)?;
    }
    if let Some((player, check)) = detect_vulcan_alert(&content) {
        out.set_item("vulcanPlayer", player)?;
        out.set_item("vulcanCheck", check)?;
    }
    Ok(out)
}

pub fn register(parent: &Bound<PyModule>) -> PyResult<()> {
    parent.add_function(wrap_pyfunction!(runtime_log_hints, parent)?)?;
    Ok(())
}

fn truncate_chars(value: &str, max_length: usize) -> String {
    if max_length == 0 {
        return String::new();
    }
    if value.chars().count() <= max_length {
        return value.to_string();
    }
    if max_length <= 3 {
        return value.chars().take(max_length).collect();
    }
    let mut out: String = value.chars().take(max_length - 3).collect();
    out.push_str("...");
    out
}

fn sanitize_line(line: &str) -> String {
    let no_ansi = ansi_re().replace_all(line, "");
    let redacted = ipv4_re().replace_all(&no_ansi, "<ip>");
    redacted.trim().to_string()
}

fn detect_level(line: &str) -> &'static str {
    if let Some(caps) = level_re().captures(line) {
        let level = caps.name("level").map(|m| m.as_str()).unwrap_or("INFO");
        if level.eq_ignore_ascii_case("WARNING") {
            return "WARN";
        }
        return match level.to_ascii_uppercase().as_str() {
            "FATAL" => "FATAL",
            "SEVERE" => "SEVERE",
            "ERROR" => "ERROR",
            "WARN" => "WARN",
            "INFO" => "INFO",
            "DEBUG" => "DEBUG",
            "TRACE" => "TRACE",
            _ => "INFO",
        };
    }
    let lowered = line.to_ascii_lowercase();
    if ["fatal", "severe", "error", "exception"]
        .iter()
        .any(|word| lowered.contains(word))
    {
        return "ERROR";
    }
    if ["warn", "warning", "failed", "timeout"]
        .iter()
        .any(|word| lowered.contains(word))
    {
        return "WARN";
    }
    "INFO"
}

fn fingerprint(line: &str) -> String {
    let mut text = sanitize_line(line).to_lowercase();
    text = prefix_re().replace_all(&text, "").to_string();
    text = full_ts_re().replace_all(&text, "").to_string();
    text = time_re().replace_all(&text, "").to_string();
    text = uuid_re().replace_all(&text, "<uuid>").to_string();
    text = ipv4_re().replace_all(&text, "<ip>").to_string();
    text = hex_re().replace_all(&text, "0x<num>").to_string();
    text = replace_numbers(&text);
    text = collapse_ws(&text);
    if text.is_empty() {
        text = "empty".to_string();
    }
    let digest = Sha1::digest(text.as_bytes());
    hex_prefix(&digest, 24)
}

fn detect_chat_message(content: &str) -> Option<(String, String)> {
    let mut stripped = content.to_string();
    let has_chat_thread = chat_thread_re().is_match(content);
    if has_chat_thread {
        stripped = chat_thread_re().replace_all(content, "").trim().to_string();
    }
    stripped = prefix_re().replace_all(&stripped, "").trim().to_string();
    if let Some(caps) = chat_plugin_re().captures(&stripped) {
        let player = caps.name("player")?.as_str().trim().to_string();
        let message = caps.name("message")?.as_str().trim().to_string();
        if !player.is_empty() && !message.is_empty() {
            return Some((player, message));
        }
    }
    if let Some(caps) = chat_player_prefix_re().captures(&stripped) {
        let player = caps.name("player")?.as_str().trim().to_string();
        let message = caps.name("message")?.as_str().trim().to_string();
        if !player.is_empty() && !message.is_empty() {
            return Some((player, message));
        }
    }
    if has_chat_thread && !stripped.is_empty() {
        return Some((String::new(), stripped));
    }
    None
}

fn detect_vulcan_alert(content: &str) -> Option<(String, String)> {
    let caps = vulcan_player_re().captures(content)?;
    let player = caps.name("player")?.as_str().trim().to_string();
    let check = caps
        .name("check")?
        .as_str()
        .trim_matches(|c: char| c == ' ' || c == ':' || c == ',' || c == '.')
        .to_string();
    Some((player, check))
}

fn detect_meaningless_message(message: &str) -> bool {
    if message.is_empty() {
        return false;
    }
    let mut previous: Option<char> = None;
    let mut run_len = 0usize;
    for ch in message.chars() {
        if Some(ch) == previous {
            run_len += 1;
        } else {
            previous = Some(ch);
            run_len = 1;
        }
        if run_len >= 8 {
            return true;
        }
    }
    let has_content = message
        .chars()
        .any(|c| c.is_alphanumeric() || ('\u{4e00}'..='\u{9fff}').contains(&c));
    !has_content && message.chars().count() >= 3
}

fn replace_numbers(text: &str) -> String {
    let chars: Vec<char> = text.chars().collect();
    let mut out = String::with_capacity(text.len());
    let mut index = 0usize;
    while index < chars.len() {
        let ch = chars[index];
        let starts_number = ch.is_ascii_digit()
            || (ch == '-' && index + 1 < chars.len() && chars[index + 1].is_ascii_digit());
        let prev_blocks = index > 0 && (chars[index - 1].is_ascii_alphabetic() || chars[index - 1] == '_');
        if starts_number && !prev_blocks {
            out.push_str("<num>");
            if ch == '-' {
                index += 1;
            }
            while index < chars.len() && chars[index].is_ascii_digit() {
                index += 1;
            }
            if index + 1 < chars.len() && chars[index] == '.' && chars[index + 1].is_ascii_digit() {
                index += 1;
                while index < chars.len() && chars[index].is_ascii_digit() {
                    index += 1;
                }
            }
            continue;
        }
        out.push(ch);
        index += 1;
    }
    out
}

fn collapse_ws(text: &str) -> String {
    let mut out = String::with_capacity(text.len());
    let mut prev_space = true;
    for ch in text.chars() {
        if ch.is_whitespace() {
            if !prev_space {
                out.push(' ');
                prev_space = true;
            }
        } else {
            out.push(ch);
            prev_space = false;
        }
    }
    if out.ends_with(' ') {
        out.pop();
    }
    out
}

fn hex_prefix(bytes: &[u8], len: usize) -> String {
    const HEX: &[u8; 16] = b"0123456789abcdef";
    let mut out = String::with_capacity(len);
    for byte in bytes {
        if out.len() >= len {
            break;
        }
        out.push(HEX[(byte >> 4) as usize] as char);
        if out.len() >= len {
            break;
        }
        out.push(HEX[(byte & 0x0f) as usize] as char);
    }
    out
}

fn ansi_re() -> &'static Regex {
    static RE: OnceLock<Regex> = OnceLock::new();
    RE.get_or_init(|| Regex::new(r"\x1b\[[0-9;]*[A-Za-z]").expect("ansi regex"))
}

fn ipv4_re() -> &'static Regex {
    static RE: OnceLock<Regex> = OnceLock::new();
    RE.get_or_init(|| Regex::new(r"\b(?:\d{1,3}\.){3}\d{1,3}\b").expect("ipv4 regex"))
}

fn uuid_re() -> &'static Regex {
    static RE: OnceLock<Regex> = OnceLock::new();
    RE.get_or_init(|| {
        Regex::new(r"(?i)\b[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}\b")
            .expect("uuid regex")
    })
}

fn hex_re() -> &'static Regex {
    static RE: OnceLock<Regex> = OnceLock::new();
    RE.get_or_init(|| Regex::new(r"(?i)\b0x[0-9a-f]+\b").expect("hex regex"))
}

fn prefix_re() -> &'static Regex {
    static RE: OnceLock<Regex> = OnceLock::new();
    RE.get_or_init(|| {
        Regex::new(
            r"(?i)^\[?\d{2}:\d{2}:\d{2}(?:[.,]\d{1,6})?\]?\s*(?:\[[^\]]+\]\s*)?(?:\[[A-Z]+\]\s*)?",
        )
        .expect("prefix regex")
    })
}

fn full_ts_re() -> &'static Regex {
    static RE: OnceLock<Regex> = OnceLock::new();
    RE.get_or_init(|| {
        Regex::new(r"^\[?(?P<date>\d{4}-\d{2}-\d{2})[ T](?P<time>\d{2}:\d{2}:\d{2})(?:[.,](?P<ms>\d{1,6}))?")
            .expect("full timestamp regex")
    })
}

fn time_re() -> &'static Regex {
    static RE: OnceLock<Regex> = OnceLock::new();
    RE.get_or_init(|| {
        Regex::new(r"^\[?(?P<time>\d{2}:\d{2}:\d{2})(?:[.,](?P<ms>\d{1,6}))?\]?")
            .expect("time regex")
    })
}

fn level_re() -> &'static Regex {
    static RE: OnceLock<Regex> = OnceLock::new();
    RE.get_or_init(|| {
        Regex::new(r"(?i)(?:^|[\[/\s:])(?P<level>FATAL|SEVERE|ERROR|WARN|WARNING|INFO|DEBUG|TRACE)(?:[\]/\s:]|$)")
            .expect("level regex")
    })
}

fn chat_thread_re() -> &'static Regex {
    static RE: OnceLock<Regex> = OnceLock::new();
    RE.get_or_init(|| Regex::new(r"(?i)\[Async Chat Thread[^\]]*\]\s*:?\s*").expect("chat thread regex"))
}

fn chat_player_prefix_re() -> &'static Regex {
    static RE: OnceLock<Regex> = OnceLock::new();
    RE.get_or_init(|| Regex::new(r"^\s*<(?P<player>[^>\s]{1,40})>\s*(?P<message>.*)$").expect("chat player regex"))
}

fn chat_plugin_re() -> &'static Regex {
    static RE: OnceLock<Regex> = OnceLock::new();
    RE.get_or_init(|| {
        Regex::new(r"(?:\[Not Secure\]\s*)?(?:\[[^\]]{1,30}\]\s*)*(?P<player>[A-Za-z0-9_]{1,16})\s*>>\s*(?P<message>.+)$")
            .expect("chat plugin regex")
    })
}

fn vulcan_player_re() -> &'static Regex {
    static RE: OnceLock<Regex> = OnceLock::new();
    RE.get_or_init(|| {
        Regex::new(r"(?i)\[Vulcan\][\]:>\s]*(?P<player>[A-Za-z0-9_]{1,16})\s+failed\s+(?P<check>[A-Za-z]+(?:\s*\([^)]+\))?)")
            .expect("vulcan regex")
    })
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn detects_carbonchat_message() {
        let parsed = detect_chat_message(
            "[16:34:47] [Async Chat Thread - #1/INFO]: [Not Secure] [生存区] TypeThe0ry >> 1",
        )
        .expect("chat");
        assert_eq!(parsed.0, "TypeThe0ry");
        assert_eq!(parsed.1, "1");
    }

    #[test]
    fn detects_vulcan_alert_but_not_lifecycle() {
        let alert = detect_vulcan_alert("[Vulcan] Steve failed Reach (VL: 5)").expect("alert");
        assert_eq!(alert.0, "Steve");
        assert_eq!(alert.1, "Reach (VL: 5)");
        assert!(detect_vulcan_alert("[Vulcan] Starting Vulcan...").is_none());
    }

    #[test]
    fn fingerprint_redacts_numbers_and_ips() {
        let a = fingerprint("[16:00:00] [Server thread/ERROR]: failed at 1.2.3.4:25565 id 123");
        let b = fingerprint("[16:00:01] [Server thread/ERROR]: failed at 5.6.7.8:25566 id 456");
        assert_eq!(a, b);
    }

    #[test]
    fn meaningless_repeat_and_symbols() {
        assert!(detect_meaningless_message("hhhhhhhh"));
        assert!(detect_meaningless_message("!!!???"));
        assert!(!detect_meaningless_message("hello world"));
    }
}
