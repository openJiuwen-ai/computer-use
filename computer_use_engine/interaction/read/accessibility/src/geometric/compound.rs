// Copyright (c) Huawei Technologies Co., Ltd. 2026. All rights reserved.

use std::collections::{HashMap, HashSet};

use super::{
    evidence::original_parent, field_text, role, seq, synthetic_key, Block, BlockId, Box, GeometricContext,
    GeometricPlan, GeometryError, NodeId, Result, Value,
};

fn control(role: &str) -> bool {
    matches!(
        role,
        "button"
            | "link"
            | "tab"
            | "menuButton"
            | "popUpButton"
            | "checkBox"
            | "radioButton"
            | "switch"
            | "disclosureTriangle"
    )
}

fn member(role: &str) -> bool {
    control(role) || matches!(role, "group" | "staticText" | "image")
}

fn native_nodes<'a>(context: &'a GeometricContext<'_>) -> Option<&'a [(String, Value)]> {
    context.facts?.observation.get("nodes")?.as_map()
}

fn complete_children<'a>(
    plan: &GeometricPlan,
    context: &'a GeometricContext<'_>,
    id: NodeId,
    child_relations: &HashMap<&str, Vec<&Value>>,
) -> Option<&'a [NodeId]> {
    let node = context.projection.node(id)?;
    if plan.evidence.get(&id)?.incomplete || node.children != node.native_children {
        return None;
    }
    let Some(native) = native_nodes(context) else {
        return Some(&node.children);
    };
    let token = &native.get(id.0)?.0;
    let rows = child_relations.get(token.as_str())?;
    for edge in rows {
        let targets = seq(edge.get("targets"));
        if edge.get("certainty").and_then(Value::as_str) != Some("confirmed")
            || !matches!(edge.get("state").and_then(Value::as_str), Some("complete" | "absent"))
            || edge
                .get("enumeration")
                .is_some_and(|mode| mode.as_str() != Some("enumerated"))
            || edge.get("offset").is_some_and(|offset| offset.as_i64() != Some(0))
            || edge
                .get("total")
                .is_some_and(|total| total.as_i64() != Some(targets.len() as i64))
            || edge.get("positions").is_some_and(|positions| {
                positions.as_seq().is_none_or(|positions| {
                    positions.len() != targets.len()
                        || positions
                            .iter()
                            .enumerate()
                            .any(|(i, pos)| pos.as_i64() != Some(i as i64))
                })
            })
            || node.children.len() != targets.len()
            || node
                .children
                .iter()
                .zip(targets)
                .any(|(id, target)| native.get(id.0).map(|(name, _)| name.as_str()) != target.as_str())
        {
            return None;
        }
    }
    Some(&node.children)
}

fn confirmed_edge(plan: &GeometricPlan, context: &GeometricContext<'_>, parent: NodeId, child: NodeId) -> bool {
    plan.projected_parents.get(&child) == Some(&parent)
        && context
            .projection
            .node(child)
            .is_some_and(|node| node.parent == Some(parent))
        && plan.evidence.get(&child).map(|node| node.scope) == plan.evidence.get(&parent).map(|node| node.scope)
        && original_parent(plan, child) == (Some(parent), true)
}

fn close_wrapper(wrapper: &Box, target: &Box) -> bool {
    wrapper.comparable(target)
        && wrapper.overlap(target) >= 0.95
        && wrapper.area().max(target.area()) / wrapper.area().min(target.area()) <= 1.5
        && wrapper.width.max(target.width) / wrapper.width.min(target.width) <= 1.5
        && wrapper.height.max(target.height) / wrapper.height.min(target.height) <= 1.5
}

fn contained_caption(caption: &Box, target: &Box) -> bool {
    let padding = 0.1 * target.width.min(target.height);
    caption.comparable(target)
        && caption.x >= target.x - padding
        && caption.y >= target.y - padding
        && caption.right() <= target.right() + padding
        && caption.bottom() <= target.bottom() + padding
}

fn one_window(plan: &GeometricPlan, context: &GeometricContext<'_>, refs: &[NodeId]) -> bool {
    if !plan.same_scope(refs, context) {
        return false;
    }
    let Some(native) = native_nodes(context) else {
        return true;
    };
    let mut windows = HashSet::new();
    for id in refs {
        let Some((_, node)) = native.get(id.0) else {
            return false;
        };
        let relations = node.get("relations").and_then(Value::as_map).unwrap_or_default();
        for (_, relation) in relations {
            if relation.get("kind").and_then(Value::as_str) != Some("window") {
                continue;
            }
            windows.extend(seq(relation.get("targets")).iter().filter_map(Value::as_str));
        }
    }
    windows.len() <= 1
}

fn compare_bounds(context: &GeometricContext<'_>, refs: &[NodeId], at: usize) -> bool {
    let Some(target) = context.geometry.get(&refs[at]) else {
        return false;
    };
    refs[..at].iter().all(|id| {
        context
            .geometry
            .get(id)
            .is_some_and(|bounds| close_wrapper(bounds, target))
    }) && refs[at + 1..].iter().all(|id| {
        context
            .geometry
            .get(id)
            .is_some_and(|bounds| contained_caption(bounds, target))
    })
}

struct Candidate {
    outer: BlockId,
    target: NodeId,
    members: Vec<BlockId>,
    refs: Vec<NodeId>,
    label: Option<NodeId>,
}

fn candidate(
    plan: &GeometricPlan,
    context: &GeometricContext<'_>,
    key: BlockId,
    child_relations: &HashMap<&str, Vec<&Value>>,
) -> Option<Candidate> {
    let first = plan.block(key)?;
    let first_id = first.primary_ref?;
    if first.source_refs != [first_id]
        || first.layout != "tree"
        || role(context.projection.node(first_id)?) != "group"
        || !context.geometry.contains_key(&first_id)
    {
        return None;
    }
    let mut members = Vec::new();
    let mut refs = Vec::new();
    let mut roles = Vec::new();
    let mut current = key;
    let mut seen = HashSet::new();
    while seen.insert(current) {
        let block = plan.block(current)?;
        let id = block.primary_ref?;
        let node = context.projection.node(id)?;
        let role = role(node);
        let evidence = plan.evidence.get(&id)?;
        if block.source_refs != [id]
            || block.origin != "native"
            || block.layout != "tree"
            || !member(role)
            || evidence.hidden
            || evidence.incomplete
            || evidence.boundary
        {
            return None;
        }
        let children = complete_children(plan, context, id, child_relations)?;
        members.push(current);
        refs.push(id);
        roles.push(role);
        match (children, block.children.as_slice()) {
            ([], []) => break,
            ([child], [key])
                if plan.block(*key)?.primary_ref == Some(*child) && confirmed_edge(plan, context, id, *child) =>
            {
                if seen.contains(key) {
                    return None;
                }
                current = *key;
            }
            _ => return None,
        }
    }
    let controls: Vec<_> = roles
        .iter()
        .enumerate()
        .filter_map(|(index, role)| control(role).then_some(index))
        .collect();
    let [at] = controls.as_slice() else {
        return None;
    };
    let at = *at;
    if at == 0
        || roles[..at].iter().any(|role| *role != "group")
        || roles[at + 1..]
            .iter()
            .any(|role| !matches!(*role, "group" | "staticText" | "image"))
        || !one_window(plan, context, &refs)
        || !compare_bounds(context, &refs, at)
    {
        return None;
    }
    let last = *refs.last()?;
    let node = context.projection.node(last)?;
    let label = (role(node) == "staticText"
        && ["name", "value", "description"]
            .iter()
            .any(|field| field_text(node, field).is_some_and(|value| !value.trim().is_empty())))
    .then_some(last);
    Some(Candidate {
        outer: key,
        target: refs[at],
        members,
        refs,
        label,
    })
}

pub(super) fn compose(plan: &mut GeometricPlan, context: &GeometricContext<'_>) -> Result<()> {
    if !plan.enabled {
        return Ok(());
    }
    let mut child_relations = HashMap::<&str, Vec<&Value>>::new();
    if let Some(facts) = context.facts {
        for edge in seq(facts.facts.get("relations")) {
            if edge.get("kind").and_then(Value::as_str) == Some("children") {
                if let Some(source) = edge.get("source").and_then(Value::as_str) {
                    child_relations.entry(source).or_default().push(edge);
                }
            }
        }
    }
    let parents: HashMap<_, _> = plan
        .active_ids()
        .flat_map(|id| plan.block(id).unwrap().children.iter().map(move |&child| (child, id)))
        .collect();
    let mut selected = Vec::new();
    let mut pending: Vec<_> = plan.roots.iter().rev().copied().collect();
    let mut used = HashSet::new();
    while let Some(key) = pending.pop() {
        if used.contains(&key) {
            continue;
        }
        if let Some(candidate) = candidate(plan, context, key, &child_relations) {
            used.extend(candidate.members.iter().copied());
            selected.push(candidate);
        } else {
            pending.extend(
                plan.block(key)
                    .ok_or(GeometryError::MissingBlock(key))?
                    .children
                    .iter()
                    .rev()
                    .copied(),
            );
        }
    }
    for candidate in selected {
        let outer = plan
            .block(candidate.outer)
            .ok_or(GeometryError::MissingBlock(candidate.outer))?;
        let refs: Vec<_> = candidate
            .refs
            .iter()
            .map(|&id| {
                context
                    .projection
                    .node(id)
                    .map(|node| node.reference.as_str())
                    .ok_or(GeometryError::Invalid("compound source"))
            })
            .collect::<Result<_>>()?;
        let key = synthetic_key("compound-control", &outer.key, &refs);
        let target = candidate
            .members
            .iter()
            .copied()
            .find(|&id| plan.block(id).unwrap().primary_ref == Some(candidate.target))
            .ok_or(GeometryError::Invalid("compound target"))?;
        let ordered: Vec<_> = std::iter::once(target)
            .chain(candidate.members.iter().copied().filter(|id| *id != target))
            .collect();
        for member in candidate.members {
            plan.block_mut(member).unwrap().children.clear();
        }
        let mut block = Block::new(key, "field", None, ordered);
        block.layout = "compound-control";
        block.origin = "visual";
        block.label_ref = candidate.label;
        block.field_target_ref = Some(candidate.target);
        block.member_roles = candidate
            .refs
            .iter()
            .map(|&id| {
                (
                    id,
                    if id == candidate.target {
                        "control"
                    } else if Some(id) == candidate.label {
                        "label"
                    } else {
                        "content"
                    },
                )
            })
            .collect();
        block.visual_unit = true;
        let block = plan.insert(block);
        let children = if let Some(&parent) = parents.get(&candidate.outer) {
            &mut plan
                .block_mut(parent)
                .ok_or(GeometryError::MissingBlock(parent))?
                .children
        } else {
            &mut plan.roots
        };
        for child in children.iter_mut().filter(|id| **id == candidate.outer) {
            *child = block;
        }
        let reference = &context.projection.node(candidate.target).unwrap().reference;
        plan.decisions.push(format!("geometric-compound-control:{reference}"));
    }
    Ok(())
}

pub(super) fn issue(block: &Block, plan: &GeometricPlan, context: &GeometricContext<'_>) -> Option<&'static str> {
    let target = match block.field_target_ref {
        Some(target)
            if block.kind == "field"
                && block.primary_ref.is_none()
                && block.source_refs.is_empty()
                && block.visual_unit =>
        {
            target
        }
        _ => return Some("compound lacks its independently referenced principal control"),
    };
    let members: Vec<_> = block.children.iter().filter_map(|&id| plan.block(id)).collect();
    let refs: Vec<_> = members.iter().filter_map(|member| member.primary_ref).collect();
    let set: HashSet<_> = refs.iter().copied().collect();
    if refs.len() < 2
        || refs.len() != block.children.len()
        || set.len() != refs.len()
        || refs[0] != target
        || members
            .iter()
            .any(|member| !member.children.is_empty() || member.source_refs.as_slice() != member.primary_ref.as_slice())
        || set != block.member_roles.iter().map(|(id, _)| *id).collect()
    {
        return Some("compound lost or duplicated an original auxiliary source");
    }
    let nodes: Vec<_> = refs.iter().filter_map(|&id| context.projection.node(id)).collect();
    if nodes.len() != refs.len()
        || nodes
            .iter()
            .filter(|node| control(role(node)))
            .map(|node| node.id)
            .collect::<Vec<_>>()
            != [target]
        || nodes.iter().any(|node| {
            !member(role(node))
                || node.field("modal") == Some(&Value::Bool(true))
                || matches!(field_text(node, "subrole"), Some("dialog" | "systemDialog" | "popover"))
        })
        || !one_window(plan, context, &refs)
    {
        return Some("compound crossed a semantic or window boundary");
    }
    let roots: Vec<_> = refs
        .iter()
        .filter(|id| {
            plan.projected_parents
                .get(id)
                .is_none_or(|parent| !set.contains(parent))
        })
        .copied()
        .collect();
    let [root] = roots.as_slice() else {
        return Some("compound lacks one original enclosing group");
    };
    if context.projection.node(*root).is_none_or(|node| role(node) != "group") {
        return Some("compound lacks one original enclosing group");
    }
    let mut order = Vec::new();
    let mut current = *root;
    while !order.contains(&current) {
        order.push(current);
        let node = context.projection.node(current).unwrap();
        if node.children != node.native_children || node.children.len() > 1 {
            return Some("compound changed an original native branch");
        }
        let Some(&child) = node.children.first() else {
            break;
        };
        if !set.contains(&child) || !confirmed_edge(plan, context, current, child) {
            return Some("compound crossed an unconfirmed original parent");
        }
        current = child;
    }
    if order.len() != refs.len() || order.iter().copied().collect::<HashSet<_>>() != set {
        return Some("compound omitted part of its original native path");
    }
    let at = order.iter().position(|id| *id == target).unwrap();
    let role_for = |id| role(context.projection.node(id).unwrap());
    if at == 0
        || order[..at].iter().any(|&id| role_for(id) != "group")
        || order[at + 1..]
            .iter()
            .any(|&id| !matches!(role_for(id), "group" | "staticText" | "image"))
    {
        return Some("compound has an independent semantic member beside its principal control");
    }
    if !compare_bounds(context, &order, at) {
        return Some("compound source rectangles no longer describe one visual control");
    }
    None
}
