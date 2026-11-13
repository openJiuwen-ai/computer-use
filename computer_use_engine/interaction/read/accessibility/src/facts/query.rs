// Copyright (c) Huawei Technologies Co., Ltd. 2026. All rights reserved.

// Project ordered driver properties into the native query evidence used by
// normalization. Query records retain provider facts and their coverage.

use std::collections::{HashMap, HashSet};

use super::{array, integer, map, required, set, text, truthy, FactsError, Result, Value};
use crate::wire_value::{WireProperty, WireValues};

pub(crate) struct Query {
    pub(crate) facts: Value,
    pub(crate) has_semantic_urls: bool,
    pub(super) interval: Value,
    pub(super) kinds: HashMap<String, String>,
    pub(super) properties: HashMap<String, Vec<(usize, WireProperty<'static>)>>,
}

fn field_status(state: &str, read: &Value) -> Option<Value> {
    let state = if state != "value" {
        state
    } else if read.get("complete") == Some(&Value::Bool(false)) {
        "truncated"
    } else {
        return None;
    };
    let mut status = map([("state", text(state))]);
    for issue in read.get("issues").and_then(Value::as_seq).into_iter().flatten() {
        for (key, value) in issue.get("nativeStatus").and_then(Value::as_map).into_iter().flatten() {
            set(&mut status, key, value.clone());
        }
        if let Some(detail) = issue.get("detail").filter(|value| !matches!(value, Value::Null)) {
            if status.get("detail").is_none() {
                set(&mut status, "detail", detail.clone());
            }
        }
        for (key, value) in issue.get("continuation").and_then(Value::as_map).into_iter().flatten() {
            set(&mut status, key, value.clone());
        }
    }
    Some(status)
}

fn relation(
    kinds: &HashMap<String, String>,
    node: &str,
    property: Value,
    value: Value,
    default_state: &str,
    details: &Value,
    completed: i64,
) -> Result<Value> {
    let items = match value {
        Value::Null => Vec::new(),
        Value::Seq(items) => items,
        item => vec![item],
    };
    let mut targets = Vec::with_capacity(items.len());
    for item in items {
        let target = item.get("node").and_then(Value::as_str)
            .filter(|target| kinds.get(*target).is_some_and(|kind| kind == "element"));
        if item.as_map().is_none_or(|record| record.len() != 2) || item.get("nativeType").is_none() || target.is_none() {
            return Err(FactsError::DriverQuery("invalid relation reference"));
        }
        targets.push(text(target.unwrap_or_default()));
    }
    let offset = details.get("offset").map_or(Some(0), integer).filter(|value| *value >= 0)
        .ok_or(FactsError::DriverQuery("invalid relation range or evidence"))?;
    let total = match details.get("total") {
        None | Some(Value::Null) => None,
        Some(value) => Some(integer(value).filter(|value| *value >= 0)
            .ok_or(FactsError::DriverQuery("invalid relation range or evidence"))?),
    };
    let state = details.get("state").map_or(Some(default_state), Value::as_str)
        .filter(|state| matches!(*state, "complete" | "partial" | "absent" | "unsupported" | "error" | "timeout"))
        .ok_or(FactsError::DriverQuery("invalid relation range or evidence"))?;
    let certainty = details.get("certainty").and_then(Value::as_str)
        .filter(|certainty| matches!(*certainty, "confirmed" | "inferred" | "unknown" | "conflicting"))
        .ok_or(FactsError::DriverQuery("invalid relation range or evidence"))?;
    let mut inspected = targets.len() as i64;
    let mut slots = None;
    if details.get("inspectedCount").is_some() || details.get("unavailablePositions").is_some() {
        let invalid = FactsError::DriverQuery("invalid relation slot progress");
        let count = details.get("inspectedCount").and_then(integer).ok_or(invalid)?;
        let gaps = details.get("unavailablePositions").and_then(Value::as_seq).filter(|items| !items.is_empty())
            .ok_or(FactsError::DriverQuery("invalid relation slot progress"))?;
        let end = offset.checked_add(count).ok_or(FactsError::DriverQuery("invalid relation slot progress"))?;
        let mut previous = None;
        let ordered = gaps.iter().all(|gap| {
            let Some(position) = integer(gap) else { return false };
            let valid = offset <= position && position < end && previous.is_none_or(|item| item < position);
            previous = Some(position);
            valid
        });
        if count != inspected + gaps.len() as i64 || !ordered || details.get("offset").is_none()
            || total.is_none() || state != "partial"
            || details.get("detail").and_then(Value::as_str).is_none_or(str::is_empty)
        {
            return Err(FactsError::DriverQuery("invalid relation slot progress"));
        }
        inspected = count;
        slots = Some((count, Value::Seq(gaps.to_vec())));
    }
    let end = offset.checked_add(inspected).ok_or(FactsError::DriverQuery("relation count exceeds its range"))?;
    let cursor = details.get("cursor");
    if total.is_some_and(|total| end > total) {
        return Err(FactsError::DriverQuery("relation count exceeds its range"));
    }
    if (offset != 0 || cursor.is_some()) && total.is_none() {
        return Err(FactsError::DriverQuery("relation continuation needs a total"));
    }
    if state == "complete" && (offset != 0 || cursor.is_some() || total.is_some_and(|total| end != total)) {
        return Err(FactsError::DriverQuery("complete relation has an unread remainder"));
    }
    if state == "absent" && (end != 0 || total.unwrap_or(0) != 0 || cursor.is_some()) {
        return Err(FactsError::DriverQuery("absent relation contains targets"));
    }
    if cursor.is_some_and(|cursor| state != "partial" || cursor.as_str().is_none_or(str::is_empty)
        || total.is_none_or(|total| end >= total))
    {
        return Err(FactsError::DriverQuery("invalid relation continuation"));
    }
    if let Some(membership) = details.get("membershipObservedAt").filter(|value| !matches!(value, Value::Null)) {
        let error = || FactsError::DriverQuery("captured relation membership interval is invalid");
        let start = membership.get("startedAt").and_then(integer).ok_or_else(error)?;
        let finish = membership.get("completedAt").and_then(integer).ok_or_else(error)?;
        if membership.as_map().is_none_or(|record| record.len() != 3)
            || membership.get("id").and_then(Value::as_str).is_none_or(str::is_empty)
            || !(0..=9_007_199_254_740_991).contains(&start)
            || !(start..=9_007_199_254_740_991).contains(&finish) || finish > completed
        {
            return Err(error());
        }
        if state != "partial" || total.is_none() || !details.get("detail").is_some_and(truthy) {
            return Err(FactsError::DriverQuery("captured relation membership needs partial coverage, count and explanation"));
        }
    }
    let mut result = map([
        ("source", text(node)), ("property", property), ("targets", Value::Seq(targets)),
        ("certainty", text(certainty)), ("state", text(state)),
    ]);
    for key in ["offset", "total", "cursor", "detail", "membershipObservedAt"] {
        if let Some(value) = details.get(key) {
            set(&mut result, key, value.clone());
        }
    }
    if let Some((count, gaps)) = slots {
        set(&mut result, "inspectedCount", Value::Int(count));
        set(&mut result, "unavailablePositions", gaps);
    }
    Ok(result)
}

fn actions(value: Value, read: &Value, details: &Value) -> Result<Value> {
    let items = match value {
        Value::Null => Vec::new(),
        Value::Seq(items) => items,
        _ => return Err(FactsError::DriverQuery("invalid action catalogue")),
    };
    let state = details.get("state").map_or_else(
        || Some(if read.get("complete").is_some_and(truthy) { "complete" } else { "unavailable" }),
        Value::as_str,
    ).ok_or(FactsError::DriverQuery("invalid action coverage"))?;
    let count = items.len() as i64;
    let total = details.get("total").map_or(Some(count), integer).ok_or(FactsError::DriverQuery("invalid action coverage"))?;
    if !matches!(state, "complete" | "partial" | "unavailable" | "unsupported" | "absent" | "error" | "timeout" | "not_read")
        || total < count || state == "complete" && total != count
    {
        return Err(FactsError::DriverQuery("invalid action coverage"));
    }
    if items.iter().any(|item| item.get("name").and_then(Value::as_str).is_none_or(str::is_empty)) {
        return Err(FactsError::DriverQuery("malformed action declaration"));
    }
    let mut result = map([("state", text(state)), ("items", Value::Seq(items))]);
    for key in ["total", "detail"] {
        if let Some(value) = details.get(key) {
            set(&mut result, key, value.clone());
        }
    }
    Ok(result)
}

pub(crate) fn collect(raw: &Value) -> Result<Query> {
    collect_values(raw, true)
}

pub(crate) fn collect_wire(raw: &Value) -> Result<Query> {
    collect_values(raw, false)
}

impl Query {
    pub(crate) fn replace_properties(&mut self, current: Query) {
        self.properties = current.properties;
    }
}

fn collect_values(raw: &Value, semantic: bool) -> Result<Query> {
    let session = required(raw, "session")?.as_str().ok_or(FactsError::Invalid("expected a session"))?;
    let mut wire = WireValues::new(raw, semantic)?;
    let platform = wire.platform;
    let source = match platform {
        "macos" => "macos.ax",
        "windows" => "windows.uia",
        "linux" => "linux.atspi",
        _ => unreachable!("platform admitted by WireValues"),
    };
    let raw_nodes = array(required(raw, "nodes")?)?;
    let mut kinds = HashMap::new();
    for node in raw_nodes {
        let token = required(node, "id")?.as_str().ok_or(FactsError::Invalid("expected a node identity"))?;
        let kind = required(node, "kind")?.as_str().ok_or(FactsError::Invalid("expected a node kind"))?;
        kinds.insert(token.to_owned(), kind.to_owned());
    }
    let read = wire.read.clone();
    let interval = required(&read, "intervalNs")?.clone();
    let interval_bounds = array(&interval)?;
    let began = interval_bounds.first().and_then(integer).ok_or(FactsError::Invalid("expected observation interval"))?;
    let ended = interval_bounds.get(1).and_then(integer).ok_or(FactsError::Invalid("expected observation interval"))?;
    let details = required(&read, "details")?;
    let surface = required(details, "surface")?.clone();
    let capture = required(details, "observation_id")?.clone();
    let roots = array(required(raw, "roots")?)?;
    let focus_root = if platform == "macos" && roots.len() == 1 { roots[0].as_str() } else { None };
    let mut focus_seeds = Vec::new();
    let mut rows = Vec::new();
    let mut relations = Vec::new();
    let mut unread = Vec::new();
    let mut outside = Vec::new();
    let mut properties = HashMap::new();
    for raw_node in raw_nodes {
        let token = required(raw_node, "id")?.as_str().ok_or(FactsError::Invalid("expected node identity"))?;
        if let Some(identity) = raw_node.get("identity").filter(|value| !matches!(value, Value::Null)) {
            wire.value(identity, 0)?;
        }
        let node_read = wire.read_info(raw_node.get("read"), false)?;
        let raw_properties = required(raw_node, "properties")?;
        let node_kind = &kinds[token];
        if node_kind != "element" {
            if let Some(items) = raw_properties.as_seq() {
                for item in items {
                    wire.property(item, false)?;
                }
            }
            continue;
        }
        if matches!(raw_properties, Value::Null) {
            unread.push(text(token));
            if platform == "windows" && node_read.get("issues").and_then(Value::as_seq).into_iter().flatten()
                .any(|issue| issue.get("reason").and_then(Value::as_str) == Some("outside_window_scope"))
            {
                outside.push(text(token));
            }
            continue;
        }
        let mut retained = Vec::new();
        let mut values = Value::Map(Vec::new());
        let mut statuses = Value::Map(Vec::new());
        let mut requested = Vec::new();
        let mut action_catalogue = map([
            ("state", text("unavailable")), ("items", Value::Seq(Vec::new())),
            ("detail", text("Action catalogue was not read")),
        ]);
        for (index, raw_property) in array(raw_properties)?.iter().enumerate() {
            let mut property = wire.property(raw_property, true)?;
            let key = &property.key;
            let namespace = property.key_namespace.as_ref();
            let name = key.as_str();
            let value = &property.value;
            let state = property.state;
            let details = required(&property.read, "details")?;
            if token == focus_root.unwrap_or_default() && namespace == "toolkit" && name == Some("focused_read_seed") {
                let focused = state == "value" && property.arguments.is_empty()
                    && value.get("nativeType").and_then(Value::as_str) == Some("AXUIElement")
                    && property.read.get("complete") == Some(&Value::Bool(true))
                    && property.read.get("issues").and_then(Value::as_seq).is_some_and(<[Value]>::is_empty)
                    && details.get("source").and_then(Value::as_str) == Some("application")
                    && details.get("attribute").and_then(Value::as_str) == Some("AXFocusedUIElement")
                    && details.get("state").and_then(Value::as_str) == Some("confirmed");
                focus_seeds.push(if focused { value.get("node").cloned() } else { None });
            }
            if details.get("relation") == Some(&Value::Bool(true)) {
                relations.push(relation(&kinds, token, key.clone(), value.clone(), state, details, ended / 1_000_000)?);
            } else if namespace == "toolkit" && name == Some("actions") {
                action_catalogue = actions(value.clone(), &property.read, details)?;
            } else {
                let provider = match key {
                    Value::Text(name) => name.clone(),
                    Value::Int(number) => number.to_string(),
                    Value::BigInt(number) => number.clone(),
                    _ => return Err(FactsError::Invalid("invalid property key")),
                };
                let provider = if namespace != source || name.is_none()
                    || !property.arguments.is_empty() || requested.iter().any(|item: &Value| item.as_str() == name)
                {
                    format!("{namespace}::{provider}#{index}")
                } else {
                    provider
                };
                if raw_property.get("value").is_some_and(|value| !matches!(value, Value::Null)) {
                    set(&mut values, &provider, value.clone());
                }
                if let Some(status) = field_status(state, &property.read) {
                    set(&mut statuses, &provider, status);
                }
                requested.push(text(provider));
            }
            let catalogue = namespace == "toolkit" && matches!(name,
                Some("attribute_names" | "action_names" | "parameterized_attribute_names" | "actions"));
            if !property.arguments.is_empty() {
                property.read = wire.read_info(raw_property.get("read"), false)?;
            }
            if catalogue || !property.arguments.is_empty() {
                retained.push((index, property.into_owned()));
            }
        }
        properties.insert(token.to_owned(), retained);
        rows.push(map([
            ("node", text(token)), ("requestedFields", Value::Seq(requested)),
            ("values", values), ("fieldStatus", statuses), ("actions", action_catalogue),
        ]));
    }
    let requested_relations = details.get("requested_relations").map_or(Some(&[][..]), Value::as_seq)
        .ok_or(FactsError::DriverQuery("invalid requested relationships"))?;
    if requested_relations.iter().any(|name| name.as_str().is_none()) {
        return Err(FactsError::DriverQuery("invalid requested relationships"));
    }
    let mut seen = HashSet::new();
    for edge in &relations {
        let token = edge.get("source").and_then(Value::as_str).unwrap_or_default();
        let key = edge.get("property").and_then(Value::as_str).ok_or(FactsError::Unsupported("numeric relation property"))?;
        if !seen.insert((token, key)) {
            return Err(FactsError::DriverQuery("duplicate query relationship"));
        }
    }
    if rows.iter().any(|row| {
        let token = row.get("node").and_then(Value::as_str).unwrap_or_default();
        requested_relations.iter().any(|name| !seen.contains(&(token, name.as_str().unwrap_or_default())))
    }) {
        return Err(FactsError::DriverQuery("requested relationship has no result"));
    }
    let reasons = read.get("issues").and_then(Value::as_seq).into_iter().flatten()
        .filter_map(|issue| issue.get("detail").filter(|detail| truthy(detail)).or_else(|| issue.get("reason")).cloned())
        .collect();
    let mut facts = map([
        ("kind", text("nodes")), ("session", text(session)), ("platform", text(platform)), ("surface", surface),
        ("observed", map([
            ("id", capture), ("startedAt", Value::Int(began / 1_000_000)),
            ("completedAt", Value::Int(ended / 1_000_000)),
        ])),
        ("roots", Value::Seq(roots.to_vec())), ("nodes", Value::Seq(rows)), ("relations", Value::Seq(relations)),
        ("requestedRelations", Value::Seq(requested_relations.to_vec())), ("unread", Value::Seq(unread)),
        ("stopReasons", Value::Seq(reasons)),
    ]);
    if let [Some(focused)] = focus_seeds.as_slice() {
        set(&mut facts, "focusedElement", focused.clone());
    }
    if !outside.is_empty() {
        set(&mut facts, "outsideWindowNodes", Value::Seq(outside));
    }
    if let Some(token) = details.get("change_token") {
        set(&mut facts, "changeToken", token.clone());
    }
    Ok(Query { facts, interval, kinds, properties, has_semantic_urls: wire.has_semantic_urls })
}
