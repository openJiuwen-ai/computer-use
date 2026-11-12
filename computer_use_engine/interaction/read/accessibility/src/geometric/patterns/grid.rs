// Copyright (c) Huawei Technologies Co., Ltd. 2026. All rights reserved.

use std::collections::{HashMap, HashSet};

use super::{source, PatternPlan, PatternRow};
use super::super::{
    alignment::{analyze, LayoutItem}, block_key, columns::project_columns, Block, BlockId, GeometricContext,
    GeometricPlan, Result,
};
use crate::numbering::NodeId;

/// Stable topological ordering returns the input itself exactly when every
/// local native constraint already points forward in that input. Unconfirmed
/// declarations on any participating source still reject the measured grid.
fn compatible_order(plan: &GeometricPlan, refs: &HashMap<NodeId, BlockId>, ordered: &[BlockId]) -> bool {
    let positions: HashMap<_, _> = ordered.iter().enumerate().map(|(index, key)| (*key, index)).collect();
    for relation in &plan.relations {
        if !matches!(relation.kind.as_str(), "flows_to" | "flows_from") {
            continue;
        }
        let Some(&source) = refs.get(&relation.source) else { continue; };
        if !relation.complete || !relation.confirmed {
            return false;
        }
        for target in &relation.targets {
            let Some(&target) = refs.get(target) else { continue; };
            let (before, after) = if relation.kind == "flows_from" { (target, source) } else { (source, target) };
            if before != after && positions[&before] >= positions[&after] {
                return false;
            }
        }
    }
    true
}

pub(super) fn select(
    plan: &GeometricPlan,
    context: &GeometricContext<'_>,
    owner: BlockId,
    block: &Block,
) -> Result<Option<PatternPlan>> {
    let Some(owner_ref) = block.primary_ref else { return Ok(None); };
    if block.children.len() < 4 {
        return Ok(None);
    }
    let mut chains = HashMap::new();
    let mut boxes = HashMap::new();
    let mut longest = Vec::new();
    let mut current = Vec::new();
    for key in &block.children {
        let chain = source::inline_chain(plan, context, *key);
        let bounds = source::item_box(plan, context, *key);
        if let (Some(chain), Some(bounds)) = (chain, bounds) {
            chains.insert(*key, chain);
            boxes.insert(*key, bounds);
            current.push(*key);
        } else {
            if current.len() > longest.len() { std::mem::swap(&mut current, &mut longest); }
            current.clear();
        }
    }
    if current.len() > longest.len() { longest = current; }
    let members = longest;
    if members.len() < 4 || members.iter().any(|member| boxes[member].space != boxes[&members[0]].space) {
        return Ok(None);
    }
    let mut references = HashMap::new();
    let scope = context.projection.node(owner_ref).map(|node| node.scope);
    for member in &members {
        for reference in source::source_keys(plan, *member) {
            if context.projection.node(reference).map(|node| node.scope) != scope
                || references.insert(reference, *member).is_some()
            {
                return Ok(None);
            }
        }
    }
    let items: Vec<_> = members.iter().map(|key| LayoutItem { key: *key, bounds: boxes[key].clone() }).collect();
    let Some(alignment) = analyze(&items, plan) else { return Ok(None); };
    let Some(columns) = project_columns(&alignment, plan) else { return Ok(None); };
    if columns.rows.len() < 2 || columns.alignments.len() < 2
        || columns.rows.iter().filter(|row| row.members.len() >= 2).count() < 2
    {
        return Ok(None);
    }
    let ordered: Vec<_> = columns.rows.iter().flat_map(|row| row.members.iter().copied()).collect();
    if !compatible_order(plan, &references, &ordered) || columns.rows.iter().any(|row| row.slots.contains(&None)) {
        return Ok(None);
    }
    let mut result = PatternPlan::new(owner, owner_ref, block, "grid", &["native-collection-items", "repeated-geometric-rows-and-columns"]);
    result.rows = columns.rows.into_iter().map(|row| {
        Ok(PatternRow {
            key: block_key(plan, "pattern-grid-row", owner, &row.members)?,
            reference: None,
            members: row.members,
            slots: row.slots.into_iter().flatten().collect(),
        })
    }).collect::<Result<_>>()?;
    result.column_count = columns.alignments.len();
    result.column_alignments = columns.alignments;
    let selected: HashSet<_> = members.iter().copied().collect();
    result.remaining_keys = block.children.iter().copied().filter(|key| !selected.contains(key)).collect();
    result.wrapper_chains = members.iter().filter_map(|key| chains.remove(key)).filter(|chain| chain.len() > 1).collect();
    Ok(Some(result))
}
