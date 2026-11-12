// Copyright (c) Huawei Technologies Co., Ltd. 2026. All rights reserved.

use std::collections::{HashMap, HashSet};

use super::{
    alignment::{cmp, same_row, LayoutItem},
    block_key, field_text, ordered_ancestry, reading_scope, role,
    scopes::{
        add_compatible_edge, comparable_members, external_flow, linked_members, native_edges, owners, stable_order,
    },
    seq, source_partition, truth, union, Block, BlockId, Box, GeometricContext, GeometricPlan, GeometryError, NodeId,
    Result, Value,
};
use crate::projection::ProjectionNode;

pub(crate) struct RowUnit {
    pub(crate) target_ref: NodeId,
    pub(crate) sources: Vec<NodeId>,
    pub(crate) bounds: Box,
}

pub(crate) fn inline_unit_refs(key: BlockId, plan: &GeometricPlan) -> Option<Vec<NodeId>> {
    let block = plan.block(key)?;
    if let Some(source) = block.primary_ref {
        if block.layout == "record-path" && block.source_refs == [source] && block.children.len() == 1 {
            let mut sources = vec![source];
            let mut visited = HashSet::from([key]);
            let mut current = block.children[0];
            while visited.insert(current) {
                let child = plan.block(current)?;
                let primary = child.primary_ref?;
                if child.source_refs != [primary] {
                    return None;
                }
                sources.push(primary);
                if child.children.is_empty() {
                    return (sources.iter().copied().collect::<HashSet<_>>().len() == sources.len()).then_some(sources);
                }
                if child.layout != "record-path" || child.children.len() != 1 {
                    return None;
                }
                current = child.children[0];
            }
            return None;
        }
        return (block.children.is_empty()
            && block.source_refs == [source]
            && !matches!(block.kind, "region" | "window" | "table" | "collection" | "record"))
        .then_some(vec![source]);
    }

    let (target, label) = (block.field_target_ref?, block.label_ref?);
    let roles: HashMap<_, _> = block.member_roles.iter().copied().collect();
    if !block.visual_unit
        || block.kind != "field"
        || !block.source_refs.is_empty()
        || !matches!(block.layout, "fields" | "control-unit")
        || target == label
        || roles.len() != block.member_roles.len()
        || roles.get(&target) != Some(&"control")
        || roles.get(&label) != Some(&"label")
        || roles
            .iter()
            .any(|(&id, &role)| id != target && id != label && role != "content")
    {
        return None;
    }
    let mut sources = Vec::new();
    let mut visited = HashSet::new();
    let mut pending = block.children.iter().rev().copied().collect::<Vec<_>>();
    while let Some(child) = pending.pop() {
        if !visited.insert(child) {
            return None;
        }
        let child = plan.block(child)?;
        let primary = child.primary_ref?;
        if child.source_refs != [primary] {
            return None;
        }
        sources.push(primary);
        pending.extend(child.children.iter().rev().copied());
    }
    let owned: HashSet<_> = sources.iter().copied().collect();
    (owned.len() == sources.len() && owned == roles.keys().copied().collect()).then_some(sources)
}

pub(crate) fn single_line(node: &ProjectionNode) -> bool {
    [
        "name",
        "description",
        "value",
        "help",
        "placeholder",
        "selectedText",
        "fullDescription",
        "valueDescription",
        "rangeDescription",
    ]
    .iter()
    .all(|name| field_text(node, name).is_none_or(|value| !value.contains(['\n', '\r', '\t', '|'])))
}

pub(crate) fn row_unit(key: BlockId, plan: &GeometricPlan, context: &GeometricContext<'_>) -> Option<RowUnit> {
    let sources = inline_unit_refs(key, plan)?;
    let scope = reading_scope(plan, context, *sources.first()?);
    if !plan.same_scope(&sources, context) || sources.iter().any(|&id| reading_scope(plan, context, id) != scope) {
        return None;
    }
    let block = plan.block(key)?;
    let path = block.layout == "record-path";
    let mut boxes = Vec::new();
    for &id in &sources {
        let node = context.projection.node(id)?;
        let evidence = plan.evidence.get(&id)?;
        if evidence.boundary && !path
            || evidence.incomplete
            || evidence.hidden
            || !path && ordered_ancestry(plan, context, id, true, true)
            || !single_line(node)
            || matches!(
                field_text(node, "subrole"),
                Some("close" | "minimize" | "zoom" | "fullscreen")
            )
            || matches!(role(node), "closeButton" | "minimizeButton" | "zoomButton")
        {
            return None;
        }
        boxes.push(context.geometry.get(&id).cloned());
        if let Some(atoms) = context.atoms.filter(|_| sources.len() > 1) {
            let atom = atoms.get(&id)?;
            if atom.partial
                || atom.declarations
                || atom.slots.iter().any(|slot| {
                    slot.block
                        || slot
                            .value
                            .as_str()
                            .is_some_and(|text| text.contains(['\r', '\n', '\t', '|']))
                })
            {
                return None;
            }
        }
    }
    Some(RowUnit {
        target_ref: block.primary_ref.or(block.field_target_ref)?,
        sources,
        bounds: union(boxes)?,
    })
}

fn prepared_rows(parent: &Block, plan: &GeometricPlan, context: &GeometricContext<'_>) -> Option<Vec<Vec<BlockId>>> {
    let node = context.projection.node(parent.primary_ref?)?;
    if !parent
        .children
        .iter()
        .any(|&child| plan.block(child).is_some_and(|block| block.visual_unit))
    {
        return None;
    }
    let groups = seq(node.field("layoutGroups"));
    if groups.is_empty()
        || groups
            .iter()
            .any(|item| item.get("kind").and_then(Value::as_str) != Some("row"))
    {
        return None;
    }
    let mut owned = HashSet::new();
    let sources = parent
        .children
        .iter()
        .map(|&key| {
            let refs = inline_unit_refs(key, plan)?;
            if !refs.iter().all(|id| owned.insert(*id)) {
                return None;
            }
            Some((key, refs))
        })
        .collect::<Option<Vec<_>>>()?;
    let mut source_rows = HashMap::new();
    for (index, group) in groups.iter().enumerate() {
        let members = seq(group.get("members"));
        if members.is_empty() {
            return None;
        }
        for member in members {
            let id = context.projection.by_reference(member.as_str()?)?.id;
            if source_rows.insert(id, index).is_some() {
                return None;
            }
        }
    }
    if owned != source_rows.keys().copied().collect() {
        return None;
    }
    let mut rows = Vec::new();
    let mut current = Vec::new();
    let mut visited = HashSet::new();
    let mut previous = None;
    for (key, refs) in sources {
        let index = source_rows[refs.first()?];
        if refs.iter().any(|id| source_rows[id] != index) {
            return None;
        }
        if Some(index) != previous {
            if !visited.insert(index) {
                return None;
            }
            if !current.is_empty() {
                rows.push(std::mem::take(&mut current));
            }
            previous = Some(index);
        }
        current.push(key);
    }
    if !current.is_empty() {
        rows.push(current);
    }
    Some(rows)
}

pub(crate) fn local_parent(
    parent: &Block,
    plan: &GeometricPlan,
    context: &GeometricContext<'_>,
    minimum: usize,
) -> bool {
    if parent.visual_unit
        || matches!(
            parent.layout,
            "records" | "record-blocks" | "record-path" | "compact-list" | "chain" | "inline" | "visual-row"
        )
        || matches!(parent.kind, "table" | "collection" | "record")
        || parent.children.len() < minimum
    {
        return false;
    }
    let Some(id) = parent.primary_ref else {
        return matches!(parent.kind, "field" | "region");
    };
    let Some(node) = context.projection.node(id) else {
        return false;
    };
    let Some(evidence) = plan.evidence.get(&id) else {
        return false;
    };
    matches!(
        role(node),
        "window" | "sheet" | "group" | "splitGroup" | "tabPanel" | "scrollArea" | "navigation"
    ) && (!truth(node.field("layoutGroups")) || prepared_rows(parent, plan, context).is_some())
        && !ordered_ancestry(plan, context, id, true, true)
        && !evidence.incomplete
        && !evidence.hidden
}

pub(crate) fn row_geometry(
    keys: &[BlockId],
    plan: &GeometricPlan,
    context: &GeometricContext<'_>,
    require_order: bool,
) -> bool {
    let Some(units) = keys
        .iter()
        .map(|&key| row_unit(key, plan, context))
        .collect::<Option<Vec<_>>>()
    else {
        return false;
    };
    let refs: Vec<_> = units.iter().flat_map(|unit| unit.sources.iter().copied()).collect();
    if refs.iter().copied().collect::<HashSet<_>>().len() != refs.len()
        || !plan.same_scope(&refs, context)
        || !plan.same_region(&refs)
    {
        return false;
    }
    let Some(&first) = refs.first() else { return false };
    let scope = reading_scope(plan, context, first);
    let Some(partition) = source_partition(plan, context, first) else {
        return false;
    };
    if refs
        .iter()
        .any(|&id| reading_scope(plan, context, id) != scope || source_partition(plan, context, id) != Some(partition))
    {
        return false;
    }
    let items: Vec<_> = keys
        .iter()
        .copied()
        .zip(units)
        .map(|(key, unit)| LayoutItem {
            key,
            bounds: unit.bounds,
        })
        .collect();
    same_row(&items, plan, require_order)
}

fn flush(
    owner: BlockId,
    parent: &Block,
    pending: &mut Vec<BlockId>,
    assembled: &mut Vec<BlockId>,
    plan: &mut GeometricPlan,
    context: &GeometricContext<'_>,
) -> Result<()> {
    let units: Vec<_> = pending.iter().filter_map(|&id| row_unit(id, plan, context)).collect();
    let roles: HashSet<_> = units
        .iter()
        .filter_map(|unit| context.projection.node(unit.target_ref).map(role))
        .collect();
    let visual = pending
        .iter()
        .any(|&key| plan.block(key).is_some_and(|block| block.visual_unit));
    if pending.len() < 2 || parent.kind != "field" && !(roles.len() > 1 || roles.contains("staticText") || visual) {
        assembled.append(pending);
    } else {
        let key = block_key(plan, "visual-row", owner, pending)?;
        let mut row = Block::new(key, "content", None, std::mem::take(pending));
        row.layout = "visual-row";
        row.origin = "visual";
        assembled.push(plan.insert(row));
        let refs = units
            .iter()
            .filter_map(|unit| {
                context
                    .projection
                    .node(unit.target_ref)
                    .map(|node| node.reference.as_str())
            })
            .collect::<Vec<_>>()
            .join(",");
        plan.decisions.push(format!("visual-row:{refs}"));
    }
    Ok(())
}

pub(super) fn group_rows(plan: &mut GeometricPlan, context: &GeometricContext<'_>) -> Result<()> {
    if !plan.enabled {
        return Ok(());
    }
    let parents = plan
        .active_ids()
        .map(|id| (id, plan.block(id).unwrap().clone()))
        .collect::<Vec<_>>();
    for (owner, parent) in parents {
        if !local_parent(&parent, plan, context, 2) {
            continue;
        }
        let candidate_rows = prepared_rows(&parent, plan, context).unwrap_or_default();
        let rows: HashMap<_, _> = candidate_rows
            .iter()
            .enumerate()
            .flat_map(|(i, row)| row.iter().map(move |&key| (key, i)))
            .collect();
        let mut output = Vec::new();
        let mut run = Vec::new();
        for &key in &parent.children {
            let child = plan.block(key).ok_or(GeometryError::MissingBlock(key))?;
            if parent.label_ref.is_some() && child.primary_ref == parent.label_ref
                || row_unit(key, plan, context).is_none()
            {
                flush(owner, &parent, &mut run, &mut output, plan, context)?;
                output.push(key);
                continue;
            }
            if let Some(last) = run.last() {
                if !rows.is_empty() && rows.get(last) != rows.get(&key) {
                    flush(owner, &parent, &mut run, &mut output, plan, context)?;
                }
            }
            if !run.is_empty() {
                let test: Vec<_> = run.iter().copied().chain(std::iter::once(key)).collect();
                if !row_geometry(&test, plan, context, false) {
                    flush(owner, &parent, &mut run, &mut output, plan, context)?;
                }
            }
            run.push(key);
        }
        flush(owner, &parent, &mut run, &mut output, plan, context)?;
        if output != parent.children {
            plan.block_mut(owner).unwrap().children = output;
        }
    }
    Ok(())
}

fn order_children(
    keys: &[BlockId],
    plan: &GeometricPlan,
    context: &GeometricContext<'_>,
    geometry: bool,
) -> Vec<BlockId> {
    if keys.len() < 2 {
        return keys.to_vec();
    }
    let owners = owners(plan, keys);
    let Some(mut edges) = native_edges(keys, &owners, &plan.relations) else {
        return keys.to_vec();
    };
    if let Some(measured) = (geometry && !linked_members(&owners, plan, context))
        .then(|| comparable_members(keys, plan, context))
        .flatten()
    {
        let overlap = keys.iter().enumerate().any(|(i, key)| {
            keys[i + 1..]
                .iter()
                .any(|other| measured[key].intersection_area(&measured[other]) > 0.0)
        });
        if !overlap {
            let mut vertical = keys.to_vec();
            vertical.sort_by(|a, b| cmp(measured[a].y, measured[b].y));
            for (i, before) in vertical.iter().enumerate() {
                let first = &measured[before];
                for after in &vertical[i + 1..] {
                    let second = &measured[after];
                    if first.horizontal_overlap(second) >= 0.6
                        && second.y - first.bottom() >= 0.25 * first.height.min(second.height)
                    {
                        add_compatible_edge(&mut edges, *before, *after);
                    }
                }
            }
        }
    }
    stable_order(keys, &edges).expect("compatible geometric and native precedence")
}

pub(super) fn order_regions(plan: &mut GeometricPlan, context: &GeometricContext<'_>) {
    if !plan.enabled {
        return;
    }
    for id in plan.active_ids().collect::<Vec<_>>() {
        let block = plan.block(id).unwrap();
        if block.children.len() < 2
            || matches!(block.kind, "table" | "collection" | "record")
            || block.layout != "tree"
        {
            continue;
        }
        let refs: Vec<_> = block
            .children
            .iter()
            .flat_map(|&child| plan.block_sources(child))
            .collect();
        let Some(&first) = refs.first() else { continue };
        let scope = reading_scope(plan, context, first);
        if !plan.same_region(&refs)
            || refs.iter().any(|&ref_id| {
                ordered_ancestry(plan, context, ref_id, true, true) || reading_scope(plan, context, ref_id) != scope
            })
        {
            continue;
        }
        let bounds = block.primary_ref.and_then(|id| context.geometry.get(&id));
        let inside = bounds.is_none_or(|bounds| {
            block.children.iter().all(|&id| {
                plan.block_bounds(id, context.geometry)
                    .is_some_and(|member| bounds.contains(&member))
            })
        });
        let ordered = order_children(&block.children, plan, context, block.label_ref.is_none() && inside);
        if ordered != block.children {
            let decision = format!("interface-reading-order:{}", block.key);
            plan.block_mut(id).unwrap().children = ordered;
            plan.decisions.push(decision);
        }
    }
}

pub(super) fn order_rows(plan: &mut GeometricPlan, context: &GeometricContext<'_>) {
    if !plan.enabled {
        return;
    }
    for id in plan.active_ids().collect::<Vec<_>>() {
        let block = plan.block(id).unwrap();
        let keys = &block.children;
        if block.layout != "visual-row"
            || block.primary_ref.is_some()
            || !block.source_refs.is_empty()
            || keys.len() < 2
            || !row_geometry(keys, plan, context, false)
        {
            continue;
        }
        let Some(measured) = comparable_members(keys, plan, context) else {
            continue;
        };
        let owners = owners(plan, keys);
        if external_flow(&owners, &plan.relations) {
            continue;
        }
        let Some(mut edges) = native_edges(keys, &owners, &plan.relations) else {
            continue;
        };
        let mut horizontal = keys.clone();
        horizontal.sort_by(|a, b| cmp(measured[a].x, measured[b].x));
        for pair in horizontal.windows(2) {
            add_compatible_edge(&mut edges, pair[0], pair[1]);
        }
        let ordered = stable_order(keys, &edges).expect("compatible row order");
        if ordered != *keys {
            let decision = format!("geometric-row-order:{}", block.key);
            plan.block_mut(id).unwrap().children = ordered;
            plan.decisions.push(decision);
        }
    }
}
