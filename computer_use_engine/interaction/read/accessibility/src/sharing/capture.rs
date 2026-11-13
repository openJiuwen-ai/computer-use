// Copyright (c) Huawei Technologies Co., Ltd. 2026. All rights reserved.

//! Capture exact native evidence for field and action declarations.

use super::*;
use crate::facts::FactSnapshot;
use crate::render::atoms::{actions, AtomOptions};

fn items(value: Option<&Value>) -> &[Value] {
    value.and_then(Value::as_seq).unwrap_or_default()
}

fn protected_value(node: &Value) -> bool {
    let states = node.get("states");
    let text = node.get("text");
    let selection = items(text.and_then(|text| text.get("selections")));
    let selection_context = !selection.is_empty() && (
        selection.len() != 1 || selection.iter().any(|span| {
            span.get("start").and_then(Value::as_i64) != Some(0)
                || span.get("end").and_then(Value::as_i64) != Some(0)
                || span.get("collapsed").and_then(Value::as_bool) != Some(true)
        })
    );
    ["focused", "selected", "editable", "protected"].iter().any(|field| {
        states.and_then(|states| states.get(field)).and_then(Value::as_bool) == Some(true)
    }) || selection_context
        || text.and_then(|text| text.get("selected_text")).is_some_and(truthy)
        || text.and_then(|text| text.get("caret_active")).and_then(Value::as_bool) == Some(true)
        || text.and_then(|text| text.get("read_only")).and_then(Value::as_bool) == Some(false)
        || ["control_value", "numeric_value"].iter().any(|field| {
            node.get(field).and_then(|value| value.get("read_only")).and_then(Value::as_bool) == Some(false)
        })
}

fn complete_help(native: &Value, fact: &Value, source: &ProjectionNode) -> bool {
    let Some(help) = native.get("help").and_then(Value::as_str) else { return false };
    field(&source.native_fields, "help").and_then(Value::as_str) == Some(help)
        && fact.get("observed").and_then(Value::as_bool) != Some(false)
        && fact.get("values").and_then(|values| values.get("help")).and_then(Value::as_str) == Some(help)
        && fact.get("fieldPaths").and_then(|fields| fields.get("help")).and_then(Value::as_seq)
            .is_some_and(|path| matches!(path, [name] if name.as_str() == Some("help")))
        && !fact.get("fieldStatus").and_then(Value::as_map).is_some_and(|status| {
            status.iter().any(|(field, _)| field == "help" || field.starts_with("help."))
        })
}

fn none(value: Option<&Value>) -> bool {
    matches!(value, None | Some(Value::Null))
}

fn complete_actions(native: &Value, fact: Option<&Value>, issues: &[&Value]) -> bool {
    let Some(actions) = native.get("actions").and_then(Value::as_seq) else { return false };
    if fact.and_then(|fact| fact.get("actions")).and_then(|actions| actions.get("state"))
        .and_then(Value::as_str) != Some("complete")
    {
        return false;
    }
    issues.iter().all(|issue| {
        let path = items(issue.get("path"));
        let [_, _, _, position, description] = path else { return false };
        let action = position.as_i64().and_then(|index| usize::try_from(index).ok()).and_then(|index| actions.get(index));
        issue.get("state").and_then(Value::as_str) == Some("unsupported")
            && description.as_str() == Some("description")
            && action.is_some_and(|action| none(action.get("description")))
            && none(issue.get("continuation"))
            && !issue.get("alternatives").is_some_and(truthy)
    })
}

fn field_record(raw: &Value, fields: &[&str], arrays: &[&str]) -> Value {
    Value::Map(fields.iter().map(|&name| {
        let value = raw.get(name).cloned().unwrap_or_else(|| {
            if arrays.contains(&name) { Value::Seq(Vec::new()) } else { Value::Null }
        });
        (name.to_owned(), value)
    }).collect())
}

fn action_record(action: &Value) -> Value {
    field_record(action, &["kind", "key", "label", "description", "target", "available", "shortcuts"], &["shortcuts"])
}

fn issue_record(issue: &Value, path: Vec<Value>) -> Value {
    let Value::Map(mut record) = field_record(issue, &["state", "detail", "continuation", "alternatives"], &["alternatives"])
        else { unreachable!("issue record is a map") };
    record.insert(0, ("path".to_owned(), Value::Seq(path)));
    Value::Map(record)
}

fn catalogue_key(
    native: &[Value], actions: &[Action], issues: &[&Value], scope: NodeId, session: &Value, capture: &Value,
) -> Value {
    let names: HashSet<_> = actions.iter().map(|action| action.name.as_str()).collect();
    let mut selected = HashMap::new();
    let mut by_name = HashMap::new();
    for (index, action) in native.iter().enumerate() {
        if let Some(name) = action.get("key").and_then(Value::as_str).filter(|name| names.contains(name)) {
            selected.insert(index, name);
            by_name.insert(name, action);
        }
    }
    let projected = selected.len() == by_name.len() && by_name.len() == names.len() && names.len() == actions.len()
        && issues.iter().all(|issue| items(issue.get("path")).get(3)
            .and_then(Value::as_i64).is_some_and(|index| 0 <= index && index < native.len() as i64));
    let native_actions = if projected {
        actions.iter().map(|action| action_record(by_name[action.name.as_str()])).collect()
    } else {
        native.iter().map(action_record).collect()
    };
    let normalized_issues = issues.iter().filter_map(|issue| {
        let path = items(issue.get("path"));
        if projected {
            let position = path[3].as_i64().unwrap() as usize;
            let name = selected.get(&position)?;
            let path = std::iter::once(Value::Text((*name).to_owned())).chain(path[4..].iter().cloned()).collect();
            Some(issue_record(issue, path))
        } else {
            Some(issue_record(issue, path[2..].to_vec()))
        }
    }).collect();
    Value::Seq(vec![
        Value::Seq(vec![session.clone(), capture.clone(), Value::Int(scope.0 as i64)]),
        Value::Seq(native_actions),
        Value::Seq(actions.iter().map(|action| Value::Text(action.name.clone())).collect()),
        Value::Seq(actions.iter().map(|action| Value::Text(action.label.clone())).collect()),
        Value::Seq(normalized_issues),
    ])
}

pub(crate) fn capture_sharing_evidence(
    projection: &Projection,
    atoms: &AtomPlan,
    snapshot: &FactSnapshot,
    compression: Option<&CompressionEvidence>,
    suppressed_actions: Option<&HashSet<(NodeId, usize)>>,
) -> Result<SharingEvidence> {
    let observation = &snapshot.observation;
    let native = observation.get("nodes").and_then(Value::as_map).ok_or(SharingError("missing native sharing sources"))?;
    let session = observation.get("session").ok_or(SharingError("missing native sharing session"))?;
    let capture = observation.get("id").ok_or(SharingError("missing native sharing capture"))?;
    let facts: HashMap<_, _> = items(snapshot.facts.get("nodes")).iter().filter_map(|fact| {
        Some((fact.get("node")?.as_str()?, fact))
    }).collect();
    let mut issues: HashMap<_, Vec<_>> = HashMap::new();
    for issue in items(observation.get("issues")) {
        if let [Value::Text(root), Value::Text(token), Value::Text(group), ..] = items(issue.get("path")) {
            if root == "nodes" && group == "actions" {
                issues.entry(token.as_str()).or_default().push(issue);
            }
        }
    }
    let empty = Vec::new();
    let mut complete = HashSet::new();
    let mut result = SharingEvidence::default();
    if let Some(compression) = compression {
        result.complete_fields = compression.complete_text.clone();
        result.contested_sources = compression.contested_sources.union(&compression.splice_conflicts).copied().collect();
    }
    for (index, (token, node)) in native.iter().enumerate() {
        let id = NodeId(index);
        let Some(source) = projection.node(id) else { continue };
        let fact = facts.get(token.as_str()).copied();
        if fact.is_some_and(|fact| complete_help(node, fact, source)) {
            result.complete_fields.insert(FactKey { source: id, attribute: "help".to_owned() });
        }
        if protected_value(node) {
            result.protected_values.insert(id);
        }
        if complete_actions(node, fact, issues.get(token.as_str()).unwrap_or(&empty)) {
            complete.insert(id);
        }
    }
    let options = AtomOptions { compact: true, suppressed_actions, ..AtomOptions::default() };
    let mut composed = HashSet::new();
    for &(source, owner) in &projection.folded_into {
        let node = projection.node(source).ok_or(SharingError("unknown folded sharing source"))?;
        let native_actions = native.get(source.0).map(|(_, node)| items(node.get("actions"))).unwrap_or_default();
        if !complete.contains(&source) || field(&node.native_fields, "actionsUnavailable").is_some_and(truthy)
            || !actions(projection, node, &options, &[]).map_err(|_| SharingError("invalid folded sharing actions"))?.is_empty()
            || native_actions.iter().any(|action| action.get("available").and_then(Value::as_bool) != Some(false))
        {
            composed.insert(owner);
        }
    }
    for &id in &atoms.order {
        if !complete.contains(&id) || composed.contains(&id) {
            continue;
        }
        let atom = &atoms.atoms[&id];
        let node = projection.node(id).ok_or(SharingError("missing visible sharing source"))?;
        if atom.actions_unavailable || node.field("actions") != field(&node.native_fields, "actions")
            || atom.actions.iter().any(|action| action.source != id)
        {
            continue;
        }
        let display = actions(projection, node, &options, &[]).map_err(|_| SharingError("invalid native sharing actions"))?;
        if display.is_empty() || display.iter().map(|action| &action.name).ne(atom.actions.iter().map(|action| &action.name))
            || display.iter().map(|action| &action.name).collect::<HashSet<_>>().len() != display.len()
        {
            continue;
        }
        let (token, native) = &native[id.0];
        let key = catalogue_key(items(native.get("actions")), &display, issues.get(token.as_str()).unwrap_or(&empty),
            node.scope, session, capture);
        let names = display.into_iter().map(|action| action.name).collect();
        result.actions.insert(id, ActionCatalogue { names, key });
    }
    Ok(result)
}
