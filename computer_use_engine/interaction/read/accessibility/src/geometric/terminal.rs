// Copyright (c) Huawei Technologies Co., Ltd. 2026. All rights reserved.

//! Choose final compact member, record and path structures before rendering.

use std::collections::HashMap;

use super::{
    alignment::{analyze, LayoutItem},
    block_key, field_text, role,
    rows::single_line,
    same_prefix,
    structure::{child_relations, confirmed_children, known_control},
    synthetic_key, Block, BlockId, GeometricContext, GeometricPlan, GeometryError, NodeId, Result, Value,
};
use crate::{
    diff::python_str_repr,
    render::text::{float, JsonFormat},
};

fn reference<'a>(context: &'a GeometricContext<'_>, id: NodeId) -> &'a str {
    &context.projection.node(id).expect("retained layout source").reference
}

fn refs_key(kind: &str, parent: &Block, refs: &[NodeId], context: &GeometricContext<'_>) -> String {
    let refs = refs.iter().map(|&id| reference(context, id)).collect::<Vec<_>>();
    synthetic_key(kind, &parent.key, &refs)
}

fn refs_label(context: &GeometricContext<'_>, refs: &[NodeId]) -> String {
    refs.iter()
        .map(|&id| reference(context, id))
        .collect::<Vec<_>>()
        .join(",")
}

fn bytes_repr_cost(value: &[u8]) -> usize {
    let quote = if value.contains(&b'\'') && !value.contains(&b'"') {
        b'"'
    } else {
        b'\''
    };
    3 + value
        .iter()
        .map(|&byte| match byte {
            b'\\' | b'\n' | b'\r' | b'\t' => 2,
            value if value == quote => 2,
            b' '..=b'~' => 1,
            _ => 4,
        })
        .sum::<usize>()
}

/// Source values have already passed the original freeze step: sequences are
/// tuples and nested records have the original mappingproxy repr wrapper.
fn string_cost(value: &Value, nested: bool) -> usize {
    match value {
        Value::Null | Value::Bool(true) => 4,
        Value::Bool(false) => 5,
        Value::Int(value) => value.to_string().len(),
        Value::BigInt(value) => value.len(),
        Value::Float(value) if value.is_finite() => float(*value, JsonFormat::Literal).len(),
        Value::Float(value) if value.is_infinite() && value.is_sign_negative() => 4,
        Value::Float(_) => 3,
        Value::Text(value) if nested => python_str_repr(value).chars().count(),
        Value::Text(value) => value.chars().count(),
        Value::Bytes(value) => bytes_repr_cost(value),
        Value::Seq(values) => {
            2 + if values.len() == 1 {
                1
            } else {
                2 * values.len().saturating_sub(1)
            } + values.iter().map(|value| string_cost(value, true)).sum::<usize>()
        }
        Value::Map(values) => {
            2 + if nested { 14 } else { 0 }
                + 2 * values.len().saturating_sub(1)
                + values
                    .iter()
                    .map(|(key, value)| python_str_repr(key).chars().count() + 2 + string_cost(value, true))
                    .sum::<usize>()
        }
    }
}

fn empty_atom(plan: &GeometricPlan, context: &GeometricContext<'_>, key: BlockId) -> Option<NodeId> {
    let block = plan.block(key)?;
    let id = block.primary_ref?;
    let evidence = plan.evidence.get(&id)?;
    if !block.children.is_empty()
        || block.layout != "tree"
        || matches!(block.kind, "window" | "region" | "field" | "record" | "table")
        || evidence.incomplete
        || evidence.hidden
    {
        return None;
    }
    let atom = context.atoms?.get(&id)?;
    (atom.slots.is_empty()
        && !atom.has_actions
        && !atom.actions_unavailable
        && !atom.declarations
        && !atom.partial
        && !atom.unexpanded_menu)
        .then_some(id)
}

pub(super) fn group_empty_atoms(plan: &mut GeometricPlan, context: &GeometricContext<'_>) {
    if !plan.enabled || context.atoms.is_none() {
        return;
    }
    let parents = plan
        .active_ids()
        .map(|id| (id, plan.block(id).unwrap().clone()))
        .collect::<Vec<_>>();
    for (owner, parent) in parents {
        if parent.layout != "tree" || matches!(parent.kind, "table" | "record" | "field") {
            continue;
        }
        let children = &parent.children;
        let mut output = Vec::new();
        let mut index = 0;
        while index < children.len() {
            let Some(id) = empty_atom(plan, context, children[index]) else {
                output.push(children[index]);
                index += 1;
                continue;
            };
            let native_parent = context.projection.node(id).and_then(|node| node.parent);
            let mut end = index + 1;
            let mut refs = vec![id];
            while end < children.len() {
                let Some(candidate) = empty_atom(plan, context, children[end]) else {
                    break;
                };
                refs.push(candidate);
                if !same_prefix(context, id, candidate)
                    || context.projection.node(candidate).and_then(|node| node.parent) != native_parent
                    || !plan.same_scope(&refs, context)
                    || !plan.same_region(&refs)
                {
                    refs.pop();
                    break;
                }
                end += 1;
            }
            if refs.len() < 3 {
                output.extend_from_slice(&children[index..end]);
            } else {
                let key = refs_key("compact-list", &parent, &refs, context);
                let mut block = Block::new(key, "collection", None, children[index..end].to_vec());
                block.layout = "compact-list";
                block.shared_prefix_ref = Some(id);
                output.push(plan.insert(block));
                plan.decisions
                    .push(format!("empty-atom-list:{}", refs_label(context, &refs)));
            }
            index = end;
        }
        if output != *children {
            plan.block_mut(owner).unwrap().children = output;
        }
    }
}

fn inline_role(role: &str) -> bool {
    matches!(
        role,
        "button"
            | "menuButton"
            | "link"
            | "checkBox"
            | "radioButton"
            | "switch"
            | "popUpButton"
            | "comboBox"
            | "slider"
            | "incrementor"
            | "closeButton"
            | "minimizeButton"
            | "zoomButton"
            | "row"
            | "menuBarItem"
            | "menuItem"
            | "staticText"
    )
}

fn inline_candidate(plan: &GeometricPlan, context: &GeometricContext<'_>, key: BlockId) -> Option<NodeId> {
    let block = plan.block(key)?;
    let id = block.primary_ref?;
    let node = context.projection.node(id)?;
    let evidence = plan.evidence.get(&id)?;
    let unopened = context
        .atoms
        .and_then(|atoms| atoms.get(&id))
        .is_some_and(|atom| atom.unexpanded_menu);
    (block.children.is_empty()
        && block.layout == "tree"
        && (!evidence.incomplete || unopened)
        && !evidence.hidden
        && single_line(node))
    .then_some(id)
}

fn inline_rows(keys: &[BlockId], plan: &GeometricPlan, context: &GeometricContext<'_>) -> Vec<usize> {
    let fallback = vec![keys.len()];
    let Some(items) = keys
        .iter()
        .map(|&key| {
            let id = plan.block(key)?.primary_ref?;
            Some(LayoutItem {
                key,
                bounds: context.geometry.get(&id)?.clone(),
            })
        })
        .collect::<Option<Vec<_>>>()
    else {
        return fallback;
    };
    let Some(alignment) = analyze(&items, plan) else {
        return fallback;
    };
    let rows = alignment.rows;
    if rows.len() < 2
        || rows.iter().any(|row| row.members.len() < 2)
        || !rows.iter().flat_map(|row| row.members.iter()).eq(keys)
        || rows.windows(2).any(|pair| pair[0].bounds.bottom() > pair[1].bounds.y)
    {
        return fallback;
    }
    rows.iter().map(|row| row.members.len()).collect()
}

pub(super) fn group_inline_members(plan: &mut GeometricPlan, context: &GeometricContext<'_>) {
    if !plan.enabled {
        return;
    }
    let parents = plan
        .active_ids()
        .map(|id| (id, plan.block(id).unwrap().clone()))
        .collect::<Vec<_>>();
    for (owner, parent) in parents {
        if parent.layout != "tree" || matches!(parent.kind, "record" | "table") {
            continue;
        }
        let children = &parent.children;
        let mut output = Vec::new();
        let mut index = 0;
        while index < children.len() {
            let Some(id) = inline_candidate(plan, context, children[index])
                .filter(|id| context.projection.node(*id).is_some_and(|node| inline_role(role(node))))
            else {
                output.push(children[index]);
                index += 1;
                continue;
            };
            let cost = |id| {
                let node = context.projection.node(id).unwrap();
                ["name", "value", "description"]
                    .into_iter()
                    .filter_map(|field| node.field(field))
                    .map(|value| string_cost(value, false))
                    .sum::<usize>()
            };
            let mut chars = cost(id);
            let mut end = index + 1;
            let mut refs = vec![id];
            while end < children.len() {
                let Some(target) = inline_candidate(plan, context, children[end]) else {
                    break;
                };
                refs.push(target);
                let added = cost(target);
                if !same_prefix(context, id, target)
                    || !plan.same_scope(&refs, context)
                    || !plan.same_region(&refs)
                    || chars + added > 240
                {
                    refs.pop();
                    break;
                }
                chars += added;
                end += 1;
            }
            let rows = inline_rows(&children[index..end], plan, context);
            let visual = rows.len() > 1;
            let mut offset = 0;
            for size in rows {
                let members = &children[index + offset..index + offset + size];
                let refs = &refs[offset..offset + size];
                if size < 3 {
                    output.extend_from_slice(members);
                } else {
                    let key = refs_key("inline", &parent, refs, context);
                    let mut block = Block::new(key, "collection", None, members.to_vec());
                    block.layout = "inline";
                    block.shared_prefix_ref = Some(refs[0]);
                    block.origin = if visual { "visual" } else { "native" };
                    output.push(plan.insert(block));
                    plan.decisions
                        .push(format!("inline-members:{}", refs_label(context, refs)));
                }
                offset += size;
            }
            index = end;
        }
        if output != *children {
            plan.block_mut(owner).unwrap().children = output;
        }
    }
}

pub(super) fn share_inline_headers(plan: &mut GeometricPlan, context: &GeometricContext<'_>) -> Result<()> {
    if !plan.enabled {
        return Ok(());
    }
    let parents = plan
        .active_ids()
        .map(|id| (id, plan.block(id).unwrap().clone()))
        .collect::<Vec<_>>();
    for (owner, parent) in parents {
        if parent.layout != "tree" {
            continue;
        }
        let children = &parent.children;
        let mut output = Vec::new();
        let mut index = 0;
        while index < children.len() {
            let first = plan
                .block(children[index])
                .ok_or(GeometryError::MissingBlock(children[index]))?;
            let Some(first_ref) = first.shared_prefix_ref.filter(|_| first.layout == "inline") else {
                output.push(children[index]);
                index += 1;
                continue;
            };
            let mut end = index + 1;
            while end < children.len() {
                let peer = plan
                    .block(children[end])
                    .ok_or(GeometryError::MissingBlock(children[end]))?;
                let Some(peer_ref) = peer.shared_prefix_ref.filter(|_| peer.layout == "inline") else {
                    break;
                };
                let refs = [first_ref, peer_ref];
                if !plan.same_scope(&refs, context)
                    || !plan.same_region(&refs)
                    || !same_prefix(context, first_ref, peer_ref)
                {
                    break;
                }
                end += 1;
            }
            if end == index + 1 {
                output.push(children[index]);
            } else {
                let key = block_key(plan, "inline-rows", owner, &children[index..end])?;
                let mut block = Block::new(key.clone(), "collection", None, children[index..end].to_vec());
                block.layout = "rows";
                block.shared_prefix_ref = Some(first_ref);
                block.origin = first.origin;
                output.push(plan.insert(block));
                plan.decisions.push(format!("shared-row-header:{key}"));
            }
            index = end;
        }
        if output != *children {
            plan.block_mut(owner).unwrap().children = output;
        }
    }
    Ok(())
}

fn inline_control(context: &GeometricContext<'_>, block: &Block) -> Option<NodeId> {
    let id = block.primary_ref?;
    if !block.children.is_empty() || block.layout != "tree" || !known_control(context, id) {
        return None;
    }
    let atom = context.atoms?.get(&id)?;
    (!atom.partial
        && !atom.declarations
        && atom.slots.iter().all(|slot| {
            !slot.block
                && !slot.preview
                && slot
                    .value
                    .as_str()
                    .is_none_or(|value| value.chars().count() <= 100 && !value.contains(['\r', '\n', '\t']))
        }))
    .then_some(id)
}

pub(super) fn plan_record_members(plan: &mut GeometricPlan, context: &GeometricContext<'_>) -> Result<()> {
    let Some(atoms) = context.atoms.filter(|_| plan.enabled) else {
        return Ok(());
    };
    let mut pending: Vec<_> = plan
        .active_ids()
        .filter_map(|id| plan.block(id))
        .filter(|block| block.layout == "record-blocks")
        .flat_map(|block| block.children.iter().copied())
        .collect();
    let relation_index = child_relations(&plan.relations);
    let mut order = Vec::new();
    while let Some(key) = pending.pop() {
        order.push(key);
        pending.extend(
            plan.block(key)
                .ok_or(GeometryError::MissingBlock(key))?
                .children
                .iter()
                .rev()
                .copied(),
        );
    }
    for key in order.into_iter().rev() {
        let block = plan.block(key).ok_or(GeometryError::MissingBlock(key))?.clone();
        if block.layout != "tree" || matches!(block.kind, "window" | "field" | "table" | "region") {
            continue;
        }
        let mut children = Vec::new();
        let mut index = 0;
        while index < block.children.len() {
            let first = block.children[index];
            let Some(first_ref) = plan.block(first).and_then(|block| inline_control(context, block)) else {
                children.push(first);
                index += 1;
                continue;
            };
            let mut end = index + 1;
            let mut refs = vec![first_ref];
            while end < block.children.len() {
                let Some(target) = plan
                    .block(block.children[end])
                    .and_then(|block| inline_control(context, block))
                else {
                    break;
                };
                let pair = [first_ref, target];
                if !same_prefix(context, first_ref, target)
                    || !plan.same_region(&pair)
                    || !plan.same_scope(&pair, context)
                {
                    break;
                }
                refs.push(target);
                end += 1;
            }
            if refs.len() < 2 {
                children.push(first);
            } else {
                let name = refs_key("record-controls", &block, &refs, context);
                let mut group = Block::new(name, "collection", None, block.children[index..end].to_vec());
                group.layout = "inline";
                group.shared_prefix_ref = Some(first_ref);
                children.push(plan.insert(group));
                plan.decisions
                    .push(format!("record-controls:{}", refs_label(context, &refs)));
            }
            index = end;
        }
        plan.block_mut(key).unwrap().children = children;
        let updated = plan.block(key).unwrap();
        let Some(id) = updated.primary_ref else {
            continue;
        };
        let node = context
            .projection
            .node(id)
            .ok_or(GeometryError::Invalid("missing record source"))?;
        if updated.kind == "record" || role(node) != "group" || updated.children.len() != 1 {
            continue;
        }
        let child = plan
            .block(updated.children[0])
            .ok_or(GeometryError::MissingBlock(updated.children[0]))?;
        let Some(target) = child.primary_ref else {
            continue;
        };
        if matches!(child.kind, "window" | "region" | "field" | "record" | "table")
            || !matches!(child.layout, "tree" | "record-path")
            || node.children != [target]
            || context
                .projection
                .node(target)
                .is_none_or(|node| node.parent != Some(id))
            || !confirmed_children(&plan.relations, &relation_index, id, &[target])
            || !plan.same_scope(&[id, target], context)
        {
            continue;
        }
        if atoms.get(&id).is_some_and(|atom| {
            !atom.partial
                && !atom.declarations
                && atom.slots.iter().all(|slot| {
                    !slot.block
                        && slot
                            .value
                            .as_str()
                            .is_none_or(|value| value.chars().count() <= 100 && !value.contains(['\n', '\r', '\t']))
                })
        }) {
            plan.block_mut(key).unwrap().layout = "record-path";
            plan.decisions.push(format!(
                "record-path:{}>{}",
                reference(context, id),
                reference(context, target)
            ));
        }
    }
    Ok(())
}

fn single_atom_cost(context: &GeometricContext<'_>, id: NodeId) -> Option<usize> {
    let atom = context.atoms?.get(&id)?;
    if atom.partial || atom.declarations {
        return None;
    }
    let mut cost = reference(context, id).chars().count() + atom.role_label.chars().count() + 2;
    cost += atom
        .state_labels
        .iter()
        .map(|label| label.chars().count() + 2)
        .sum::<usize>();
    for slot in &atom.slots {
        if slot.block
            || slot.preview
            || slot
                .value
                .as_str()
                .is_some_and(|value| value.contains(['\r', '\n', '\t']))
        {
            return None;
        }
        cost += string_cost(&slot.value, false) + slot.label.map_or(0, |label| label.chars().count()) + 4;
    }
    Some(cost + atom.action_cost + if atom.actions_unavailable { 34 } else { 0 })
}

pub(super) fn plan_visual_chains(plan: &mut GeometricPlan, context: &GeometricContext<'_>) -> Result<()> {
    if !plan.enabled || context.atoms.is_none() {
        return Ok(());
    }
    let mut pending = plan.roots.iter().rev().copied().collect::<Vec<_>>();
    let mut order = Vec::new();
    while let Some(key) = pending.pop() {
        order.push(key);
        pending.extend(
            plan.block(key)
                .ok_or(GeometryError::MissingBlock(key))?
                .children
                .iter()
                .rev()
                .copied(),
        );
    }
    let mut costs = HashMap::<BlockId, usize>::new();
    for key in order.into_iter().rev() {
        let block = plan.block(key).ok_or(GeometryError::MissingBlock(key))?;
        let Some(id) = block.primary_ref else {
            continue;
        };
        let Some(cost) = single_atom_cost(context, id) else {
            continue;
        };
        costs.insert(key, cost);
        if block.layout != "tree"
            || matches!(block.kind, "window" | "region" | "table" | "field" | "record")
            || block.children.len() != 1
        {
            continue;
        }
        let child_key = block.children[0];
        let child = plan.block(child_key).ok_or(GeometryError::MissingBlock(child_key))?;
        let Some(target) = child.primary_ref else {
            continue;
        };
        let node = context
            .projection
            .node(id)
            .ok_or(GeometryError::Invalid("visual chain source"))?;
        let Some(child_cost) = costs.get(&child_key).copied() else {
            continue;
        };
        if !matches!(child.layout, "tree" | "chain")
            || matches!(child.kind, "window" | "region" | "table" | "field" | "record")
            || field_text(node, "visualChainChild") != Some(reference(context, target))
            || node.children != [target]
            || [id, target]
                .iter()
                .any(|id| plan.evidence.get(id).is_none_or(|item| item.incomplete || item.hidden))
            || !plan.same_scope(&[id, target], context)
        {
            continue;
        }
        let combined = cost + child_cost + 3;
        if combined > 280 {
            continue;
        }
        plan.block_mut(key).unwrap().layout = "chain";
        costs.insert(key, combined);
        plan.decisions.push(format!(
            "visual-chain:{}>{}",
            reference(context, id),
            reference(context, target)
        ));
    }
    Ok(())
}
