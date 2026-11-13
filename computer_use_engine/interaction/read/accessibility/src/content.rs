// Copyright (c) Huawei Technologies Co., Ltd. 2026. All rights reserved.

//! Select readable field placements over the same captured atoms and geometric
//! blocks. Source facts and executable action identities remain independently owned.

use std::collections::{HashMap, HashSet};

use super::{
    geometric::{Block, BlockId, GeometricPlan},
    numbering::NodeId,
    projection::{CompressionEvidence, Projection, ProjectionNode},
    render::atoms::{Atom, AtomPlan, Claim, ClaimKind, Disposition, DispositionKind, Format, Placement, Slot},
    value::Value,
};

pub(crate) mod readable;

const REQUIRED_CAPABILITIES: [&str; 4] = ["click", "typeText", "replaceText", "focus"];
const INDEPENDENT: [&str; 8] = [
    "selected", "focused", "busy", "invalid", "modal", "incompleteLabelRelation", "actionsUnavailable", "preserveGroup",
];
const DUPLICATE_INDEPENDENT: [&str; 16] = [
    "selected", "focused", "busy", "invalid", "editable", "pressable", "focusable", "selectable",
    "expandable", "submenu", "sensitive", "protected", "modal", "incompleteLabelRelation", "actionsUnavailable",
    "preserveGroup",
];

#[derive(Debug, Eq, PartialEq)]
pub(crate) enum ContentError {
    MissingBlock(BlockId),
    MissingNode(NodeId),
    MissingAtom(NodeId),
    InvalidForest,
    MismatchedSource,
}

type Result<T> = std::result::Result<T, ContentError>;

#[derive(Clone, Copy, Debug, Eq, PartialEq)]
pub(crate) enum MemberMode {
    Complete,
    ActionSource,
    Composed,
    CaptionReference,
}

#[derive(Debug)]
pub(crate) struct FieldMember {
    pub(crate) source: NodeId,
    pub(crate) mode: MemberMode,
    pub(crate) consumed_slots: Vec<String>,
}

#[derive(Debug)]
pub(crate) struct FieldContribution {
    pub(crate) source: NodeId,
    pub(crate) source_slot: Slot,
    pub(crate) target_slot: String,
}

#[derive(Debug)]
pub(crate) struct FieldPresentation {
    pub(crate) block: BlockId,
    pub(crate) target: NodeId,
    pub(crate) slots: Vec<Slot>,
    pub(crate) members: Vec<FieldMember>,
    pub(crate) contributions: Vec<FieldContribution>,
    pub(crate) dispositions: Vec<Disposition>,
    pub(crate) claims: Vec<Claim>,
    pub(crate) heading: Option<NodeId>,
}

#[derive(Debug, Default)]
pub(crate) struct FieldContentPlan {
    pub(crate) fields: Vec<FieldPresentation>,
    index: HashMap<BlockId, usize>,
}

impl FieldContentPlan {
    pub(crate) fn get(&self, block: BlockId) -> Option<&FieldPresentation> {
        self.index.get(&block).map(|index| &self.fields[*index])
    }

    pub(crate) fn excluded_sources(&self) -> HashSet<NodeId> {
        self.fields.iter().flat_map(|field|
            std::iter::once(field.target).chain(field.members.iter().map(|member| member.source))
        ).collect()
    }
}

fn node(projection: &Projection, id: NodeId) -> Result<&ProjectionNode> {
    projection.node(id).ok_or(ContentError::MissingNode(id))
}

fn atom(atoms: &AtomPlan, id: NodeId) -> Result<&Atom> {
    atoms.atoms.get(&id).ok_or(ContentError::MissingAtom(id))
}

fn block(interface: &GeometricPlan, id: BlockId) -> Result<&Block> {
    interface.block(id).ok_or(ContentError::MissingBlock(id))
}

fn require_source(projection: &Projection, interface: &GeometricPlan, atoms: &AtomPlan) -> Result<()> {
    if atoms.order != interface.target_refs { return Err(ContentError::MismatchedSource); }
    for id in &atoms.order {
        if atom(atoms, *id)?.reference != node(projection, *id)?.reference {
            return Err(ContentError::MismatchedSource);
        }
    }
    Ok(())
}

fn role(node: &ProjectionNode) -> &str {
    node.field("role").and_then(Value::as_str).unwrap_or_default()
}

fn trim(value: &str) -> &str {
    value.trim_matches(|character: char| character.is_whitespace() || matches!(character, '\u{1c}'..='\u{1f}'))
}

fn members(interface: &GeometricPlan, block: &Block) -> Result<Vec<NodeId>> {
    let mut seen = HashSet::new();
    let mut pending: Vec<_> = block.children.iter().rev().copied().collect();
    let mut members = Vec::new();
    while let Some(id) = pending.pop() {
        if !seen.insert(id) { return Err(ContentError::InvalidForest); }
        let entry = interface.block(id).ok_or(ContentError::MissingBlock(id))?;
        members.extend(&entry.source_refs);
        pending.extend(entry.children.iter().rev());
    }
    Ok(members)
}

fn complete(node: &ProjectionNode, slot: &Slot, evidence: Option<&CompressionEvidence>) -> bool {
    node.field("_completeTextValues").and_then(Value::as_seq).is_some_and(|fields|
        fields.iter().any(|field| field.as_str() == Some(slot.key.as_str())))
        || !slot.facts.is_empty() && evidence.is_some_and(|evidence|
            slot.facts.iter().all(|fact| evidence.complete_text.contains(&fact.key)))
}

fn passive_fields<'a>(
    atom: &'a Atom,
    node: &ProjectionNode,
    focused: Option<NodeId>,
    evidence: Option<&CompressionEvidence>,
) -> &'a [Slot] {
    if role(node) != "staticText" || !node.children.is_empty() || !atom.states.is_empty()
        || atom.coverage.partial() || atom.actions_unavailable || !atom.declarations.is_empty()
        || focused == Some(atom.id)
    {
        return &[];
    }
    let Some(capabilities) = node.field("capabilities").filter(|item| item.as_map().is_some()) else { return &[] };
    if REQUIRED_CAPABILITIES.iter().any(|key| capabilities.get(key).and_then(Value::as_str) != Some("unavailable"))
        || node.field("actions").is_none() || node.field("enabled") == Some(&Value::Bool(false))
        || INDEPENDENT.iter().any(|key| super::geometric::truth(node.field(key)))
    {
        return &[];
    }
    let Some(value) = atom.slots.first().and_then(|slot| slot.value.as_str()) else { return &[] };
    if trim(value).is_empty() || value.contains(['\n', '\r', '\t']) { return &[]; }
    if atom.slots.iter().all(|slot| matches!(slot.key.as_str(), "name" | "description" | "value")
        && slot.protection.is_none() && slot.format == Format::Text
        && slot.value.as_str() == Some(value) && complete(node, slot, evidence))
    {
        &atom.slots
    } else {
        &[]
    }
}

fn duplicate_caption<'a>(
    member: &'a Atom,
    member_node: &ProjectionNode,
    target: &Atom,
    target_node: &ProjectionNode,
    field: &Block,
    interface: &GeometricPlan,
    focused: Option<NodeId>,
    evidence: Option<&CompressionEvidence>,
) -> &'a [Slot] {
    if !field.visual_unit || field.layout != "control-unit" || !matches!(role(target_node), "button" | "link")
        || role(member_node) != "staticText" || !member_node.children.is_empty() || !member.states.is_empty()
        || !member.actions.is_empty() || member.actions_unavailable || !member.declarations.is_empty()
        || member.coverage.partial() || focused == Some(member.id)
    {
        return &[];
    }
    let Some(capabilities) = member_node.field("capabilities").filter(|item| item.as_map().is_some()) else { return &[] };
    if REQUIRED_CAPABILITIES.iter().any(|key| !matches!(capabilities.get(key).and_then(Value::as_str), Some("unknown" | "unavailable")))
        || member_node.field("actions").is_none_or(|value| super::geometric::truth(Some(value)))
        || member_node.field("enabled") == Some(&Value::Bool(false))
        || DUPLICATE_INDEPENDENT.iter().any(|key| super::geometric::truth(member_node.field(key)))
        || member_node.parent != Some(target.id) || interface.projected_parents.get(&member.id) != Some(&target.id)
        || target_node.children != [member.id]
    {
        return &[];
    }
    let mut captions = target.slots.iter().filter(|slot| slot.placement == Placement::Caption);
    let Some(caption) = captions.next().filter(|slot| slot.format == Format::Text) else { return &[] };
    if captions.next().is_some() || !complete(target_node, caption, evidence) { return &[]; }
    let Some(value) = caption.value.as_str().filter(|text| !text.is_empty() && !text.contains(['\n', '\r', '\t']))
        else { return &[] };
    if member.slots.iter().all(|slot| matches!(slot.key.as_str(), "name" | "description" | "value")
        && slot.protection.is_none() && slot.format == Format::Text
        && slot.value.as_str() == Some(value) && complete(member_node, slot, evidence))
    {
        &member.slots
    } else {
        &[]
    }
}

fn caption(slots: &mut Vec<Slot>, source: &str, incoming: &[Slot]) -> Option<String> {
    let mut captions = slots.iter().enumerate().filter(|(_, slot)| slot.placement == Placement::Caption);
    let existing = captions.next();
    if existing.is_some_and(|(_, slot)| slot.value != incoming[0].value) || captions.next().is_some() { return None; }
    let facts = incoming.iter().flat_map(|slot| slot.facts.iter().cloned());
    if let Some((index, _)) = existing {
        slots[index].facts.extend(facts);
        return Some(slots[index].key.clone());
    }
    let mut selected = incoming[0].clone();
    selected.key = format!("field-label:{source}");
    selected.label = None;
    selected.placement = Placement::Caption;
    selected.facts = facts.collect();
    let key = selected.key.clone();
    slots.insert(0, selected);
    Some(key)
}

fn help(slots: &mut Vec<Slot>, source: &str, incoming: &[Slot]) -> String {
    let equivalent = slots.iter_mut().find(|slot| (slot.key == "help" || slot.key.starts_with("field-help:"))
        && slot.value == incoming[0].value);
    let facts = incoming.iter().flat_map(|slot| slot.facts.iter().cloned());
    if let Some(slot) = equivalent {
        slot.facts.extend(facts);
        slot.placement = Placement::Block;
        return slot.key.clone();
    }
    let mut selected = incoming[0].clone();
    selected.key = format!("field-help:{source}");
    selected.label = Some("Help");
    selected.placement = Placement::Block;
    selected.facts = facts.collect();
    let key = selected.key.clone();
    slots.push(selected);
    key
}

pub(crate) fn plan_fields(
    projection: &Projection,
    interface: &GeometricPlan,
    atoms: &AtomPlan,
    evidence: Option<&CompressionEvidence>,
    captured_details: bool,
) -> Result<FieldContentPlan> {
    require_source(projection, interface, atoms)?;
    let mut output = FieldContentPlan::default();
    for block_id in interface.active_ids() {
        let field = block(interface, block_id)?;
        if field.layout == "compound-control" { continue; }
        let Some(target_id) = field.field_target_ref else { continue };
        let Some(target) = atoms.atoms.get(&target_id) else { continue };
        let target_node = node(projection, target_id)?;
        let mut plan = FieldPresentation {
            block: block_id, target: target_id, slots: target.slots.clone(),
            members: Vec::new(), contributions: Vec::new(), dispositions: Vec::new(), claims: Vec::new(), heading: None,
        };
        let roles: HashMap<_, _> = field.member_roles.iter().copied().collect();
        for source in members(interface, field)? {
            if source == target_id { continue; }
            let member = atom(atoms, source)?;
            let member_node = node(projection, source)?;
            let relation = roles.get(&source).copied();
            let duplicate = if captured_details && relation == Some("label") {
                duplicate_caption(member, member_node, target, target_node, field, interface, atoms.focused, evidence)
            } else { &[] };
            let incoming = if !duplicate.is_empty() { duplicate }
                else if captured_details { passive_fields(member, member_node, atoms.focused, evidence) }
                else { &[] };
            let destination = match (incoming.is_empty(), relation) {
                (false, Some("label")) => caption(&mut plan.slots, &member.reference, incoming),
                (false, Some("help")) => Some(help(&mut plan.slots, &member.reference, incoming)),
                _ => None,
            };
            let Some(destination) = destination else {
                plan.members.push(FieldMember { source, mode: MemberMode::Complete, consumed_slots: Vec::new() });
                continue;
            };
            let consumed_slots: Vec<_> = incoming.iter().map(|slot| slot.key.clone()).collect();
            let mode = if !duplicate.is_empty() { MemberMode::CaptionReference }
                else if !member.actions.is_empty() { MemberMode::ActionSource }
                else { MemberMode::Composed };
            plan.contributions.extend(incoming.iter().map(|slot| FieldContribution {
                source, source_slot: slot.clone(), target_slot: destination.clone(),
            }));
            plan.dispositions.extend(member.dispositions.iter().cloned().map(|mut disposition| {
                if consumed_slots.contains(&disposition.slot) {
                    disposition.target = target_id;
                    disposition.slot = destination.clone();
                    disposition.kind = DispositionKind::Equivalent;
                } else {
                    disposition.kind = DispositionKind::Details;
                }
                disposition
            }));
            let represented: HashSet<_> = incoming.iter().flat_map(|slot| slot.facts.iter().map(|fact| &fact.key)).collect();
            plan.claims.extend(member.claims.iter().filter(|claim| represented.contains(&claim.fact)).cloned().map(|mut claim| {
                claim.target = target_id;
                claim.kind = ClaimKind::Equivalent;
                claim
            }));
            plan.members.push(FieldMember { source, mode, consumed_slots });
        }
        if let Some(label) = field.label_ref {
            if plan.members.iter().any(|member| member.source == label && member.mode == MemberMode::Complete) {
                plan.heading = Some(label);
            }
        }
        output.index.insert(block_id, output.fields.len());
        output.fields.push(plan);
    }
    Ok(output)
}

#[cfg(test)]
mod tests {
    use super::*;
    use crate::{geometric::GeometricContext, render::atoms::plan_atoms, wire::decode_json};

    #[test]
    fn exact_passive_caption_can_move_but_unknown_input_remains_independent() {
        for (capability, mode) in [("unavailable", MemberMode::Composed), ("unknown", MemberMode::Complete)] {
            let fixture = format!(r#"{{
                "ref":"0","role":"window","children":[
                    {{"ref":"1","role":"staticText","name":"Label", "_completeTextValues":["name"],
                     "actions":[], "capabilities":{{"click":"{capability}","typeText":"unavailable",
                      "replaceText":"unavailable","focus":"unavailable"}},"children":[]}},
                    {{"ref":"2","role":"textField","children":[]}}
                ]
            }}"#);
            let aliases = (0..3).map(|id| (id.to_string(), NodeId(id))).collect();
            let projection = Projection::prepare(decode_json(fixture.as_bytes()).unwrap(), &aliases, Default::default()).unwrap();
            let atoms = plan_atoms(&projection, &Default::default()).unwrap();
            let context = GeometricContext { projection: &projection, geometry: &HashMap::new(), facts: None, atoms: None };
            let mut interface = GeometricPlan::seed(&context, true).unwrap();
            let label = interface.source_block(NodeId(1)).unwrap();
            let target = interface.source_block(NodeId(2)).unwrap();
            let mut field = Block::new("field".to_owned(), "field", None, vec![label, target]);
            field.field_target_ref = Some(NodeId(2));
            field.label_ref = Some(NodeId(1));
            field.member_roles = vec![(NodeId(1), "label"), (NodeId(2), "control")];
            let field = interface.insert(field);
            let root = interface.roots[0];
            interface.block_mut(root).unwrap().children = vec![field];
            let interface = interface.freeze().unwrap();
            let output = plan_fields(&projection, &interface, &atoms, None, true).unwrap();
            let selected = output.get(field).unwrap();
            assert_eq!(selected.members[0].source, NodeId(1));
            assert_eq!(selected.members[0].mode, mode);
            if mode == MemberMode::Composed {
                assert_eq!(selected.slots[0].key, "field-label:1");
                assert_eq!(selected.contributions[0].source, NodeId(1));
                assert_eq!(selected.slots[0].facts[0].key.source, NodeId(1));
                assert!(selected.dispositions.iter().any(|entry|
                    entry.fact.key.source == NodeId(1) && entry.target == NodeId(2) && entry.kind == DispositionKind::Equivalent));
                assert_eq!(selected.heading, None);
            } else {
                assert!(selected.contributions.is_empty());
                assert_eq!(selected.heading, Some(NodeId(1)));
            }
        }
    }
}
