// Copyright (c) Huawei Technologies Co., Ltd. 2026. All rights reserved.

//! Place existing form labels, note fields and visual-row explanations without
//! transferring their action owners or inventing native associations.

use std::collections::{HashMap, HashSet};

use super::{atom, block, node, require_source, role, trim, ContentError, Result};
use crate::{
    geometric::{BlockId, GeometricPlan},
    numbering::NodeId,
    projection::{Projection, ProjectionNode},
    render::atoms::{Atom, AtomPlan, Placement, Slot},
    value::Value,
};

const FORM_BOUNDARIES: [&str; 8] = [
    "textArea", "textField", "webArea", "table", "list", "outline", "document", "scrollArea",
];

pub(crate) struct SharedField<'a> {
    pub(crate) refs: &'a [NodeId],
    pub(crate) slot_key: &'a str,
    pub(crate) slot: &'a Slot,
}

/// The readable stage consumes existing shared declarations. An adapter must
/// retain all field declarations in their original order for each block.
pub(crate) trait Sharing {
    fn fields(&self, block: BlockId) -> impl Iterator<Item = SharedField<'_>>;
    fn omitted(&self, source: NodeId, key: &str) -> bool;
}

#[derive(Debug, Default)]
pub(crate) struct ReadableNode {
    pub(crate) note_slots: Vec<String>,
    pub(crate) block_slots: Vec<String>,
    pub(crate) note_actions: Vec<usize>,
    pub(crate) toggle_slot: Option<&'static str>,
    pub(crate) glyph: Option<&'static str>,
    pub(crate) notes_only: bool,
    pub(crate) visual_row: Vec<NodeId>,
}

#[derive(Debug)]
pub(crate) struct ReadableHeading {
    pub(crate) reference: NodeId,
    pub(crate) caption: Option<Slot>,
    pub(crate) sources: Vec<NodeId>,
    pub(crate) shared_index: Option<usize>,
    pub(crate) transparent: bool,
}

#[derive(Debug, Default)]
pub(crate) struct ReadablePlan {
    pub(crate) nodes: Vec<(NodeId, ReadableNode)>,
    pub(crate) headings: Vec<(BlockId, ReadableHeading)>,
    style_index: HashMap<NodeId, usize>,
    heading_index: HashMap<BlockId, usize>,
}

impl ReadablePlan {
    pub(crate) fn node(&self, source: NodeId) -> Option<&ReadableNode> {
        self.style_index.get(&source).map(|index| &self.nodes[*index].1)
    }

    pub(crate) fn heading(&self, block: BlockId) -> Option<&ReadableHeading> {
        self.heading_index.get(&block).map(|index| &self.headings[*index].1)
    }

    fn style(&mut self, source: NodeId) -> &mut ReadableNode {
        let index = *self.style_index.entry(source).or_insert_with(|| {
            self.nodes.push((source, ReadableNode::default()));
            self.nodes.len() - 1
        });
        &mut self.nodes[index].1
    }
}

fn identifier(value: &Value) -> bool {
    let Some(value) = value.as_str() else { return false };
    if matches!(value, "Title" | "_Title") { return true; }
    let Some((name, suffix)) = value.rsplit_once('_') else { return false };
    let mut characters = name.bytes();
    characters.next().is_some_and(|item| item.is_ascii_alphabetic())
        && characters.all(|item| item.is_ascii_alphanumeric() || item == b'_')
        && matches!(suffix, "title" | "label" | "lable" | "name" | "widget" | "button" | "desc" | "line")
}

fn glyph(role: &str, value: &Value) -> Option<&'static str> {
    if !matches!(role, "checkBox" | "radioButton") { return None; }
    if role == "checkBox" && value.as_str() == Some("mixed") { return Some("[-]"); }
    let enabled = match value {
        Value::Bool(value) => *value,
        Value::Int(1) => true,
        Value::Int(0) => false,
        Value::Text(value) if matches!(value.as_str(), "on" | "true" | "1") => true,
        Value::Text(value) if matches!(value.as_str(), "off" | "false" | "0") => false,
        _ => return None,
    };
    Some(match (role, enabled) {
        ("radioButton", true) => "(*)",
        ("radioButton", false) => "( )",
        (_, true) => "[x]",
        (_, false) => "[ ]",
    })
}

fn passive(atom: &Atom, node: &ProjectionNode, keys: &[String]) -> bool {
    !keys.is_empty()
        && keys.iter().map(String::as_str).collect::<HashSet<_>>()
            == atom.slots.iter().map(|slot| slot.key.as_str()).collect::<HashSet<_>>()
        && matches!(role(node), "staticText" | "image")
        && atom.actions.is_empty() && !atom.actions_unavailable && atom.states.is_empty() && node.children.is_empty()
        && atom.declarations.is_empty() && !atom.coverage.partial()
        && node.field("capabilities").is_none_or(|value| value.as_map().is_some_and(|items|
            items.iter().all(|(_, value)| value.as_str() != Some("available"))))
}

fn form_refs(projection: &Projection, interface: &GeometricPlan) -> Result<HashSet<NodeId>> {
    let mut refs = HashSet::new();
    let mut visited = HashSet::new();
    for id in interface.active_ids() {
        let field = block(interface, id)?;
        if field.kind != "field" || field.visual_unit { continue; }
        let Some(label) = field.label_ref else { continue };
        refs.insert(label);
        let mut pending = vec![id];
        while let Some(id) = pending.pop() {
            if !visited.insert(id) { continue; }
            let member = block(interface, id)?;
            refs.extend(&member.source_refs);
            if let Some(primary) = member.primary_ref {
                if FORM_BOUNDARIES.contains(&role(node(projection, primary)?)) { continue; }
            }
            pending.extend(&member.children);
        }
    }
    Ok(refs)
}

fn node_styles(projection: &Projection, interface: &GeometricPlan, atoms: &AtomPlan) -> Result<ReadablePlan> {
    let form = form_refs(projection, interface)?;
    let mut output = ReadablePlan::default();
    for reference in &atoms.order {
        let atom = atom(atoms, *reference)?;
        let node = node(projection, *reference)?;
        let role = role(node);
        let glyph = atom.slots.iter().find(|slot| slot.key == "value").and_then(|slot| glyph(role, &slot.value));
        let mut note_slots: Vec<_> = atom.slots.iter().filter(|slot| form.contains(reference)
            && matches!(slot.key.as_str(), "name" | "value") && matches!(role, "staticText" | "image")
            && identifier(&slot.value)).map(|slot| slot.key.clone()).collect();
        let captions = !note_slots.is_empty();
        note_slots.extend(atom.slots.iter().filter(|slot| slot.key == "selectedText" && slot.value.as_str() == Some(""))
            .map(|slot| slot.key.clone()));
        let block_slots: Vec<_> = atom.slots.iter().filter(|slot| matches!(slot.key.as_str(), "help" | "fullDescription")
            || slot.key == "description" && slot.value.as_str().is_some_and(|value| value.chars().count() > 40))
            .map(|slot| slot.key.clone()).collect();
        let note_actions: Vec<_> = atom.actions.iter().filter(|action|
            !matches!(role, "window" | "sheet" | "dialog" | "alert" | "application")
            && action.source == *reference
            && matches!(action.record.get("kind").and_then(Value::as_str), Some("raise" | "increment" | "decrement")))
            .map(|action| action.catalogue_index).collect();
        if note_slots.is_empty() && block_slots.is_empty() && note_actions.is_empty() && glyph.is_none() { continue; }
        let notes_only = captions && passive(atom, node, &note_slots);
        *output.style(*reference) = ReadableNode {
            note_slots, block_slots, note_actions, toggle_slot: glyph.map(|_| "value"),
            glyph, notes_only, visual_row: Vec::new(),
        };
    }
    Ok(output)
}

fn headings(
    projection: &Projection,
    interface: &GeometricPlan,
    sharing: &impl Sharing,
    output: &mut ReadablePlan,
) -> Result<()> {
    for id in interface.active_ids() {
        let field = block(interface, id)?;
        let Some(reference) = field.label_ref.filter(|reference|
            field.kind == "field" && !field.visual_unit && field.heading_style == "caption"
                && output.node(*reference).is_some_and(|style| style.notes_only))
            else { continue };
        let mut question = None;
        for (index, group) in sharing.fields(id).enumerate() {
            if group.slot_key != "description" || group.refs.len() <= 1
                || group.slot.value.as_str().is_none_or(|value| trim(value).is_empty())
            {
                continue;
            }
            let mut radio = true;
            for source in group.refs {
                if role(node(projection, *source)?) != "radioButton" { radio = false; break; }
            }
            if !radio { continue; }
            if question.is_some() { question = None; break; }
            question = Some((index, group));
        }
        let mut heading = ReadableHeading {
            reference, caption: None, sources: Vec::new(), shared_index: None, transparent: false,
        };
        if let Some((index, group)) = question {
            heading.caption = Some(group.slot.clone());
            heading.sources.extend_from_slice(group.refs);
            heading.shared_index = Some(index);
        }
        output.heading_index.insert(id, output.headings.len());
        output.headings.push((id, heading));
    }
    for (id, heading) in &mut output.headings {
        if heading.caption.is_some() { continue; }
        let field = block(interface, *id)?;
        let children = field.children.iter().map(|child| block(interface, *child).map(|block| (*child, block)))
            .collect::<Result<Vec<_>>>()?;
        let mut content = children.iter().filter(|(_, child)| child.primary_ref != Some(heading.reference));
        if let Some((child, _)) = content.next() {
            heading.transparent = content.next().is_none() && output.heading_index.contains_key(child);
        }
    }
    Ok(())
}

fn control(role: &str) -> bool {
    matches!(role, "button" | "menuButton" | "link" | "checkBox" | "radioButton" | "switch" | "popUpButton" | "comboBox"
        | "slider" | "incrementor" | "closeButton" | "minimizeButton" | "zoomButton" | "textField" | "textArea")
}

fn explanations(
    projection: &Projection,
    interface: &GeometricPlan,
    atoms: &AtomPlan,
    sharing: &impl Sharing,
    output: &mut ReadablePlan,
) -> Result<()> {
    let mut rows: HashMap<BlockId, Vec<NodeId>> = HashMap::new();
    let mut locations = HashMap::new();
    let mut ordered_locations = Vec::new();
    let mut pending: Vec<_> = interface.roots.iter().rev().map(|id| (*id, None)).collect();
    let mut visited = HashSet::new();
    while let Some((id, mut row)) = pending.pop() {
        if !visited.insert(id) { return Err(ContentError::InvalidForest); }
        let entry = block(interface, id)?;
        if entry.origin == "visual" && entry.layout == "visual-row" { row = Some(id); }
        if let Some(row) = row {
            let members = rows.entry(row).or_default();
            for reference in &entry.source_refs {
                members.push(*reference);
                if locations.insert(*reference, row).is_none() { ordered_locations.push(*reference); }
            }
        }
        pending.extend(entry.children.iter().rev().map(|child| (*child, row)));
    }
    for reference in ordered_locations {
        if role(node(projection, reference)?) != "image" || sharing.omitted(reference, "description") { continue; }
        let atom = atom(atoms, reference)?;
        let Some(slot) = atom.slots.iter().find(|slot| slot.key == "description"
            && slot.value.as_str().is_some_and(|value| !trim(value).is_empty()))
            else { continue };
        let members = &rows[&locations[&reference]];
        let neighbours: Vec<_> = members.iter().copied().filter(|member| *member != reference).collect();
        let mut controls = false;
        for neighbour in &neighbours {
            if control(role(node(projection, *neighbour)?)) { controls = true; break; }
        }
        if !controls || slot.placement != Placement::Block
            && output.node(reference).is_none_or(|style| !style.block_slots.iter().any(|slot| slot == "description"))
        {
            continue;
        }
        let style = output.style(reference);
        if !style.block_slots.iter().any(|slot| slot == "description") {
            style.block_slots.push("description".to_owned());
        }
        style.visual_row = neighbours;
    }
    Ok(())
}

pub(crate) fn plan(
    projection: &Projection,
    interface: &GeometricPlan,
    atoms: &AtomPlan,
    sharing: &impl Sharing,
) -> Result<ReadablePlan> {
    require_source(projection, interface, atoms)?;
    let mut output = node_styles(projection, interface, atoms)?;
    headings(projection, interface, sharing, &mut output)?;
    explanations(projection, interface, atoms, sharing, &mut output)?;
    Ok(output)
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn identifiers_and_toggle_symbols_are_exact() {
        for value in ["Title", "_Title", "A_title", "Title_lable", "Field__label"] {
            assert!(identifier(&Value::Text(value.to_owned())));
        }
        for value in ["title", "__Title", "A_Label", "你_title", "an ordinary title"] {
            assert!(!identifier(&Value::Text(value.to_owned())));
        }
        assert_eq!(glyph("checkBox", &Value::Text("mixed".to_owned())), Some("[-]"));
        assert_eq!(glyph("radioButton", &Value::Bool(true)), Some("(*)"));
        assert_eq!(glyph("radioButton", &Value::Text("mixed".to_owned())), None);
        assert_eq!(glyph("checkBox", &Value::Float(1.0)), None);
    }
}
