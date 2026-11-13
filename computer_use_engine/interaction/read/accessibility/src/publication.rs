// Copyright (c) Huawei Technologies Co., Ltd. 2026. All rights reserved.

//! Read-owned facts crossing the session publication boundary. The host
//! retains delivery, alias lifetimes, and cache invalidation.

pub(crate) mod loading;
pub(crate) mod menu;
pub(crate) mod view;

pub(crate) mod lookup;
pub(crate) mod progress;

use std::collections::{HashMap, HashSet};

use super::{facts::FactSnapshot, value::Value};

#[derive(Clone, Copy, Debug, Eq, PartialEq)]
pub(crate) struct Binding<'a> {
    pub(crate) session: &'a str,
    pub(crate) instance: Option<&'a str>,
    pub(crate) root_window: Option<&'a str>,
    pub(crate) surface: Option<&'a str>,
    pub(crate) node: Option<&'a str>,
}

impl<'a> Binding<'a> {
    fn with_node(self, node: &'a str) -> Self {
        Self {
            node: Some(node),
            ..self
        }
    }
}

#[derive(Clone, Copy, Debug, Eq, PartialEq)]
pub(crate) struct Continuation<'a> {
    pub(crate) cursor: &'a str,
    pub(crate) offset: i64,
    pub(crate) total: i64,
}

#[derive(Clone, Copy, Debug, Eq, PartialEq)]
pub(crate) struct ExpectedPage<'a> {
    pub(crate) property: &'a str,
    pub(crate) limit: i64,
    /// The host has already matched this native cursor to its original target.
    pub(crate) previous: Option<Continuation<'a>>,
}

#[derive(Clone, Copy, Debug, Eq, PartialEq)]
pub(crate) struct NativeCursor<'a> {
    pub(crate) session: &'a str,
    pub(crate) surface: Option<&'a str>,
    pub(crate) node: &'a str,
    pub(crate) property: &'a str,
    pub(crate) continuation: Continuation<'a>,
}

#[derive(Debug, Eq, PartialEq)]
pub(crate) enum PublicationError {
    Invalid(&'static str),
    MissingRelationshipCoverage,
    ChangedScopeOrBudget,
    MissingPage,
    ChangedPageRangeOrBudget,
}

type Result<T> = std::result::Result<T, PublicationError>;

fn field<'a>(value: &'a Value, key: &'static str) -> Result<&'a Value> {
    value.get(key).ok_or(PublicationError::Invalid(key))
}

fn string<'a>(value: &'a Value, key: &'static str) -> Result<&'a str> {
    field(value, key)?
        .as_str()
        .ok_or(PublicationError::Invalid(key))
}

fn rows<'a>(value: &'a Value, key: &'static str) -> Result<&'a [Value]> {
    field(value, key)?
        .as_seq()
        .ok_or(PublicationError::Invalid(key))
}

fn entries<'a>(value: &'a Value, key: &'static str) -> Result<&'a [(String, Value)]> {
    field(value, key)?
        .as_map()
        .ok_or(PublicationError::Invalid(key))
}

fn range_offset(relation: &Value) -> Result<i64> {
    relation.get("offset").map_or(Ok(0), |value| {
        value.as_i64().ok_or(PublicationError::Invalid("offset"))
    })
}

fn relation_slots(relation: &Value) -> Result<i64> {
    if let Some(count) = relation.get("inspectedCount") {
        return count
            .as_i64()
            .ok_or(PublicationError::Invalid("inspectedCount"));
    }
    i64::try_from(rows(relation, "targets")?.len())
        .map_err(|_| PublicationError::Invalid("targets"))
}

fn continuation(relation: &Value) -> Result<Option<Continuation<'_>>> {
    if relation.get("cursor").is_none() {
        return Ok(None);
    }
    let offset = range_offset(relation)?
        .checked_add(relation_slots(relation)?)
        .ok_or(PublicationError::Invalid("relation range"))?;
    let total = field(relation, "total")?
        .as_i64()
        .ok_or(PublicationError::Invalid("total"))?;
    Ok(Some(Continuation {
        cursor: string(relation, "cursor")?,
        offset,
        total,
    }))
}

/// Admit the same query scope and relation ranges as NativeTransport.nodes.
/// Return raw cursor registrations in source order for the existing transport
/// store. Public continuation aliases are derived separately after numbering.
pub(crate) fn admit_nodes<'a>(
    query: &'a Value,
    target: Binding<'_>,
    platform: &str,
    max_nodes: usize,
    follow_relations: &[&str],
    page: Option<ExpectedPage<'_>>,
) -> Result<Vec<NativeCursor<'a>>> {
    let requested: HashSet<_> = rows(query, "requestedRelations")?
        .iter()
        .filter_map(Value::as_str)
        .collect();
    if follow_relations
        .iter()
        .any(|name| !requested.contains(name))
    {
        return Err(PublicationError::MissingRelationshipCoverage);
    }
    let session = string(query, "session")?;
    let surface = match field(query, "surface")? {
        Value::Null => None,
        Value::Text(value) => Some(value.as_str()),
        _ => return Err(PublicationError::Invalid("surface")),
    };
    let roots = rows(query, "roots")?;
    if session != target.session
        || surface != target.surface
        || string(query, "platform")? != platform
        || roots.len() != 1
        || roots[0].as_str() != target.node
        || rows(query, "nodes")?.len() > max_nodes
    {
        return Err(PublicationError::ChangedScopeOrBudget);
    }
    let relations = rows(query, "relations")?;
    if let Some(page) = page {
        let relation = relations
            .iter()
            .find(|relation| {
                relation.get("source").and_then(Value::as_str) == target.node
                    && relation.get("property").and_then(Value::as_str) == Some(page.property)
            })
            .ok_or(PublicationError::MissingPage)?;
        if matches!(string(relation, "state")?, "complete" | "partial")
            && string(relation, "certainty")? == "confirmed"
            && (range_offset(relation)? != page.previous.map_or(0, |old| old.offset)
                || page.previous.is_some_and(|old| {
                    relation.get("total").and_then(Value::as_i64) != Some(old.total)
                })
                || relation_slots(relation)? > page.limit)
        {
            return Err(PublicationError::ChangedPageRangeOrBudget);
        }
    }
    let mut cursors = Vec::new();
    for relation in relations {
        if let Some(continuation) = continuation(relation)? {
            cursors.push(NativeCursor {
                session,
                surface,
                node: string(relation, "source")?,
                property: string(relation, "property")?,
                continuation,
            });
        }
    }
    Ok(cursors)
}

#[derive(Debug, PartialEq)]
pub(crate) enum CapturedText<'a> {
    Value(&'a str),
    Reference {
        token: &'a str,
        total_units: Option<&'a Value>,
    },
}

#[derive(Debug, PartialEq)]
pub(crate) struct ElementBinding<'a> {
    pub(crate) reference: &'a str,
    pub(crate) target: Binding<'a>,
    pub(crate) container: bool,
    pub(crate) outside_window: bool,
    pub(crate) scope_kind: Option<&'a str>,
    pub(crate) observed: &'a Value,
    pub(crate) text: Option<CapturedText<'a>>,
    pub(crate) children: Option<Continuation<'a>>,
}

#[derive(Debug, Eq, PartialEq)]
pub(crate) struct PublicContinuation<'a> {
    pub(crate) reference: &'a str,
    pub(crate) kind: &'a str,
    pub(crate) target: Binding<'a>,
    pub(crate) property: &'a str,
    pub(crate) continuation: Continuation<'a>,
}

#[derive(Debug, PartialEq)]
pub(crate) struct RetainedBindings<'a> {
    pub(crate) elements: Vec<ElementBinding<'a>>,
    pub(crate) pages: Vec<PublicContinuation<'a>>,
}

fn index<'a>(record: &'a Value, key: &'static str) -> Result<HashMap<&'a str, &'a Value>> {
    Ok(entries(record, key)?
        .iter()
        .map(|(key, value)| (key.as_str(), value))
        .collect())
}

fn ordinary(node: &Value) -> bool {
    let writable = node
        .get("states")
        .and_then(|states| states.get("editable"))
        .and_then(Value::as_bool)
        == Some(true)
        || node
            .get("actions")
            .and_then(Value::as_seq)
            .into_iter()
            .flatten()
            .any(|action| {
                if action.get("available").and_then(Value::as_bool) != Some(true)
                    || !matches!(
                        action.get("kind").and_then(Value::as_str),
                        Some("set_value" | "replace_selection")
                    )
                {
                    return false;
                }
                let Some([group, name]) = action.get("target").and_then(Value::as_seq) else {
                    return false;
                };
                matches!(
                    (group.as_str(), name.as_str()),
                    (Some("control_value"), Some("value"))
                        | (
                            Some("text"),
                            Some("content" | "selected_text" | "selections")
                        )
                )
            });
    !writable
        && matches!(
            node.get("role").and_then(Value::as_str),
            Some("group" | "unknown" | "window" | "sheet" | "dialog" | "alert")
        )
}

fn captured_text<'a>(
    node: Option<&'a Value>,
    field_name: Option<&str>,
    text_ref: Option<&'a Value>,
) -> Option<CapturedText<'a>> {
    if let Some(token) = text_ref
        .and_then(|value| value.get("textRef"))
        .and_then(Value::as_str)
    {
        let total_units = text_ref
            .and_then(|value| value.get("totalUnits"))
            .filter(|value| **value != Value::Null);
        return Some(CapturedText::Reference { token, total_units });
    }
    let (node, field_name) = (node?, field_name?);
    if node.get("fieldStatus")?.get(field_name).is_some() {
        return None;
    }
    node.get("values")?
        .get(field_name)?
        .as_str()
        .map(CapturedText::Value)
}

/// Formally numbered references are already in source order. Keep every
/// binding, even for unread or presentation-hidden nodes. The host separately
/// attaches member-window identity and enforces its current lifetime.
pub(crate) fn retain_bindings<'a>(
    capture: &'a FactSnapshot,
    semantic: &'a Value,
    target: Binding<'a>,
    include_public_pages: bool,
) -> Result<RetainedBindings<'a>> {
    let facts = &capture.facts;
    let native = index(&capture.observation, "nodes")?;
    let containers = index(&capture.metadata, "input_containers")?;
    let read_fields = index(&capture.metadata, "text_read_fields")?;
    let value_fields = index(semantic, "value_fields")?;
    let relation_fields = index(&capture.metadata, "relation_fields")?;
    let mut node_rows = HashMap::new();
    for node in rows(facts, "nodes")? {
        node_rows.insert(string(node, "node")?, node);
    }
    let mut text_refs = HashMap::new();
    for row in rows(semantic, "text_refs")? {
        text_refs.insert(
            (string(row, "ref")?, string(row, "key")?),
            field(row, "value")?,
        );
    }
    let observed = field(facts, "observed")?;
    let scope_kind = field(semantic, "scope_kind")?.as_str();
    let refs = entries(semantic, "refs")?;
    let aliases = if include_public_pages {
        refs.iter()
            .map(|(reference, token)| {
                let token = token
                    .as_str()
                    .ok_or(PublicationError::Invalid("reference token"))?;
                Ok((token, reference.as_str()))
            })
            .collect::<Result<HashMap<_, _>>>()?
    } else {
        HashMap::new()
    };
    let mut children = HashMap::new();
    let mut pages = Vec::new();
    for relation in rows(facts, "relations")? {
        let token = string(relation, "source")?;
        let next = if string(relation, "certainty")? == "confirmed" {
            continuation(relation)?
        } else {
            None
        };
        let kind = string(relation, "kind")?;
        if kind == "children" {
            children.insert(token, next);
        }
        if include_public_pages {
            if let Some(continuation) = next {
                let key = string(relation, "recordKey")?;
                if let Some(property) = relation_fields
                    .get(token)
                    .and_then(|row| row.get(key))
                    .and_then(Value::as_str)
                {
                    pages.push(PublicContinuation {
                        reference: *aliases
                            .get(token)
                            .ok_or(PublicationError::Invalid("page source alias"))?,
                        kind,
                        target: target.with_node(token),
                        property,
                        continuation,
                    });
                }
            }
        }
    }
    let mut elements = Vec::with_capacity(refs.len());
    for (reference, token) in refs {
        let token = token
            .as_str()
            .ok_or(PublicationError::Invalid("reference token"))?;
        let explicit = if let Some(path) = read_fields
            .get(token)
            .and_then(|value| value.as_seq())
            .filter(|path| !path.is_empty())
        {
            let parts = path
                .iter()
                .map(|part| {
                    part.as_str()
                        .ok_or(PublicationError::Invalid("text read field"))
                })
                .collect::<Result<Vec<_>>>()?;
            Some(parts.join("."))
        } else {
            None
        };
        let field_name = explicit.as_deref().or_else(|| {
            value_fields
                .get(reference.as_str())
                .and_then(|value| value.as_str())
        });
        let source = field_name
            .and_then(|name| text_refs.get(&(reference.as_str(), name)))
            .copied();
        let container = containers
            .get(token)
            .and_then(|value| value.as_bool())
            .unwrap_or_else(|| native.get(token).is_some_and(|value| ordinary(value)));
        let outside_window = capture.observation.get("backend").and_then(Value::as_str) == Some("windows")
            && native.get(token).and_then(|node| node.get("supplements")).and_then(Value::as_seq)
                .into_iter().flatten().any(|item| {
                    item.get("key").and_then(Value::as_str) == Some("observation_scope")
                        && item.get("value").and_then(|value| value.get("ownership")).and_then(Value::as_str) == Some("outside")
                });
        elements.push(ElementBinding {
            reference,
            target: target.with_node(token),
            scope_kind,
            observed,
            container,
            outside_window,
            text: captured_text(node_rows.get(token).copied(), field_name, source),
            children: children.get(token).copied().flatten(),
        });
    }
    Ok(RetainedBindings { elements, pages })
}

#[cfg(test)]
mod tests {
    use super::*;
    use crate::wire::decode_json;

    fn json(source: &str) -> Value {
        decode_json(source.as_bytes()).unwrap()
    }

    fn target() -> Binding<'static> {
        Binding {
            session: "generation",
            instance: Some("application"),
            root_window: Some("owner-window"),
            surface: Some("surface"),
            node: Some("root"),
        }
    }

    fn snapshot() -> FactSnapshot {
        FactSnapshot {
            query: json(
                r#"{
                "session":"generation","platform":"macos","surface":"surface","roots":["root"],
                "observed":{"id":"sample","startedAt":1,"completedAt":2},
                "nodes":[{"node":"root","requestedFields":["AXTitle","AXPosition","AXRole"],
                    "values":{"AXTitle":"original","AXPosition":[2,3],"AXRole":"AXWindow"},
                    "fieldStatus":{"AXHelp":{"state":"absent"},"AXDescription":{"state":"notProbed"}},
                    "actions":{"state":"complete","items":[{"name":"AXPress","detail":"raw"}]}}],
                "requestedRelations":["AXChildren"],
                "relations":[
                    {"source":"root","property":"AXChildren","state":"partial","certainty":"confirmed",
                     "offset":2,"inspectedCount":3,"targets":["child","hidden"],"total":9,"cursor":"native-next"},
                    {"source":"child","property":"AXRows","state":"partial","certainty":"inferred",
                     "targets":[],"total":7,"cursor":"candidate-next"}
                ]
            }"#,
            ),
            observation: json(
                r#"{"nodes":{
                "root":{"role":"window"},
                "child":{"role":"group","actions":[{"kind":"set_value","available":true,"target":["text","content"]}]},
                "hidden":{"role":"group"}
            }}"#,
            ),
            facts: json(
                r#"{
                "observed":{"id":"sample","startedAt":1,"completedAt":2},
                "nodes":[
                    {"node":"root","values":{"control_value.value":"wrong source","text.content":"preview"},
                     "fieldStatus":{"text.content":{"state":"partial"}}},
                    {"node":"child","values":{"text.content":"retained child"},"fieldStatus":{}}
                ],
                "relations":[
                    {"source":"root","recordKey":"children","kind":"children","certainty":"confirmed",
                     "offset":2,"inspectedCount":3,"targets":["child","hidden"],"total":9,"cursor":"native-next"},
                    {"source":"child","recordKey":"rows","kind":"rows","certainty":"unknown",
                     "targets":[],"total":7,"cursor":"candidate-next"}
                ]
            }"#,
            ),
            metadata: json(
                r#"{
                "input_containers":{"root":false},
                "text_read_fields":{"root":["text","content"]},
                "relation_fields":{"root":{"children":"AXChildren"},"child":{"rows":"AXRows"}}
            }"#,
            ),
        }
    }

    #[test]
    fn admission_checks_scope_coverage_and_checked_slots_before_exporting_raw_cursors() {
        let capture = snapshot();
        let earlier = Continuation {
            cursor: "previous",
            offset: 2,
            total: 9,
        };
        let page = ExpectedPage {
            property: "AXChildren",
            limit: 3,
            previous: Some(earlier),
        };
        let cursors = admit_nodes(
            &capture.query,
            target(),
            "macos",
            1,
            &["AXChildren"],
            Some(page),
        )
        .unwrap();
        assert_eq!(
            cursors[0].continuation,
            Continuation {
                cursor: "native-next",
                offset: 5,
                total: 9
            }
        );
        assert_eq!(cursors[1].continuation.cursor, "candidate-next");
        assert_eq!(
            admit_nodes(&capture.query, target(), "macos", 1, &["AXRows"], None),
            Err(PublicationError::MissingRelationshipCoverage)
        );
        assert_eq!(
            admit_nodes(
                &capture.query,
                Binding {
                    node: Some("child"),
                    ..target()
                },
                "macos",
                1,
                &[],
                None
            ),
            Err(PublicationError::ChangedScopeOrBudget)
        );
        assert_eq!(
            admit_nodes(&capture.query, target(), "macos", 0, &[], None),
            Err(PublicationError::ChangedScopeOrBudget)
        );
        assert_eq!(
            admit_nodes(
                &capture.query,
                target(),
                "macos",
                1,
                &[],
                Some(ExpectedPage { limit: 2, ..page })
            ),
            Err(PublicationError::ChangedPageRangeOrBudget)
        );
    }

    #[test]
    fn export_keeps_native_targets_and_source_order_without_registering_a_second_cache() {
        let capture = snapshot();
        let semantic = json(
            r#"{
            "refs":{"4":"root","7":"hidden","2":"child"},
            "scope_kind":"window",
            "value_fields":{"4":"control_value.value","2":"text.content"},
            "text_refs":[{"ref":"4","key":"text.content","value":{"textRef":"text-1","totalUnits":17}}]
        }"#,
        );
        let retained = retain_bindings(&capture, &semantic, target(), true).unwrap();
        assert_eq!(
            retained
                .elements
                .iter()
                .map(|element| element.reference)
                .collect::<Vec<_>>(),
            ["4", "7", "2"]
        );
        assert_eq!(retained.elements[0].target, target());
        assert!(!retained.elements[0].container);
        assert_eq!(
            retained.elements[0].text,
            Some(CapturedText::Reference {
                token: "text-1",
                total_units: Some(&Value::Int(17))
            })
        );
        assert_eq!(retained.elements[1].target.node, Some("hidden"));
        assert!(retained.elements[1].container);
        assert_eq!(retained.elements[1].text, None);
        assert!(!retained.elements[2].container);
        assert_eq!(
            retained.elements[2].text,
            Some(CapturedText::Value("retained child"))
        );
        assert_eq!(retained.elements[0].children.unwrap().offset, 5);
        assert_eq!(retained.pages.len(), 1);
        assert_eq!(retained.pages[0].property, "AXChildren");
        assert_eq!(retained.pages[0].reference, "4");
        assert_eq!(retained.pages[0].kind, "children");
    }
}
