// Copyright (c) Huawei Technologies Co., Ltd. 2026. All rights reserved.

//! UIA and AT-SPI text fields. Only publish offsets proved by the provider.

use super::{span, any, append, integer, is_text, map, nonnegative, path, take, text, Builder, Value};

fn record(value: &Value, allowed: &[&str]) -> bool {
    value.as_map().is_some_and(|fields| fields.iter().all(|(name, _)| allowed.contains(&name.as_str())))
}

fn count(value: &Value, key: &str) -> Option<i64> {
    value.get(key).and_then(integer).filter(|value| (0..=9_007_199_254_740_991).contains(value))
}

fn flag(value: &Value, key: &str) -> Option<bool> {
    value.get(key).and_then(Value::as_bool)
}

fn string<'a>(value: &'a Value, key: &str) -> Option<&'a str> {
    value.get(key).and_then(Value::as_str)
}

fn has_fields(value: &Value) -> bool {
    value.as_map().is_some_and(|items| !items.is_empty())
}

fn status(value: &Value) -> bool {
    record(value, &["state", "detail"])
        && matches!(string(value, "state"), Some("absent" | "unsupported" | "error" | "timeout" | "truncated"))
        && value.get("detail").is_none_or(is_text)
}

fn statuses(value: Option<&Value>) -> bool {
    value.and_then(Value::as_map).is_some_and(|items| items.iter().all(|(_, item)| status(item)))
}

fn anchor(value: &Value) -> bool {
    record(value, &["source", "unit", "units", "complete", "sha256"])
        && string(value, "source") == Some("Text.DocumentRange")
        && string(value, "unit") == Some("utf16")
        && count(value, "units").is_some_and(|units| units <= 65_536)
        && flag(value, "complete").is_some()
        && string(value, "sha256").is_some_and(|hash| hash.len() == 64
            && hash.bytes().all(|c| c.is_ascii_digit() || (b'a'..=b'f').contains(&c)))
}

fn position(value: &Value, units: i64, line: bool) -> Option<(i64, i64)> {
    if !record(value, if line { &["start", "end", "degenerate", "text"] } else { &["start", "end", "degenerate"] }) {
        return None;
    }
    let (start, end) = count(value, "start").zip(count(value, "end"))?;
    let collapsed = flag(value, "degenerate")?;
    (start <= end && end <= units && (!collapsed || start == end)).then_some((start, end))
}

fn not_probed(value: &Value) -> bool {
    record(value, &["state", "detail"]) && string(value, "detail").is_some()
}

fn caret(value: &Value) -> bool {
    if string(value, "state") == Some("notProbed") { return not_probed(value); }
    let complete = string(value, "state") == Some("complete");
    if !record(value, &["source", "values", "fieldStatus", "state"])
        || string(value, "source") != Some("IUIAutomationTextPattern2.GetCaretRange")
        || !(complete || string(value, "state") == Some("partial"))
        || !statuses(value.get("fieldStatus"))
    {
        return false;
    }
    let Some(values) = value.get("values").filter(|value| record(value, &["isActive", "anchor", "range"])) else { return false };
    if flag(values, "isActive").is_none()
        || complete && (values.as_map().unwrap_or_default().len() != 3 || value.get("fieldStatus").is_some_and(has_fields))
        || values.get("anchor").is_some_and(|value| !anchor(value))
    {
        return false;
    }
    values.get("range").is_none_or(|range| values.get("anchor").and_then(|value| count(value, "units"))
        .is_some_and(|units| position(range, units, false).is_some() && flag(range, "degenerate") == Some(true)))
}

fn object_ranges(value: &Value) -> bool {
    if !record(value, &["source", "anchor", "objects", "total", "state", "order", "detail", "anchorStatus", "orderStatus"])
        || string(value, "source") != Some("IUIAutomation.TextPattern.RangeFromChild")
        || !matches!(string(value, "state"), Some("complete" | "partial"))
        || !matches!(string(value, "order"), Some("nativeEndpoints" | "unconfirmed" | "overlapping"))
        || value.get("detail").is_some_and(|value| !is_text(value))
        || ["anchorStatus", "orderStatus"].iter().any(|name| value.get(name).is_some_and(|value| !status(value)))
    {
        return false;
    }
    let Some(anchor_value) = value.get("anchor").filter(|value| anchor(value)) else { return false };
    let units = count(anchor_value, "units").unwrap_or_default();
    let Some(items) = value.get("objects").and_then(Value::as_seq) else { return false };
    let Some(total) = count(value, "total") else { return false };
    let complete = string(value, "state") == Some("complete");
    if items.len() as i64 > total.min(32)
        || complete && (items.len() as i64 != total || value.get("anchorStatus").is_some())
    {
        return false;
    }
    items.iter().all(|item| {
        let node = string(item, "node").is_some_and(|name| !name.is_empty());
        let fields = item.get("values");
        let gaps = item.get("fieldStatus");
        record(item, &["node", "values", "fieldStatus"])
            && (item.get("node").is_none() || node)
            && statuses(gaps)
            && fields.is_some_and(|fields| fields.as_map().is_some() &&
                if has_fields(fields) { position(fields, units, false).is_some() } else { gaps.is_some_and(has_fields) })
            && (!complete || node && !gaps.is_some_and(has_fields))
    })
}

fn line_ranges(value: &Value) -> bool {
    if string(value, "state") == Some("notProbed") { return not_probed(value); }
    if !record(value, &["source", "requestedUnit", "unitSupport", "anchor", "ranges", "state",
        "documentEndReached", "detail", "anchorStatus"])
        || !matches!(string(value, "source"), Some("IUIAutomation.TextPattern.TextUnit.Line"
            | "IUIAutomation.TextChildPattern.TextRange.TextUnit.Line"
            | "System.Windows.Automation.TextPattern; exact HWND/runtime read-only supplement.TextUnit.Line"))
        || string(value, "requestedUnit") != Some("Line")
        || string(value, "unitSupport") != Some("unconfirmed")
        || !matches!(string(value, "state"), Some("complete" | "partial"))
        || flag(value, "documentEndReached").is_none()
        || string(value, "detail").is_none()
        || value.get("anchorStatus").is_some_and(|value| !status(value))
    {
        return false;
    }
    let Some(anchor_value) = value.get("anchor").filter(|value| anchor(value)) else { return false };
    let units = count(anchor_value, "units").unwrap_or_default();
    let Some(ranges) = value.get("ranges").and_then(Value::as_seq).filter(|items| items.len() <= 32) else { return false };
    let complete = string(value, "state") == Some("complete");
    if complete && (flag(value, "documentEndReached") != Some(true) || flag(anchor_value, "complete") != Some(true)
        || value.get("anchorStatus").is_some())
    {
        return false;
    }
    let (mut cursor, mut preview, mut covered) = (0, 0, true);
    for item in ranges {
        let Some(fields) = item.get("values").filter(|value| value.as_map().is_some()) else { return false };
        let Some(gaps) = item.get("fieldStatus") else { return false };
        if !record(item, &["values", "fieldStatus"]) || !statuses(Some(gaps)) || !record(gaps, &["position", "text"]) {
            return false;
        }
        if !has_fields(fields) {
            if complete || !has_fields(gaps) { return false; }
            continue;
        }
        let Some((start, end)) = position(fields, units, true) else { return false };
        let Some(content) = string(fields, "text") else { return false };
        let length = content.encode_utf16().count() as i64;
        if start < cursor || length > end - start || length != end - start && gaps.get("text").is_none()
            || complete && has_fields(gaps)
        {
            return false;
        }
        covered &= start == cursor;
        cursor = end;
        preview += length;
    }
    preview <= 16_384 && (!complete || covered && cursor == units)
}

fn agreement(node: &Builder<'_>, anchor: &Value, output: &Value, raw: &Value, line: bool) -> Option<&'static str> {
    let Some(content) = string(node.values, "Text.DocumentRange") else { return Some("document snapshot unavailable") };
    let document: Vec<_> = content.encode_utf16().collect();
    let units = count(anchor, "units").unwrap_or_default() as usize;
    let captured = node.row.get("fieldStatus").and_then(|status| status.get("Text.DocumentRange"));
    if document.len() < units {
        let retained = captured.is_some_and(|status| string(status, "state") == Some("truncated")
            && status.get("textRef").is_some_and(super::super::truthy)
            && count(status, "totalUnits").is_some_and(|total| total as usize >= units));
        if !retained { return Some("anchor extends beyond this preview; exact anchor in details"); }
    } else {
        if flag(anchor, "complete") == Some(true) && captured.is_none() && document.len() != units {
            return Some("anchor conflicts with the complete document length");
        }
        let Ok(prefix) = String::from_utf16(&document[..units]) else { return Some("anchor splits a UTF-16 character") };
        let hash: String = crate::diff::sha256(prefix.as_bytes()).iter().map(|byte| format!("{byte:02x}")).collect();
        if string(anchor, "sha256") != Some(&hash) { return Some("anchor conflicts with this document snapshot"); }
    }
    let Some((start, end)) = count(output, "start").zip(count(output, "end")) else { return None };
    if [start, end].iter().any(|offset| document.get(*offset as usize).is_some_and(|unit| (0xdc00..=0xdfff).contains(unit))) {
        return Some("Text endpoints split a UTF-16 character.");
    }
    if line {
        let fields = raw.get("values").unwrap_or(raw);
        let start = start as usize;
        if start < document.len() && string(fields, "text").is_some_and(|text| text.encode_utf16()
            .zip(document[start..].iter().copied()).any(|(left, right)| left != right))
        {
            return Some("Text span conflicts with the captured document.");
        }
    }
    None
}

fn uia_span(node: &mut Builder<'_>, name: &str, outer: &Value, raw: &Value, at: &[Value]) -> Value {
    let mut output = span(node, raw, at);
    let anchor = outer.get("anchor").or_else(|| outer.get("values").and_then(|fields| fields.get("anchor")));
    let detail = match anchor {
        None => Some("Text anchor is unavailable."),
        Some(anchor) => {
            // Compare the anchor before taking a position out of the captured span.
            let prefix = agreement(node, anchor, &Value::Null, raw, false);
            prefix.or_else(|| {
                if outer.get("anchorStatus").is_some() {
                    Some("The document anchor changed during collection.")
                } else {
                    agreement(node, anchor, &output, raw, name == "Text.LineRanges")
                }
            })
        }
    };
    if let Some(detail) = detail {
        node.gap(at, "conflict", Some(detail));
        let position = map(["start", "end"].into_iter().filter_map(|key| take(&mut output, key).map(|value| (key, value))));
        if has_fields(&position) {
            node.supplement(&format!("{name}.position"), &position, Some("text.unconfirmed_position"), false);
        }
    }
    output
}

fn coverage(node: &mut Builder<'_>, name: &str, field: &str, value: &Value) {
    let evidence = map([
        ("unitSupport", "unit_support"), ("requestedUnit", "requested_unit"),
        ("documentEndReached", "document_end_observed"), ("order", "order"), ("total", "total"), ("detail", "detail"),
    ].into_iter().filter_map(|(raw, target)| value.get(raw).cloned().map(|value| (target, value))));
    if has_fields(&evidence) {
        node.supplement(&format!("{name}.coverage"), &evidence, Some(&format!("text.{field}.coverage")), false);
    }
    for key in ["orderStatus", "anchorStatus"] {
        if let Some(gap) = value.get(key) {
            node.issue(&path(&["text", field]), string(gap, "state").unwrap_or("error"),
                gap.get("detail").cloned(), None, Vec::new());
        }
    }
}

pub(in crate::facts) fn field(node: &mut Builder<'_>, name: &str, value: &Value) -> bool {
    let (key, valid) = match name {
        "Text.CaretRange" => ("caret", caret(value)),
        "Text.ObjectRanges" => ("embedded", object_ranges(value)),
        "Text.LineRanges" => ("line_ranges", line_ranges(value)),
        _ => ("", true),
    };
    if !valid {
        node.supplement(name, value, None, false);
        node.gap(&path(&["text", key]), "error", Some("Malformed text-range evidence cannot establish confirmed offsets."));
        return true;
    }
    match name {
        "Text.DocumentRange" | "Text.text" | "Text.characterCount" | "Text.IsReadOnly" | "Text.SupportedTextSelection" => {
            let (key, output, expected): (_, _, fn(&Value) -> bool) = match name {
                "Text.characterCount" => ("character_count", value.clone(), nonnegative),
                "Text.IsReadOnly" => ("read_only", value.clone(), |value| value.as_bool().is_some() || value.as_str() == Some("mixed")),
                "Text.SupportedTextSelection" => {
                    let selected = match value.as_str() {
                        Some("None") => "none", Some("Single") => "single", Some("Multiple") => "multiple",
                        _ => {
                            node.text_info();
                            node.gap(&path(&["text", "supported_selection"]), "error", Some("Unrecognized text selection support declaration."));
                            node.supplement(name, value, None, false);
                            return true;
                        }
                    };
                    ("supported_selection", text(selected), any)
                }
                _ => ("content", value.clone(), is_text),
            };
            node.text_info();
            node.put(name, &["text", key], output, expected);
        }
        "Text.caretOffset" => {
            node.text_info();
            if nonnegative(value) {
                node.put(name, &["text", "caret"], map([
                    ("start", value.clone()), ("end", value.clone()), ("collapsed", Value::Bool(true)),
                    ("attributes", Value::Map(Vec::new())),
                ]), any);
            } else {
                node.gap(&path(&["text", "caret"]), "error", Some("Caret offset is not a nonnegative integer."));
            }
        }
        "Text.selections" | "Text.Selection" | "Text.SelectionBounds" | "Text.VisibleRanges" => {
            node.text_info();
            let key = if name == "Text.VisibleRanges" { "visible_ranges" } else { "selections" };
            let items = if name == "Text.selections" {
                value.as_seq().unwrap_or(std::slice::from_ref(value))
            } else if let Some(items) = value.get("ranges").and_then(Value::as_seq) {
                items
            } else {
                return false;
            };
            let spans: Vec<_> = items.iter().enumerate().map(|(index, item)| {
                span(node, item, &append(path(&["text", key]), Value::Int(index as i64)))
            }).collect();
            if name == "Text.SelectionBounds" && node.text_info().get(key).is_some() {
                node.supplement(name, value, Some("text_selection_geometry"), false);
            } else {
                node.put(name, &["text", key], Value::Seq(spans), any);
            }
            if name != "Text.selections" {
                if value.get("state").is_some_and(|value| !matches!(value, Value::Null) && value.as_str() != Some("complete")) {
                    node.issue(&path(&["text", key]), "partial", value.get("detail").cloned(), None, Vec::new());
                }
                if let Some(total) = value.get("total").filter(|value| nonnegative(value)) {
                    node.supplement(&format!("{name}.coverage"), &map([("total", total.clone())]),
                        Some(&format!("text.{key}.coverage")), false);
                }
            }
        }
        "Text.CaretRange" | "Text.LineRanges" | "Text.ObjectRanges" => {
            node.text_info();
            if string(value, "state") == Some("notProbed") {
                node.issue(&path(&["text", key]), "not_read", value.get("detail").cloned(), None, Vec::new());
                return true;
            }
            if name == "Text.CaretRange" {
                if let Some(values) = value.get("values") {
                    if let Some(active) = flag(values, "isActive") {
                        node.put(name, &["text", "caret_active"], Value::Bool(active), any);
                    }
                    if let Some(range) = values.get("range") {
                        let caret = uia_span(node, name, value, range, &path(&["text", key]));
                        node.put(name, &["text", key], caret, any);
                    }
                }
                for (_, status) in value.get("fieldStatus").and_then(Value::as_map).into_iter().flatten() {
                    node.issue(&path(&["text", key]), string(status, "state").unwrap_or("error"), status.get("detail").cloned(), None, Vec::new());
                }
            } else {
                let items = if name == "Text.LineRanges" { "ranges" } else { "objects" };
                let mut output = Vec::new();
                for (index, item) in value.get(items).and_then(Value::as_seq).unwrap_or_default().iter().enumerate() {
                    let mut at = append(path(&["text", key]), Value::Int(index as i64));
                    if name == "Text.LineRanges" {
                        output.push(uia_span(node, name, value, item, &at));
                    } else if let Some(identity) = string(item, "node").filter(|name| node.kinds.get(*name).is_some_and(|kind| kind == "element")) {
                        at.push(Value::Int(1));
                        output.push(Value::Seq(vec![text(identity), uia_span(node, name, value, item, &at)]));
                    } else {
                        node.gap(&at, "unavailable", Some("Embedded object reference was not obtained."));
                    }
                }
                node.put(name, &["text", key], Value::Seq(output), any);
                coverage(node, name, key, value);
                if string(value, "state") != Some("complete") {
                    node.issue(&path(&["text", key]), "partial", value.get("detail").cloned(), None, Vec::new());
                }
            }
        }
        _ => return false,
    }
    true
}
