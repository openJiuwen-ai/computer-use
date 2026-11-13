// Copyright (c) Huawei Technologies Co., Ltd. 2026. All rights reserved.

use std::collections::{HashMap, HashSet};

use super::{
    alignment::{cmp, compare_keys, coordinate, Alignment, Anchor},
    BlockId, Box, GeometricPlan,
};

#[derive(Clone, Debug)]
pub(crate) struct ColumnRow {
    pub(crate) members: Vec<BlockId>,
    pub(crate) slots: Vec<Option<usize>>,
}

#[derive(Clone, Debug)]
pub(crate) struct ColumnProjection {
    pub(crate) alignments: Vec<&'static str>,
    pub(crate) rows: Vec<ColumnRow>,
}

fn select_anchors(analysis: &Alignment, plan: &GeometricPlan) -> Vec<Anchor> {
    let available: HashSet<_> = analysis
        .rows
        .iter()
        .flat_map(|row| row.members.iter().copied())
        .collect();
    let mut selected = Vec::new();
    let mut claimed = HashSet::new();
    let rank = |kind| match kind {
        "left" => 0,
        "right" => 1,
        _ => 2,
    };
    loop {
        let choice = analysis
            .anchors
            .iter()
            .filter_map(|anchor| {
                if !anchor.members.iter().all(|key| available.contains(key)) {
                    return None;
                }
                let (members, rows): (Vec<_>, Vec<_>) = anchor
                    .members
                    .iter()
                    .copied()
                    .zip(anchor.rows.iter().copied())
                    .filter(|(key, _)| !claimed.contains(key))
                    .unzip();
                if members.len() < 2 || rows.iter().copied().collect::<HashSet<_>>().len() != rows.len() {
                    return None;
                }
                Some(Anchor {
                    kind: anchor.kind,
                    position: anchor.position,
                    members,
                    rows,
                })
            })
            .min_by(|a, b| {
                b.members
                    .len()
                    .cmp(&a.members.len())
                    .then_with(|| rank(a.kind).cmp(&rank(b.kind)))
                    .then_with(|| cmp(a.position, b.position))
                    .then_with(|| compare_keys(plan, &a.members, &b.members))
            });
        let Some(choice) = choice else { break };
        claimed.extend(choice.members.iter().copied());
        selected.push(choice);
    }
    selected.sort_by(|a, b| {
        cmp(a.position, b.position)
            .then_with(|| a.kind.cmp(b.kind))
            .then_with(|| compare_keys(plan, &a.members, &b.members))
    });
    selected
}

fn clear_neighbour<'a>(
    candidate: &Box,
    witness: &Box,
    anchor: &Anchor,
    scale: f64,
    obstacles: impl Iterator<Item = &'a Box>,
) -> bool {
    let (before, after) = if candidate.y < witness.y {
        (candidate, witness)
    } else {
        (witness, candidate)
    };
    let gap = after.y - before.bottom();
    if gap < 0.0 || gap > 3.0 * scale.min(candidate.height).min(witness.height) {
        return false;
    }
    let current = coordinate(candidate, anchor.kind);
    let matching = coordinate(witness, anchor.kind);
    let low = anchor.position.min(current).min(matching);
    let high = anchor.position.max(current).max(matching);
    !obstacles.into_iter().any(|bounds| {
        bounds.y < after.y && bounds.bottom() > before.bottom() && bounds.x < high && bounds.right() > low
    })
}

fn refine_floating(
    analysis: &Alignment,
    anchors: &[&Anchor],
    rows: &[ColumnRow],
    assigned: &HashMap<BlockId, usize>,
    conflicting: &HashSet<usize>,
) -> Vec<ColumnRow> {
    let boxes: HashMap<_, _> = analysis.items.iter().map(|item| (item.key, &item.bounds)).collect();
    if analysis.scale <= 0.0
        || boxes.keys().copied().collect::<HashSet<_>>()
            != rows.iter().flat_map(|row| row.members.iter().copied()).collect()
        || boxes.values().map(|bounds| &bounds.space).collect::<HashSet<_>>().len() != 1
    {
        return rows.to_vec();
    }
    let mut result = Vec::with_capacity(rows.len());
    for (row_index, row) in rows.iter().enumerate() {
        if conflicting.contains(&row_index) {
            result.push(row.clone());
            continue;
        }
        let neighbours: Vec<_> = [row_index.checked_sub(1), Some(row_index + 1)]
            .into_iter()
            .flatten()
            .filter_map(|i| rows.get(i))
            .flat_map(|row| {
                row.members
                    .iter()
                    .copied()
                    .zip(&row.slots)
                    .filter_map(|(key, slot)| Some(((*slot)?, key)))
            })
            .collect();
        let mut proposals = Vec::<(usize, usize)>::new();
        for (member_index, (&key, current)) in row.members.iter().zip(&row.slots).enumerate() {
            if current.is_some() || assigned.contains_key(&key) {
                continue;
            }
            let bounds = boxes[&key];
            let mut candidates = HashSet::new();
            for &(column, witness_key) in &neighbours {
                if row.slots.contains(&Some(column))
                    || row.slots.iter().enumerate().any(|(index, slot)| {
                        slot.is_some_and(|slot| {
                            if index < member_index {
                                slot >= column
                            } else {
                                slot <= column
                            }
                        })
                    })
                {
                    continue;
                }
                let anchor = anchors[column];
                let witness = boxes[&witness_key];
                let tolerance = 0.4 * analysis.scale.min(bounds.height).min(witness.height);
                if (coordinate(bounds, anchor.kind) - anchor.position).abs() > tolerance {
                    continue;
                }
                if neighbours.iter().any(|&(other_column, other_key)| {
                    let other = boxes[&other_key];
                    other_column != column
                        && bounds.right().min(other.right()) - bounds.x.max(other.x)
                            > 0.35 * bounds.width.min(other.width)
                }) {
                    continue;
                }
                if clear_neighbour(
                    bounds,
                    witness,
                    anchor,
                    analysis.scale,
                    boxes
                        .iter()
                        .filter_map(|(&id, &item)| (id != key && id != witness_key).then_some(item)),
                ) {
                    candidates.insert(column);
                }
            }
            if candidates.len() == 1 {
                proposals.push((member_index, candidates.into_iter().next().unwrap()));
            }
        }
        let mut output = row.clone();
        for &(index, column) in &proposals {
            if !proposals
                .iter()
                .any(|&(other, target)| index != other && (column == target || (index < other) != (column < target)))
            {
                output.slots[index] = Some(column);
            }
        }
        result.push(output);
    }
    result
}

pub(crate) fn project_columns(analysis: &Alignment, plan: &GeometricPlan) -> Option<ColumnProjection> {
    if analysis.rows.len() < 2 {
        return None;
    }
    let selected = select_anchors(analysis, plan);
    if selected.len() < 2 {
        return None;
    }
    let column_for: HashMap<_, _> = selected
        .iter()
        .enumerate()
        .flat_map(|(i, anchor)| anchor.members.iter().map(move |&key| (key, i)))
        .collect();
    let mut conflicting = HashSet::new();
    let mut rows = Vec::new();
    for (index, row) in analysis.rows.iter().enumerate() {
        let mut slots: Vec<_> = row.members.iter().map(|key| column_for.get(key).copied()).collect();
        let known: Vec<_> = slots.iter().flatten().copied().collect();
        if known.windows(2).any(|pair| pair[0] >= pair[1]) {
            slots.fill(None);
            conflicting.insert(index);
        }
        rows.push(ColumnRow {
            members: row.members.clone(),
            slots,
        });
    }
    let retained: Vec<_> = (0..selected.len())
        .filter(|&column| rows.iter().filter(|row| row.slots.contains(&Some(column))).count() >= 2)
        .collect();
    if retained.len() < 2 {
        return None;
    }
    let remap: HashMap<_, _> = retained.iter().enumerate().map(|(new, &old)| (old, new)).collect();
    for row in &mut rows {
        for slot in &mut row.slots {
            *slot = slot.and_then(|old| remap.get(&old).copied());
        }
    }
    if rows.iter().filter(|row| row.slots.iter().flatten().count() > 1).count() < 2 {
        return None;
    }
    let anchors: Vec<_> = retained.iter().map(|&column| &selected[column]).collect();
    let rows = refine_floating(analysis, &anchors, &rows, &column_for, &conflicting);
    Some(ColumnProjection {
        alignments: anchors.iter().map(|anchor| anchor.kind).collect(),
        rows,
    })
}

use super::{
    alignment::{analyze, LayoutItem},
    block_key, role,
    rows::row_unit,
    same_prefix,
    scopes::{comparable_members, external_flow, native_edges, stable_order},
    Block, GeometricContext, GeometryError, NodeId, Result,
};

fn column_row(row: &Block) -> bool {
    row.primary_ref.is_none()
        && row.source_refs.is_empty()
        && !row.children.is_empty()
        && matches!(row.layout, "visual-row" | "inline")
}

fn control_role(role: &str) -> bool {
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
    )
}

fn project_run(
    parent_id: BlockId,
    parent: &Block,
    row_keys: &[BlockId],
    plan: &mut GeometricPlan,
    context: &GeometricContext<'_>,
) -> Result<Option<BlockId>> {
    let rows = row_keys
        .iter()
        .map(|&key| plan.block(key).ok_or(GeometryError::MissingBlock(key)))
        .collect::<Result<Vec<_>>>()?;
    if rows.len() < 2 || rows.iter().any(|row| !column_row(row)) {
        return Ok(None);
    }
    let keys: Vec<_> = rows.iter().flat_map(|row| row.children.iter().copied()).collect();
    let refs: Vec<_> = keys.iter().flat_map(|&key| plan.block_sources(key)).collect();
    if keys.iter().copied().collect::<HashSet<_>>().len() != keys.len()
        || !plan.same_region(&refs)
        || comparable_members(&keys, plan, context).is_none()
    {
        return Ok(None);
    }
    let Some(units) = keys
        .iter()
        .map(|&key| Some((key, row_unit(key, plan, context)?)))
        .collect::<Option<HashMap<_, _>>>()
    else {
        return Ok(None);
    };
    let items: Vec<_> = keys
        .iter()
        .map(|&key| LayoutItem {
            key,
            bounds: units[&key].bounds.clone(),
        })
        .collect();
    let Some(projected) = analyze(&items, plan).and_then(|analysis| project_columns(&analysis, plan)) else {
        return Ok(None);
    };
    if !projected
        .rows
        .iter()
        .map(|row| &row.members)
        .eq(rows.iter().map(|row| &row.children))
    {
        return Ok(None);
    }
    let roles: Vec<_> = projected
        .rows
        .iter()
        .flat_map(|row| {
            row.members
                .iter()
                .zip(&row.slots)
                .filter_map(|(key, slot)| slot.and_then(|_| context.projection.node(units[key].target_ref).map(role)))
        })
        .collect();
    let atomic = keys.iter().all(|&key| {
        plan.block(key)
            .is_some_and(|block| block.primary_ref.is_some() && block.children.is_empty())
    });
    let mut record = atomic
        && (parent.layout == "records"
            || roles.iter().any(|role| matches!(*role, "staticText" | "heading"))
                && roles
                    .iter()
                    .any(|role| control_role(role) || matches!(*role, "textField" | "textArea" | "comboBox")));
    if !record
        && rows
            .iter()
            .all(|row| row.layout == "inline" && row.shared_prefix_ref.is_some())
        && projected
            .rows
            .iter()
            .all(|row| row.slots.len() == projected.alignments.len() && !row.slots.contains(&None))
    {
        return Ok(None);
    }
    let owners: HashMap<_, _> = keys
        .iter()
        .flat_map(|&key| units[&key].sources.iter().map(move |&id| (id, key)))
        .collect();
    let Some(edges) = native_edges(&keys, &owners, &plan.relations) else {
        return Ok(None);
    };
    if stable_order(&keys, &edges).as_ref() != Some(&keys) || external_flow(&owners, &plan.relations) {
        return Ok(None);
    }
    let mut columns = Vec::<Vec<NodeId>>::new();
    for index in 0..projected.alignments.len() {
        let refs: Vec<_> = projected
            .rows
            .iter()
            .flat_map(|row| {
                row.members
                    .iter()
                    .zip(&row.slots)
                    .filter_map(|(key, slot)| (*slot == Some(index)).then_some(units[key].target_ref))
            })
            .collect();
        if parent.layout != "records"
            && refs
                .iter()
                .filter_map(|&id| context.projection.node(id).map(role))
                .collect::<HashSet<_>>()
                .len()
                != 1
        {
            record = false;
        }
        columns.push(refs);
    }
    let mut prefixes = Vec::new();
    let mut labels = Vec::new();
    for (index, refs) in columns.iter().enumerate() {
        let first = refs
            .first()
            .copied()
            .ok_or(GeometryError::Invalid("empty projected column"))?;
        prefixes.push((record && refs.iter().all(|&id| same_prefix(context, first, id))).then_some(first));
        labels.push(
            if refs
                .iter()
                .all(|&id| context.projection.node(id).is_some_and(|node| control_role(role(node))))
            {
                "Actions".to_owned()
            } else {
                format!("Content {}", index + 1)
            },
        );
    }
    for (&key, row) in row_keys.iter().zip(projected.rows) {
        let block = plan.block_mut(key).ok_or(GeometryError::MissingBlock(key))?;
        block.kind = if record { "record" } else { "content" };
        block.layout = if record { "inline" } else { "visual-row" };
        block.shared_prefix_ref = None;
        block.column_slots = row.slots;
    }
    let kind = if record { "geometric-columns" } else { "geometric-grid" };
    let key = if parent.layout == "records" {
        parent_id
    } else {
        let key = block_key(plan, kind, parent_id, &keys)?;
        let mut block = Block::new(
            key,
            if record { "collection" } else { "content" },
            None,
            row_keys.to_vec(),
        );
        block.layout = if record { "records" } else { "visual-grid" };
        block.origin = "visual";
        plan.insert(block)
    };
    let block = plan.block_mut(key).unwrap();
    block.columns = labels;
    block.column_prefix_refs = prefixes;
    block.column_alignments = projected.alignments;
    plan.decisions.push(format!("{kind}:{}", parent.key));
    Ok(Some(key))
}

fn flush(
    owner: BlockId,
    parent: &Block,
    run: &mut Vec<BlockId>,
    output: &mut Vec<BlockId>,
    plan: &mut GeometricPlan,
    context: &GeometricContext<'_>,
) -> Result<()> {
    if let Some(group) = project_run(owner, parent, run, plan, context)? {
        output.push(group);
        run.clear();
    } else {
        output.append(run);
    }
    Ok(())
}

pub(super) fn plan_columns(plan: &mut GeometricPlan, context: &GeometricContext<'_>) -> Result<()> {
    if !plan.enabled {
        return Ok(());
    }
    let parents = plan
        .active_ids()
        .map(|id| (id, plan.block(id).unwrap().clone()))
        .collect::<Vec<_>>();
    for (owner, parent) in parents {
        if matches!(parent.kind, "field" | "table" | "record") || !matches!(parent.layout, "tree" | "records") {
            continue;
        }
        if parent.layout == "records" {
            project_run(owner, &parent, &parent.children, plan, context)?;
            continue;
        }
        let mut output = Vec::new();
        let mut run = Vec::new();
        for &child in &parent.children {
            if column_row(plan.block(child).ok_or(GeometryError::MissingBlock(child))?) {
                if !run.is_empty()
                    && !plan.same_region(
                        &run.iter()
                            .copied()
                            .chain(std::iter::once(child))
                            .flat_map(|id| plan.block_sources(id))
                            .collect::<Vec<_>>(),
                    )
                {
                    flush(owner, &parent, &mut run, &mut output, plan, context)?;
                }
                run.push(child);
            } else {
                flush(owner, &parent, &mut run, &mut output, plan, context)?;
                output.push(child);
            }
        }
        flush(owner, &parent, &mut run, &mut output, plan, context)?;
        if output != parent.children {
            plan.block_mut(owner).unwrap().children = output;
        }
    }
    Ok(())
}
