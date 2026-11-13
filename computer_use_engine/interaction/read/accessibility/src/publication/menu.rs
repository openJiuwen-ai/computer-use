// Copyright (c) Huawei Technologies Co., Ltd. 2026. All rights reserved.

//! Capture-local menu presentation evidence. The host owns window scope and lifetime.

use std::collections::{HashMap, HashSet};

use super::{FactSnapshot, Value};

pub(super) fn text<'a>(value: &'a Value, key: &str) -> Option<&'a str> {
    value.get(key).and_then(Value::as_str)
}

pub(super) fn seq<'a>(value: &'a Value, key: &str) -> &'a [Value] {
    value.get(key).and_then(Value::as_seq).unwrap_or_default()
}

fn states<'a>(node: &'a Value, key: &str) -> Option<&'a Value> {
    node.get("states")?.get(key)
}

fn raw_relation<'a>(node: &'a Value, kind: &str) -> Option<&'a Value> {
    node.get("relations")?.get(kind)
}

fn index(snapshot: &FactSnapshot) -> HashMap<&str, &Value> {
    snapshot
        .observation
        .get("nodes")
        .and_then(Value::as_map)
        .into_iter()
        .flatten()
        .map(|(token, value)| (token.as_str(), value))
        .collect()
}

fn confirmed_children<'a>(snapshot: &'a FactSnapshot, token: &str) -> Option<&'a [Value]> {
    let mut matches = seq(&snapshot.facts, "relations")
        .iter()
        .filter(|row| text(row, "source") == Some(token) && text(row, "kind") == Some("children"));
    let relation = matches.next()?;
    (matches.next().is_none())
        .then_some(relation)
        .and_then(complete_children)
}

fn complete_children(relation: &Value) -> Option<&[Value]> {
    let targets = relation.get("targets")?.as_seq()?;
    (text(relation, "state") == Some("complete")
        && text(relation, "certainty") == Some("confirmed")
        && relation
            .get("offset")
            .map_or(true, |offset| offset.as_i64() == Some(0))
        && relation
            .get("total")
            .map_or(true, |total| total.as_i64() == Some(targets.len() as i64)))
    .then_some(targets)
}

fn complete_targets<'a>(
    node: &'a Value,
    kind: &str,
    require_available: bool,
) -> Option<&'a [Value]> {
    let relation = raw_relation(node, kind)?;
    let targets = relation.get("targets")?.as_seq()?;
    if text(relation, "kind") != Some(kind)
        || !matches!(text(relation, "evidence"), Some("reported" | "verified"))
        || text(relation, "enumeration") != Some("enumerated")
        || !matches!(
            relation.get("offset"),
            None | Some(Value::Null | Value::Int(0))
        )
        || relation.get("total").and_then(Value::as_i64) != Some(targets.len() as i64)
        || require_available && !seq(relation, "unavailable_positions").is_empty()
    {
        return None;
    }
    if let Some(positions) = relation
        .get("positions")
        .filter(|value| **value != Value::Null)
    {
        let positions = positions.as_seq()?;
        if positions.len() != targets.len()
            || positions
                .iter()
                .enumerate()
                .any(|(index, value)| value.as_i64() != Some(index as i64))
        {
            return None;
        }
    }
    Some(targets)
}

fn contains(values: &[Value], token: &str) -> bool {
    values.iter().any(|value| value.as_str() == Some(token))
}

fn single(values: &[Value]) -> Option<&str> {
    let [token] = values else { return None };
    token.as_str()
}

pub(crate) fn presentations(snapshot: &FactSnapshot) -> Vec<(&str, &str)> {
    let nodes = index(snapshot);
    let mut pending: Vec<_> = seq(&snapshot.observation, "roots")
        .iter()
        .filter_map(Value::as_str)
        .collect();
    let mut seen = HashSet::new();
    let mut output = Vec::new();
    while let Some(opener) = pending.pop() {
        if !seen.insert(opener) {
            continue;
        }
        let Some(node) = nodes.get(opener) else {
            continue;
        };
        let children =
            raw_relation(node, "children").map_or(&[][..], |value| seq(value, "targets"));
        let children = children.iter().filter_map(Value::as_str);
        pending.extend(children.clone());
        if !matches!(
            text(node, "role"),
            Some("menu_button" | "popup_button" | "combo_box" | "menu_bar_item")
        ) || states(node, "expanded") == Some(&Value::Bool(false))
        {
            continue;
        }
        output.extend(children.filter_map(|token| {
            let child = nodes.get(token)?;
            (matches!(text(child, "role"), Some("menu" | "list"))
                && states(child, "visible") != Some(&Value::Bool(false)))
            .then_some((opener, token))
        }));
    }
    output
}

pub(crate) fn removed(snapshot: &FactSnapshot, opener: &str, content: &str) -> bool {
    let node = snapshot
        .observation
        .get("nodes")
        .and_then(|nodes| nodes.get(opener));
    node.is_some_and(|node| {
        states(node, "expanded") == Some(&Value::Bool(false))
            || confirmed_children(snapshot, opener)
                .is_some_and(|targets| !contains(targets, content))
    })
}

pub(crate) fn complete_content(snapshot: &FactSnapshot, root: &str) -> bool {
    let nodes = index(snapshot);
    let mut relations: HashMap<&str, Vec<&Value>> = HashMap::new();
    for row in seq(&snapshot.facts, "relations")
        .iter()
        .filter(|row| text(row, "kind") == Some("children"))
    {
        if let Some(token) = text(row, "source") {
            relations.entry(token).or_default().push(row);
        }
    }
    let mut pending = vec![root];
    let mut seen = HashSet::new();
    while let Some(token) = pending.pop() {
        if !seen.insert(token) {
            continue;
        }
        let Some(node) = nodes.get(token) else {
            return false;
        };
        let Some(rows) = relations.get(token) else {
            if matches!(
                text(node, "role"),
                Some("list" | "menu" | "window" | "group" | "tree" | "table")
            ) {
                return false;
            }
            continue;
        };
        let [relation] = rows.as_slice() else {
            return false;
        };
        let Some(targets) = complete_children(relation) else {
            return false;
        };
        pending.extend(targets.iter().filter_map(Value::as_str));
    }
    true
}

fn actionable(node: &Value) -> bool {
    text(node, "role") == Some("menu_item")
        && states(node, "enabled") == Some(&Value::Bool(true))
        && states(node, "visible") != Some(&Value::Bool(false))
        && states(node, "showing") != Some(&Value::Bool(false))
        && seq(node, "actions").iter().any(|action| {
            text(action, "key") == Some("AXPress")
                && action.get("available") != Some(&Value::Bool(false))
        })
}

pub(crate) fn new_local_menu<'a>(
    before: &FactSnapshot,
    after: &'a FactSnapshot,
    target: &str,
    root: &str,
    published: &HashSet<&str>,
) -> Option<&'a str> {
    let before = index(before);
    let after = index(after);
    let mut token = target;
    let mut child = None;
    let mut visited = HashSet::new();
    let mut menus = HashSet::new();
    while visited.insert(token) {
        let old = *before.get(token)?;
        let new = *after.get(token)?;
        let role = text(old, "role")?;
        if role == "unknown" || text(new, "role") != Some(role) {
            return None;
        }
        if let Some(child) = child {
            for node in [old, new] {
                let relation = raw_relation(node, "children")?;
                if text(relation, "evidence") == Some("candidate")
                    || !contains(seq(relation, "targets"), child)
                {
                    return None;
                }
            }
        }
        // The common window root cannot establish another control's menu.
        if token != root || token == target {
            if let Some((old_children, new_children)) =
                complete_targets(old, "children", true).zip(complete_targets(new, "children", true))
            {
                for menu in new_children.iter().filter_map(Value::as_str) {
                    if contains(old_children, menu) || before.contains_key(menu) {
                        continue;
                    }
                    let Some(node) = after.get(menu) else {
                        continue;
                    };
                    if text(node, "role") != Some("menu")
                        || states(node, "visible") == Some(&Value::Bool(false))
                        || states(node, "showing") == Some(&Value::Bool(false))
                        || complete_targets(node, "parent", true).and_then(single) != Some(token)
                    {
                        continue;
                    }
                    let Some(children) = complete_targets(node, "children", true) else {
                        continue;
                    };
                    if children.iter().filter_map(Value::as_str).any(|item| {
                        published.contains(item)
                            && after.get(item).is_some_and(|node| {
                                actionable(node)
                                    && complete_targets(node, "parent", true).and_then(single)
                                        == Some(menu)
                            })
                    }) {
                        menus.insert(menu);
                    }
                }
            }
        }
        if token == root {
            return (menus.len() == 1)
                .then(|| menus.into_iter().next())
                .flatten();
        }
        let parents = complete_targets(old, "parent", true)?;
        if Some(parents) != complete_targets(new, "parent", true) {
            return None;
        }
        child = Some(token);
        token = single(parents)?;
    }
    None
}

pub(crate) fn collection_issues(snapshot: &FactSnapshot) -> Vec<(&str, Option<&str>)> {
    seq(&snapshot.observation, "issues")
        .iter()
        .filter_map(|issue| {
            let state = text(issue, "state")?;
            let path = seq(issue, "path");
            ((path.is_empty()
                || matches!(path, [Value::Text(key)] if key == "nodes" || key == "roots"))
                && !matches!(state, "absent" | "unsupported"))
            .then_some((state, text(issue, "detail")))
        })
        .collect()
}

const PAYLOAD: &[&str] = &[
    "description",
    "help",
    "placeholder",
    "url",
    "control_value",
    "numeric_value",
    "text",
];
const ABSENCE_PATHS: &[&[&str]] = &[
    &["description"],
    &["help"],
    &["placeholder"],
    &["url"],
    &["control_value", "value"],
    &["text", "selected_text"],
];

fn absent(issue: &Value) -> bool {
    matches!(text(issue, "state"), Some("absent" | "unsupported"))
}

fn path_starts(path: &[Value], prefix: &[&str]) -> bool {
    path.len() >= prefix.len()
        && path
            .iter()
            .zip(prefix)
            .all(|(item, expected)| item.as_str() == Some(expected))
}

fn empty_payload(node: &Value, token: &str, issues: &[Value]) -> bool {
    PAYLOAD
        .iter()
        .all(|key| matches!(node.get(key), None | Some(Value::Null)))
        && ABSENCE_PATHS.iter().all(|required| {
            issues.iter().any(|issue| {
                let path = seq(issue, "path");
                path_starts(path, &["nodes", token])
                    && required.len() >= path.len() - 2
                    && path[2..]
                        .iter()
                        .zip(*required)
                        .all(|(item, expected)| item.as_str() == Some(expected))
                    && absent(issue)
            })
        })
}

pub(crate) fn sharing_control_signature<'a>(
    snapshot: &'a FactSnapshot,
    root: &str,
) -> Option<(&'a str, &'a str)> {
    let observation = &snapshot.observation;
    let root_token = single(seq(observation, "roots"))?;
    if text(observation, "backend") != Some("macos") || root_token != root {
        return None;
    }
    let issues = seq(observation, "issues");
    let nodes = observation.get("nodes")?;
    let dialog = nodes.get(root)?;
    if text(dialog, "role") != Some("dialog")
        || states(dialog, "modal") != Some(&Value::Bool(false))
        || states(dialog, "focused") != Some(&Value::Bool(false))
        || !empty_payload(dialog, root, issues)
    {
        return None;
    }
    let button_token = single(complete_targets(dialog, "children", false)?)?;
    let button = nodes.get(button_token)?;
    if text(button, "role") != Some("button")
        || text(button, "name") != Some("WindowSharingSessionButton")
        || states(button, "enabled") != Some(&Value::Bool(true))
        || states(button, "focused") != Some(&Value::Bool(false))
        || !empty_payload(button, button_token, issues)
        || !complete_targets(button, "children", false)?.is_empty()
        || !seq(button, "actions").iter().any(|action| {
            text(action, "kind") == Some("invoke")
                && text(action, "key") == Some("AXPress")
                && action.get("available") != Some(&Value::Bool(false))
        })
    {
        return None;
    }
    for issue in issues {
        let path = seq(issue, "path");
        if path.is_empty() || path.len() == 1 && path[0].as_str() == Some("nodes") {
            return None;
        }
        let required: &[&[&str]] = if path_starts(path, &["nodes", root_token]) {
            &[
                &["role"],
                &["states", "modal"],
                &["states", "focused"],
                &["relations", "children"],
            ]
        } else if path_starts(path, &["nodes", button_token]) {
            &[
                &["role"],
                &["name"],
                &["actions"],
                &["states", "enabled"],
                &["states", "focused"],
                &["relations", "children"],
            ]
        } else {
            continue;
        };
        let relative = &path[2..];
        if relative.is_empty()
            || required.iter().any(|required| {
                relative
                    .iter()
                    .zip(*required)
                    .all(|(item, expected)| item.as_str() == Some(expected))
            })
            || relative[0]
                .as_str()
                .is_some_and(|field| PAYLOAD.contains(&field))
                && !absent(issue)
        {
            return None;
        }
    }
    Some((root_token, button_token))
}

pub(crate) fn capture_companion_evidence<'a>(
    snapshot: &'a FactSnapshot,
    root: &str,
) -> Option<(&'a str, &'a str)> {
    let (root, button) = sharing_control_signature(snapshot, root)?;
    let observation = &snapshot.observation;
    if text(observation.get("nodes")?.get(root)?, "name") != Some("Window") {
        return None;
    }
    for issue in seq(observation, "issues") {
        let path = seq(issue, "path");
        if path.len() < 2 || path[0].as_str() != Some("nodes") {
            return None;
        }
        if matches!(path[1].as_str(), Some(token) if token == root || token == button)
            && (path.len() == 2
                || matches!(path[2].as_str(), Some("name" | "geometry" | "actions")))
        {
            return None;
        }
    }
    Some((root, button))
}

#[cfg(test)]
mod tests {
    use super::*;

    fn string(value: &str) -> Value {
        Value::Text(value.to_owned())
    }

    fn map<const N: usize>(items: [(&str, Value); N]) -> Value {
        Value::Map(
            items
                .into_iter()
                .map(|(key, value)| (key.to_owned(), value))
                .collect(),
        )
    }

    fn list(items: &[&str]) -> Value {
        Value::Seq(items.iter().map(|item| string(item)).collect())
    }

    fn relation(kind: &str, targets: &[&str], evidence: &str) -> Value {
        map([
            ("kind", string(kind)),
            ("targets", list(targets)),
            ("total", Value::Int(targets.len() as i64)),
            ("evidence", string(evidence)),
            ("enumeration", string("enumerated")),
        ])
    }

    fn capture(menu: bool, parent_evidence: &str, issue: bool) -> FactSnapshot {
        let opener_children: &[&str] = if menu { &["menu"] } else { &[] };
        let mut nodes = vec![
            (
                "window",
                map([
                    ("role", string("window")),
                    (
                        "relations",
                        map([("children", relation("children", &["opener"], "reported"))]),
                    ),
                ]),
            ),
            (
                "opener",
                map([
                    ("role", string("popup_button")),
                    (
                        "relations",
                        map([
                            (
                                "children",
                                relation("children", opener_children, "reported"),
                            ),
                            ("parent", relation("parent", &["window"], "reported")),
                        ]),
                    ),
                ]),
            ),
        ];
        if menu {
            nodes.extend([
                (
                    "menu",
                    map([
                        ("role", string("menu")),
                        (
                            "relations",
                            map([
                                ("children", relation("children", &["item"], "reported")),
                                ("parent", relation("parent", &["opener"], parent_evidence)),
                            ]),
                        ),
                    ]),
                ),
                (
                    "item",
                    map([
                        ("role", string("menu_item")),
                        ("states", map([("enabled", Value::Bool(true))])),
                        (
                            "actions",
                            Value::Seq(vec![map([("key", string("AXPress"))])]),
                        ),
                        (
                            "relations",
                            map([("parent", relation("parent", &["menu"], "reported"))]),
                        ),
                    ]),
                ),
            ]);
        }
        let facts = [
            ("window", &["opener"][..]),
            ("opener", opener_children),
            ("menu", &["item"][..]),
        ]
        .into_iter()
        .take(if menu { 3 } else { 2 })
        .map(|(source, children)| {
            map([
                ("source", string(source)),
                ("kind", string("children")),
                ("certainty", string("confirmed")),
                (
                    "state",
                    string(if issue && source == "opener" {
                        "partial"
                    } else {
                        "complete"
                    }),
                ),
                ("targets", list(children)),
            ])
        })
        .collect();
        FactSnapshot {
            observation: map([
                ("roots", list(&["window"])),
                (
                    "nodes",
                    Value::Map(
                        nodes
                            .into_iter()
                            .map(|(key, value)| (key.to_owned(), value))
                            .collect(),
                    ),
                ),
            ]),
            facts: map([("relations", Value::Seq(facts))]),
            metadata: Value::Null,
            query: Value::Null,
        }
    }

    #[test]
    fn keeps_menu_discovery_separate_from_confirmed_effect_and_removal() {
        let before = capture(false, "reported", false);
        let after = capture(true, "reported", false);
        let uncertain_parent = capture(true, "candidate", false);
        let partial_before = capture(false, "reported", true);
        let published = HashSet::from(["item"]);
        assert!(presentations(&before).is_empty());
        assert_eq!(presentations(&after), [("opener", "menu")]);
        assert_eq!(presentations(&uncertain_parent), [("opener", "menu")]);
        assert_eq!(
            new_local_menu(&before, &after, "opener", "window", &published),
            Some("menu")
        );
        assert_eq!(
            new_local_menu(&before, &uncertain_parent, "opener", "window", &published),
            None
        );
        assert_eq!(
            new_local_menu(&before, &after, "opener", "window", &HashSet::new()),
            None
        );
        assert!(removed(&before, "opener", "menu"));
        assert!(!removed(&partial_before, "opener", "menu"));
        assert!(complete_content(&after, "window"));
        assert!(!complete_content(&partial_before, "window"));
    }
}
