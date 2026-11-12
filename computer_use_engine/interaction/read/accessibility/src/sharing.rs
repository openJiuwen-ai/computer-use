// Copyright (c) Huawei Technologies Co., Ltd. 2026. All rights reserved.

//! Source-conserving local declarations for a finalized interface.

use std::collections::{HashMap, HashSet};
use std::hash::Hash;

use super::{
    disclosure::DisclosurePlan,
    geometric::{BlockId, GeometricPlan},
    numbering::NodeId,
    projection::{CompressionEvidence, FactKey, Projection, ProjectionNode},
    render::{atoms::{Action, Atom, AtomPlan, Format, Placement, Slot}, lines::reference_set},
    value::Value,
};

mod capture;
mod slices;
#[cfg(test)]
mod tests;
pub(crate) use capture::capture_sharing_evidence;
pub(crate) use slices::{
    plan_text_slices, TextSliceDocument, TextSliceField, TextSliceMode, TextSlicePlan, TextSliceRequest,
};

#[derive(Debug, Eq, PartialEq)]
pub(crate) struct SharingError(pub &'static str);
type Result<T> = std::result::Result<T, SharingError>;

#[derive(Clone, Debug)]
pub(crate) struct SharedActionGroup {
    pub refs: Vec<NodeId>,
    pub actions: Vec<Action>,
    pub estimated_saved_characters: usize,
}

#[derive(Clone, Debug)]
pub(crate) struct SharedFieldGroup {
    pub refs: Vec<NodeId>,
    pub slot_key: String,
    pub slot: Slot,
    pub source_slots: Vec<(NodeId, Slot)>,
    pub estimated_saved_characters: usize,
}

#[derive(Default)]
pub(crate) struct BlockSharing {
    pub actions: Vec<SharedActionGroup>,
    pub fields: Vec<SharedFieldGroup>,
}

#[derive(Default)]
pub(crate) struct SharingPlan {
    pub by_block: HashMap<BlockId, BlockSharing>,
    pub omitted_slots: HashMap<NodeId, HashSet<String>>,
    pub omitted_actions: HashMap<NodeId, HashSet<usize>>,
}

#[derive(Clone, Debug)]
pub(crate) struct ActionCatalogue {
    pub names: Vec<String>,
    pub key: Value,
}

#[derive(Default)]
pub(crate) struct SharingEvidence {
    pub actions: HashMap<NodeId, ActionCatalogue>,
    pub complete_fields: HashSet<FactKey>,
    pub contested_sources: HashSet<NodeId>,
    pub protected_values: HashSet<NodeId>,
}

pub(crate) struct SharingRequest<'a> {
    pub projection: &'a Projection,
    pub interface: &'a GeometricPlan,
    pub atoms: &'a AtomPlan,
    pub evidence: &'a SharingEvidence,
    pub compression: Option<&'a CompressionEvidence>,
    pub disclosure: Option<&'a DisclosurePlan>,
    pub excluded_sources: &'a HashSet<NodeId>,
}

#[derive(Eq, PartialEq, Hash)]
enum Canonical {
    Null,
    Bool(bool),
    Int(i64),
    BigInt(String),
    Float(u64),
    Text(String),
    Bytes(Vec<u8>),
    Seq(Vec<Canonical>),
    Map(Vec<(String, Canonical)>),
}

fn canonical(value: &Value) -> Option<Canonical> {
    Some(match value {
        Value::Null => Canonical::Null,
        Value::Bool(value) => Canonical::Bool(*value),
        Value::Int(value) => Canonical::Int(*value),
        Value::BigInt(value) => Canonical::BigInt(value.clone()),
        Value::Float(value) if !value.is_nan() => Canonical::Float(if *value == 0.0 { 0 } else { value.to_bits() }),
        Value::Float(_) => return None,
        Value::Text(value) => Canonical::Text(value.clone()),
        Value::Bytes(value) => Canonical::Bytes(value.clone()),
        Value::Seq(values) => Canonical::Seq(values.iter().map(canonical).collect::<Option<_>>()?),
        Value::Map(values) => {
            let mut values = values.iter().map(|(key, value)| Some((key.clone(), canonical(value)?)))
                .collect::<Option<Vec<_>>>()?;
            values.sort_by(|left, right| left.0.cmp(&right.0));
            Canonical::Map(values)
        }
    })
}

fn field<'a>(fields: &'a [(String, Value)], name: &str) -> Option<&'a Value> {
    fields.iter().find(|(key, _)| key == name).map(|(_, value)| value)
}

fn truthy(value: &Value) -> bool {
    match value {
        Value::Null => false,
        Value::Bool(value) => *value,
        Value::Int(value) => *value != 0,
        Value::BigInt(value) => value != "0",
        Value::Float(value) => *value != 0.0,
        Value::Text(value) => !value.is_empty(),
        Value::Bytes(value) => !value.is_empty(),
        Value::Seq(value) => !value.is_empty(),
        Value::Map(value) => !value.is_empty(),
    }
}

fn python_space(value: char) -> bool {
    value.is_whitespace() || matches!(value, '\u{1c}'..='\u{1f}')
}

#[derive(Eq, PartialEq, Hash)]
struct MemberScope {
    native_scope: NodeId,
    boundary: NodeId,
    blocking: Canonical,
    // Empty values never unify operation capabilities or current state.
    state: Option<(Vec<Canonical>, bool)>,
}

fn member_scope(request: &SharingRequest<'_>, atom: &Atom, value_only: bool) -> Result<Option<MemberScope>> {
    let node = request.projection.node(atom.id).ok_or(SharingError("missing sharing source"))?;
    let mut boundary = atom.id;
    let mut pending = atom.id;
    let mut seen = HashSet::new();
    while seen.insert(pending) {
        let source = request.projection.node(pending).ok_or(SharingError("missing sharing ancestor"))?;
        if matches!(field(&source.native_fields, "role").and_then(Value::as_str), Some("window" | "sheet" | "menu" | "menuBar"))
            || source.parent.is_none()
        {
            boundary = pending;
            break;
        }
        pending = source.parent.expect("sharing ancestor has a parent");
    }
    let Some(blocking) = node.field("blockedBy").map_or_else(|| Some(Canonical::Seq(Vec::new())), canonical) else {
        return Ok(None);
    };
    let state = if value_only {
        None
    } else {
        let state = ["capabilities", "enabled", "busy", "invalid"].into_iter().map(|key| {
            canonical(node.field(key).unwrap_or(&Value::Null))
        }).collect::<Option<Vec<_>>>();
        let Some(state) = state else { return Ok(None) };
        Some((state, atom.actions_unavailable))
    };
    Ok(Some(MemberScope { native_scope: node.scope, boundary, blocking, state }))
}

struct Planner<'a> {
    request: SharingRequest<'a>,
    display_parents: HashMap<BlockId, BlockId>,
}

impl Planner<'_> {
    fn declaration_parent(&self, refs: &[NodeId]) -> Result<Option<BlockId>> {
        if refs.len() < 2 {
            return Ok(None);
        }
        let interface = self.request.interface;
        let mut paths = Vec::with_capacity(refs.len());
        for &id in refs {
            let mut path = Vec::new();
            let mut seen = HashSet::from([id]);
            let mut parent = self.request.projection.node(id).ok_or(SharingError("unknown sharing source"))?.parent;
            while let Some(id) = parent.filter(|id| seen.insert(*id)) {
                path.push(id);
                parent = self.request.projection.node(id).ok_or(SharingError("unknown sharing ancestor"))?.parent;
            }
            paths.push(path);
        }
        for &id in &paths[0] {
            let Some(&block_id) = interface.ref_locations.get(&id) else { continue };
            if paths.iter().any(|path| {
                path.iter().position(|item| *item == id).is_none_or(|position| {
                    path[..=position].iter().any(|item| self.request.evidence.contested_sources.contains(item))
                })
            }) {
                continue;
            }
            let block = interface.block(block_id).ok_or(SharingError("unknown sharing block"))?;
            if block.primary_ref != Some(id) || block.origin != "native" {
                continue;
            }
            let mut found = HashSet::new();
            let mut pending = block.children.clone();
            let mut visited = HashSet::new();
            while let Some(id) = pending.pop() {
                if !visited.insert(id) {
                    return Err(SharingError("repeated sharing block"));
                }
                let block = interface.block(id).ok_or(SharingError("unknown sharing child block"))?;
                found.extend(block.source_refs.iter().copied());
                pending.extend(block.children.iter().copied());
            }
            if refs.iter().all(|reference| found.contains(reference)) {
                return self.display_parent(refs, block_id).map(Some);
            }
        }
        Ok(None)
    }

    fn display_parent(&self, refs: &[NodeId], native_owner: BlockId) -> Result<BlockId> {
        let interface = self.request.interface;
        let mut paths = Vec::with_capacity(refs.len());
        for id in refs {
            let mut path = Vec::new();
            let mut current = *interface.ref_locations.get(id).ok_or(SharingError("missing sharing block location"))?;
            let mut seen = HashSet::new();
            while current != native_owner {
                if !seen.insert(current) {
                    return Err(SharingError("cyclic sharing block"));
                }
                path.push(current);
                let Some(parent) = self.display_parents.get(&current) else { return Ok(native_owner) };
                current = *parent;
            }
            paths.push(path);
        }
        for &id in &paths[0] {
            if paths.iter().all(|path| path.contains(&id)) {
                let block = interface.block(id).ok_or(SharingError("unknown shared display block"))?;
                if matches!(block.kind, "field" | "region") {
                    return Ok(id);
                }
            }
        }
        Ok(native_owner)
    }

    fn rendered_refs(&self, refs: &[NodeId]) -> Result<String> {
        let refs = refs.iter().map(|reference| self.request.atoms.atoms.get(reference)
            .map(|atom| atom.reference.as_str()).ok_or(SharingError("missing sharing atom"))).collect::<Result<Vec<_>>>()?;
        reference_set(refs.into_iter()).map_err(|_| SharingError("invalid sharing reference"))
    }
}

fn string_cost(value: &str) -> usize {
    2 + value.chars().map(|character| match character {
        '"' | '\\' | '\n' | '\r' | '\t' | '\u{8}' | '\u{c}' => 2,
        '\0'..='\u{1f}' => 6,
        _ => 1,
    }).sum::<usize>()
}

fn action_saving(refs: &[NodeId], spelling: &str, actions: &[Action]) -> usize {
    let content = actions.iter().map(|action| {
        string_cost(&action.label) + if action.ambiguous { string_cost(&action.name) + 9 } else { 0 }
    }).sum::<usize>() + actions.len().saturating_sub(1) * 2;
    let individual = refs.len() * ("Secondary Actions: ".len() + content + 2);
    let declaration = "Secondary Actions [elements ]: ".len() + spelling.chars().count() + content + 1;
    individual.saturating_sub(declaration)
}

fn field_saving(refs: &[NodeId], spelling: &str, slot: &Slot) -> usize {
    let content = string_cost(slot.value.as_str().expect("sharing selects strings"));
    let label = slot.label.map_or(slot.key.as_str(), |label| label);
    let local_label_cost = slot.label.map_or(0, |label| label.chars().count() + 2);
    let individual = refs.len() * (local_label_cost + content + 2);
    let declaration = label.chars().count() + " [elements ]: ".len() + spelling.chars().count() + content + 1;
    individual.saturating_sub(declaration)
}

fn empty_value_profitable(refs: &[NodeId], spelling: &str, slot: &Slot) -> bool {
    let mut cost = 0;
    let mut digits = 0_usize;
    for character in spelling.chars() {
        if character.is_ascii_digit() {
            digits += 1;
        } else {
            cost += digits.div_ceil(3) + 1;
            digits = 0;
        }
    }
    cost += digits.div_ceil(3);
    refs.len() * if slot.label.is_some() { 3 } else { 1 } > cost + 10
}

fn grouped<K: Eq + Hash, V>(
    indexes: &mut HashMap<K, usize>, groups: &mut Vec<Vec<V>>, key: K, value: V,
) {
    let index = *indexes.entry(key).or_insert_with(|| {
        groups.push(Vec::new());
        groups.len() - 1
    });
    groups[index].push(value);
}

fn action_signature(actions: &[Action]) -> Option<Vec<(&str, &str, bool, Canonical)>> {
    actions.iter().map(|action| {
        Some((action.name.as_str(), action.label.as_str(), action.ambiguous, canonical(&action.record)?))
    }).collect()
}

fn share_actions(planner: &Planner<'_>, plan: &mut SharingPlan) -> Result<()> {
    let request = &planner.request;
    let mut indexes = HashMap::new();
    let mut groups = Vec::new();
    for &id in &request.atoms.order {
        if request.excluded_sources.contains(&id) || request.evidence.contested_sources.contains(&id) {
            continue;
        }
        let Some(source) = request.evidence.actions.get(&id) else { continue };
        let atom = &request.atoms.atoms[&id];
        if atom.actions.is_empty() || atom.actions_unavailable || atom.actions.iter().any(|action| action.source != id)
            || atom.actions.iter().map(|action| &action.name).ne(source.names.iter())
        {
            continue;
        }
        let (Some(scope), Some(key), Some(signature)) = (
            member_scope(request, atom, false)?, canonical(&source.key), action_signature(&atom.actions),
        ) else { continue };
        grouped(&mut indexes, &mut groups, (scope, key, signature), id);
    }
    for refs in groups {
        let Some(block) = planner.declaration_parent(&refs)? else { continue };
        let actions = &request.atoms.atoms[&refs[0]].actions;
        let saved = action_saving(&refs, &planner.rendered_refs(&refs)?, actions);
        if saved == 0 {
            continue;
        }
        for &id in &refs {
            let member = &request.atoms.atoms[&id];
            let indices = member.actions.iter().map(|action| action.catalogue_index).collect();
            if plan.omitted_actions.insert(id, indices).is_some() {
                return Err(SharingError("shared action occurrence has multiple declarations"));
            }
        }
        plan.by_block.entry(block).or_default().actions.push(SharedActionGroup {
            refs, actions: actions.clone(), estimated_saved_characters: saved,
        });
    }
    Ok(())
}

fn empty_value_eligible(request: &SharingRequest<'_>, atom: &Atom, node: &ProjectionNode) -> bool {
    let Some(evidence) = request.compression else { return false };
    let id = atom.id;
    !request.evidence.protected_values.contains(&id)
        && !evidence.concrete_inputs.contains(&id)
        && !evidence.semantic_regions.contains(&id)
        && request.atoms.focused != Some(id)
        && !request.atoms.selected.contains(&id)
        && !["focused", "selected", "editable", "protected"].iter().any(|name| {
            node.field(name).and_then(Value::as_bool) == Some(true)
        })
        && !node.field("selectedText").is_some_and(truthy)
        && !atom.slots.iter().any(|slot| matches!(slot.protection, Some("input" | "focus")))
}

fn auxiliary_text(atom: &Atom, key: &str) -> bool {
    let captions: &[&str] = if key == "help" { &["name", "description"] } else { &["name"] };
    atom.slots.iter().any(|slot| {
        captions.contains(&slot.key.as_str()) && slot.format == Format::Text && !slot.has_preview()
            && slot.value.as_str().is_some_and(|value| !value.trim_matches(python_space).is_empty())
    })
}

fn empty_value_groups<'a>(planner: &Planner<'_>, members: &[(NodeId, &'a Slot)]) -> Result<Vec<Vec<(NodeId, &'a Slot)>>> {
    let interface = planner.request.interface;
    let mut indexes = HashMap::new();
    let mut candidates = Vec::new();
    for &(id, slot) in members {
        let mut parent = planner.request.projection.node(id).ok_or(SharingError("unknown empty sharing source"))?.parent;
        let mut seen = HashSet::from([id]);
        while let Some(current) = parent.filter(|current| seen.insert(*current)) {
            if planner.request.evidence.contested_sources.contains(&current) {
                break;
            }
            if let Some(&block) = interface.ref_locations.get(&current) {
                let candidate = interface.block(block).ok_or(SharingError("unknown empty sharing block"))?;
                if candidate.primary_ref == Some(current) && candidate.origin == "native" {
                    grouped(&mut indexes, &mut candidates, block, (id, slot));
                }
            }
            parent = planner.request.projection.node(current).ok_or(SharingError("unknown empty sharing ancestor"))?.parent;
        }
    }
    candidates.sort_by_key(|candidate| std::cmp::Reverse(candidate.len()));
    let mut selected = Vec::new();
    let mut consumed = HashSet::new();
    for candidate in candidates {
        let remaining = candidate.into_iter().filter(|(id, _)| !consumed.contains(id)).collect::<Vec<_>>();
        let refs = remaining.iter().map(|(id, _)| *id).collect::<Vec<_>>();
        if planner.declaration_parent(&refs)?.is_none() {
            continue;
        }
        let spelling = planner.rendered_refs(&refs)?;
        let slot = remaining[0].1;
        if field_saving(&refs, &spelling, slot) > 0 && empty_value_profitable(&refs, &spelling, slot) {
            selected.push(remaining);
            consumed.extend(refs);
        }
    }
    Ok(selected)
}

fn share_fields(planner: &Planner<'_>, plan: &mut SharingPlan) -> Result<()> {
    let request = &planner.request;
    let mut indexes = HashMap::new();
    let mut groups = Vec::new();
    for &id in &request.atoms.order {
        if request.excluded_sources.contains(&id) || request.evidence.contested_sources.contains(&id) {
            continue;
        }
        let atom = &request.atoms.atoms[&id];
        let node = request.projection.node(id).ok_or(SharingError("missing field sharing source"))?;
        for slot in &atom.slots {
            let Some(value) = slot.value.as_str() else { continue };
            let value_only = slot.key == "value";
            let supported = matches!(slot.key.as_str(), "help" | "description") && !value.is_empty()
                && auxiliary_text(atom, &slot.key)
                || value_only && value.is_empty() && empty_value_eligible(request, atom, node);
            if !supported || slot.has_preview() || slot.protection.is_some() || slot.placement != Placement::Inline
                || slot.facts.is_empty() || request.disclosure.is_some_and(|plan| plan.get(id, &slot.key).is_some())
                || slot.facts.iter().any(|fact| !request.evidence.complete_fields.contains(&fact.key)
                    || fact.value.as_str() != Some(value))
            {
                continue;
            }
            let Some(scope) = member_scope(request, atom, value_only)? else { continue };
            grouped(&mut indexes, &mut groups, (scope, slot.key.as_str(), slot.label, slot.format.name(), value), (id, slot));
        }
    }
    for members in groups {
        let groups = if members[0].1.key == "value" { empty_value_groups(planner, &members)? } else { vec![members] };
        for group in groups {
            let refs = group.iter().map(|(id, _)| *id).collect::<Vec<_>>();
            let Some(block) = planner.declaration_parent(&refs)? else { continue };
            let slot = group[0].1;
            let saved = field_saving(&refs, &planner.rendered_refs(&refs)?, slot);
            if saved == 0 {
                continue;
            }
            for &id in &refs {
                if !plan.omitted_slots.entry(id).or_default().insert(slot.key.clone()) {
                    return Err(SharingError("shared field occurrence has multiple declarations"));
                }
            }
            plan.by_block.entry(block).or_default().fields.push(SharedFieldGroup {
                refs, slot_key: slot.key.clone(), slot: slot.clone(),
                source_slots: group.into_iter().map(|(id, slot)| (id, slot.clone())).collect(),
                estimated_saved_characters: saved,
            });
        }
    }
    Ok(())
}

pub(crate) fn plan_sharing(request: SharingRequest<'_>) -> Result<SharingPlan> {
    let target_refs: HashSet<_> = request.interface.target_refs.iter().copied().collect();
    if target_refs.len() != request.interface.target_refs.len()
        || target_refs.len() != request.atoms.atoms.len()
        || !target_refs.iter().all(|id| request.atoms.atoms.contains_key(id))
        || request.atoms.order.iter().copied().collect::<HashSet<_>>() != target_refs
        || request.atoms.order.len() != target_refs.len()
    {
        return Err(SharingError("sharing requires matching interface and atom sources"));
    }
    let mut display_parents = HashMap::new();
    for id in request.interface.active_ids() {
        let block = request.interface.block(id).expect("active sharing block");
        for &child in &block.children {
            if display_parents.insert(child, id).is_some() {
                return Err(SharingError("shared display block has multiple parents"));
            }
        }
    }
    let planner = Planner { request, display_parents };
    let mut result = SharingPlan::default();
    share_actions(&planner, &mut result)?;
    share_fields(&planner, &mut result)?;
    Ok(result)
}

impl super::content::readable::Sharing for SharingPlan {
    fn fields(&self, block: BlockId) -> impl Iterator<Item = super::content::readable::SharedField<'_>> {
        self.by_block.get(&block).into_iter().flat_map(|group| &group.fields).map(|group| {
            super::content::readable::SharedField {
                refs: &group.refs, slot_key: &group.slot_key, slot: &group.slot,
            }
        })
    }

    fn omitted(&self, source: NodeId, key: &str) -> bool {
        self.omitted_slots.get(&source).is_some_and(|slots| slots.contains(key))
    }
}
