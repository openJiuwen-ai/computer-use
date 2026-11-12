// Copyright (c) Huawei Technologies Co., Ltd. 2026. All rights reserved.

use super::{
    role,
    rows::{inline_unit_refs, row_unit},
    AtomLayoutEvidence, Block, GeometricContext, GeometricPlan, NodeId,
};

fn caption_label(context: &GeometricContext<'_>, id: NodeId) -> bool {
    let Some(node) = context.projection.node(id) else {
        return false;
    };
    let Some(atom) = context.atoms.and_then(|atoms| atoms.get(&id)) else {
        return false;
    };
    if role(node) != "staticText"
        || !node.children.is_empty()
        || !atom.state_labels.is_empty()
        || atom.has_actions
        || atom.actions_unavailable
        || atom.declarations
        || atom.partial
        || atom.focused
    {
        return false;
    }
    let [slot] = atom.slots.as_slice() else {
        return false;
    };
    matches!(slot.key.as_str(), "name" | "value" | "description")
        && slot.format == "text"
        && slot
            .value
            .as_str()
            .is_some_and(|value| !value.trim().is_empty() && !value.contains(['\r', '\n', '\t']))
}

fn inline_atom(atom: &AtomLayoutEvidence) -> bool {
    !atom.declarations
        && atom
            .slots
            .iter()
            .all(|slot| !slot.block && slot.value.as_str().is_none_or(|value| !value.contains(['\n', '\r'])))
}

fn inline_field(plan: &GeometricPlan, context: &GeometricContext<'_>, block: &Block, label_ref: NodeId) -> bool {
    let Some(atoms) = context.atoms else {
        return false;
    };
    let Some(label) = context.geometry.get(&label_ref) else {
        return false;
    };
    let Some(first) = block.children.iter().copied().find(|&key| {
        plan.block(key)
            .is_some_and(|child| child.primary_ref != Some(label_ref))
    }) else {
        return false;
    };
    let child = plan.block(first).unwrap();
    let single = [first];
    let members = if child.layout == "visual-row" {
        child.children.as_slice()
    } else if child.primary_ref.is_some() && child.children.is_empty() {
        &single
    } else {
        return false;
    };
    if members.is_empty() {
        return false;
    }
    let Some(unit_refs) = members
        .iter()
        .map(|&key| inline_unit_refs(key, plan))
        .collect::<Option<Vec<_>>>()
    else {
        return false;
    };
    let refs: Vec<_> = unit_refs.into_iter().flatten().collect();
    if !plan.same_region(
        &std::iter::once(label_ref)
            .chain(refs.iter().copied())
            .collect::<Vec<_>>(),
    ) || !atoms.get(&label_ref).is_some_and(inline_atom)
    {
        return false;
    }
    if members.iter().any(|&key| {
        row_unit(key, plan, context).is_none_or(|unit| {
            let bounds = &unit.bounds;
            bounds.space != label.space
                || label.vertical_overlap(bounds) < 0.6
                || bounds.x < label.right() - 0.2 * label.width
        })
    }) {
        return false;
    }
    refs.iter().all(|id| atoms.get(id).is_some_and(inline_atom))
}

pub(super) fn select_templates(plan: &mut GeometricPlan, context: &GeometricContext<'_>) {
    if !plan.enabled {
        return;
    }
    for id in plan.active_ids().collect::<Vec<_>>() {
        let block = plan.block(id).unwrap();
        if block.kind != "field" || block.primary_ref.is_some() {
            continue;
        }
        let Some(label) = block.label_ref else {
            continue;
        };
        let style = if caption_label(context, label) {
            "caption"
        } else {
            "atom"
        };
        let mut layout = if block.field_target_ref.is_none() && inline_field(plan, context, block, label) {
            "field-inline"
        } else {
            "fields"
        };
        if block.visual_unit
            && block.field_target_ref.is_some()
            && context.atoms.is_some_and(|atoms| {
                block
                    .member_roles
                    .iter()
                    .all(|(id, _)| atoms.get(id).is_some_and(inline_atom))
            })
        {
            layout = "control-unit";
        }
        let block = plan.block_mut(id).unwrap();
        block.layout = layout;
        block.heading_style = style;
        let decision = format!("geometric-field-template:{}:{layout}:{style}", block.key);
        plan.decisions.push(decision);
    }
}
