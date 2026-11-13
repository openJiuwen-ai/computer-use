// Copyright (c) Huawei Technologies Co., Ltd. 2026. All rights reserved.

use std::cmp::Reverse;
use std::collections::{BinaryHeap, HashMap, HashSet};

use super::{
    alignment::{analyze, cmp, compare_keys, key_name, Alignment, LayoutItem},
    evidence::{ordered_ancestry, reading_scope, rtl, rtl_text},
    field_text, role, seq, truth, BlockId, Box, GeometricContext, GeometricPlan, NodeId, Relation, Value,
};

pub(crate) fn stable_order(keys: &[BlockId], edges: &[(BlockId, BlockId)]) -> Option<Vec<BlockId>> {
    let positions: HashMap<_, _> = keys.iter().enumerate().map(|(i, key)| (*key, i)).collect();
    let mut incoming = vec![0; keys.len()];
    let mut outgoing = vec![Vec::new(); keys.len()];
    for &(before, after) in edges {
        let (Some(&before), Some(&after)) = (positions.get(&before), positions.get(&after)) else {
            continue;
        };
        if before != after && !outgoing[before].contains(&after) {
            outgoing[before].push(after);
            incoming[after] += 1;
        }
    }
    let mut ready: BinaryHeap<_> = incoming
        .iter()
        .enumerate()
        .filter_map(|(index, &count)| (count == 0).then_some(Reverse(index)))
        .collect();
    let mut result = Vec::with_capacity(keys.len());
    while let Some(Reverse(index)) = ready.pop() {
        result.push(keys[index]);
        for &after in &outgoing[index] {
            incoming[after] -= 1;
            if incoming[after] == 0 {
                ready.push(Reverse(after));
            }
        }
    }
    (result.len() == keys.len()).then_some(result)
}

pub(crate) fn native_edges(
    keys: &[BlockId],
    owners: &HashMap<NodeId, BlockId>,
    relations: &[Relation],
) -> Option<Vec<(BlockId, BlockId)>> {
    let mut edges = Vec::new();
    for relation in relations {
        if !matches!(relation.kind.as_str(), "flows_to" | "flows_from") {
            continue;
        }
        let Some(&source) = owners.get(&relation.source) else {
            continue;
        };
        if !relation.complete || !relation.confirmed {
            return None;
        }
        for target in &relation.targets {
            let Some(&target) = owners.get(target) else { continue };
            let edge = if relation.kind == "flows_from" {
                (target, source)
            } else {
                (source, target)
            };
            if edge.0 != edge.1 && !edges.contains(&edge) {
                edges.push(edge);
            }
        }
    }
    stable_order(keys, &edges).map(|_| edges)
}

pub(crate) fn external_flow(owners: &HashMap<NodeId, BlockId>, relations: &[Relation]) -> bool {
    relations
        .iter()
        .filter(|relation| matches!(relation.kind.as_str(), "flows_to" | "flows_from"))
        .any(|relation| {
            let endpoints = std::iter::once(&relation.source).chain(&relation.targets);
            endpoints.clone().any(|id| owners.contains_key(id)) && !endpoints.clone().all(|id| owners.contains_key(id))
        })
}

pub(crate) fn add_compatible_edge(edges: &mut Vec<(BlockId, BlockId)>, before: BlockId, after: BlockId) {
    let mut pending = vec![after];
    let mut seen = HashSet::new();
    while let Some(key) = pending.pop() {
        if key == before {
            return;
        }
        if seen.insert(key) {
            pending.extend(edges.iter().filter_map(|&(left, right)| (left == key).then_some(right)));
        }
    }
    if !edges.contains(&(before, after)) {
        edges.push((before, after));
    }
}

fn original_role<'a>(context: &'a GeometricContext<'_>, id: NodeId) -> Option<&'a str> {
    let node = context.projection.node(id)?;
    node.native_fields
        .iter()
        .find_map(|(key, value)| (key == "role").then(|| value.as_str()).flatten())
}

fn ordered_outside_unit(context: &GeometricContext<'_>, id: NodeId) -> bool {
    let mut seen = HashSet::from([id]);
    let mut current = context.projection.node(id).and_then(|node| node.parent);
    while let Some(id) = current {
        if !seen.insert(id) {
            return true;
        }
        let Some(role) = original_role(context, id) else {
            return true;
        };
        if matches!(
            role,
            "list"
                | "outline"
                | "table"
                | "row"
                | "cell"
                | "column"
                | "document"
                | "menu"
                | "menuBar"
                | "toolbar"
                | "tabGroup"
        ) {
            return true;
        }
        if matches!(role, "window" | "sheet") {
            return false;
        }
        current = context.projection.node(id).and_then(|node| node.parent);
    }
    false
}

fn unwrap_source(context: &GeometricContext<'_>, mut id: NodeId) -> Option<NodeId> {
    let mut seen = HashSet::new();
    while seen.insert(id) {
        let node = context.projection.node(id)?;
        let field = |name| {
            node.native_fields
                .iter()
                .find_map(|(key, value)| (key == name).then_some(value))
        };
        if !matches!(original_role(context, id)?, "group" | "webArea")
            || node.native_children.len() != 1
            || ["actions", "selected", "editable"]
                .iter()
                .any(|name| truth(field(name)))
        {
            return Some(id);
        }
        id = node.native_children[0];
    }
    None
}

fn declares_prose(context: &GeometricContext<'_>, id: NodeId) -> bool {
    let Some(node) = unwrap_source(context, id).and_then(|id| context.projection.node(id)) else {
        return false;
    };
    let mut headings = 0;
    let mut body = false;
    for role in node
        .native_children
        .iter()
        .filter_map(|&child| unwrap_source(context, child).and_then(|id| original_role(context, id)))
    {
        match role {
            "heading" => headings += 1,
            "staticText" | "paragraph" | "list" | "code" => body = true,
            "group" => {}
            _ => return false,
        }
    }
    headings >= 2 && body
}

pub(crate) fn document_flow_scope(plan: &GeometricPlan, context: &GeometricContext<'_>, key: BlockId) -> bool {
    let Some(block) = plan.block(key) else { return false };
    let mut current = block
        .primary_ref
        .or(block.label_ref)
        .or_else(|| plan.block_sources(key).first().copied());
    let mut seen = HashSet::new();
    while let Some(id) = current.filter(|id| seen.insert(*id)) {
        let Some(role) = original_role(context, id) else {
            return false;
        };
        if role == "document" || declares_prose(context, id) {
            return true;
        }
        if matches!(role, "window" | "sheet") {
            return false;
        }
        current = context.projection.node(id).and_then(|node| node.parent);
    }
    false
}

fn measured_units(
    keys: impl Iterator<Item = BlockId>,
    plan: &GeometricPlan,
    context: &GeometricContext<'_>,
) -> Vec<LayoutItem> {
    let mut result = Vec::new();
    for key in keys {
        let Some(block) = plan.block(key).filter(|block| block.layout != "source-only") else {
            continue;
        };
        let refs = plan.block_sources(key);
        let Some(anchor) = block.primary_ref.or_else(|| refs.first().copied()) else {
            continue;
        };
        let Some(node) = context.projection.node(anchor) else {
            continue;
        };
        if plan.evidence.get(&anchor).is_none_or(|evidence| evidence.hidden)
            || node.field("modal") == Some(&Value::Bool(true))
            || matches!(
                field_text(node, "subrole"),
                Some(
                    "dialog"
                        | "systemDialog"
                        | "popover"
                        | "closeButton"
                        | "minimizeButton"
                        | "zoomButton"
                        | "fullScreenButton"
                        | "close"
                        | "minimize"
                        | "zoom"
                        | "fullScreen"
                )
            )
            || matches!(role(node), "closeButton" | "minimizeButton" | "zoomButton")
            || ordered_outside_unit(context, anchor)
            || !plan.same_scope(&refs, context)
            || ["name", "value", "description", "help", "placeholder"]
                .into_iter()
                .filter_map(|name| field_text(node, name))
                .any(rtl)
        {
            continue;
        }
        if let Some(bounds) = plan.block_bounds(key, context.geometry) {
            result.push(LayoutItem { key, bounds });
        }
    }
    let mut counts = HashMap::new();
    for item in &result {
        *counts.entry(box_key(&item.bounds)).or_insert(0_usize) += 1;
    }
    let ambiguous: HashSet<_> = result
        .iter()
        .filter(|item| counts[&box_key(&item.bounds)] > 1)
        .map(|item| item.key)
        .collect();
    result.retain(|item| !ambiguous.contains(&item.key));
    result
}

fn box_key(bounds: &Box) -> (&str, [u64; 4]) {
    let bits = |value: f64| if value == 0.0 { 0 } else { value.to_bits() };
    (
        &bounds.space,
        [bounds.x, bounds.y, bounds.width, bounds.height].map(bits),
    )
}

pub(crate) fn owners(plan: &GeometricPlan, children: &[BlockId]) -> HashMap<NodeId, BlockId> {
    children
        .iter()
        .flat_map(|&child| plan.block_sources(child).into_iter().map(move |id| (id, child)))
        .collect()
}

pub(crate) fn comparable_members(
    keys: &[BlockId],
    plan: &GeometricPlan,
    context: &GeometricContext<'_>,
) -> Option<HashMap<BlockId, Box>> {
    let refs: Vec<_> = keys.iter().flat_map(|&key| plan.block_sources(key)).collect();
    let first = *refs.first()?;
    let scope = reading_scope(plan, context, first);
    if !plan.same_scope(&refs, context)
        || refs.iter().any(|&id| {
            plan.evidence.get(&id).is_none_or(|node| node.hidden || node.incomplete)
                || ordered_ancestry(plan, context, id, true, true)
                || rtl_text(context, id)
                || reading_scope(plan, context, id) != scope
        })
    {
        return None;
    }
    let measured = keys
        .iter()
        .map(|&key| Some((key, plan.block_bounds(key, context.geometry)?)))
        .collect::<Option<HashMap<_, _>>>()?;
    let space = measured.values().next()?.space.as_str();
    measured
        .values()
        .all(|bounds| bounds.space == space)
        .then_some(measured)
}

pub(crate) fn linked_members(
    owners: &HashMap<NodeId, BlockId>,
    plan: &GeometricPlan,
    context: &GeometricContext<'_>,
) -> bool {
    owners.iter().any(|(&reference, owner)| {
        let Some(node) = context.projection.node(reference) else {
            return false;
        };
        ["titleRef", "visualLabelRef"]
            .iter()
            .filter_map(|key| node.field(key))
            .chain(seq(node.field("labelTargets")))
            .any(|item| {
                item.as_str()
                    .and_then(|alias| context.projection.by_reference(alias))
                    .and_then(|node| owners.get(&node.id))
                    .is_some_and(|target| target != owner)
            })
    }) || plan.relations.iter().any(|relation| {
        matches!(
            relation.kind.as_str(),
            "labelled_by" | "labels" | "described_by" | "describes"
        ) && owners.get(&relation.source).is_some_and(|source| {
            relation
                .targets
                .iter()
                .filter_map(|target| owners.get(target))
                .any(|target| target != source)
        })
    })
}

#[derive(Clone, Debug)]
pub(crate) struct ScopePlan {
    pub(crate) items: Vec<LayoutItem>,
    pub(crate) alignment: Alignment,
    pub(crate) before: Vec<(BlockId, BlockId)>,
    pub(crate) keep_together: Vec<Vec<BlockId>>,
    pub(crate) source_order: Vec<BlockId>,
    pub(crate) unplaced: Vec<BlockId>,
    pub(crate) segments: Vec<Vec<BlockId>>,
}

pub(crate) fn scope_plan(plan: &GeometricPlan, context: &GeometricContext<'_>, owner: BlockId) -> Option<ScopePlan> {
    let parent = plan.block(owner)?;
    if !plan.enabled
        || parent.children.len() < 2
        || !matches!(parent.layout, "tree" | "fields" | "field-inline")
        || matches!(parent.kind, "table" | "collection" | "record")
        || parent.visual_unit
        || document_flow_scope(plan, context, owner)
    {
        return None;
    }
    if let Some(reference) = parent.primary_ref {
        let node = context.projection.node(reference)?;
        if !matches!(
            role(node),
            "window"
                | "sheet"
                | "group"
                | "splitGroup"
                | "tabPanel"
                | "scrollArea"
                | "navigation"
                | "webArea"
                | "application"
        ) || plan.evidence.get(&reference)?.hidden
        {
            return None;
        }
    } else if !matches!(parent.kind, "field" | "region") {
        return None;
    }
    let keys = &parent.children;
    let sources: HashMap<_, _> = keys.iter().map(|&child| (child, plan.block_sources(child))).collect();
    let owners: HashMap<_, _> = keys
        .iter()
        .flat_map(|child| sources[child].iter().map(move |&id| (id, *child)))
        .collect();
    if !plan.same_scope(&owners.keys().copied().collect::<Vec<_>>(), context) {
        return None;
    }
    let mut items = measured_units(
        keys.iter().copied().filter(|&child| {
            parent.label_ref.is_none() || plan.block(child).and_then(|block| block.primary_ref) != parent.label_ref
        }),
        plan,
        context,
    );
    let space = items.first()?.bounds.space.clone();
    if items.iter().any(|item| item.bounds.space != space) {
        return None;
    }
    if let Some(bounds) = parent.primary_ref.and_then(|id| context.geometry.get(&id)) {
        items.retain(|item| bounds.contains(&item.bounds));
        if items.is_empty() {
            return None;
        }
    }
    let alignment = analyze(&items, plan)?;
    let bands: HashMap<_, _> = alignment
        .rows
        .iter()
        .enumerate()
        .flat_map(|(i, row)| row.members.iter().map(move |&key| (key, i)))
        .collect();
    for (i, left) in items.iter().enumerate() {
        if items[i + 1..].iter().any(|right| {
            bands[&left.key] != bands[&right.key]
                && left.bounds.intersection_area(&right.bounds) > 0.0
                && !left.bounds.contains(&right.bounds)
                && !right.bounds.contains(&left.bounds)
        }) {
            return None;
        }
    }
    let before = native_edges(keys, &owners, &plan.relations)?;
    if external_flow(&owners, &plan.relations) {
        return None;
    }
    let mut protected = HashSet::<Vec<BlockId>>::new();
    let mut protect = |members: Vec<BlockId>| {
        let mut members = members
            .into_iter()
            .collect::<HashSet<_>>()
            .into_iter()
            .collect::<Vec<_>>();
        members.sort_by(|&a, &b| key_name(plan, a).cmp(key_name(plan, b)));
        if members.len() > 1 {
            protected.insert(members);
        }
    };
    for relation in &plan.relations {
        if matches!(
            relation.kind.as_str(),
            "labelled_by" | "labels" | "described_by" | "describes"
        ) && relation.complete
            && relation.confirmed
        {
            protect(
                std::iter::once(&relation.source)
                    .chain(&relation.targets)
                    .filter_map(|id| owners.get(id).copied())
                    .collect(),
            );
        }
    }
    for (&id, &owner) in &owners {
        let node = context.projection.node(id)?;
        for target in ["titleRef", "visualLabelRef"]
            .iter()
            .filter_map(|key| node.field(key))
            .chain(seq(node.field("labelTargets")))
        {
            if let Some(&target) = target
                .as_str()
                .and_then(|alias| context.projection.by_reference(alias))
                .and_then(|node| owners.get(&node.id))
            {
                protect(vec![owner, target]);
            }
        }
    }
    if let Some(node) = parent.primary_ref.and_then(|id| context.projection.node(id)) {
        for group in seq(node.field("layoutGroups")) {
            protect(
                seq(group.get("members"))
                    .iter()
                    .filter_map(|member| {
                        member
                            .as_str()
                            .and_then(|alias| context.projection.by_reference(alias))
                            .and_then(|node| owners.get(&node.id).copied())
                    })
                    .collect(),
            );
        }
    }
    let mut keep_together: Vec<_> = protected.into_iter().collect();
    keep_together.sort_by(|a, b| compare_keys(plan, a, b));
    let mut segments = Vec::<Vec<BlockId>>::new();
    let mut current = Vec::new();
    let mut unplaced = Vec::new();
    for &child in keys {
        if plan.block(child)?.layout == "source-only" {
            continue;
        }
        if bands.contains_key(&child) {
            if !current.is_empty()
                && !plan.same_region(
                    &current
                        .iter()
                        .chain(std::iter::once(&child))
                        .flat_map(|member| sources[member].iter().copied())
                        .collect::<Vec<_>>(),
                )
            {
                segments.push(std::mem::take(&mut current));
            }
            current.push(child);
        } else {
            unplaced.push(child);
            if !current.is_empty() {
                segments.push(std::mem::take(&mut current));
            }
        }
    }
    if !current.is_empty() {
        segments.push(current);
    }
    let segment_of: HashMap<_, _> = segments
        .iter()
        .enumerate()
        .flat_map(|(i, segment)| segment.iter().map(move |&key| (key, i)))
        .collect();
    let positions: HashMap<_, _> = keys.iter().enumerate().map(|(i, &key)| (key, i)).collect();
    if before
        .iter()
        .any(|&(a, b)| segment_of.get(&a) != segment_of.get(&b) && positions[&a] > positions[&b])
    {
        return None;
    }
    Some(ScopePlan {
        items,
        alignment,
        before,
        keep_together,
        source_order: keys.clone(),
        unplaced,
        segments,
    })
}

pub(crate) fn pane_members(keys: &[BlockId], plan: &GeometricPlan, context: &GeometricContext<'_>) -> Vec<BlockId> {
    keys.iter()
        .copied()
        .filter(|&key| {
            let Some(block) = plan.block(key).filter(|block| block.layout != "source-only") else {
                return false;
            };
            let Some(id) = block.primary_ref else { return false };
            let Some(node) = context.projection.node(id) else {
                return false;
            };
            if plan.evidence.get(&id).is_none_or(|evidence| evidence.hidden)
                || node.field("modal") == Some(&Value::Bool(true))
            {
                return false;
            }
            matches!(role(node), "scrollArea" | "splitGroup" | "webArea")
                || block.kind == "region" && matches!(block.position.as_deref(), Some("left" | "middle" | "right"))
        })
        .collect()
}

pub(crate) fn pane_columns(keys: &[BlockId], plan: &GeometricPlan, context: &GeometricContext<'_>) -> Vec<BlockId> {
    let mut panes: Vec<_> = pane_members(keys, plan, context)
        .into_iter()
        .filter_map(|key| Some((key, plan.block_bounds(key, context.geometry)?)))
        .collect();
    if panes.len() < 2
        || panes.iter().any(|(_, bounds)| bounds.space != panes[0].1.space)
        || !plan.same_scope(
            &panes
                .iter()
                .filter_map(|(key, _)| plan.block(*key)?.primary_ref)
                .collect::<Vec<_>>(),
            context,
        )
    {
        return Vec::new();
    }
    panes
        .sort_by(|(a, left), (b, right)| cmp(left.x, right.x).then_with(|| key_name(plan, *a).cmp(key_name(plan, *b))));
    for (i, (_, left)) in panes.iter().enumerate() {
        if panes[i + 1..].iter().any(|(_, right)| {
            left.bottom().min(right.bottom()) - left.y.max(right.y) < 0.6 * left.height.min(right.height)
                || left.right() > right.x + 0.05 * left.width.min(right.width)
        }) {
            return Vec::new();
        }
    }
    panes.into_iter().map(|(key, _)| key).collect()
}
