// Copyright (c) Huawei Technologies Co., Ltd. 2026. All rights reserved.

use std::collections::{HashMap, HashSet};

use super::{
    super::{active_text, flag, has_value}, BlockId, CapturedGroupField, Classified, GroupError, GroupRequest, NodeId,
    Result, Value,
};
use crate::render::atoms::Format;

pub(super) fn seq(value: Option<&Value>) -> &[Value] { value.and_then(Value::as_seq).unwrap_or_default() }
pub(super) fn field<'a>(value: &'a Value, field: &str) -> Option<&'a str> { value.get(field).and_then(Value::as_str) }
pub(super) fn present(value: Option<&Value>) -> bool { value.is_some_and(|value| !matches!(value, Value::Null)) }

impl<'r, 'a> Classified<'r, 'a> {
    pub(super) fn native(&self, id: NodeId) -> Option<&'a Value> {
        self.request.snapshot.observation.get("nodes")?.get(self.request.tokens.get(id.0)?)
    }

    pub(super) fn reference(&self, id: NodeId) -> Result<&str> {
        self.request.atoms.source_references.get(&id).map(String::as_str)
            .or_else(|| self.request.projection.node(id).map(|node| node.reference.as_str()))
            .or_else(|| self.request.semantic.get("refs").and_then(Value::as_map).and_then(|aliases|
                aliases.iter().find(|(_, token)| token.as_str().and_then(|token| self.aliases.get(token)) == Some(&id))
                    .map(|(reference, _)| reference.as_str())))
            .ok_or(GroupError::MissingSource(id))
    }

    pub(super) fn refs(&self, children: &[BlockId]) -> Result<Vec<NodeId>> {
        let mut refs = Vec::new();
        for child in children {
            refs.extend(self.members.get(child).ok_or(GroupError::MissingBlock(*child))?);
        }
        Ok(refs)
    }

    pub(super) fn recovery(&self, projected: &[NodeId]) -> Result<Vec<NodeId>> {
        let mut wanted: HashSet<_> = projected.iter().copied().collect();
        for (source, owner) in &self.request.projection.folded_into {
            if wanted.contains(owner) { wanted.insert(*source); }
        }
        for id in projected {
            let atom = self.request.atoms.atoms.get(id).ok_or(GroupError::MissingSource(*id))?;
            wanted.extend(atom.slots.iter().flat_map(|slot| slot.facts.iter().map(|fact| fact.key.source)));
        }
        let mut ordered = Vec::new();
        for id in self.request.projection.source_order.iter().chain(&self.alias_order) {
            if wanted.remove(id) { ordered.push(*id); }
        }
        Ok(ordered)
    }

    pub(super) fn permits_descendants(&self, block: BlockId) -> Result<bool> {
        let block = self.request.interface.block(block).ok_or(GroupError::MissingBlock(block))?;
        for id in &block.source_refs {
            if let Some(node) = self.native(*id) {
                if matches!(field(node, "role"), Some("window" | "web_area" | "scroll_area" | "table" | "list")) {
                    let state = node.get("states");
                    if !flag(state.and_then(|state| state.get("modal"))) && !flag(state.and_then(|state| state.get("editable"))) {
                        continue;
                    }
                }
            }
            if self.protections.get(id).is_some_and(|reasons| reasons.iter().any(|reason|
                !matches!(*reason, "incomplete-relation" | "incomplete-child-coverage")))
            {
                return Ok(false);
            }
        }
        Ok(true)
    }
}

fn reason(reasons: &mut Vec<&'static str>, value: &'static str) {
    if !reasons.contains(&value) { reasons.push(value); }
}

fn protections(
    classified: &Classified<'_, '_>,
    id: NodeId,
    uncertain: &HashSet<&str>,
) -> Vec<&'static str> {
    let request = classified.request;
    let Some(native) = classified.native(id) else { return vec!["missing-native-source"] };
    let node = request.projection.node(id);
    let view = |key: &str| node.and_then(|node| node.native_fields.iter().find(|(name, _)| name == key)).map(|(_, value)| value);
    let role = field(native, "role");
    let mut result = Vec::new();
    if !matches!(role, Some("static_text" | "heading" | "paragraph" | "group" | "section" | "list" | "list_item"
        | "row" | "cell" | "column" | "document"))
    {
        result.push("independent-or-unknown-role");
    }
    if let Some(actions) = native.get("actions").and_then(Value::as_seq) {
        if actions.iter().enumerate().any(|(index, action)| {
            let kind = field(action, "kind");
            action.get("available") != Some(&Value::Bool(false))
                && (present(action.get("key")) || !matches!(kind, Some("focus" | "set_value" | "replace_selection")))
                && !matches!(kind, Some("show_menu" | "scroll_into_view"))
                && !request.deferred_actions.is_some_and(|actions| actions.contains(&(id, index)))
        }) {
            result.push("independent-actions");
        }
    } else {
        result.push("unknown-actions");
    }
    if ["click", "typeText", "replaceText", "focus"].iter().any(|key|
        view("capabilities").and_then(|items| items.get(key)).and_then(Value::as_str) == Some("available"))
    {
        result.push("independent-input");
    }
    let state = |key: &str| native.get("states").and_then(|items| items.get(key));
    if ["focused", "focusable", "selected", "selectable", "editable", "protected", "busy", "modal", "has_popup", "virtualized"]
        .iter().any(|key| flag(state(key)))
        || state("enabled") == Some(&Value::Bool(false)) || present(state("checked"))
        || present(state("expanded")) && matches!(role, Some("row" | "list_item"))
        || ["visible", "showing"].iter().any(|key| state(key) == Some(&Value::Bool(false)))
    {
        result.push("active-or-important-state");
    }
    let read_only = |group: &str| native.get(group).and_then(|group| group.get("read_only"));
    if read_only("text") == Some(&Value::Bool(false)) || active_text(native) {
        result.push("input-or-selection");
    }
    if role == Some("document") && read_only("text") != Some(&Value::Bool(true)) {
        result.push("unconfirmed-readonly-document");
    }
    if ["control_value", "numeric_value"].iter().any(|group| read_only(group) == Some(&Value::Bool(false))) {
        result.push("writable-value");
    }
    if seq(native.get("supplements")).iter().any(|item| {
        let key = field(item, "key").unwrap_or_default();
        !key.starts_with("provider.") && !(key == "disclosing" && item.get("value") == Some(&Value::Bool(false)))
    }) {
        result.push("additional-native-meaning");
    }
    if request.tokens.get(id.0).is_some_and(|token| uncertain.contains(token.as_str())) {
        result.push("native-uncertainty");
    }
    if ["error", "invalid", "blockedBy", "modified", "unsaved"].iter().any(|key| has_value(view(key))) {
        result.push("error-or-blocked-state");
    }
    for (_, relation) in native.get("relations").and_then(Value::as_map).into_iter().flatten() {
        let kind = field(relation, "kind");
        let targets = seq(relation.get("targets"));
        if !matches!(kind, Some("children" | "parent" | "window")) && !targets.is_empty() {
            reason(&mut result, "independent-semantic-relation");
        }
        let total = relation.get("total").filter(|value| !matches!(value, Value::Null));
        if (kind == Some("children") || !targets.is_empty() || total.is_some_and(|total| *total != Value::Int(0)))
            && (field(relation, "evidence") == Some("candidate") || field(relation, "enumeration") != Some("enumerated")
                || relation.get("offset").is_some_and(|offset| !matches!(offset, Value::Null | Value::Int(0)))
                || total.is_some_and(|total| *total != Value::Int(targets.len() as i64)))
        {
            reason(&mut result, "incomplete-relation");
        }
    }
    if let Some(atom) = request.atoms.atoms.get(&id) {
        if !atom.actions.is_empty() || atom.actions_unavailable { result.push("displayed-or-unknown-actions"); }
        if !atom.states.is_empty() { result.push("displayed-state"); }
        if atom.coverage.partial() || atom.coverage.unexpanded_menu { result.push("incomplete-child-coverage"); }
        if atom.slots.iter().any(|slot|
            slot.protection.is_some() || matches!(slot.format, Format::Unavailable | Format::Selection | Format::Span | Format::Preview))
        {
            result.push("protected-display-field");
        }
        if atom.slots.iter().flat_map(|slot| &slot.facts).any(|fact| {
            request.tokens.get(fact.key.source.0).is_none_or(|token| !classified.aliases.contains_key(token.as_str()))
        }) {
            result.push("unresolved-field-source");
        }
    }
    result
}

pub(super) fn uncertain(snapshot: &Value) -> HashSet<&str> {
    seq(snapshot.get("issues")).iter().filter_map(|issue| {
        if matches!(field(issue, "state"), Some("absent" | "unsupported")) { return None; }
        let [Value::Text(scope), Value::Text(token), ..] = seq(issue.get("path")) else { return None };
        (scope == "nodes").then_some(token.as_str())
    }).collect()
}

fn block_members(request: &GroupRequest<'_>) -> Result<HashMap<BlockId, Vec<NodeId>>> {
    let mut members = HashMap::<BlockId, Vec<NodeId>>::new();
    let mut seen = HashSet::new();
    let mut pending: Vec<_> = request.interface.roots.iter().rev().map(|root| (*root, false)).collect();
    while let Some((id, entered)) = pending.pop() {
        let block = request.interface.block(id).ok_or(GroupError::MissingBlock(id))?;
        if entered {
            let mut seen_refs = HashSet::new();
            let mut refs = Vec::new();
            for source in &block.source_refs {
                if seen_refs.insert(*source) { refs.push(*source); }
            }
            for child in &block.children {
                for source in members.get(child).ok_or(GroupError::MissingBlock(*child))? {
                    if seen_refs.insert(*source) { refs.push(*source); }
                }
            }
            members.insert(id, refs);
        } else {
            if !seen.insert(id) { return Err(GroupError::InvalidMembership); }
            pending.push((id, true));
            pending.extend(block.children.iter().rev().map(|child| (*child, false)));
        }
    }
    Ok(members)
}

pub(super) fn classify<'r, 'a>(request: &'r GroupRequest<'a>) -> Result<Classified<'r, 'a>> {
    let capture = ["backend", "session", "id"].map(|field| request.snapshot.observation.get(field).and_then(Value::as_str));
    let [Some(backend), Some(session), Some(id)] = capture else { return Err(GroupError::MissingCapture) };
    let token_index: HashMap<_, _> = request.tokens.iter().enumerate().map(|(i, token)| (token.as_str(), NodeId(i))).collect();
    let mut aliases = HashMap::new();
    let mut alias_order = Vec::new();
    let references = request.semantic.get("refs").and_then(Value::as_map).unwrap_or_default();
    let mut reference_ids = HashMap::with_capacity(references.len());
    for (reference, token) in references {
        let source = token.as_str().and_then(|token| token_index.get(token).map(|&id| (token, id)));
        reference_ids.entry(reference.as_str()).or_insert(source.map(|(_, id)| id));
        if let Some((token, id)) = source {
            aliases.insert(token, id);
            alias_order.push(id);
        }
    }
    let mut classified = Classified {
        request, protections: HashMap::new(), members: block_members(request)?, native_fields: HashMap::new(),
        capture: [backend, session, id].map(str::to_owned), aliases, alias_order,
    };
    let uncertain = uncertain(&request.snapshot.observation);
    for id in &request.projection.source_order {
        let reasons = protections(&classified, *id, &uncertain);
        if !reasons.is_empty() { classified.protections.insert(*id, reasons); }
    }
    for (_, native) in request.snapshot.observation.get("nodes").and_then(Value::as_map).into_iter().flatten() {
        for (_, relation) in native.get("relations").and_then(Value::as_map).into_iter().flatten() {
            if matches!(field(relation, "kind"), Some("children" | "parent" | "window")) { continue; }
            for target in seq(relation.get("targets")).iter().filter_map(Value::as_str) {
                if let Some(id) = classified.aliases.get(target) {
                    classified.protections.entry(*id).or_default().push("semantic-relation-endpoint");
                }
            }
        }
    }
    let records: HashMap<_, _> = seq(request.snapshot.facts.get("nodes")).iter().filter_map(|record|
        field(record, "node").map(|token| (token, record))).collect();
    for row in seq(request.semantic.get("fields")) {
        let (Some(reference), Some(provider), Some(field_value)) = (field(row, "ref"), field(row, "key"), row.get("value"))
            else { continue };
        let Some(source) = reference_ids.get(reference).copied().flatten() else { continue };
        let Some(record) = request.tokens.get(source.0).and_then(|token| records.get(token.as_str()).copied()) else { continue };
        if record.get("observed") == Some(&Value::Bool(false)) || field(field_value, "representation") != Some("field")
            || !seq(record.get("requestedFields")).iter().any(|key| key.as_str() == Some(provider))
            || record.get("fieldStatus").and_then(|items| items.get(provider))
                .and_then(|status| field(status, "state")).is_some_and(|state| state != "complete")
        {
            continue;
        }
        let Some(value) = record.get("values").and_then(|items| items.get(provider)) else { continue };
        let Some(attribute) = field(field_value, "field") else { continue };
        classified.native_fields.entry((source, attribute.to_owned())).or_default().push(CapturedGroupField {
            source, native_field: provider.to_owned(), value: value.clone(),
            normalized: field_value.get("normalized").cloned().unwrap_or(Value::Null), attribute: attribute.to_owned(),
        });
    }
    Ok(classified)
}
