// Copyright (c) Huawei Technologies Co., Ltd. 2026. All rights reserved.

use std::collections::HashSet;
use std::hash::Hash;

use super::{BlockId, GeometricContext, GeometricPlan, PatternPlan};
use crate::{geometric::role, value::Value};

fn distinct<T: Copy + Eq + Hash>(items: &[T]) -> Option<HashSet<T>> {
    let set: HashSet<_> = items.iter().copied().collect();
    (set.len() == items.len()).then_some(set)
}

fn reason(plan: &GeometricPlan, context: &GeometricContext<'_>, pattern: &PatternPlan) -> Option<&'static str> {
    const OWNER: &str = "selected pattern owner differs from its real source";
    const INVENTORY: &str = "selected pattern lost its original input inventory";
    const MEMBERS: &str = "selected pattern duplicated, invented or omitted an original member";
    const GRID: &str = "selected grid differs from its materialized rows or columns";
    const GRID_ROW: &str = "selected grid row changed its independently sourced units";
    const NATIVE_ROW: &str = "selected native item changed its original row identity";
    const TABLE: &str = "selected table row changed its original cells";
    const WRAPPER: &str = "selected pattern has an invalid source wrapper path";

    let Some(owner) = plan.block(pattern.owner).filter(|block| block.primary_ref == Some(pattern.owner_ref)) else {
        return Some(OWNER);
    };
    let Some(owner_node) = context.projection.node(pattern.owner_ref) else { return Some(OWNER); };
    let inputs = &pattern.input_keys;
    let Some(input_set) = distinct(inputs).filter(|set| !set.is_empty()) else { return Some(INVENTORY); };
    if inputs.iter().any(|id| plan.block(*id).is_none()) { return Some(INVENTORY); }
    let mut expected = HashSet::new();
    let mut pending = owner_node.children.clone();
    while let Some(reference) = pending.pop() {
        let Some(node) = context.projection.node(reference) else {
            return Some("selected pattern owner has conflicting original membership");
        };
        if !expected.insert(reference) {
            return Some("selected pattern owner has conflicting original membership");
        }
        pending.extend(&node.children);
    }
    let supplied: Vec<_> = inputs.iter().flat_map(|id| plan.block_sources(*id)).collect();
    if distinct(&supplied).as_ref() != Some(&expected) {
        return Some("selected pattern input differs from the original owner scope");
    }
    let selected: Vec<_> = if pattern.kind == "grid" {
        pattern.rows.iter().flat_map(|row| row.members.iter().copied()).collect()
    } else {
        let Some(selected) = pattern.rows.iter().map(|row| plan.by_key(&row.key)).collect() else { return Some(MEMBERS); };
        selected
    };
    let Some(selected) = distinct(&selected).filter(|set| !set.is_empty()) else { return Some(MEMBERS); };
    let Some(remaining) = distinct(&pattern.remaining_keys) else { return Some(MEMBERS); };
    if !selected.is_disjoint(&remaining) || selected.len() + remaining.len() != input_set.len()
        || selected.union(&remaining).any(|id| !input_set.contains(id))
    {
        return Some(MEMBERS);
    }
    if pattern.kind == "grid" {
        let grids: Vec<_> = owner.children.iter().filter_map(|id| plan.block(*id))
            .filter(|block| block.layout == "visual-grid").collect();
        let [grid] = grids.as_slice() else { return Some(GRID); };
        if grid.columns.len() != pattern.column_count || pattern.column_count < 2
            || grid.children.iter().copied().map(Some).ne(pattern.rows.iter().map(|row| plan.by_key(&row.key)))
        {
            return Some(GRID);
        }
        for row in &pattern.rows {
            let Some(block) = plan.by_key(&row.key).and_then(|id| plan.block(id)) else { return Some(GRID_ROW); };
            if row.reference.is_some() || block.primary_ref.is_some() || !block.source_refs.is_empty()
                || block.layout != "visual-row" || block.children != row.members || row.members.len() != row.slots.len()
                || block.column_slots.iter().copied().ne(row.slots.iter().copied().map(Some))
                || !row.slots.windows(2).all(|slots| slots[0] < slots[1])
                || row.slots.iter().any(|slot| *slot >= pattern.column_count)
            {
                return Some(GRID_ROW);
            }
        }
    } else if matches!(pattern.kind, "table" | "cards" | "list") {
        for row in &pattern.rows {
            let (Some(reference), Some(block)) = (row.reference, plan.by_key(&row.key).and_then(|id| plan.block(id))) else {
                return Some(NATIVE_ROW);
            };
            let Some(node) = context.projection.node(reference) else { return Some(NATIVE_ROW); };
            if block.primary_ref != Some(reference) || role(node) != "row" || row.members.iter().any(|id| plan.block(*id).is_none()) {
                return Some(NATIVE_ROW);
            }
            if pattern.kind == "table" {
                if row.members.iter().map(|id| plan.block(*id).and_then(|block| block.primary_ref)).ne(node.children.iter().copied().map(Some))
                    || row.slots.len() != row.members.len()
                {
                    return Some(TABLE);
                }
                for (id, slot) in row.members.iter().zip(&row.slots) {
                    let column = plan.block(*id).and_then(|block| block.primary_ref)
                        .and_then(|reference| context.projection.node(reference))
                        .and_then(|node| node.field("columnRange"));
                    let Some(column) = column else { return Some("selected table column differs from its native extent"); };
                    let matches_slot = match column.get("location") {
                        Some(Value::Int(value)) => usize::try_from(*value) == Ok(*slot),
                        Some(Value::BigInt(value)) => value.parse::<usize>() == Ok(*slot),
                        _ => false,
                    };
                    if !matches_slot || column.get("length") != Some(&Value::Int(1)) {
                        return Some("selected table column differs from its native extent");
                    }
                }
            } else if block.children != row.members {
                return Some("selected card changed its original content members");
            }
        }
    } else {
        return Some("selected pattern has an unknown layout kind");
    }
    for path in &pattern.wrapper_chains {
        if path.len() < 2 || distinct(path).is_none() || !selected.contains(&path[0]) { return Some(WRAPPER); }
        let refs: Option<Vec<_>> = path.iter().map(|id| plan.block(*id).and_then(|block| block.primary_ref)
            .filter(|reference| expected.contains(reference))).collect();
        let Some(refs) = refs else { return Some(WRAPPER); };
        for pair in refs.windows(2) {
            let [parent, child] = pair else { unreachable!("two path members") };
            if context.projection.node(*parent).is_none_or(|node| node.children != [*child])
                || plan.projected_parents.get(child) != Some(parent)
            {
                return Some("selected pattern wrapper path crosses an original parent");
            }
        }
    }
    None
}

pub(crate) fn pattern_issues(plan: &GeometricPlan, context: &GeometricContext<'_>) -> Vec<(BlockId, &'static str)> {
    let mut issues = Vec::new();
    let mut owners = HashSet::new();
    for pattern in &plan.geometric_patterns {
        let reason = if owners.insert(pattern.owner) { reason(plan, context, pattern) }
            else { Some("several selected patterns claim the same source owner") };
        if let Some(reason) = reason {
            let owner = if plan.block(pattern.owner).is_some() { Some(pattern.owner) } else {
                plan.active_ids().find(|id| plan.block(*id).is_some_and(|block| block.source_refs.contains(&pattern.owner_ref)))
                    .or_else(|| plan.roots.first().copied())
            };
            if let Some(owner) = owner { issues.push((owner, reason)); }
        }
    }
    issues
}
