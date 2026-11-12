// Copyright (c) Huawei Technologies Co., Ltd. 2026. All rights reserved.

//! Positive read-content evidence used by the existing host refresh policies.

use std::collections::{HashMap, HashSet};

use super::{FactSnapshot, PublicationError, Value};

const CONTENT_PATHS: &[&[&str]] = &[
    &["role"],
    &["role_detail"],
    &["name"],
    &["description"],
    &["help"],
    &["placeholder"],
    &["url"],
    &["control_value", "value"],
    &["numeric_value", "value"],
    &["text", "content"],
    &["states", "checked"],
    &["states", "selected"],
    &["states", "expanded"],
    &["states", "window_state"],
    &["states", "enabled"],
    &["states", "modal"],
    &["states", "visible"],
    &["states", "showing"],
    &["structure", "row_count"],
    &["structure", "column_count"],
    &["structure", "set_size"],
    &["structure", "selected_count"],
    &["structure", "scroll_x", "position"],
    &["structure", "scroll_y", "position"],
];

const MEANINGFUL_PATHS: &[&[&str]] = &[
    &["name"],
    &["description"],
    &["placeholder"],
    &["text", "content"],
    &["control_value", "value"],
    &["numeric_value", "value"],
];

fn present(value: Option<&Value>) -> Option<&Value> {
    value.filter(|value| **value != Value::Null)
}

struct Content<'a> {
    nodes: HashMap<&'a str, &'a Value>,
    issues: HashMap<&'a str, HashSet<Vec<&'a str>>>,
    incomplete_collection: bool,
    budget_limited: bool,
    began: Option<i64>,
}

impl<'a> Content<'a> {
    fn capture(snapshot: &'a FactSnapshot) -> Self {
        let observation = &snapshot.observation;
        let nodes = observation
            .get("nodes")
            .and_then(Value::as_map)
            .into_iter()
            .flatten()
            .map(|(token, node)| (token.as_str(), node))
            .collect();
        let mut issues: HashMap<_, HashSet<_>> = HashMap::new();
        let mut incomplete_collection = false;
        let mut budget_limited = false;
        for issue in observation
            .get("issues")
            .and_then(Value::as_seq)
            .into_iter()
            .flatten()
        {
            let state = issue.get("state").and_then(Value::as_str);
            if matches!(state, Some("partial" | "timeout" | "not_read"))
                && issue
                    .get("detail")
                    .and_then(Value::as_str)
                    .is_some_and(|detail| detail.to_ascii_lowercase().contains("budget"))
            {
                budget_limited = true;
            }
            let Some(path) = issue.get("path").and_then(Value::as_seq) else {
                continue;
            };
            match path {
                [] | [Value::Text(_)] if path.is_empty() || path[0].as_str() == Some("nodes") => {
                    incomplete_collection |= !matches!(state, Some("absent" | "unsupported"));
                }
                [Value::Text(namespace), Value::Text(token), fields @ ..]
                    if namespace == "nodes" =>
                {
                    if let Some(fields) =
                        fields.iter().map(Value::as_str).collect::<Option<Vec<_>>>()
                    {
                        issues.entry(token.as_str()).or_default().insert(fields);
                    }
                }
                _ => {}
            }
        }
        let began = observation
            .get("interval_ns")
            .and_then(Value::as_seq)
            .and_then(|interval| interval.first())
            .and_then(Value::as_i64);
        Self {
            nodes,
            issues,
            incomplete_collection,
            budget_limited,
            began,
        }
    }

    fn affected(&self, token: &str, path: &[&str]) -> bool {
        self.issues
            .get(token)
            .is_some_and(|issues| (0..=path.len()).any(|length| issues.contains(&path[..length])))
    }

    fn known(&self, token: &str, path: &[&str]) -> Option<&'a Value> {
        let value = path
            .iter()
            .try_fold(*self.nodes.get(token)?, |value, field| value.get(field))?;
        present(Some(value)).filter(|_| !self.affected(token, path))
    }

    fn meaningful(&self, token: &str) -> bool {
        self.known(token, &["role"])
            .and_then(Value::as_str)
            .is_some_and(|role| {
                !matches!(role, "group" | "split_group" | "scroll_area" | "unknown")
            })
            || MEANINGFUL_PATHS.iter().any(|path| {
                self.known(token, path)
                    .is_some_and(|value| value.as_str() != Some(""))
            })
    }

    fn complete_children(&self, token: &str, name: &str, relation: &Value) -> bool {
        let Some(targets) = relation.get("targets").and_then(Value::as_seq) else {
            return false;
        };
        children(relation)
            && present(relation.get("enumeration"))
                .and_then(Value::as_str)
                .unwrap_or("enumerated")
                == "enumerated"
            && present(relation.get("offset")).is_none_or(|offset| offset.as_i64() == Some(0))
            && present(relation.get("positions")).is_none_or(|positions| {
                positions.as_seq().is_some_and(|items| {
                    items.len() == targets.len()
                        && items
                            .iter()
                            .enumerate()
                            .all(|(index, value)| value.as_i64() == i64::try_from(index).ok())
                })
            })
            && present(relation.get("total"))
                .is_none_or(|total| total.as_i64() == i64::try_from(targets.len()).ok())
            && !self.affected(token, &["relations", name])
            && !self.incomplete_collection
    }
}

fn children(relation: &Value) -> bool {
    relation.get("kind").and_then(Value::as_str) == Some("children")
        && relation.get("evidence").and_then(Value::as_str) != Some("candidate")
}

pub(crate) fn same_content_scope(
    before: Option<&FactSnapshot>,
    after: Option<&FactSnapshot>,
) -> bool {
    let (Some(before), Some(after)) = (before, after) else {
        return false;
    };
    before
        .observation
        .get("roots")
        .and_then(Value::as_seq)
        .is_some_and(|roots| !roots.is_empty())
        && ["backend", "session", "roots"]
            .iter()
            .all(|field| before.observation.get(field) == after.observation.get(field))
        && (before.metadata == Value::Null
            || after.metadata == Value::Null
            || present(before.metadata.get("surface")) == present(after.metadata.get("surface")))
}

fn different(left: &Value, right: &Value) -> bool {
    !matches!((left, right), (Value::Float(a), Value::Float(b)) if a.is_nan() && b.is_nan())
        && left != right
}

fn changed_content(before: &Content<'_>, after: &Content<'_>) -> bool {
    for (&token, old_node) in &before.nodes {
        let Some(node) = after.nodes.get(token) else {
            continue;
        };
        if CONTENT_PATHS.iter().any(|path| {
            before
                .known(token, path)
                .zip(after.known(token, path))
                .is_some_and(|(left, right)| different(left, right))
        }) {
            return true;
        }
        for (name, old_relation) in old_node
            .get("relations")
            .and_then(Value::as_map)
            .into_iter()
            .flatten()
        {
            if !before.complete_children(token, name, old_relation) {
                continue;
            }
            let Some(relation) = node
                .get("relations")
                .and_then(|relations| relations.get(name))
                .filter(|relation| {
                    children(relation)
                        && present(relation.get("view")) == present(old_relation.get("view"))
                })
            else {
                continue;
            };
            let targets = relation
                .get("targets")
                .and_then(Value::as_seq)
                .unwrap_or(&[]);
            let old_targets = old_relation
                .get("targets")
                .and_then(Value::as_seq)
                .unwrap_or(&[]);
            let added = targets.iter().filter_map(Value::as_str).any(|target| {
                !before.nodes.contains_key(target)
                    && !old_targets
                        .iter()
                        .any(|value| value.as_str() == Some(target))
                    && after.meaningful(target)
            });
            if added {
                return true;
            }
            let removed = after.complete_children(token, name, relation)
                && old_targets.iter().filter_map(Value::as_str).any(|target| {
                    !targets.iter().any(|value| value.as_str() == Some(target))
                        && before.meaningful(target)
                });
            if removed {
                return true;
            }
        }
    }
    false
}

pub(crate) fn has_content_progress(
    before: Option<&FactSnapshot>,
    after: Option<&FactSnapshot>,
) -> bool {
    if !same_content_scope(before, after) {
        return false;
    }
    let (Some(before), Some(after)) = (before, after) else {
        return false;
    };
    changed_content(&Content::capture(before), &Content::capture(after))
}

pub(crate) fn prefer_earlier_budget_capture(
    before: Option<&FactSnapshot>,
    after: Option<&FactSnapshot>,
) -> bool {
    if !same_content_scope(before, after) {
        return false;
    }
    let (Some(before), Some(after)) = (before, after) else {
        return false;
    };
    let before = Content::capture(before);
    let after = Content::capture(after);
    if after
        .began
        .zip(before.began)
        .is_none_or(|(later, earlier)| later < earlier)
        || !after.budget_limited
        || changed_content(&before, &after)
    {
        return false;
    }
    before.nodes.keys().any(|token| {
        !after.nodes.contains_key(token)
            || CONTENT_PATHS.iter().any(|path| {
                before.known(token, path).is_some() && after.known(token, path).is_none()
            })
    })
}

/// Compare a native capture against the comparison-only fields retained by an
/// earlier transport representation. The common path compares two captures directly.
pub(crate) fn compare_projection(
    native: &FactSnapshot,
    observation: Value,
    surface: Option<String>,
    mode: &str,
    native_is_before: bool,
) -> Result<bool, PublicationError> {
    let metadata = match observation.get("has_collection").and_then(Value::as_bool) {
        Some(true) => Value::Map(vec![(
            "surface".into(),
            surface.map_or(Value::Null, Value::Text),
        )]),
        Some(false) => Value::Null,
        None => return Err(PublicationError::Invalid("comparison collection")),
    };
    let projected = FactSnapshot {
        observation,
        metadata,
        facts: Value::Null,
        query: Value::Null,
    };
    let (before, after) = if native_is_before {
        (Some(native), Some(&projected))
    } else {
        (Some(&projected), Some(native))
    };
    match mode {
        "scope" => Ok(same_content_scope(before, after)),
        "progress" => Ok(has_content_progress(before, after)),
        "retain_earlier" => Ok(prefer_earlier_budget_capture(before, after)),
        _ => Err(PublicationError::Invalid("comparison mode")),
    }
}

#[cfg(test)]
mod tests {
    use super::*;
    use crate::wire::decode_json;

    fn snapshot(nodes: &str, issues: &str) -> FactSnapshot {
        let decode = |text: &str| decode_json(text.as_bytes()).unwrap();
        FactSnapshot {
            observation: decode(&format!(
                r#"{{"backend":"macos","session":"s","roots":["root"],"interval_ns":[1,2],
                                             "nodes":{nodes},"issues":{issues}}}"#
            )),
            metadata: decode(r#"{"surface":"window"}"#),
            facts: Value::Null,
            query: Value::Null,
        }
    }

    #[test]
    fn content_progress_needs_comparable_fields_or_proven_child_coverage() {
        let before = snapshot(
            r#"{"root":{"role":"window","name":"Calendar","states":{"focused":false},
            "relations":{"children":{"kind":"children","targets":[],"total":0}}}}"#,
            "[]",
        );
        let after_nodes = r#"{"root":{"role":"window","name":"Calendar","states":{"focused":true},
            "relations":{"children":{"kind":"children","targets":["child"],"total":1}}},
            "child":{"role":"button"}}"#;
        let after = snapshot(after_nodes, "[]");
        assert!(has_content_progress(Some(&before), Some(&after)));
        assert!(has_content_progress(Some(&after), Some(&before)));

        let partial = snapshot(
            r#"{"root":{"role":"window","name":"Calendar",
            "relations":{"children":{"kind":"children","targets":[]}}}}"#,
            r#"[{"path":["nodes"],"state":"partial","detail":"time budget"}]"#,
        );
        assert!(!has_content_progress(Some(&partial), Some(&after)));
        assert!(prefer_earlier_budget_capture(Some(&after), Some(&partial)));

        let uncertain = snapshot(
            r#"{"root":{"role":"window","name":"Different","states":{"focused":true}}}"#,
            r#"[{"path":["nodes","root","name"],"state":"timeout","detail":"read budget"}]"#,
        );
        assert!(!has_content_progress(Some(&before), Some(&uncertain)));
        assert!(prefer_earlier_budget_capture(
            Some(&before),
            Some(&uncertain)
        ));
        let known = snapshot(r#"{"root":{"role":"window","name":"Different"}}"#, "[]");
        assert!(has_content_progress(Some(&before), Some(&known)));
        assert!(!prefer_earlier_budget_capture(Some(&before), Some(&known)));
    }

    #[test]
    fn mixed_comparison_retains_coverage_and_collection_identity() {
        let full = snapshot(r#"{"root":{"role":"window","name":"Editor"}}"#, "[]");
        let partial = || {
            let mut value = snapshot(
                r#"{"root":{"role":"window"}}"#,
                r#"[{"path":["nodes","root","name"],"state":"timeout","detail":"collection budget"}]"#,
            ).observation;
            let Value::Map(fields) = &mut value else {
                unreachable!()
            };
            fields.push(("has_collection".into(), Value::Bool(true)));
            value
        };
        assert_eq!(
            compare_projection(
                &full,
                partial(),
                Some("window".into()),
                "retain_earlier",
                true
            ),
            Ok(true)
        );
        assert_eq!(
            compare_projection(
                &full,
                partial(),
                Some("window".into()),
                "retain_earlier",
                false
            ),
            Ok(false)
        );
        assert_eq!(
            compare_projection(&full, partial(), Some("other".into()), "scope", true),
            Ok(false)
        );
    }
}
