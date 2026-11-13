// Copyright (c) Huawei Technologies Co., Ltd. 2026. All rights reserved.

use std::collections::{HashMap, HashSet};

use super::{append, array, field_name, integer, map, path, required, set, text, truthy, Path, Result, Value, NODE_FIELDS};

fn data(value: &Value) -> Value {
    match value {
        Value::Float(value) if !value.is_finite() => map([("number", text(
            if value.is_nan() { "NaN" } else if value.is_sign_positive() { "Infinity" } else { "-Infinity" }
        ))]),
        Value::Seq(items) => Value::Seq(items.iter().map(data).collect()),
        Value::Map(items) => Value::Map(items.iter().map(|(key, value)| (key.clone(), data(value))).collect()),
        value => value.clone(),
    }
}

struct Fields {
    values: Value,
    paths: Value,
}

impl Fields {
    fn new(node: &Value) -> Self {
        let mut fields = Self { values: Value::Map(Vec::new()), paths: Value::Map(Vec::new()) };
        for name in NODE_FIELDS {
            let Some(value) = node.get(name).filter(|item| !matches!(item, Value::Null)) else { continue };
            match *name {
                "actions" | "relations" => (),
                "supplements" => {
                    for (index, item) in value.as_seq().into_iter().flatten().enumerate() {
                        let path = append(path(&[name]), Value::Int(index as i64));
                        if item.get("value") == Some(&Value::Null) {
                            let fields_with_values = item.as_map().into_iter().flatten()
                                .filter(|(key, _)| key != "value").cloned().collect();
                            fields.add(path, &Value::Map(fields_with_values));
                        } else {
                            fields.add(path, item);
                        }
                    }
                }
                "geometry" | "states" | "control_value" | "numeric_value" | "text" | "structure" => {
                    for (part, item) in value.as_map().into_iter().flatten() {
                        fields.add(path(&[name, part]), item);
                    }
                }
                _ if value.as_map().is_some_and(<[(String, Value)]>::is_empty)
                    || value.as_seq().is_some_and(<[Value]>::is_empty) => (),
                _ => fields.add(path(&[name]), value),
            }
        }
        fields
    }

    fn add(&mut self, path: Path, value: &Value) {
        let name = field_name(&path);
        set(&mut self.values, &name, data(value));
        set(&mut self.paths, &name, Value::Seq(path));
    }
}

fn priority(state: &str) -> u8 {
    match state {
        "conflict" => 8, "error" => 7, "timeout" => 6, "unavailable" | "redacted" => 5,
        "stale" => 4, "partial" => 3, "not_read" => 2, "unsupported" => 1, _ => 0,
    }
}

fn issue_path(issue: &Value) -> &[Value] { issue.get("path").and_then(Value::as_seq).unwrap_or_default() }

fn state(issue: &Value) -> &str { issue.get("state").and_then(Value::as_str).unwrap_or_default() }

fn status(issues: &[&Value]) -> Option<Value> {
    let mut primary = *issues.first()?;
    for issue in &issues[1..] {
        if priority(state(issue)) > priority(state(primary)) { primary = issue; }
    }
    let mut result = map([
        ("state", text(state(primary))), ("issues", Value::Seq(issues.iter().map(|issue| data(issue)).collect())),
    ]);
    if let Some(detail) = primary.get("detail").filter(|detail| truthy(detail)) {
        set(&mut result, "detail", detail.clone());
    }
    if let Some(continuation) = issues.iter().rev().filter_map(|issue| issue.get("continuation")).find(|item|
        item.get("kind").and_then(Value::as_str) == Some("text")
        && item.get("consistency").and_then(Value::as_str) == Some("captured"))
    {
        for (key, field) in [("token", "textRef"), ("unit", "unit"), ("total", "totalUnits"), ("delivered", "delivered")] {
            set(&mut result, field, continuation.get(key).map_or(Value::Null, data));
        }
    }
    Some(result)
}

struct Issues<'a> {
    attached: HashMap<&'a str, Vec<&'a Value>>,
}

impl<'a> Issues<'a> {
    fn new(issues: &'a [Value]) -> Self {
        let mut attached: HashMap<_, Vec<_>> = HashMap::new();
        for issue in issues {
            let path = issue_path(issue);
            if path.first().and_then(Value::as_str) == Some("nodes") {
                if let Some(token) = path.get(1).and_then(Value::as_str) {
                    attached.entry(token).or_default().push(issue);
                }
            }
        }
        Self { attached }
    }

    fn node(&self, token: &str) -> &[&'a Value] {
        self.attached.get(token).map_or(&[], Vec::as_slice)
    }
}

fn matching<'a>(attached: &[&'a Value], path: &[Value], overlaps: bool, out: &mut Vec<&'a Value>) {
    out.clear();
    out.extend(attached.iter().copied().filter(|issue| {
        let own = &issue_path(issue)[2..];
        path.starts_with(own) || overlaps && own.starts_with(path)
    }));
}

fn relation(token: &str, key: &str, relation: &Value, issues: &[&Value]) -> Value {
    let selected = status(issues);
    let mut relation_state = selected.as_ref().map_or("complete", state);
    if matches!(relation_state, "absent" | "unsupported") && !issues.iter().any(|issue|
        issue_path(issue).len() <= 4 && matches!(state(issue), "absent" | "unsupported"))
    {
        relation_state = "partial";
    }
    let kind = relation.get("kind").cloned().unwrap_or(Value::Null);
    let targets = relation.get("targets").cloned().unwrap_or(Value::Seq(Vec::new()));
    let mut result = map([
        ("source", text(token)), ("property", text(key)), ("recordKey", text(key)),
        ("kind", kind), ("targets", targets), ("state", text(relation_state)),
        ("certainty", text(if relation.get("evidence").and_then(Value::as_str) == Some("candidate") { "unknown" } else { "confirmed" })),
    ]);
    if let Some(mode) = relation.get("enumeration").filter(|mode| mode.as_str() != Some("enumerated")) {
        set(&mut result, "enumeration", mode.clone());
        if selected.is_none() { set(&mut result, "state", text("partial")); }
    }
    if let Some(view) = relation.get("view") { set(&mut result, "view", view.clone()); }
    if let Some(positions) = relation.get("positions") {
        set(&mut result, "positions", positions.clone());
        if let Some(items) = positions.as_seq() {
            if let Some(start) = items.first().and_then(integer) {
                if items.iter().enumerate().all(|(index, item)| integer(item) == start.checked_add(index as i64)) {
                    set(&mut result, "offset", Value::Int(start));
                }
            }
        }
    }
    for key in ["offset", "total"] {
        if let Some(value) = relation.get(key) { set(&mut result, key, value.clone()); }
    }
    if let Some(count) = relation.get("inspected_count") {
        set(&mut result, "inspectedCount", count.clone());
        set(&mut result, "unavailablePositions", relation.get("unavailable_positions").cloned().unwrap_or(Value::Seq(Vec::new())));
    }
    if let Some(status) = selected {
        if let Some(value) = status.get("issues") { set(&mut result, "issues", value.clone()); }
        if let Some(value) = status.get("detail").filter(|value| truthy(value)) { set(&mut result, "detail", value.clone()); }
        if let Some(continuation) = issues.iter().rev().find_map(|issue| issue.get("continuation")) {
            if let Some(token) = continuation.get("token") { set(&mut result, "cursor", token.clone()); }
            if let Some(consistency) = continuation.get("consistency") { set(&mut result, "consistency", consistency.clone()); }
        }
    }
    result
}

pub(super) fn build(observation: &Value, observed_at: &Value) -> Result<Value> {
    let issues = array(required(observation, "issues")?)?;
    let indexed = Issues::new(issues);
    let mut matching_issues = Vec::new();
    let mut rows = Vec::new();
    let mut edges = Vec::new();
    let mut unread = Vec::new();
    for (token, node) in required(observation, "nodes")?.as_map().into_iter().flatten() {
        let mut fields = Fields::new(node);
        let attached = indexed.node(token);
        let mut statuses = Value::Map(Vec::new());
        if !attached.is_empty() {
            for (name, path) in fields.paths.as_map().into_iter().flatten() {
                matching(attached, path.as_seq().unwrap_or_default(), false, &mut matching_issues);
                if let Some(status) = status(&matching_issues) { set(&mut statuses, name, status); }
            }
        }
        let mut constrained_relations = HashSet::new();
        for issue in attached {
            let path = &issue_path(issue)[2..];
            if path.first().and_then(Value::as_str) == Some("relations") {
                if path.len() > 2 {
                    if let Some(key) = path.get(1).and_then(Value::as_str) { constrained_relations.insert(key); }
                }
                continue;
            }
            let name = if path.is_empty() { "node".to_owned() } else { field_name(path) };
            if fields.paths.get(&name).is_none() { set(&mut fields.paths, &name, Value::Seq(path.to_vec())); }
            if statuses.get(&name).is_none() {
                matching(attached, path, false, &mut matching_issues);
                if let Some(status) = status(&matching_issues) { set(&mut statuses, &name, status); }
            }
        }
        matching(attached, &path(&["actions"]), false, &mut matching_issues);
        let action_status = status(&matching_issues);
        let action_items = node.get("actions");
        let action_state = action_status.as_ref().map_or(
            if action_items.is_none() { "not_read" } else { "complete" }, state);
        let mut actions = map([
            ("state", text(action_state)), ("items", action_items.map_or_else(|| Value::Seq(Vec::new()), data)),
        ]);
        if let Some(issues) = action_status.as_ref().and_then(|status| status.get("issues")) {
            set(&mut actions, "issues", issues.clone());
        }
        let relations = node.get("relations").and_then(Value::as_map).unwrap_or_default();
        let observed = fields.values.as_map().is_some_and(|values| !values.is_empty()) || action_items.is_some() || !relations.is_empty();
        if !observed { unread.push(text(token)); }
        let mut seen = HashSet::new();
        let requested = fields.values.as_map().into_iter().flatten().chain(statuses.as_map().into_iter().flatten())
            .filter_map(|(name, _)| seen.insert(name).then(|| text(name))).collect();
        rows.push(map([
            ("node", text(token)), ("values", fields.values), ("fieldPaths", fields.paths), ("fieldStatus", statuses),
            ("requestedFields", Value::Seq(requested)), ("actions", actions), ("observed", Value::Bool(observed)),
        ]));
        for (key, record) in relations {
            let path = path(&["relations", key]);
            matching(attached, &path, constrained_relations.contains(key.as_str()), &mut matching_issues);
            edges.push(relation(token, key, record, &matching_issues));
        }
    }
    let stop_reasons = issues.iter().filter_map(|issue| {
        let path = issue_path(issue);
        if path.len() >= 2 && path.first().and_then(Value::as_str) == Some("nodes") { return None; }
        let state = issue.get("detail").and_then(Value::as_str).filter(|detail| !detail.is_empty()).unwrap_or_else(|| state(issue));
        Some(if path.is_empty() { text(state) } else { text(format!("{}: {state}", field_name(path))) })
    }).collect();
    Ok(map([
        ("kind", text("nodes")), ("session", required(observation, "session")?.clone()),
        ("platform", required(observation, "backend")?.clone()), ("observed", observed_at.clone()),
        ("roots", required(observation, "roots")?.clone()), ("nodes", Value::Seq(rows)),
        ("relations", Value::Seq(edges)), ("unread", Value::Seq(unread)), ("stopReasons", Value::Seq(stop_reasons)),
        ("coordinateSpaces", data(required(observation, "coordinate_spaces")?)),
        ("issues", Value::Seq(issues.iter().map(data).collect())),
    ]))
}
