// Copyright (c) Huawei Technologies Co., Ltd. 2026. All rights reserved.

//! Source-bound relationship and containment evidence for geometric scopes.

use std::collections::{HashMap, HashSet};

use super::{
    field_text, region_role_name, role, seq, truth, GeometricContext, GeometricPlan, NodeEvidence, ParentReport,
    Relation,
};
use crate::{numbering::NodeId, projection::ProjectionNode, semantic::presentation_role, value::Value};

fn fields<'a>(row: &'a [(String, Value)], key: &str) -> Option<&'a Value> {
    row.iter().find_map(|(name, value)| (name == key).then_some(value))
}

fn is_true(value: Option<&Value>) -> bool {
    value == Some(&Value::Bool(true))
}

fn is_false(value: Option<&Value>) -> bool {
    value == Some(&Value::Bool(false))
}

fn present(value: Option<&Value>) -> bool {
    value.is_some_and(|value| *value != Value::Null)
}

fn integer(value: &Value) -> Option<i128> {
    match value {
        Value::Int(value) => Some(i128::from(*value)),
        Value::BigInt(value) => value.parse().ok(),
        Value::Bool(value) => Some(i128::from(*value)),
        _ => None,
    }
}

fn incomplete(read: Option<&Value>, total: Option<&Value>, count: usize) -> bool {
    let read = read.map(integer).unwrap_or(Some(count as i128));
    let total = total.map(integer).unwrap_or(read);
    matches!((read, total), (Some(read), Some(total)) if read < total)
}

pub(super) fn node_evidence(node: &ProjectionNode) -> NodeEvidence {
    let native = |name| fields(&node.native_fields, name);
    let field = |name| node.field(name);
    NodeEvidence {
        scope: node.scope,
        incomplete: present(field("partialReason"))
            || present(native("partialReason"))
            || incomplete(native("childrenRead"), native("childCount"), node.native_children.len()),
        independent: truth(field("actions"))
            || ["pressable", "selectable", "editable", "focused", "selected"]
                .iter()
                .any(|name| is_true(field(name)))
            || field("capabilities")
                .and_then(Value::as_map)
                .is_some_and(|values| values.iter().any(|(_, value)| value.as_str() == Some("available"))),
        boundary: region_role_name(role(node))
            || matches!(
                role(node),
                "window" | "sheet" | "table" | "list" | "outline" | "row" | "cell" | "column" | "menu" | "menuBar"
            )
            || is_true(field("modal"))
            || matches!(field_text(node, "subrole"), Some("dialog" | "systemDialog" | "popover")),
        hidden: is_false(field("visible")) || is_false(field("showing")) || is_true(field("offscreen")),
    }
}

fn text<'a>(record: &'a Value, name: &str) -> Option<&'a str> {
    record.get(name).and_then(Value::as_str)
}

fn zero_offset(value: Option<&Value>, empty_known: bool) -> bool {
    match value {
        None | Some(Value::Int(0)) => true,
        Some(Value::Null) => empty_known,
        _ => false,
    }
}

fn exact_positions(value: Option<&Value>, count: usize, empty_known: bool) -> bool {
    match value {
        None => true,
        Some(Value::Null) => empty_known,
        Some(Value::Seq(values)) => {
            values.len() == count
                && values
                    .iter()
                    .enumerate()
                    .all(|(i, value)| value.as_i64() == Some(i as i64))
        }
        _ => false,
    }
}

fn total_equals(value: Option<&Value>, count: usize) -> bool {
    value.is_none_or(|value| integer(value) == Some(count as i128))
}

#[derive(Default)]
struct ParentState {
    roots: bool,
    uncertain: bool,
    parents: HashSet<NodeId>,
    missing_parent: bool,
}

pub(super) fn capture(context: &GeometricContext<'_>) -> (Vec<Relation>, HashMap<NodeId, ParentReport>) {
    let Some(snapshot) = context.facts else {
        return (
            Vec::new(),
            context
                .projection
                .source_order
                .iter()
                .filter_map(|&id| {
                    context
                        .projection
                        .node(id)
                        .map(|node| (id, ParentReport::Semantic(node.parent)))
                })
                .collect(),
        );
    };
    let nodes = snapshot
        .observation
        .get("nodes")
        .and_then(Value::as_map)
        .unwrap_or_default();
    let ids: HashMap<_, _> = nodes
        .iter()
        .enumerate()
        .map(|(index, (token, _))| (token.as_str(), NodeId(index)))
        .collect();
    let mut states: HashMap<_, ParentState> = ids.values().map(|id| (*id, ParentState::default())).collect();
    let mut relations = Vec::new();
    for relation in seq(snapshot.facts.get("relations")) {
        let source = text(relation, "source").and_then(|token| ids.get(token)).copied();
        let Some(kind) = text(relation, "kind") else { continue };
        let raw_targets = seq(relation.get("targets"));
        let targets = raw_targets
            .iter()
            .filter_map(|value| value.as_str().and_then(|token| ids.get(token)))
            .copied()
            .collect::<Vec<_>>();
        let confirmed = text(relation, "certainty") == Some("confirmed");
        let state = text(relation, "state");
        let enumerated = relation
            .get("enumeration")
            .is_none_or(|value| value.as_str() == Some("enumerated"));
        let empty_known = raw_targets.is_empty() && relation.get("total").and_then(integer) == Some(0);
        let absent = state == Some("absent") && confirmed && empty_known;
        let complete = (state == Some("complete") || absent)
            && targets.len() == raw_targets.len()
            && zero_offset(relation.get("offset"), empty_known)
            && total_equals(relation.get("total"), targets.len())
            && exact_positions(relation.get("positions"), targets.len(), empty_known);
        if kind == "parent" {
            if let Some(report) = source.and_then(|id| states.get_mut(&id)) {
                let reliable = confirmed
                    && matches!(state, Some("complete" | "absent"))
                    && enumerated
                    && zero_offset(relation.get("offset"), false)
                    && total_equals(relation.get("total"), raw_targets.len())
                    && exact_positions(relation.get("positions"), raw_targets.len(), false);
                if !reliable {
                    report.uncertain = true;
                } else if raw_targets.is_empty() {
                    report.roots = true;
                } else {
                    report.parents.extend(targets.iter().copied());
                    report.missing_parent |= targets.len() != raw_targets.len();
                }
            }
        } else if kind == "children" {
            let reliable = confirmed && matches!(state, Some("complete" | "partial")) && enumerated;
            for target in &targets {
                let report = states.get_mut(target).expect("known source");
                match (reliable, source) {
                    (true, Some(source)) => {
                        report.parents.insert(source);
                    }
                    (true, None) => report.missing_parent = true,
                    _ => report.uncertain = true,
                }
            }
        }
        if let Some(source) = source {
            relations.push(Relation {
                source,
                property: text(relation, "property").unwrap_or_default().to_owned(),
                kind: kind.to_owned(),
                targets,
                complete,
                confirmed,
            });
        }
    }
    let parents = states
        .into_iter()
        .map(|(id, state)| {
            let report = if state.parents.len() > 1
                || state.roots && (!state.parents.is_empty() || state.missing_parent)
                || state.missing_parent && !state.parents.is_empty()
            {
                ParentReport::Conflict
            } else if state.uncertain || state.missing_parent {
                ParentReport::Unknown
            } else if let Some(parent) = state.parents.into_iter().next() {
                ParentReport::Confirmed(parent)
            } else if state.roots {
                ParentReport::Root
            } else {
                ParentReport::Unknown
            };
            (id, report)
        })
        .collect();
    (relations, parents)
}

pub(super) fn original_parent(plan: &GeometricPlan, id: NodeId) -> (Option<NodeId>, bool) {
    match plan.parent_reports.get(&id) {
        Some(ParentReport::Confirmed(id)) => (Some(*id), true),
        Some(ParentReport::Root) => (None, true),
        Some(ParentReport::Semantic(id)) => (*id, true),
        _ => (None, false),
    }
}

pub(super) fn original_role(context: &GeometricContext<'_>, id: NodeId) -> Option<String> {
    if let Some(node) = context.projection.node(id) {
        return fields(&node.native_fields, "role")
            .and_then(Value::as_str)
            .map(str::to_owned);
    }
    let nodes = context.facts?.observation.get("nodes")?.as_map()?;
    let native = &nodes.get(id.0)?.1;
    Some(presentation_role(text(native, "role")))
}

fn ordered_role(role: &str, web_area: bool) -> bool {
    matches!(
        role,
        "list"
            | "outline"
            | "table"
            | "row"
            | "cell"
            | "column"
            | "document"
            | "menu"
            | "menuBar"
            | "toolbar"
            | "tabGroup"
    ) || web_area && role == "webArea"
}

pub(crate) fn ordered_ancestry(
    plan: &GeometricPlan,
    context: &GeometricContext<'_>,
    source: NodeId,
    include_source: bool,
    web_area: bool,
) -> bool {
    let (mut current, known) = if include_source {
        (Some(source), true)
    } else {
        original_parent(plan, source)
    };
    if !known {
        return true;
    }
    let mut seen = HashSet::new();
    while let Some(id) = current {
        if !seen.insert(id) {
            return true;
        }
        let Some(role) = original_role(context, id) else {
            return true;
        };
        if ordered_role(&role, web_area) {
            return true;
        }
        if matches!(role.as_str(), "window" | "sheet") {
            return false;
        }
        let (parent, known) = original_parent(plan, id);
        if !known {
            return true;
        }
        current = parent;
    }
    false
}

pub(crate) fn reading_scope(
    plan: &GeometricPlan,
    context: &GeometricContext<'_>,
    source: NodeId,
) -> Vec<(NodeId, bool)> {
    let mut scopes = Vec::new();
    let mut current = Some(source);
    let mut seen = HashSet::new();
    while let Some(id) = current {
        let Some(role) = original_role(context, id).filter(|_| seen.insert(id)) else {
            scopes.push((id, false));
            break;
        };
        let window = matches!(role.as_str(), "window" | "sheet");
        let isolated = context.projection.node(id).is_some_and(|node| {
            is_true(fields(&node.native_fields, "modal"))
                || matches!(
                    fields(&node.native_fields, "subrole").and_then(Value::as_str),
                    Some("dialog" | "systemDialog" | "popover")
                )
        });
        if window || role == "scrollArea" || isolated {
            scopes.push((id, true));
        }
        if window {
            break;
        }
        let (parent, known) = original_parent(plan, id);
        if !known {
            scopes.push((id, false));
            break;
        }
        current = parent;
    }
    scopes
}

pub(crate) fn source_partition(plan: &GeometricPlan, context: &GeometricContext<'_>, source: NodeId) -> Option<NodeId> {
    let (mut parent, mut known) = original_parent(plan, source);
    let mut seen = HashSet::new();
    while let Some(id) = parent.filter(|_| known) {
        if !seen.insert(id) {
            return None;
        }
        let node = context.projection.node(id)?;
        let field = |name| fields(&node.native_fields, name);
        if truth(field("partialReason"))
            || is_false(field("visible"))
            || is_false(field("showing"))
            || is_true(field("offscreen"))
            || incomplete(field("childrenRead"), field("childCount"), node.native_children.len())
        {
            return None;
        }
        if node.native_children.len() != 1 {
            return Some(id);
        }
        (parent, known) = original_parent(plan, id);
    }
    None
}

include!("rtl.rs");

pub(crate) fn rtl_text(context: &GeometricContext<'_>, id: NodeId) -> bool {
    let Some(node) = context.projection.node(id) else {
        return true;
    };
    ["name", "value", "description", "help", "placeholder"]
        .into_iter()
        .filter_map(|name| fields(&node.native_fields, name).and_then(Value::as_str))
        .any(rtl)
}

pub(super) fn rtl(value: &str) -> bool {
    value.chars().any(|character| {
        let code = character as u32;
        RTL_RANGES
            .binary_search_by(|&(start, end)| {
                if code < start {
                    std::cmp::Ordering::Greater
                } else if code > end {
                    std::cmp::Ordering::Less
                } else {
                    std::cmp::Ordering::Equal
                }
            })
            .is_ok()
    })
}
