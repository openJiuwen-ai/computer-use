// Copyright (c) Huawei Technologies Co., Ltd. 2026. All rights reserved.

//! Positive native evidence that a selected window has yet to expose content.

use std::collections::{HashMap, HashSet};

use super::{FactSnapshot, Value};

fn number(value: &Value) -> Option<f64> {
    match value {
        Value::Int(value) => Some(*value as f64),
        Value::BigInt(value) => value.parse::<f64>().ok(),
        Value::Float(value) => Some(*value),
        _ => None,
    }
    .filter(|value| value.is_finite())
}

fn rect(geometry: &Value) -> Option<[f64; 4]> {
    if geometry.get("valid").and_then(Value::as_bool) == Some(false) {
        return None;
    }
    let values = [
        number(geometry.get("x")?)?,
        number(geometry.get("y")?)?,
        number(geometry.get("width")?)?,
        number(geometry.get("height")?)?,
    ];
    (values[2] > 0.0 && values[3] > 0.0).then_some(values)
}

fn nonempty(value: Option<&Value>) -> bool {
    value.is_some_and(|value| *value != Value::Null && value.as_str() != Some(""))
}

fn inner<'a>(node: &'a Value, group: &str, field: &str) -> Option<&'a Value> {
    node.get(group).and_then(|value| value.get(field))
}

fn items<'a>(value: &'a Value, field: &str) -> &'a [Value] {
    value.get(field).and_then(Value::as_seq).unwrap_or_default()
}

fn entries<'a>(value: &'a Value, field: &str) -> &'a [(String, Value)] {
    value.get(field).and_then(Value::as_map).unwrap_or_default()
}

fn named_content(node: &Value) -> bool {
    ["name", "description", "help", "placeholder", "url"]
        .iter()
        .any(|field| nonempty(node.get(field)))
        || nonempty(inner(node, "control_value", "value"))
        || inner(node, "numeric_value", "value").is_some_and(|value| *value != Value::Null)
        || nonempty(inner(node, "text", "content"))
}

fn content(node: &Value) -> bool {
    !matches!(
        node.get("role").and_then(Value::as_str),
        Some("group" | "split_group" | "scroll_area" | "unknown")
    ) || named_content(node)
        || !items(node, "actions").is_empty()
}

fn shell(observation: &Value) -> bool {
    let nodes: HashMap<_, _> = entries(observation, "nodes")
        .iter()
        .map(|(key, node)| (key.as_str(), node))
        .collect();
    let [root] = items(observation, "roots") else {
        return false;
    };
    let Some(root) = root.as_str() else {
        return false;
    };
    let Some(root_node) = nodes.get(root) else {
        return false;
    };
    if !matches!(
        root_node.get("role").and_then(Value::as_str),
        Some("window" | "sheet")
    ) || nonempty(inner(root_node, "control_value", "value"))
        || nonempty(inner(root_node, "text", "content"))
    {
        return false;
    }
    let mut limited_relations = HashSet::new();
    let mut unavailable_labels = HashSet::new();
    for issue in items(observation, "issues") {
        let path = items(issue, "path");
        if path.is_empty() || (path.len() == 1 && path[0].as_str() == Some("nodes")) {
            return false;
        }
        let [namespace, token, field, suffix @ ..] = path else {
            continue;
        };
        if namespace.as_str() != Some("nodes") {
            continue;
        }
        let (Some(token), Some(field)) = (token.as_str(), field.as_str()) else {
            continue;
        };
        let state = issue.get("state").and_then(Value::as_str);
        if field == "relations" && state != Some("absent") {
            if let Some(relation) = suffix.first().and_then(Value::as_str) {
                limited_relations.insert((token, relation));
            }
        }
        if matches!(
            field,
            "name" | "description" | "help" | "placeholder" | "url" | "control_value" | "text"
        ) && !matches!(state, Some("absent" | "unsupported"))
        {
            unavailable_labels.insert(token);
        }
    }

    let mut children = HashMap::new();
    let mut titles = HashSet::new();
    let mut external = HashSet::new();
    for (&key, node) in &nodes {
        for (record_key, relation) in entries(node, "relations") {
            let limited = limited_relations.contains(&(key, record_key.as_str()));
            let confirmed = relation.get("evidence").and_then(Value::as_str) != Some("candidate");
            let targets = items(relation, "targets");
            match relation.get("kind").and_then(Value::as_str) {
                Some("parent" | "window") => {
                    external.extend(targets.iter().filter_map(Value::as_str))
                }
                Some("labelled_by") if key == root && !limited && confirmed => {
                    titles.extend(targets.iter().filter_map(Value::as_str))
                }
                Some("children") => {
                    if children.contains_key(key)
                        || limited
                        || !confirmed
                        || relation
                            .get("enumeration")
                            .and_then(Value::as_str)
                            .unwrap_or("enumerated")
                            != "enumerated"
                        || relation
                            .get("positions")
                            .and_then(Value::as_seq)
                            .is_some_and(|positions| {
                                positions.len() != targets.len()
                                    || positions
                                        .iter()
                                        .enumerate()
                                        .any(|(i, value)| value.as_i64() != i64::try_from(i).ok())
                            })
                        || relation
                            .get("total")
                            .and_then(Value::as_i64)
                            .is_some_and(|total| Some(total) != i64::try_from(targets.len()).ok())
                    {
                        return false;
                    }
                    children.insert(key, targets);
                }
                _ => {}
            }
        }
    }
    if !children.contains_key(root)
        || nodes
            .keys()
            .any(|key| !children.contains_key(key) && !external.contains(key))
        || children
            .values()
            .flat_map(|targets| targets.iter())
            .any(|child| {
                child
                    .as_str()
                    .is_none_or(|child| !children.contains_key(child))
            })
    {
        return false;
    }

    let mut pending = vec![root];
    let mut visited = HashSet::new();
    while let Some(current) = pending.pop() {
        if !visited.insert(current) {
            return false;
        }
        pending.extend(children[current].iter().filter_map(Value::as_str));
    }
    if visited.union(&titles).count() != children.len()
        || titles.iter().any(|title| !children.contains_key(title))
    {
        return false;
    }

    for (&key, targets) in &children {
        if key == root {
            continue;
        }
        let node = nodes[key];
        let role = node.get("role").and_then(Value::as_str);
        if role == Some("button")
            && matches!(
                node.get("role_detail").and_then(Value::as_str),
                Some("close" | "minimize" | "zoom" | "fullscreen")
            )
            && children[root]
                .iter()
                .any(|value| value.as_str() == Some(key))
        {
            continue;
        }
        let title = titles.contains(key) && role == Some("static_text") && targets.is_empty();
        if !title
            && (!matches!(role, Some("group" | "split_group" | "scroll_area"))
                || named_content(node))
        {
            return false;
        }
        let actions = node.get("actions").and_then(Value::as_seq);
        if inner(node, "states", "editable").and_then(Value::as_bool) == Some(true)
            || actions.is_none_or(|actions| {
                actions.iter().any(|action| {
                    !matches!(
                        action.get("kind").and_then(Value::as_str),
                        Some("show_menu" | "scroll" | "scroll_into_view")
                    )
                })
            })
            || unavailable_labels.contains(key)
        {
            return false;
        }
    }
    true
}

pub(crate) fn window_content_unexposed(snapshot: &FactSnapshot) -> bool {
    let observation = &snapshot.observation;
    let roots: HashSet<_> = items(observation, "roots")
        .iter()
        .filter_map(Value::as_str)
        .collect();
    let nodes = entries(observation, "nodes");
    let client = nodes
        .iter()
        .filter(|(key, _)| roots.contains(key.as_str()))
        .find_map(|(_, node)| {
            let client = inner(node, "geometry", "client")?;
            Some((client, rect(client)?))
        });
    let Some((client, [x, y, w, h])) = client else {
        return shell(observation);
    };
    if !items(&snapshot.facts, "stopReasons").is_empty()
        || !items(&snapshot.facts, "unread").is_empty()
    {
        return false;
    }
    for (key, node) in nodes {
        if roots.contains(key.as_str())
            || node.get("role").and_then(Value::as_str) == Some("window")
            || node.get("role_detail").and_then(Value::as_str) == Some("title_bar")
            || !content(node)
        {
            continue;
        }
        let Some(bounds) = inner(node, "geometry", "bounds") else {
            return false;
        };
        let Some([bx, by, bw, bh]) = rect(bounds) else {
            return false;
        };
        if bounds.get("space") != client.get("space")
            || (x + w).min(bx + bw) > x.max(bx) && (y + h).min(by + bh) > y.max(by)
        {
            return false;
        }
    }
    true
}

pub(crate) fn busy_nodes(snapshot: &FactSnapshot) -> Vec<&str> {
    entries(&snapshot.observation, "nodes")
        .iter()
        .filter_map(|(key, node)| {
            (inner(node, "states", "busy").and_then(Value::as_bool) == Some(true))
                .then_some(key.as_str())
        })
        .collect()
}

pub(crate) fn refreshable(snapshot: &FactSnapshot) -> bool {
    if snapshot.observation.get("backend").and_then(Value::as_str) != Some("windows") {
        return true;
    }
    items(&snapshot.facts, "unread").is_empty()
        && !items(&snapshot.observation, "issues").iter().any(|issue| {
            match issue.get("state").and_then(Value::as_str) {
                Some("timeout") => true,
                Some("error") => items(issue, "path").iter().any(|item| item.as_str() == Some("relations")),
                _ => false,
            }
        })
}

#[cfg(test)]
mod tests {
    use super::*;
    use crate::wire::decode_json;

    fn snapshot(nodes: &str, issues: &str) -> FactSnapshot {
        FactSnapshot {
            observation: decode_json(
                format!(r#"{{"roots":["root"],"nodes":{nodes},"issues":{issues}}}"#).as_bytes(),
            )
            .unwrap(),
            facts: Value::Null,
            metadata: Value::Null,
            query: Value::Null,
        }
    }

    #[test]
    fn shell_requires_complete_chrome_only_content() {
        let nodes = r#"{
            "root":{"role":"window","relations":{"children":{"kind":"children","targets":["chrome"]}}},
            "chrome":{"role":"button","role_detail":"close","states":{"busy":true},
                      "relations":{"children":{"kind":"children","targets":[]}}}
        }"#;
        let complete = snapshot(nodes, "[]");
        assert_eq!(busy_nodes(&complete), ["chrome"]);
        assert!(window_content_unexposed(&complete));
        let incomplete = snapshot(nodes, r#"[{"path":["nodes"],"state":"partial"}]"#);
        assert!(!window_content_unexposed(&incomplete));
        let content = snapshot(
            &nodes.replace("\"window\"", "\"window\",\"text\":{\"content\":\"Body\"}"),
            "[]",
        );
        assert!(!window_content_unexposed(&content));
    }
}
