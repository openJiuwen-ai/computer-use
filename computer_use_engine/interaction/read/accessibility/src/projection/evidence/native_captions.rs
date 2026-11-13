// Copyright (c) Huawei Technologies Co., Ltd. 2026. All rights reserved.

use std::collections::{HashMap, HashSet};

use super::{
    empty, enumerated, eq_path, overlaps, present, range, relations, rows, seq, starts, targets, text, truth,
    Aliases, Issue, Issues, NodeId, Value,
};
use crate::projection::PassiveCaptionSource;

const NUMERIC_FIELDS: &[&str] = &[
    "value", "minimum", "maximum", "small_step", "large_step", "description", "read_only", "allowed_values",
];
const STRUCTURE_FIELDS: &[&str] = &[
    "heading_level", "landmark", "landmark_description", "level", "index", "set_size", "selected_count", "row_count",
    "column_count", "row_index", "column_index", "row_span", "column_span", "can_select_multiple", "selection_required",
    "orientation", "collection_mode", "scroll_x", "scroll_y",
];
const TEXT_FIELDS: &[&str] = &[
    "source", "unit", "content", "content_field", "character_count", "selected_text", "read_only", "runs", "selections",
    "caret", "caret_active", "visible_ranges", "line_ranges", "embedded", "supported_selection",
];

fn complete(issues: &[Issue<'_>], path: &[&str], allow_absent: bool, allow_unsupported: bool) -> bool {
    issues.iter().filter(|issue| overlaps(issue.local, path)).all(|issue| {
        allow_absent && eq_path(issue.local, path) && !issue.pending()
            && (issue.state() == Some("absent") || allow_unsupported && issue.state() == Some("unsupported"))
    })
}

fn optional_record(issues: &[Issue<'_>], name: &str, record: Option<&Value>, fields: &[&str]) -> bool {
    issues.iter().all(|issue| {
        if issue.local.is_empty() {
            return false;
        }
        if !starts(issue.local, &[name]) {
            return true;
        }
        let missing = match issue.local {
            [_] => !present(record),
            [_, field] => field.as_str().is_some_and(|name| {
                fields.contains(&name) && !present(record.and_then(|record| record.get(name)))
            }),
            _ => false,
        };
        missing && matches!(issue.state(), Some("absent" | "unsupported")) && !issue.pending()
    })
}

fn incidental_metadata(node: &Value, states: &Value, issues: &[Issue<'_>]) -> bool {
    let structure = node.get("structure");
    if !optional_record(issues, "structure", structure, STRUCTURE_FIELDS)
        || rows(structure).iter().any(|(key, value)| key != "level" && present(Some(value)))
        || structure.and_then(|structure| structure.get("level")).is_some_and(|value| {
            !matches!(value, Value::Null) && value.as_i64() != Some(0)
        })
        || !matches!(text(states, "expanded"), None | Some("collapsed"))
    {
        // A true static-text disclosure (expanded) remains a displayed state.
        return false;
    }
    let numeric = node.get("numeric_value");
    if !optional_record(issues, "numeric_value", numeric, NUMERIC_FIELDS) {
        return false;
    }
    rows(numeric).iter().all(|(key, value)| {
        if !present(Some(value)) {
            return true;
        }
        matches!(key.as_str(), "minimum" | "maximum") && match value {
            Value::Int(_) | Value::BigInt(_) => true,
            Value::Float(value) => value.is_finite(),
            _ => false,
        }
    })
}

fn passive_text(info: &Value, value: &str, issues: &[Issue<'_>]) -> bool {
    if !eq_path(seq(info.get("content_field")), &["control_value", "value"])
        || present(info.get("content"))
        || text(info, "unit") != Some("utf16_code_unit")
        || present(info.get("read_only"))
        || !complete(issues, &["text", "read_only"], true, true)
        || !complete(issues, &["text", "selections"], false, false)
        || !empty(info.get("selected_text"))
        || !complete(issues, &["text", "selected_text"], !present(info.get("selected_text")), true)
        || present(info.get("caret"))
        || present(info.get("caret_active")) && info.get("caret_active").and_then(Value::as_bool) != Some(false)
        || ["runs", "embedded", "visible_ranges", "line_ranges", "supported_selection"].iter()
            .any(|key| present(info.get(key)))
        || info.get("character_count").is_some_and(|count| {
            present(Some(count)) && count.as_i64() != Some(value.encode_utf16().count() as i64)
        })
        || TEXT_FIELDS.iter().any(|key| !complete(issues, &["text", key], !present(info.get(key)), true))
    {
        return false;
    }
    info.get("selections").and_then(Value::as_seq).is_some_and(|spans| spans.iter().all(|span| {
        span.get("start").and_then(Value::as_i64) == Some(0)
            && span.get("end").and_then(Value::as_i64) == Some(0)
            && span.get("collapsed").and_then(Value::as_bool) == Some(true)
            && empty(span.get("text"))
            && !truth(span, "geometry") && !truth(span, "attributes") && !present(span.get("read_ref"))
    }))
}

fn passive_native<'a>(node: &'a Value, issues: &[Issue<'_>]) -> Option<&'a str> {
    let control = node.get("control_value")?;
    let value = text(control, "value").filter(|text| !text.is_empty())?;
    let actions = node.get("actions")?.as_seq()?;
    let states = node.get("states")?;
    if text(node, "role") != Some("static_text") || truth(node, "role_detail")
        || !complete(issues, &["role"], false, false)
        || !complete(issues, &["role_detail"], !present(node.get("role_detail")), true)
        || !complete(issues, &["control_value", "value"], false, false)
        || !complete(issues, &["actions"], false, false)
        || ["name", "description", "help", "identifier", "placeholder", "url"].iter().any(|name| {
            !empty(node.get(name)) || !complete(issues, &[name], !present(node.get(name)), true)
        })
        || !empty(control.get("description"))
        || !complete(issues, &["control_value", "description"], !present(control.get("description")), true)
        || present(control.get("read_only")) || !complete(issues, &["control_value", "read_only"], true, true)
    {
        return None;
    }
    for (key, expected) in [("enabled", true), ("focused", false), ("selected", false), ("busy", false), ("modal", false)] {
        if states.get(key).and_then(Value::as_bool) != Some(expected) || !complete(issues, &["states", key], false, false) {
            return None;
        }
    }
    if present(states.get("checked")) || present(states.get("window_state")) {
        return None;
    }
    let optional = [("editable", false), ("focusable", false), ("selectable", false), ("protected", false),
        ("has_popup", false), ("active", false), ("main_window", false), ("visible", true), ("showing", true),
        ("offscreen", false), ("virtualized", false)];
    if optional.iter().any(|(key, expected)| {
        present(states.get(key)) && states.get(key).and_then(Value::as_bool) != Some(*expected)
    }) || optional.iter().map(|(key, _)| *key).chain(["checked", "expanded", "window_state"]).any(|key| {
        !complete(issues, &["states", key], !present(states.get(key)), true)
    }) || !incidental_metadata(node, states, issues) {
        return None;
    }
    if rows(node.get("relations")).iter().any(|(_, relation)| {
        !matches!(text(relation, "kind"), Some("children" | "parent" | "window")) && !targets(relation).is_empty()
    }) {
        return None;
    }
    for kind in ["labels", "labelled_by"] {
        let mut found = relations(node, kind);
        if let Some((key, relation)) = found.next() {
            if found.next().is_some() || !targets(relation).is_empty() || !range(relation, 0, false)
                || !enumerated(relation) || !complete(issues, &["relations", key], true, false)
            {
                return None;
            }
        } else if !complete(issues, &["relations", kind], false, false) {
            return None;
        }
    }
    for (kind, path) in [("focus", &[][..]), ("set_value", &["control_value", "value"][..]),
        ("replace_selection", &["text", "selections"][..])]
    {
        let mut matches = actions.iter().filter(|action| text(action, "kind") == Some(kind));
        let action = matches.next()?;
        let target = action.get("target");
        if matches.next().is_some() || present(action.get("key")) || action.get("available").and_then(Value::as_bool) != Some(false)
            || (if path.is_empty() { present(target) } else { !eq_path(seq(target), path) })
        {
            return None;
        }
    }
    if actions.iter().any(|action| !matches!(
        text(action, "kind"), Some("focus" | "set_value" | "replace_selection" | "show_menu" | "scroll_into_view")
    )) || !node.get("text").is_some_and(|info| passive_text(info, value, issues)) {
        return None;
    }
    Some(value)
}

fn window_targets(node: &Value, issues: &[Issue<'_>], aliases: &Aliases<'_>) -> Option<Vec<NodeId>> {
    let mut matches = relations(node, "window");
    let Some((key, relation)) = matches.next() else {
        return complete(issues, &["relations", "window"], true, false).then(Vec::new);
    };
    let targets = targets(relation);
    if matches.next().is_some() || targets.len() > 1 || !range(relation, targets.len(), false)
        || !enumerated(relation) || !complete(issues, &["relations", key], targets.is_empty(), false)
    {
        return None;
    }
    targets.iter().map(|value| aliases.get(value.as_str()?).copied()).collect()
}

pub(super) fn capture(native: &Value, semantic: &Value, aliases: &Aliases<'_>, issue_rows: &Issues<'_>) -> HashMap<NodeId, PassiveCaptionSource> {
    let nodes = rows(native.get("nodes"));
    let mut incoming: HashMap<&str, Vec<&str>> = HashMap::new();
    for (token, node) in nodes {
        for (_, relation) in relations(node, "children") {
            for target in targets(relation).iter().filter_map(Value::as_str) {
                incoming.entry(target).or_default().push(token);
            }
        }
    }
    let mut parents = HashMap::new();
    let mut inconsistent = HashSet::new();
    let mut windows = HashMap::new();
    let mut window_boundaries = HashSet::new();
    for (token, node) in nodes {
        let Some(&id) = aliases.get(token.as_str()) else { continue };
        let candidates = incoming.get(token.as_str()).map(Vec::as_slice).unwrap_or(&[]);
        let issues = issue_rows.get(token.as_str()).map(Vec::as_slice).unwrap_or(&[]);
        windows.insert(id, window_targets(node, issues, aliases));
        if matches!(text(node, "role"), Some("window" | "dialog" | "alert")) {
            window_boundaries.insert(id);
        }
        let mut declared = relations(node, "parent");
        let first = declared.next();
        if candidates.len() > 1 || declared.next().is_some() {
            inconsistent.insert(id);
            continue;
        }
        let [parent] = candidates else { continue };
        let Some(&parent_id) = aliases.get(parent) else { continue };
        if let Some((key, relation)) = first {
            if !matches!(targets(relation), [target] if target.as_str() == Some(parent))
                || !range(relation, 1, false) || !enumerated(relation)
                || !complete(issues, &["relations", key], false, false)
            {
                inconsistent.insert(id);
                continue;
            }
        } else if !complete(issues, &["relations", "parent"], false, false) {
            inconsistent.insert(id);
            continue;
        }
        parents.insert(id, parent_id);
    }
    let mut result = HashMap::new();
    for (reference, token) in rows(semantic.get("refs")) {
        let Some(token) = token.as_str() else { continue };
        let Some(&id) = aliases.get(token) else { continue };
        if inconsistent.contains(&id) || semantic.get("value_fields").and_then(|fields| text(fields, reference)) != Some("control_value.value") {
            continue;
        }
        let Some(node) = native.get("nodes").and_then(|nodes| nodes.get(token)) else { continue };
        let issues = issue_rows.get(token).map(Vec::as_slice).unwrap_or(&[]);
        let Some(value) = passive_native(node, issues) else { continue };
        let mut ancestors = Vec::new();
        let mut current = id;
        while let Some(&parent) = parents.get(&current) {
            if parent == id || ancestors.contains(&parent) || inconsistent.contains(&parent) && !window_boundaries.contains(&parent) {
                break;
            }
            ancestors.push(parent);
            if window_boundaries.contains(&parent) {
                break;
            }
            current = parent;
        }
        let mut reported_windows = Vec::new();
        for member in std::iter::once(id).chain(ancestors.iter().copied()) {
            if let Some(Some(targets)) = windows.get(&member) {
                reported_windows.extend(targets.iter().map(|&target| (member, Some(target))));
            } else {
                reported_windows.push((member, None));
            }
        }
        result.insert(id, PassiveCaptionSource { value: value.to_owned(), ancestors, reported_windows });
    }
    result
}
