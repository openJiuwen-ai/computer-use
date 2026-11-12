// Copyright (c) Huawei Technologies Co., Ltd. 2026. All rights reserved.

use std::collections::{HashMap, HashSet};

use super::{
    alignment::{cmp, key_name, LayoutItem},
    partition::{partition, paths, Axis, Region},
    scopes::{pane_columns, pane_members, scope_plan, stable_order, ScopePlan},
    synthetic_key, Block, BlockId, GeometricContext, GeometricPlan, GeometryError, Result,
};

#[derive(Debug)]
pub(crate) struct ScopeRelations {
    pub(crate) source_order: Vec<BlockId>,
    pub(crate) before: Vec<(BlockId, BlockId)>,
    pub(crate) keep_together: Vec<Vec<BlockId>>,
    pub(crate) segments: Vec<Vec<BlockId>>,
    pub(crate) rows: Vec<Vec<BlockId>>,
    pub(crate) anchors: Vec<Vec<BlockId>>,
}

pub(super) fn alignments(plan: &GeometricPlan, context: &GeometricContext<'_>) -> HashMap<BlockId, ScopePlan> {
    plan.active_ids()
        .filter_map(|key| scope_plan(plan, context, key).map(|scope| (key, scope)))
        .collect()
}

pub(super) fn capture_relations(alignment: &HashMap<BlockId, ScopePlan>) -> HashMap<BlockId, ScopeRelations> {
    alignment
        .iter()
        .map(|(&key, plan)| {
            let mut seen = HashSet::new();
            let anchors = plan
                .alignment
                .anchors
                .iter()
                .filter_map(|anchor| {
                    (anchor.members.len() > 1 && seen.insert(&anchor.members)).then_some(anchor.members.clone())
                })
                .collect();
            (
                key,
                ScopeRelations {
                    source_order: plan.source_order.clone(),
                    before: plan.before.clone(),
                    keep_together: plan.keep_together.clone(),
                    segments: plan.segments.clone(),
                    rows: plan.alignment.rows.iter().map(|row| row.members.clone()).collect(),
                    anchors,
                },
            )
        })
        .collect()
}

fn active_relations(scope: &mut ScopePlan, graph: &HashMap<BlockId, ScopeRelations>, key: BlockId) {
    if let Some(graph) = graph.get(&key).filter(|graph| graph.source_order == scope.source_order) {
        scope.before.clone_from(&graph.before);
        scope.keep_together.clone_from(&graph.keep_together);
        scope.segments.clone_from(&graph.segments);
    }
}

pub(super) fn canonicalize(
    plan: &mut GeometricPlan,
    context: &GeometricContext<'_>,
    alignment: &HashMap<BlockId, ScopePlan>,
    graph: &HashMap<BlockId, ScopeRelations>,
) -> Result<()> {
    for owner in plan.active_ids().collect::<Vec<_>>() {
        let parent = plan.block(owner).ok_or(GeometryError::MissingBlock(owner))?;
        let cached = alignment
            .get(&owner)
            .filter(|scope| scope.source_order == parent.children);
        let Some(mut scope) = cached.cloned().or_else(|| scope_plan(plan, context, owner)) else {
            continue;
        };
        active_relations(&mut scope, graph, owner);
        let mut ordered = parent.children.clone();
        let positions: HashMap<_, _> = parent.children.iter().enumerate().map(|(i, &key)| (key, i)).collect();
        for segment in &scope.segments {
            let set: HashSet<_> = segment.iter().copied().collect();
            let candidates: Vec<_> = scope
                .alignment
                .rows
                .iter()
                .flat_map(|row| row.members.iter().copied())
                .filter(|key| set.contains(key))
                .collect();
            if let Some(local) = stable_order(&candidates, &scope.before) {
                for (&source, replacement) in segment.iter().zip(local) {
                    ordered[positions[&source]] = replacement;
                }
            }
        }
        if ordered != parent.children {
            let decision = format!("geometric-source-order:{}", parent.key);
            plan.block_mut(owner).unwrap().children = ordered;
            plan.decisions.push(decision);
        }
    }
    Ok(())
}

fn materialize(
    plan: &mut GeometricPlan,
    region: &Region,
    owner: &str,
    measurements: &HashMap<BlockId, &LayoutItem>,
) -> Vec<BlockId> {
    if region.children.is_empty() {
        return region.keys.clone();
    }
    if region.axis == Some(Axis::Horizontal) {
        return region
            .children
            .iter()
            .flat_map(|child| materialize(plan, child, owner, measurements))
            .collect();
    }
    let left = |child: &Region| {
        child
            .keys
            .iter()
            .map(|key| measurements[key].bounds.x)
            .reduce(f64::min)
            .unwrap()
    };
    let mut physical: Vec<_> = (0..region.children.len()).collect();
    physical.sort_by(|&a, &b| cmp(left(&region.children[a]), left(&region.children[b])));
    let mut children = Vec::new();
    for (index, child) in region.children.iter().enumerate() {
        let keys = child.keys.iter().map(|&key| key_name(plan, key)).collect::<Vec<_>>();
        let key = synthetic_key("geometric-region", owner, &keys);
        let members = materialize(plan, child, &key, measurements);
        let position = if physical.first() == Some(&index) {
            "left"
        } else if physical.last() == Some(&index) {
            "right"
        } else {
            "middle"
        };
        let mut block = Block::new(key, "region", None, members);
        block.origin = "visual";
        block.position = Some(position.to_owned());
        block.region_role = Some("region");
        children.push(plan.insert(block));
    }
    children
}

pub(super) fn arrange(
    plan: &mut GeometricPlan,
    context: &GeometricContext<'_>,
    graph: &HashMap<BlockId, ScopeRelations>,
) -> Result<()> {
    for key in plan.active_ids().collect::<Vec<_>>() {
        let parent = plan.block(key).ok_or(GeometryError::MissingBlock(key))?.clone();
        if parent.kind == "field" || plan.regions.iter().any(|(id, _)| *id == key) {
            continue;
        }
        let Some(mut scope) = scope_plan(plan, context, key) else {
            continue;
        };
        active_relations(&mut scope, graph, key);
        let measured: HashMap<_, _> = scope.items.iter().map(|item| (item.key, item)).collect();
        let unit_sources: HashMap<_, _> = parent
            .children
            .iter()
            .map(|&child| (child, plan.block_sources(child)))
            .collect();
        let mut replacements = HashMap::new();
        let mut consumed = HashSet::new();
        let mut trees = HashMap::new();
        let mut segment_members = HashSet::new();
        for segment in &scope.segments {
            let set: HashSet<_> = segment.iter().copied().collect();
            segment_members.extend(set.iter().copied());
            if scope
                .keep_together
                .iter()
                .any(|group| group.iter().any(|id| set.contains(id)) && !group.iter().all(|id| set.contains(id)))
            {
                trees.insert(segment[0], Region::leaf(segment.clone()));
                continue;
            }
            let items: Vec<_> = segment.iter().map(|id| measured[id].clone()).collect();
            let groups: Vec<_> = scope
                .keep_together
                .iter()
                .filter(|group| group.iter().all(|id| set.contains(id)))
                .cloned()
                .collect();
            let edges: Vec<_> = scope
                .before
                .iter()
                .filter(|(a, b)| set.contains(a) && set.contains(b))
                .copied()
                .collect();
            let pane_ids = pane_members(segment, plan, context);
            let alignment = (items.len() == scope.items.len()).then_some(&scope.alignment);
            let region = partition(plan, &items, &groups, &edges, alignment, &pane_ids)?;
            replacements.insert(segment[0], materialize(plan, &region, &parent.key, &measured));
            trees.insert(segment[0], region);
            consumed.extend(set);
        }
        let forest: Vec<_> = parent
            .children
            .iter()
            .filter_map(|id| {
                trees
                    .remove(id)
                    .or_else(|| (!segment_members.contains(id)).then(|| Region::leaf(vec![*id])))
            })
            .collect();
        let paths = paths(&forest)?;
        if paths.len() != parent.children.len() || paths.iter().any(|(id, _)| !unit_sources.contains_key(id)) {
            return Err(GeometryError::Invalid(
                "geometric region forest lost an original reading unit",
            ));
        }
        let mut source_paths = HashMap::new();
        for (child, path) in paths {
            for &source in &unit_sources[&child] {
                if source_paths.insert(source, path.clone()).is_some() {
                    return Err(GeometryError::Invalid(
                        "geometric region source appears in multiple units",
                    ));
                }
            }
        }
        plan.regions.push((key, source_paths));
        let mut children = Vec::new();
        for &child in &parent.children {
            if let Some(units) = replacements.remove(&child) {
                children.extend(units);
            } else if !consumed.contains(&child) {
                children.push(child);
            }
        }
        if children != parent.children {
            plan.block_mut(key).unwrap().children = children;
            plan.decisions.push(format!("geometric-regions:{}", parent.key));
        }
    }
    describe_panes(plan, context)
}

fn describe_panes(plan: &mut GeometricPlan, context: &GeometricContext<'_>) -> Result<()> {
    let parents = plan
        .active_ids()
        .map(|key| plan.block(key).unwrap().clone())
        .collect::<Vec<_>>();
    for parent in parents {
        if matches!(parent.kind, "table" | "collection" | "record") {
            continue;
        }
        let panes = pane_columns(&parent.children, plan, context);
        for (index, id) in panes.iter().copied().enumerate() {
            let position = if index == 0 {
                "left"
            } else if index + 1 == panes.len() {
                "right"
            } else {
                "middle"
            };
            let block = plan.block_mut(id).ok_or(GeometryError::MissingBlock(id))?;
            if block.position.as_deref() != Some(position) {
                block.position = Some(position.to_owned());
                let decision = format!("geometric-native-pane:{}:{position}", block.key);
                plan.decisions.push(decision);
            }
        }
    }
    Ok(())
}
