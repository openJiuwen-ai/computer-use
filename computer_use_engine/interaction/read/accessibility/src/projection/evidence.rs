// Copyright (c) Huawei Technologies Co., Ltd. 2026. All rights reserved.

//! Evidence is derived only from the original normalized capture.

use std::collections::{HashMap, HashSet};

use crate::facts::FactSnapshot;

use super::{truthy, CompressionEvidence, FactKey, NodeId, ProjectionError, Result, Value};

mod native_captions;

type Aliases<'a> = HashMap<&'a str, NodeId>;
type Issues<'a> = HashMap<&'a str, Vec<Issue<'a>>>;

fn seq(value: Option<&Value>) -> &[Value] { value.and_then(Value::as_seq).unwrap_or(&[]) }
fn rows(value: Option<&Value>) -> &[(String, Value)] { value.and_then(Value::as_map).unwrap_or(&[]) }
fn text<'a>(value: &'a Value, key: &str) -> Option<&'a str> { value.get(key).and_then(Value::as_str) }
fn present(value: Option<&Value>) -> bool { value.is_some_and(|value| !matches!(value, Value::Null)) }
fn empty(value: Option<&Value>) -> bool { !present(value) || value.and_then(Value::as_str) == Some("") }
fn truth(value: &Value, key: &str) -> bool { value.get(key).is_some_and(truthy) }
fn eq_path(actual: &[Value], expected: &[&str]) -> bool { actual.len() == expected.len() && starts(actual, expected) }
fn starts(actual: &[Value], expected: &[&str]) -> bool {
    actual.len() >= expected.len() && actual.iter().zip(expected).all(|(part, name)| part.as_str() == Some(name))
}
fn overlaps(actual: &[Value], expected: &[&str]) -> bool {
    if actual.len() >= expected.len() { starts(actual, expected) } else { starts(actual, &expected[..actual.len()]) }
}
fn targets(relation: &Value) -> &[Value] { seq(relation.get("targets")) }

fn relations<'a, 'b>(node: &'a Value, kind: &'b str) -> impl Iterator<Item = (&'a str, &'a Value)> + 'b where 'a: 'b {
    rows(node.get("relations")).iter().filter(move |(_, relation)| text(relation, "kind") == Some(kind))
        .map(|(key, relation)| (key.as_str(), relation))
}

fn relation<'a>(node: &'a Value, kind: &str) -> Option<(&'a str, &'a Value)> {
    let mut relations = relations(node, kind);
    let first = relations.next()?;
    relations.next().is_none().then_some(first)
}

fn range(relation: &Value, count: usize, total_optional: bool) -> bool {
    let total = relation.get("total");
    (total.and_then(Value::as_i64) == Some(count as i64) || (total_optional && !present(total)))
        && (!present(relation.get("offset")) || relation.get("offset").and_then(Value::as_i64) == Some(0))
        && (!present(relation.get("positions")) || relation.get("positions").and_then(Value::as_seq).is_some_and(|positions| {
            positions.len() == count && positions.iter().enumerate().all(|(index, value)| value.as_i64() == Some(index as i64))
        }))
}

fn enumerated(relation: &Value) -> bool {
    text(relation, "enumeration") == Some("enumerated") && text(relation, "evidence") != Some("candidate")
}

#[derive(Clone, Copy)]
struct Issue<'a> {
    local: &'a [Value],
    record: &'a Value,
}

impl Issue<'_> {
    fn state(&self) -> Option<&str> { text(self.record, "state") }
    fn pending(&self) -> bool { present(self.record.get("continuation")) || truth(self.record, "alternatives") }
}

fn issues(observation: &Value) -> (Issues<'_>, bool) {
    let mut result: Issues<'_> = HashMap::new();
    let mut global_gap = false;
    for record in seq(observation.get("issues")) {
        let path = seq(record.get("path"));
        if path.is_empty() || eq_path(path, &["nodes"]) && text(record, "state") != Some("partial") {
            global_gap = true;
        }
        if let [first, token, local @ ..] = path {
            if first.as_str() == Some("nodes") {
                if let Some(token) = token.as_str() {
                    result.entry(token).or_default().push(Issue { local, record });
                }
            }
        }
    }
    (result, global_gap)
}

fn complete_relation(key: &str, issues: &[Issue<'_>], empty: bool) -> bool {
    issues.iter().filter(|issue| overlaps(issue.local, &["relations", key])).all(|issue| {
        empty && eq_path(issue.local, &["relations", key]) && issue.state() == Some("absent") && !issue.pending()
    })
}

fn full_children<'a>(node: &'a Value, issues: &[Issue<'_>]) -> Option<&'a [Value]> {
    let (key, relation) = relation(node, "children")?;
    let targets = targets(relation);
    let count = targets.len();
    (range(relation, count, false) && enumerated(relation) && complete_relation(key, issues, count == 0)
        && targets.iter().filter_map(Value::as_str).collect::<HashSet<_>>().len() == count).then_some(targets)
}

fn unlabelled_group(node: &Value, issues: &[Issue<'_>]) -> bool {
    text(node, "role") == Some("group") && ["labelled_by", "labels"].into_iter().all(|kind| {
        relation(node, kind).is_some_and(|(key, relation)| {
            targets(relation).is_empty() && range(relation, 0, false)
                && enumerated(relation) && complete_relation(key, issues, true)
        })
    })
}

fn reported_owner<'a>(node: &'a Value, issues: &[Issue<'_>], kind: &str) -> Option<&'a str> {
    let (key, relation) = relation(node, kind)?;
    let [target] = targets(relation) else { return None };
    (range(relation, 1, true) && enumerated(relation) && complete_relation(key, issues, false))
        .then(|| target.as_str()).flatten()
}

fn provider_wrapper(node: &Value, issues: &[Issue<'_>], diagnostics: &[Value]) -> bool {
    if text(node, "role") != Some("group") || present(node.get("role_detail"))
        || ["name", "description", "help", "identifier", "placeholder", "url", "control_value", "numeric_value",
            "text", "structure", "shortcuts"].iter().any(|key| present(node.get(key)))
    {
        return false;
    }
    let Some(actions) = node.get("actions").and_then(Value::as_seq) else { return false };
    for action in actions {
        let target = action.get("target");
        let matching = match text(action, "kind") {
            Some("focus") => !present(target),
            Some("set_value") => eq_path(seq(target), &["control_value", "value"]),
            Some("replace_selection") => eq_path(seq(target), &["text", "selections"]),
            _ => false,
        };
        if present(action.get("key")) || action.get("available").and_then(Value::as_bool) != Some(false) || !matching
            || ["label", "description", "shortcuts"].iter().any(|name| truth(action, name))
        {
            return false;
        }
    }
    if issues.iter().any(|issue| !matches!(issue.state(), Some("unsupported" | "absent")) || issue.pending())
        || rows(node.get("geometry")).iter().any(|(_, bounds)| bounds.get("valid").and_then(Value::as_bool) == Some(false))
        || rows(node.get("states")).iter().any(|(key, value)| {
            !matches!(value, Value::Null) && !(key == "focused" && value.as_bool() == Some(false)
                || key == "enabled" && value.as_bool() == Some(true))
        })
    {
        return false;
    }
    let mut supplements = seq(node.get("supplements")).iter().enumerate().filter(|(index, _)| {
        !diagnostics.iter().any(|path| matches!(
            path.as_seq(), Some([group, position]) if group.as_str() == Some("supplements") && position.as_i64() == Some(*index as i64)
        ))
    });
    let Some((_, item)) = supplements.next() else { return false };
    if supplements.next().is_some() || text(item, "key") != Some("provider.role_detail") || text(item, "value").is_none_or(str::is_empty) {
        return false;
    }
    let Some((key, child)) = relation(node, "children") else { return false };
    let restricted = |key| issues.iter().any(|issue| starts(issue.local, &["relations", key]));
    if targets(child).len() != 1 || !range(child, 1, true) || !enumerated(child) || restricted(key) {
        return false;
    }
    rows(node.get("relations")).iter().all(|(key, relation)| {
        let kind = text(relation, "kind");
        let targets = targets(relation);
        if kind == Some("children") {
            true
        } else if matches!(kind, Some("parent" | "window")) && !targets.is_empty() {
            targets.len() == 1 && range(relation, 1, true) && enumerated(relation) && !restricted(key)
        } else {
            targets.is_empty() && issues.iter().any(|issue| {
                eq_path(issue.local, &["relations", key]) && matches!(issue.state(), Some("unsupported" | "absent"))
            })
        }
    })
}

fn concrete_input(node: &Value) -> bool {
    matches!(text(node, "role"), Some("text_field" | "text_area" | "combo_box" | "document"))
        || node.get("states").and_then(|states| states.get("editable")).and_then(Value::as_bool) == Some(true)
        || ["control_value", "numeric_value", "text"].iter().any(|name| {
            node.get(name).and_then(|group| group.get("read_only")).and_then(Value::as_bool) == Some(false)
        })
}

fn nonzero(value: Option<&Value>) -> bool {
    value.is_some_and(|value| match value {
        Value::Null | Value::Int(0) => false,
        Value::Float(value) => *value != 0.0,
        _ => true,
    })
}

fn semantic_region(node: &Value) -> bool {
    if let Some(structure) = node.get("structure") {
        if ["heading_level", "landmark", "landmark_description", "index", "set_size", "selected_count", "row_count",
            "column_count", "row_index", "column_index", "row_span", "column_span", "collection_mode"]
            .iter().any(|name| present(structure.get(name)))
            || nonzero(structure.get("level"))
            || ["can_select_multiple", "selection_required"].iter()
                .any(|name| structure.get(name).and_then(Value::as_bool) == Some(true))
            || ["scroll_x", "scroll_y"].iter().any(|name| structure.get(name).is_some_and(|axis| {
                axis.get("scrollable").and_then(Value::as_bool) == Some(true) || nonzero(axis.get("position"))
            }))
        {
            return true;
        }
    }
    let active_text = node.get("text").is_some_and(|text| {
        let spans = seq(text.get("selections"));
        text.get("caret_active").and_then(Value::as_bool) == Some(true) || spans.len() > 1
            || spans.iter().any(|span| span.get("collapsed").and_then(Value::as_bool) == Some(false)
                || nonzero(span.get("start")) || nonzero(span.get("end")))
    });
    active_text || node.get("states").is_some_and(|states| {
        ["protected", "offscreen", "active", "has_popup", "virtualized"].iter()
            .any(|name| states.get(name).and_then(Value::as_bool) == Some(true))
            || text(states, "expanded") == Some("partial")
    }) || seq(node.get("supplements")).iter().any(|item| {
        matches!(text(item, "key"), Some("item_status" | "item_type")) && !empty(item.get("value"))
    })
}

const CONTENT: &[&[&str]] = &[&["role"], &["name"], &["description"], &["control_value", "value"],
    &["numeric_value", "value"], &["text", "content"]];
const VALUE_LINE_PATHS: &[&[&str]] = &[&["role"], &["control_value", "value"], &["text", "source"],
    &["text", "unit"], &["text", "content_field"]];

fn value_line(node: &Value, fact: &Value, source: &Value, issues: &[Issue<'_>]) -> bool {
    let Some(value) = node.get("control_value").and_then(|control| text(control, "value")).filter(|text| !text.is_empty()) else {
        return false;
    };
    let Some(info) = node.get("text") else { return false };
    text(info, "source").is_some_and(|source| !source.is_empty())
        && eq_path(seq(info.get("content_field")), &["control_value", "value"])
        && matches!(text(info, "unit"), Some("utf16_code_unit" | "unicode_codepoint"))
        && !present(info.get("content"))
        && text(source, "field") == Some("value")
        && eq_path(seq(source.get("path")), &["control_value", "value"])
        && text(source, "normalized") == Some(value)
        && fact.get("values").and_then(|values| text(values, "control_value.value")) == Some(value)
        && !issues.iter().any(|issue| VALUE_LINE_PATHS.iter().any(|path| overlaps(issue.local, path))
            || starts(issue.local, &["text", "content"]) && (!matches!(issue.state(), Some("absent" | "unsupported")) || issue.pending()))
}

fn source_conflicts(
    evidence: &mut CompressionEvidence, native: &Value, semantic: &Value, aliases: &Aliases<'_>,
    ids: &HashMap<String, NodeId>, issue_rows: &Issues<'_>,
) -> Result<()> {
    let mut scopes = HashMap::new();
    let mut pending: Vec<_> = seq(semantic.get("roots")).iter().map(|root| (root, None)).collect();
    while let Some((node, mut scope)) = pending.pop() {
        let reference = text(node, "ref").ok_or(ProjectionError::Invalid("semantic evidence has no reference"))?;
        if matches!(text(node, "role"), Some("window" | "sheet")) {
            scope = semantic.get("refs").and_then(|refs| text(refs, reference));
        }
        if let (Some(scope), Some(id)) = (scope, ids.get(reference)) {
            scopes.insert(*id, scope);
        }
        pending.extend(seq(node.get("children")).iter().map(|child| (child, scope)));
    }
    let mut incoming: HashMap<NodeId, HashSet<NodeId>> = HashMap::new();
    let mut declared_parent = HashMap::new();
    let mut declared_window = HashMap::new();
    let mut tokens = HashMap::new();
    for (token, node) in rows(native.get("nodes")) {
        let Some(&id) = aliases.get(token.as_str()) else { continue };
        tokens.insert(id, token.as_str());
        let issues = issue_rows.get(token.as_str()).map(Vec::as_slice).unwrap_or(&[]);
        if let Some(parent) = reported_owner(node, issues, "parent") {
            declared_parent.insert(id, parent);
        }
        if let Some(window) = reported_owner(node, issues, "window") {
            declared_window.insert(id, window);
        }
        for (_, relation) in relations(node, "children").filter(|(_, relation)| text(relation, "evidence") != Some("candidate")) {
            for child in targets(relation).iter().filter_map(Value::as_str).filter_map(|token| aliases.get(token)) {
                incoming.entry(*child).or_default().insert(id);
            }
        }
    }
    for (&child, parents) in &incoming {
        if parents.len() > 1 {
            evidence.contested_sources.insert(child);
            evidence.contested_sources.extend(parents);
            evidence.splice_conflicts.insert(child);
            evidence.splice_conflicts.extend(parents);
        }
    }
    let window_conflict = |id| declared_window.get(&id).zip(scopes.get(&id)).is_some_and(|(declared, expected)| declared != expected);
    for (&parent, children) in &evidence.complete_children {
        for &child in children {
            let window = window_conflict(child);
            let ownership = incoming.get(&child).is_some_and(|parents| parents.len() > 1)
                || declared_parent.get(&child).is_some_and(|declared| Some(declared) != tokens.get(&parent));
            if ownership || window {
                evidence.contested_sources.extend([parent, child]);
            }
            if window {
                evidence.splice_conflicts.extend([parent, child]);
            }
        }
    }
    for id in declared_window.keys().copied().filter(|&id| window_conflict(id)) {
        evidence.contested_sources.insert(id);
        evidence.splice_conflicts.insert(id);
        if let Some(parents) = incoming.get(&id) {
            evidence.splice_conflicts.extend(parents);
        }
    }
    Ok(())
}

impl CompressionEvidence {
    pub(crate) fn capture(snapshot: &FactSnapshot, semantic: &Value, ids: &HashMap<String, NodeId>) -> Result<Self> {
        let mut evidence = Self::default();
        let native = &snapshot.observation;
        let (issue_rows, global_gap) = issues(native);
        let by_token: HashMap<_, _> = seq(snapshot.facts.get("nodes")).iter().filter_map(|row| Some((text(row, "node")?, row))).collect();
        let refs = semantic.get("refs").ok_or(ProjectionError::Invalid("semantic evidence has no aliases"))?;
        let mut counts = HashMap::new();
        for (_, token) in rows(Some(refs)) {
            let token = token.as_str().ok_or(ProjectionError::Invalid("native token must be text"))?;
            *counts.entry(token).or_insert(0) += 1;
        }
        let mut aliases = HashMap::new();
        let mut related = HashSet::new();
        let mut label_targets = HashSet::new();
        let mut parent_count = HashMap::new();
        for (_, node) in rows(native.get("nodes")) {
            for (_, relation) in rows(node.get("relations")) {
                let kind = text(relation, "kind");
                for token in targets(relation).iter().filter_map(Value::as_str) {
                    match kind {
                        Some("children") => { *parent_count.entry(token).or_insert(0) += 1; }
                        Some("parent" | "window") => {}
                        _ => { related.insert(token); }
                    }
                    if matches!(kind, Some("labelled_by" | "labels")) {
                        label_targets.insert(token);
                    }
                }
            }
        }
        for (reference, token) in rows(Some(refs)) {
            let token = token.as_str().expect("validated source token");
            let Some(&id) = ids.get(reference) else { return Err(ProjectionError::UnknownReference(reference.clone())) };
            if counts.get(token) == Some(&1) {
                aliases.insert(token, id);
            }
            let Some(node) = native.get("nodes").and_then(|nodes| nodes.get(token)) else { continue };
            let issues = issue_rows.get(token).map(Vec::as_slice).unwrap_or(&[]);
            let diagnostics = seq(snapshot.metadata.get("diagnostic_fields").and_then(|items| items.get(token)));
            if !related.contains(token) && parent_count.get(token).copied().unwrap_or(0) <= 1 && provider_wrapper(node, issues, diagnostics) {
                evidence.provider_wrappers.insert(id);
            }
            if !label_targets.contains(token) && unlabelled_group(node, issues) {
                evidence.unlabelled_groups.insert(id);
            }
        }
        for (&token, &id) in &aliases {
            let Some(node) = native.get("nodes").and_then(|nodes| nodes.get(token)) else { continue };
            let issues = issue_rows.get(token).map(Vec::as_slice).unwrap_or(&[]);
            let children = full_children(node, issues).and_then(|children| children.iter().map(|child| {
                let token = child.as_str()?;
                native.get("nodes")?.get(token)?;
                aliases.get(token).copied()
            }).collect::<Option<Vec<_>>>());
            if let Some(children) = children {
                evidence.complete_children.insert(id, children);
            } else if relations(node, "children").next().is_some()
                || issues.iter().any(|issue| overlaps(issue.local, &["relations", "children"]))
            {
                evidence.incomplete_children.insert(id);
            }
            if concrete_input(node) { evidence.concrete_inputs.insert(id); }
            if semantic_region(node) { evidence.semantic_regions.insert(id); }
            if issues.iter().any(|issue| (!matches!(issue.state(), Some("absent" | "unsupported")) || issue.pending())
                && CONTENT.iter().any(|path| overlaps(issue.local, path)))
            {
                evidence.incomplete_content.insert(id);
            }
        }
        for row in seq(semantic.get("fields")) {
            let (Some(reference), Some(key), Some(source)) = (text(row, "ref"), text(row, "key"), row.get("value")) else {
                return Err(ProjectionError::Invalid("invalid semantic field ledger"));
            };
            let Some(field) = text(source, "field") else { continue };
            let (Some(&id), Some(token)) = (ids.get(reference), text(refs, reference)) else { continue };
            let Some(native_fact) = by_token.get(token) else { continue };
            let value = native_fact.get("values").and_then(|values| text(values, key));
            let statuses = native_fact.get("fieldStatus");
            if matches!(field, "name" | "description" | "value") && value.is_some()
                && statuses.and_then(|statuses| statuses.get(key)).is_none()
            {
                evidence.complete_text.insert(FactKey { source: id, attribute: field.to_owned() });
            }
            let issues = issue_rows.get(token).map(Vec::as_slice).unwrap_or(&[]);
            let Some(node) = native.get("nodes").and_then(|nodes| nodes.get(token)) else { continue };
            if !global_gap && key == "control_value.value"
                && semantic.get("value_fields").and_then(|fields| text(fields, reference)) == Some(key)
                && value_line(node, native_fact, source, issues)
            {
                evidence.control_value_lines.insert(id);
            }
            let group = match field {
                "valueDescription" => "control_value",
                "rangeDescription" => "numeric_value",
                _ => continue,
            };
            let path = [group, "description"];
            if node.get(group).and_then(|group| text(group, "description")) == Some("")
                && eq_path(seq(source.get("path")), &path) && value == Some("")
                && !rows(statuses).iter().any(|(status, _)| status == key
                    || status.strip_prefix(key).is_some_and(|suffix| suffix.starts_with('.'))
                    || key.strip_prefix(status).is_some_and(|suffix| suffix.starts_with('.')))
                && !issues.iter().any(|issue| overlaps(issue.local, &path))
            {
                evidence.complete_descriptions.insert(FactKey { source: id, attribute: field.to_owned() });
            }
        }
        source_conflicts(&mut evidence, native, semantic, &aliases, ids, &issue_rows)?;
        if !global_gap {
            evidence.caption_sources = native_captions::capture(native, semantic, &aliases, &issue_rows);
        }
        Ok(evidence)
    }
}
