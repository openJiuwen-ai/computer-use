// Copyright (c) Huawei Technologies Co., Ltd. 2026. All rights reserved.

//! Complete atomic slot placement only after layout and disclosure evidence exists.

use std::collections::HashMap;

use crate::{
    disclosure::DisclosurePlan,
    facts::FactSnapshot,
    geometric::GeometricPlan,
    numbering::NodeId,
    projection::{CompressionEvidence, Projection},
    value::Value,
};

use super::{AtomPlan, ClaimKind, DispositionKind, Placement, Result, Slot, TextError};

mod urls;
pub(crate) use urls::{plan_urls, Caption as UrlCaption};

#[derive(Default)]
pub(crate) struct AtomFinalizeOptions<'a> {
    pub(crate) compact: bool,
    pub(crate) public_api: bool,
    pub(crate) share_descriptions: bool,
    pub(crate) subtree: bool,
    pub(crate) interface: Option<&'a GeometricPlan>,
    pub(crate) disclosure: Option<&'a DisclosurePlan>,
    pub(crate) evidence: Option<&'a CompressionEvidence>,
    pub(crate) snapshot: Option<&'a FactSnapshot>,
    pub(crate) semantic: Option<&'a Value>,
    pub(crate) tokens: &'a [String],
}

fn share_field_labels(atoms: &mut AtomPlan, projection: &Projection, options: &AtomFinalizeOptions<'_>) -> Result<()> {
    let Some(interface) = options.interface.filter(|interface| interface.enabled) else {
        return Ok(());
    };
    if !options.compact || !options.public_api || !options.share_descriptions || options.subtree {
        return Ok(());
    }
    let mut applicable = HashMap::new();
    let mut pending: Vec<_> = interface.roots.iter().rev().map(|id| (*id, Vec::new())).collect();
    while let Some((id, mut labels)) = pending.pop() {
        let block = interface.block(id).ok_or(TextError::Invalid("field label block"))?;
        if block.kind == "window" {
            labels.clear();
        }
        if let Some(label) = block.label_ref.filter(|id| {
            block.kind == "field" && atoms.atoms.contains_key(id) && interface.ref_locations.contains_key(id)
        }) {
            labels.push(label);
        }
        for source in &block.source_refs {
            applicable.insert(*source, labels.clone());
        }
        pending.extend(block.children.iter().rev().map(|id| (*id, labels.clone())));
    }
    for id in &atoms.order {
        let atom = atoms.atoms.get_mut(id).ok_or(TextError::Invalid("field label atom"))?;
        let label = atom
            .slots
            .iter()
            .find(|slot| slot.key == "visualLabelRef")
            .and_then(|slot| slot.value.as_seq())
            .and_then(|values| {
                let [label] = values else { return None };
                projection.by_reference(label.as_str()?)
            });
        let Some(label) = label else { continue };
        if label.id == *id
            || !applicable.get(id).is_some_and(|labels| labels.contains(&label.id))
            || projection.node(*id).is_none_or(|node| node.scope != label.scope)
        {
            continue;
        }
        atom.slots.retain(|slot| slot.key != "visualLabelRef");
        for item in atom
            .dispositions
            .iter_mut()
            .filter(|item| item.slot == "visualLabelRef" && item.target == *id)
        {
            item.slot = "field-label-context".to_owned();
            item.kind = DispositionKind::Equivalent;
        }
        for item in atom
            .claims
            .iter_mut()
            .filter(|item| item.fact.source == *id && item.fact.attribute == "visualLabelRef")
        {
            item.kind = ClaimKind::Equivalent;
        }
    }
    Ok(())
}

pub(super) fn complete_slot(
    projection: &Projection,
    id: NodeId,
    slot: &Slot,
    evidence: Option<&CompressionEvidence>,
) -> bool {
    let Some(evidence) = evidence else { return false };
    projection
        .node(id)
        .and_then(|node| node.provenance.iter().find(|(key, _)| key == &slot.key))
        .is_some_and(|(_, presented)| {
            !presented.parts.is_empty()
                && presented.parts.iter().all(|part| {
                    !part.facts.is_empty() && part.facts.iter().all(|fact| evidence.complete_text.contains(fact))
                })
        })
}

fn quoted_size(text: &str) -> usize {
    2 + text
        .chars()
        .map(|value| match value {
            '"' | '\\' | '\n' | '\r' | '\t' | '\u{8}' | '\u{c}' => 2,
            '\0'..='\u{1f}' | '\u{7f}'..='\u{9f}' | '\u{2028}' | '\u{2029}' => 6,
            _ => value.len_utf8(),
        })
        .sum::<usize>()
}

fn field_size(value: &str) -> usize {
    let whitespace = |value: char| value.is_whitespace() || matches!(value, '\u{1c}'..='\u{1f}');
    let quote = value.is_empty()
        || value != value.trim_matches(whitespace)
        || value.contains(|value| {
            matches!(value, ',' | '"' | '>' | '\\' | '|' | ';' | '{' | '}'
            | '\0'..='\u{1f}' | '\u{7f}'..='\u{9f}' | '\u{2028}' | '\u{2029}')
        })
        || value.starts_with(['<', '+', '~', '|'])
        || value.contains("(partial:")
        || value.contains("(showing ");
    if quote {
        quoted_size(value)
    } else {
        value.len()
    }
}

fn prefers_inline(value: &str, label: Option<&str>, depth: usize) -> bool {
    if !value.contains(['\n', '\r']) {
        return false;
    }
    let normalized = value.replace("\r\n", "\n").replace('\r', "\n");
    let block = 1
        + depth
        + 1
        + label.unwrap_or("Value").len()
        + 2
        + normalized
            .split('\n')
            .map(|paragraph| depth + 3 + field_size(paragraph))
            .sum::<usize>()
        + normalized.bytes().filter(|byte| *byte == b'\n').count();
    let scalar = quoted_size(value) + 2 + label.map_or(0, |label| label.len() + 2);
    block.saturating_sub(scalar) >= 8
}

fn select_multiline(atoms: &mut AtomPlan, projection: &Projection, options: &AtomFinalizeOptions<'_>) -> Result<()> {
    if options.evidence.is_none() {
        return Ok(());
    }
    let mut depths = HashMap::new();
    for id in &atoms.order {
        let atom = atoms.atoms.get_mut(id).ok_or(TextError::Invalid("multiline atom"))?;
        let depth = atom
            .parent
            .and_then(|parent| depths.get(&parent).copied())
            .map_or(0, |depth: usize| depth + 1);
        depths.insert(*id, depth);
        for slot in &mut atom.slots {
            if slot.placement == Placement::Block
                && !slot.has_preview()
                && options
                    .disclosure
                    .is_none_or(|disclosure| disclosure.get(*id, &slot.key).is_none())
                && complete_slot(projection, *id, slot, options.evidence)
                && slot
                    .value
                    .as_str()
                    .is_some_and(|value| prefers_inline(value, slot.label, depth))
            {
                slot.placement = Placement::Inline;
            }
        }
    }
    Ok(())
}

pub(crate) fn finalize_atoms(
    atoms: &mut AtomPlan,
    projection: &Projection,
    options: &AtomFinalizeOptions<'_>,
) -> Result<()> {
    share_field_labels(atoms, projection, options)?;
    select_multiline(atoms, projection, options)?;
    urls::finalize_urls(atoms, projection, options)?;
    atoms.shared_index_basis = atoms
        .atoms
        .values()
        .flat_map(|atom| &atom.slots)
        .filter(|slot| slot.key == "index")
        .take(3)
        .count()
        >= 3;
    Ok(())
}
