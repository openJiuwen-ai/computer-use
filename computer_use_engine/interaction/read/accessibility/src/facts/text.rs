// Copyright (c) Huawei Technologies Co., Ltd. 2026. All rights reserved.

mod provider;
pub(super) use provider::field as provider_field;

use super::{any, append, integer, is_text, map, nonnegative, number, path, set, take, text, Builder, FactsError, Value};

fn span_endpoints(node: &mut Builder<'_>, start: Option<&Value>, end: Option<&Value>, length: Option<&Value>)
    -> Option<(i128, i128)>
{
    let measured = end.is_none_or(|value| matches!(value, Value::Null));
    let (start, other) = start.zip(if measured { length } else { end })?;
    if ![start, other].into_iter().all(|value| matches!(value, Value::Int(_) | Value::BigInt(_))) {
        return None;
    }
    let normalized = start.as_i128().zip(other.as_i128()).and_then(|(start, other)| {
        if measured { start.checked_add(other).map(|end| (start, end)) } else { Some((start, other)) }
    });
    let Some((start, end)) = normalized else {
        node.normalization_error.get_or_insert(FactsError::Unsupported("text range integers outside the native range"));
        return None;
    };
    if start < 0 || end < start { return None; }
    if i64::try_from(start).is_err() || i64::try_from(end - start).is_err() {
        node.normalization_error.get_or_insert(FactsError::Unsupported("text range integers outside the native range"));
        return None;
    }
    Some((start, end))
}

pub(super) fn span(node: &mut Builder<'_>, raw: &Value, at: &[Value]) -> Value {
    let mut result = Value::Map(Vec::new());
    if raw.as_map().is_none() {
        node.gap(at, "error", Some("Text range is not a record."));
        set(&mut result, "attributes", Value::Map(Vec::new()));
        return result;
    }
    let values = raw.get("values").unwrap_or(raw);
    if values.as_map().is_none() {
        node.gap(at, "error", Some("Text range values are unavailable."));
        set(&mut result, "attributes", Value::Map(Vec::new()));
        return result;
    }
    let positions = values.get("range").unwrap_or(values);
    if positions.as_map().is_some() {
        let start = positions.get("start").or_else(|| positions.get("startOffset")).or_else(|| positions.get("location"));
        let end = positions.get("end").or_else(|| positions.get("endOffset"));
        if let Some((start, end)) = span_endpoints(node, start, end, positions.get("length")) {
            set(&mut result, "start", Value::from_i128(start));
            set(&mut result, "end", Value::from_i128(end));
        } else if start.is_some_and(|value| !matches!(value, Value::Null)) || end.is_some_and(|value| !matches!(value, Value::Null)) {
            node.gap(at, "error", Some("Text range endpoints are invalid or incomplete."));
        }
    }
    for (key, field) in [("text", "text"), ("degenerate", "collapsed"), ("collapsed", "collapsed")] {
        if let Some(item) = values.get(key).filter(|item| if key == "text" { is_text(item) } else { matches!(item, Value::Bool(_)) }) {
            set(&mut result, field, item.clone());
        }
    }
    if result.get("collapsed").is_none() && result.get("start").is_some() {
        let collapsed = result.get("start") == result.get("end");
        set(&mut result, "collapsed", Value::Bool(collapsed));
    }
    if let Some(bounds) = values.get("bounds").or_else(|| values.get("rectangles")).and_then(Value::as_seq) {
        let geometry = bounds.iter().enumerate().filter(|(_, item)| item.as_map().is_some()).map(|(ordinal, item)| {
            let at = append(append(at.to_vec(), text("geometry")), Value::Int(ordinal as i64));
            node.bounds(item, &at)
        }).collect();
        set(&mut result, "geometry", Value::Seq(geometry));
    }
    let mut attributes = Value::Map(Vec::new());
    if let Some(value) = values.get("readOnly").filter(|value| matches!(value, Value::Bool(_)) || value.as_str() == Some("mixed")) {
        set(&mut attributes, "read_only", value.clone());
    }
    set(&mut result, "attributes", attributes);
    if let Some(statuses) = raw.get("fieldStatus") {
        if let Some(statuses) = statuses.as_map() {
            for (key, status) in statuses {
                if status.as_map().is_none() {
                    node.gap(&append(at.to_vec(), text(key)), "error", Some("Text range field status is not a record."));
                    node.supplement(&format!("text_range.failed_status.{key}"), status, None, false);
                    continue;
                }
                let field = match key.as_str() { "degenerate" => "collapsed", "readOnly" => "attributes", key => key };
                node.issue(&append(at.to_vec(), text(field)), status.get("state").and_then(Value::as_str).unwrap_or("error"),
                    status.get("detail").cloned(), None, Vec::new());
                if key == "position" {
                    take(&mut result, "start");
                    take(&mut result, "end");
                }
            }
        } else {
            node.gap(at, "error", Some("Text range status is not a record."));
            node.supplement("text_range.failed_status", statuses, None, false);
        }
    }
    result
}

pub(super) fn attributed_run(node: &mut Builder<'_>, value: &Value, index: usize) -> Value {
    let at = append(path(&["text", "runs"]), Value::Int(index as i64));
    let mut attributes = Value::Map(Vec::new());
    if value.as_map().is_none() {
        node.gap(&at, "error", Some("Attributed run is unavailable."));
        return map([("attributes", attributes)]);
    }
    let empty = Value::Map(Vec::new());
    let mut result = span(node, value.get("range").unwrap_or(&empty), &at);
    if let Some(raw) = value.get("attributes") {
        if let Some(raw) = raw.as_map() {
            for (key, item) in raw {
                let target = match key.as_str() {
                    "AXLanguage" => Some("language"),
                    "AXHeadingLevel" => Some("heading_level"),
                    "AXLink" => Some("link"),
                    "AXMisspelled" | "AXMarkedMisspelled" => Some("misspelled"),
                    _ => None,
                };
                if let Some(target) = target.filter(|_| is_text(item) || matches!(item, Value::Bool(_)) || number(item).is_some()) {
                    set(&mut attributes, target, item.clone());
                } else if !matches!(key.as_str(), "AXFont" | "AXForegroundColor" | "AXBackgroundColor" | "AXUnderline" | "AXStrikethrough") {
                    node.supplement(&format!("attributed_run.{index}.{key}"), item, None, false);
                }
            }
        } else {
            node.supplement(&format!("attributed_run.{index}.attributes"), raw, None, false);
            node.gap(&append(at, text("attributes")), "error", Some("Attributed text attributes are not a record."));
        }
    }
    set(&mut result, "attributes", attributes);
    result
}

pub(super) fn field(node: &mut Builder<'_>, name: &str, value: &Value) -> bool {
    match name {
        "AXSelectedText" | "AXNumberOfCharacters" => {
            node.text_info();
            let (field, expected): (&str, fn(&Value) -> bool) = if name == "AXSelectedText" {
                ("selected_text", is_text)
            } else {
                ("character_count", nonnegative)
            };
            node.put(name, &["text", field], value.clone(), expected);
        }
        "AXSelectedTextRange" | "AXSelectedTextRanges" | "AXVisibleCharacterRange" => {
            let field = if name == "AXVisibleCharacterRange" { "visible_ranges" } else { "selections" };
            let items = value.as_seq().unwrap_or(std::slice::from_ref(value));
            let (unlocated, items): (Vec<_>, Vec<_>) = items.iter().partition(|item|
                item.get("location").and_then(integer) == Some(i64::MAX));
            if !unlocated.is_empty() {
                node.supplement(name, value, Some("text.unlocated_range"), false);
                let state = if items.is_empty() {
                    if unlocated.iter().all(|item| item.get("length").and_then(integer) == Some(0)) { "absent" } else { "error" }
                } else { "partial" };
                node.gap(&path(&["text", field]), state, Some("AX reports NSNotFound; no character position is available."));
                if items.is_empty() { return true; }
            }
            node.text_info();
            let spans = items.iter().enumerate().map(|(index, item)| {
                let at = append(path(&["text", field]), Value::Int(index as i64));
                span(node, item, &at)
            }).collect();
            node.put(name, &["text", field], Value::Seq(spans), any);
        }
        _ => return false,
    }
    true
}

fn capture(node: &mut Builder<'_>, provider: &str, arguments: &[Value], value: Option<&Value>, read: &Value) -> bool {
    let Some(details) = read.get("details") else { return false };
    let statuses = node.row.get("fieldStatus");
    let subrole = node.values.get("AXSubrole").and_then(Value::as_str);
    let subrole_status = statuses.and_then(|statuses| statuses.get("AXSubrole"))
        .and_then(|status| status.get("state")).and_then(Value::as_str);
    let protected = node.groups.get("states").and_then(|states| states.get("protected")) == Some(&Value::Bool(true));
    if node.values.get("AXValue").and_then(Value::as_str) != Some("") || protected
        || subrole == Some("AXSecureTextField") || subrole.is_none() && !matches!(subrole_status, Some("absent" | "unsupported"))
        || ["AXValue", "AXNumberOfCharacters", provider].iter().any(|field| statuses.and_then(|statuses| statuses.get(field)).is_some())
        || read.get("complete") != Some(&Value::Bool(true))
        || read.get("issues").and_then(Value::as_seq).is_some_and(|issues| !issues.is_empty())
        || read.get("api").and_then(Value::as_str) != Some("AXUIElementCopyParameterizedAttributeValue")
    {
        return false;
    }
    let Some(count) = details.get("character_count_before").and_then(integer).filter(|count| (1..=4096).contains(count))
        else { return false };
    let [requested] = arguments else { return false };
    let Some(content) = value.and_then(Value::as_str) else { return false };
    if requested.as_map().is_none_or(|fields| fields.len() != 2)
        || requested.get("location").and_then(integer) != Some(0)
        || requested.get("length").and_then(integer) != Some(count)
        || content.encode_utf16().count() != count as usize
        || details.get("character_count_after").and_then(integer) != Some(count)
        || ["character_count_before_status", "character_count_after_status", "native_status"].iter().any(|field|
            details.get(field).and_then(integer) != Some(0))
    {
        return false;
    }
    let info = node.text_info();
    take(info, "content_field");
    set(info, "character_count", Value::Int(count));
    node.put(provider, &["text", "content"], text(content), any);
    true
}

pub(super) fn methods(node: &mut Builder<'_>) {
    for (ordinal, property) in node.properties {
        let args = &property.arguments;
        if args.is_empty() { continue; }
        let key = match &property.key {
            Value::Text(value) | Value::BigInt(value) => value.clone(),
            Value::Int(value) => value.to_string(),
            _ => continue,
        };
        let namespace = property.key_namespace.as_ref();
        let name = format!("{namespace}::{key}#{ordinal}");
        let value = node.values.get(&name);
        let mut evidence = map([("arguments", Value::Seq(args.clone()))]);
        if matches!((namespace, key.as_str()), ("toolkit", "text_character_count") | ("macos.ax", "AXStringForRange")) {
            set(&mut evidence, "read", property.read.clone());
        }
        if let Some(value) = value { set(&mut evidence, "result", value.clone()); }
        if namespace == "toolkit" && key == "text_character_count" && args.as_slice() == [text("AXNumberOfCharacters")] {
            node.supplement(&name, &evidence, Some("provider.macos.text_capture_count"), true);
            continue;
        }
        if namespace == "macos.ax" && key == "AXStringForRange" {
            let complete = capture(node, &name, args, value, &property.read);
            if complete { node.text_read = true; }
            let name = if complete { format!("{name}.evidence") } else { name };
            node.supplement(&name, &evidence, Some("provider.macos.parameterized_text"), complete);
            continue;
        }
        if namespace == "toolkit" && key == "action_description" {
            if let ([Value::Text(target)], Some(Value::Text(description))) = (args.as_slice(), value) {
                if let Some((index, action)) = node.actions.iter_mut().enumerate()
                    .find(|(_, action)| action.get("key").and_then(Value::as_str) == Some(target))
                {
                    let previous = action.get("description").and_then(Value::as_str);
                    if previous.is_none_or(|previous| description.starts_with(previous)) {
                        let shortcuts = take(action, "shortcuts");
                        set(action, "description", text(description));
                        if let Some(shortcuts) = shortcuts { set(action, "shortcuts", shortcuts); }
                        let at = vec![text("actions"), Value::Int(index as i64), text("description")];
                        node.source(&name, &at);
                        if node.row.get("fieldStatus").and_then(|status| status.get(&name)).is_none() {
                            let mut absolute = path(&["nodes", node.identity]);
                            absolute.extend(at);
                            node.issues.retain(|issue| issue.get("path").and_then(Value::as_seq) != Some(&absolute)
                                || issue.get("state").and_then(Value::as_str) != Some("partial"));
                        }
                        continue;
                    }
                }
            }
        }
        node.supplement(&name, &evidence, Some(&format!("provider.macos.{namespace}.{key}")), false);
    }
}
