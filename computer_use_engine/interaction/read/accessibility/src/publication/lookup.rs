// Copyright (c) Huawei Technologies Co., Ltd. 2026. All rights reserved.

//! The macOS path matcher restricted to one already acquired query graph.

use std::collections::{HashMap, HashSet};

use super::{field, range_offset, rows, string, Binding, PublicationError};
use crate::{facts::FactSnapshot, value::Value};

const MAX_NODES: usize = 4096;

#[derive(Debug, Default)]
pub(crate) struct Condition<'a> {
    pub(crate) role: Option<&'a str>,
    pub(crate) identifier: Option<&'a str>,
    pub(crate) name: Option<&'a str>,
}

#[derive(Debug)]
pub(crate) enum Error<E> {
    Checkpoint(E),
    Invalid(PublicationError),
    Unavailable,
    RootUnavailable,
    Failed { code: &'static str, detail: String },
}

impl<E> From<PublicationError> for Error<E> {
    fn from(error: PublicationError) -> Self {
        Self::Invalid(error)
    }
}

fn incomplete<E>(detail: impl Into<String>) -> Error<E> {
    Error::Failed {
        code: "observation_incomplete",
        detail: detail.into(),
    }
}

#[derive(Clone, Copy, Eq, PartialEq)]
enum Field<'a> {
    Unknown,
    Absent,
    Text(&'a str),
    Boolean(bool),
}

impl Field<'_> {
    fn truthy(self) -> bool {
        match self {
            Self::Text(value) => !value.is_empty(),
            Self::Boolean(value) => value,
            _ => false,
        }
    }
}

fn role_name(raw: Field<'_>) -> String {
    let Field::Text(raw) = raw else {
        return "unknown".to_owned();
    };
    let name = raw.strip_prefix("AX").unwrap_or(raw);
    let mut chars = name.chars();
    let Some(first) = chars.next() else {
        return "unknown".to_owned();
    };
    let mut lower = first.to_lowercase();
    let first = lower
        .next()
        .expect("a character always has a lowercase form");
    if !first.is_ascii_lowercase()
        || lower.next().is_some()
        || !chars
            .as_str()
            .bytes()
            .all(|byte| byte.is_ascii_alphanumeric())
    {
        return "unknown".to_owned();
    }
    let mut name = String::with_capacity(name.len());
    name.push(first);
    name.push_str(chars.as_str());
    name
}

struct Query<'a, F> {
    root: &'a str,
    nodes: HashMap<&'a str, &'a Value>,
    edges: HashMap<(&'a str, &'a str), &'a Value>,
    completed_edges: HashMap<(&'a str, &'static str), Vec<&'a str>>,
    follow_content: bool,
    native_relation_pages: bool,
    checkpoint: F,
}

impl<'a, F, E> Query<'a, F>
where
    F: FnMut() -> std::result::Result<(), E>,
{
    fn checkpoint(&mut self) -> std::result::Result<(), Error<E>> {
        (self.checkpoint)().map_err(Error::Checkpoint)
    }

    fn node(&mut self, token: &str) -> std::result::Result<&'a Value, Error<E>> {
        self.checkpoint()?;
        let Some(&node) = self.nodes.get(token) else {
            return Err(if self.nodes.len() >= MAX_NODES {
                incomplete("Query exhausted its native node budget.")
            } else {
                Error::Unavailable
            });
        };
        if token == self.root {
            if let Some(status) = node
                .get("fieldStatus")
                .and_then(|status| status.get("AXRole"))
            {
                if status.get("state").and_then(Value::as_str) == Some("error")
                    && status.get("nativeDomain").and_then(Value::as_str) == Some("AXError")
                    && status.get("nativeCode").and_then(Value::as_i64) == Some(-25202)
                {
                    return Err(Error::RootUnavailable);
                }
            }
        }
        Ok(node)
    }

    fn field(&mut self, token: &str, property: &str) -> std::result::Result<Field<'a>, Error<E>> {
        let node = self.node(token)?;
        let state = field(node, "fieldStatus")?
            .get(property)
            .and_then(|status| status.get("state"))
            .and_then(Value::as_str);
        if matches!(state, Some("absent" | "unsupported")) {
            return Ok(Field::Absent);
        }
        if state.is_some() {
            return Ok(Field::Unknown);
        }
        let value = field(node, "values")?.get(property);
        let typed = if matches!(property, "AXFocused" | "AXModal") {
            value
                .and_then(Value::as_bool)
                .map_or(Field::Unknown, Field::Boolean)
        } else {
            match value {
                Some(Value::Null) => Field::Absent,
                Some(Value::Text(value)) => Field::Text(value),
                _ => Field::Unknown,
            }
        };
        Ok(typed)
    }

    fn relation(
        &mut self,
        token: &'a str,
        kind: &'static str,
        property: &'static str,
        read_missing: bool,
    ) -> std::result::Result<Vec<&'a str>, Error<E>> {
        self.node(token)?;
        let key = (token, property);
        if let Some(complete) = self.completed_edges.get(&key) {
            return Ok(complete.clone());
        }
        let Some(&relation) = self.edges.get(&key) else {
            if read_missing {
                self.checkpoint()?;
                return Err(Error::Unavailable);
            }
            return Err(incomplete(format!(
                "Required native relation was not collected: {kind}"
            )));
        };
        let state = string(relation, "state")?;
        let mut targets = Vec::new();
        if !matches!(state, "absent" | "unsupported") {
            if string(relation, "certainty")? != "confirmed"
                || !matches!(state, "complete" | "partial")
                || range_offset(relation)? != 0
            {
                return Err(incomplete(format!(
                    "Required native relation is incomplete: {kind}"
                )));
            }
            if relation
                .get("unavailablePositions")
                .and_then(Value::as_seq)
                .is_some_and(|v| !v.is_empty())
            {
                return Err(incomplete(
                    "Required native relation contains unavailable object slots.",
                ));
            }
            for target in rows(relation, "targets")? {
                targets.push(
                    target
                        .as_str()
                        .ok_or(PublicationError::Invalid("relation target"))?,
                );
            }
            let cursor = relation
                .get("cursor")
                .filter(|value| **value != Value::Null);
            let total = relation
                .get("total")
                .and_then(Value::as_i64)
                .unwrap_or_default();
            if cursor.is_some() || targets.is_empty() && state == "partial" && total > 0 {
                if targets.len() >= MAX_NODES {
                    return Err(incomplete(
                        "Relation continuation exceeded the query budget.",
                    ));
                }
                // A missing first page uses the direct captured node path when
                // batching is enabled. Other pages reach the captured relation
                // port after one more deadline checkpoint.
                if cursor.is_some() || !self.native_relation_pages || self.nodes.len() >= MAX_NODES
                {
                    self.checkpoint()?;
                }
                return Err(Error::Unavailable);
            }
            if targets.len() > MAX_NODES || state != "complete" {
                return Err(incomplete(
                    "The query needs a relation with an unread remainder.",
                ));
            }
        }
        self.completed_edges.insert(key, targets.clone());
        Ok(targets)
    }

    fn name(&mut self, token: &'a str) -> std::result::Result<Field<'a>, Error<E>> {
        for property in ["AXTitle", "AXDescription"] {
            let value = self.field(token, property)?;
            if value == Field::Unknown || value.truthy() {
                return Ok(value);
            }
        }
        let labels = self.relation(token, "labelledBy", "AXTitleUIElement", false)?;
        let label = match labels.as_slice() {
            [] => return Ok(Field::Text("")),
            [label] => *label,
            _ => return Ok(Field::Unknown),
        };
        let title = self.field(label, "AXTitle")?;
        if title == Field::Unknown || title.truthy() {
            return Ok(title);
        }
        let value = self.field(label, "AXValue")?;
        Ok(if value == Field::Absent {
            Field::Text("")
        } else {
            value
        })
    }

    fn matches(
        &mut self,
        token: &'a str,
        condition: &Condition<'_>,
    ) -> std::result::Result<Option<bool>, Error<E>> {
        let mut unknown = false;
        for (selector, expected) in [
            ("role", condition.role),
            ("identifier", condition.identifier),
            ("name", condition.name),
        ] {
            let Some(expected) = expected else {
                continue;
            };
            let value = match selector {
                "role" => self.field(token, "AXRole")?,
                "identifier" => self.field(token, "AXIdentifier")?,
                _ => self.name(token)?,
            };
            if value == Field::Unknown {
                unknown = true;
            } else if if selector == "role" {
                role_name(value) != expected
            } else {
                value != Field::Text(expected)
            } {
                return Ok(Some(false));
            }
        }
        Ok((!unknown).then_some(true))
    }

    fn transparent(
        &mut self,
        token: &'a str,
        role: &str,
    ) -> std::result::Result<Option<bool>, Error<E>> {
        match role {
            "menu" => return Ok(Some(true)),
            "group" | "unknown" | "scrollArea" => (),
            _ => return Ok(Some(false)),
        }
        let values = [
            self.name(token)?,
            self.field(token, "AXIdentifier")?,
            self.field(token, "AXSubrole")?,
            self.field(token, "AXFocused")?,
            self.field(token, "AXModal")?,
        ];
        if values.iter().any(|value| value.truthy()) {
            return Ok(Some(false));
        }
        let actions = field(self.node(token)?, "actions")?;
        if !rows(actions, "items")?.is_empty() {
            return Ok(Some(false));
        }
        if values.contains(&Field::Unknown) || string(actions, "state")? != "complete" {
            return Ok(None);
        }
        Ok(Some(true))
    }

    fn contents(&mut self, token: &'a str) -> std::result::Result<Vec<&'a str>, Error<E>> {
        let mut children = self.relation(token, "children", "AXChildren", false)?;
        if self.follow_content {
            for (kind, property) in [
                ("visibleRows", "AXVisibleRows"),
                ("visibleCells", "AXVisibleCells"),
                ("rows", "AXRows"),
            ] {
                children.extend(self.relation(token, kind, property, true)?);
            }
        }
        let mut seen = HashSet::new();
        children.retain(|child| seen.insert(*child));
        Ok(children)
    }

    fn resolve(&mut self, path: &[Condition<'_>]) -> std::result::Result<&'a str, Error<E>> {
        let mut selected = self.root;
        let mut anchors = vec![selected];
        for (step, condition) in path.iter().enumerate() {
            let candidates = if step == 0 {
                vec![self.root]
            } else {
                self.contents(selected)?
            };
            let mut found = None;
            let mut incomplete_match = false;
            let mut visited = HashSet::new();
            let mut active = HashSet::new();
            let mut stack: Vec<_> = candidates
                .into_iter()
                .rev()
                .map(|node| (node, true))
                .collect();
            while let Some((token, entering)) = stack.pop() {
                if !entering {
                    active.remove(token);
                    continue;
                }
                self.checkpoint()?;
                if active.contains(token) {
                    return Err(incomplete("Path containment is cyclic."));
                }
                if visited.contains(token) {
                    continue;
                }
                if visited.len() >= MAX_NODES {
                    return Err(incomplete("Path containment exceeds its node budget."));
                }
                visited.insert(token);
                let role = role_name(self.field(token, "AXRole")?);
                if token != self.root && matches!(role.as_str(), "window" | "sheet") {
                    continue;
                }
                let matched = self.matches(token, condition)?;
                incomplete_match |= matched.is_none();
                if matched == Some(true) && found.replace(token).is_some() {
                    return Err(Error::Failed {
                        code: "ambiguous_target",
                        detail: format!("Path step {} has more than one native match.", step + 1),
                    });
                }
                let descend = if step == 0 {
                    Some(
                        !matches!(condition.role, Some("window" | "sheet" | "menuBar"))
                            && !(matched == Some(false)
                                && role == "textField"
                                && condition.role == Some("textField")),
                    )
                } else if matched == Some(true) {
                    Some(false)
                } else {
                    self.transparent(token, &role)?
                };
                incomplete_match |= descend.is_none();
                if descend == Some(true) {
                    let children = self.contents(token)?;
                    active.insert(token);
                    stack.push((token, false));
                    stack.extend(children.into_iter().rev().map(|child| (child, true)));
                }
            }
            if incomplete_match {
                return Err(incomplete(format!(
                    "Path step {} cannot establish a unique match from the available facts.",
                    step + 1
                )));
            }
            selected = found.ok_or_else(|| Error::Failed {
                code: "element_not_found",
                detail: format!("No native element matches path step {}.", step + 1),
            })?;
            if step > 0 && anchors.contains(&selected) {
                return Err(incomplete("Path steps cannot select an ancestor again."));
            }
            if !anchors.contains(&selected) {
                anchors.push(selected);
            }
        }
        Ok(selected)
    }
}

/// The Python session owns scope selection, positive caching and the live-query
/// fallback. This call only returns the raw node selected within that scope.
pub(crate) fn resolve<'a, E>(
    capture: &'a FactSnapshot,
    target: Binding<'_>,
    path: &[Condition<'_>],
    capabilities: &[String],
    checkpoint: impl FnMut() -> std::result::Result<(), E>,
) -> std::result::Result<&'a str, Error<E>> {
    let facts = &capture.query;
    let surface = match field(facts, "surface")? {
        Value::Null => None,
        Value::Text(surface) => Some(surface.as_str()),
        _ => return Err(PublicationError::Invalid("surface").into()),
    };
    if string(facts, "platform")? != "macos"
        || string(facts, "session")? != target.session
        || surface != target.surface
    {
        return Err(Error::Unavailable);
    }
    let root = rows(facts, "roots")?
        .iter()
        .filter_map(Value::as_str)
        .find(|token| Some(*token) == target.node)
        .ok_or(Error::Unavailable)?;
    let mut nodes = HashMap::new();
    for row in rows(facts, "nodes")? {
        nodes.insert(string(row, "node")?, row);
    }
    let mut edges = HashMap::new();
    for edge in rows(facts, "relations")? {
        edges.insert((string(edge, "source")?, string(edge, "property")?), edge);
    }
    Query {
        root,
        nodes,
        edges,
        completed_edges: HashMap::new(),
        follow_content: capabilities
            .iter()
            .any(|name| name == "nativeFollowRelations"),
        native_relation_pages: capabilities
            .iter()
            .any(|name| name == "nativeRelationPages"),
        checkpoint,
    }
    .resolve(path)
}

#[cfg(test)]
mod tests {
    use super::*;
    use crate::wire::decode_json;

    const TARGET: Binding<'_> = Binding {
        session: "session",
        instance: Some("app"),
        root_window: Some("window"),
        surface: Some("surface"),
        node: Some("root"),
    };

    fn capture(nodes: &str, relations: &str) -> FactSnapshot {
        let query = format!(
            r#"{{"session":"session","platform":"macos","surface":"surface","roots":["root"],"nodes":[{nodes}],"relations":[{relations}]}}"#
        );
        FactSnapshot {
            observation: Value::Null,
            facts: Value::Null,
            metadata: Value::Null,
            query: decode_json(query.as_bytes()).unwrap(),
        }
    }

    #[test]
    fn raw_path_checks_exact_roots_actions_unknowns_and_duplicate_containment() {
        let nodes = r#"
            {"node":"root","values":{"AXRole":"AXWindow"},"fieldStatus":{}},
            {"node":"wrapper","values":{"AXRole":"AXGroup","AXFocused":false,"AXModal":false},
             "fieldStatus":{"AXTitle":{"state":"absent"},"AXDescription":{"state":"unsupported"},
             "AXIdentifier":{"state":"absent"},"AXSubrole":{"state":"absent"}},
             "actions":{"state":"complete","items":[]}},
            {"node":"button","values":{"AXRole":"AXButton","AXTitle":"Confirm"},"fieldStatus":{}}
        "#;
        let relations = r#"
            {"source":"root","property":"AXChildren","state":"complete","certainty":"confirmed","targets":["wrapper","wrapper"]},
            {"source":"wrapper","property":"AXChildren","state":"complete","certainty":"confirmed","targets":["button"]},
            {"source":"wrapper","property":"AXTitleUIElement","state":"unsupported"}
        "#;
        let path = [
            Condition {
                role: Some("window"),
                ..Condition::default()
            },
            Condition {
                role: Some("button"),
                name: Some("Confirm"),
                ..Condition::default()
            },
        ];
        let snapshot = capture(nodes, relations);
        assert_eq!(
            resolve(&snapshot, TARGET, &path, &[], || Ok::<_, ()>(())).unwrap(),
            "button"
        );
        assert!(matches!(
            resolve(
                &snapshot,
                Binding {
                    node: Some("wrapper"),
                    ..TARGET
                },
                &path,
                &[],
                || Ok::<_, ()>(())
            ),
            Err(Error::Unavailable)
        ));
        assert!(matches!(
            resolve(
                &snapshot,
                TARGET,
                &path,
                &["nativeFollowRelations".to_owned()],
                || Ok::<_, ()>(())
            ),
            Err(Error::Unavailable)
        ));
        let named_wrapper = nodes.replace(
            r#""AXIdentifier":{"state":"absent"}"#,
            r#""AXIdentifier":{"state":"error"}"#,
        );
        assert!(matches!(
            resolve(
                &capture(&named_wrapper, relations),
                TARGET,
                &path,
                &[],
                || Ok::<_, ()>(())
            ),
            Err(Error::Failed {
                code: "observation_incomplete",
                ..
            })
        ));
    }

    #[test]
    fn captured_lookup_propagates_the_original_checkpoint_error() {
        let snapshot = capture(
            r#"{"node":"root","values":{"AXRole":"AXWindow"},"fieldStatus":{}}"#,
            "",
        );
        let path = [Condition {
            role: Some("window"),
            ..Condition::default()
        }];
        let mut checks = 0;
        let result = resolve(&snapshot, TARGET, &path, &[], || {
            checks += 1;
            if checks == 2 {
                Err("deadline")
            } else {
                Ok(())
            }
        });
        assert!(matches!(result, Err(Error::Checkpoint("deadline"))));
        assert_eq!(checks, 2);
    }
}
