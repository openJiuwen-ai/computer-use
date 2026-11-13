// Copyright (c) Huawei Technologies Co., Ltd. 2026. All rights reserved.

use std::collections::{BTreeSet, HashMap, HashSet};

use super::{
    compound, field_text, patterns::pattern_issues, role, rows::inline_unit_refs, same_prefix,
    units::transparent_group, Block, BlockId, GeometricContext, GeometricPlan, GeometryError, NodeId, Result,
};

fn column_issue(
    plan: &GeometricPlan,
    context: &GeometricContext<'_>,
    block: &Block,
    rows: &[&Block],
) -> Option<&'static str> {
    let slots: Vec<Vec<_>> = rows
        .iter()
        .map(|row| {
            if row.column_slots.is_empty() {
                (0..row.children.len()).map(Some).collect()
            } else {
                row.column_slots.clone()
            }
        })
        .collect();
    if rows.len() < 2
        || block.columns.is_empty()
        || block.column_prefix_refs.len() != block.columns.len()
        || !block.column_alignments.is_empty()
            && (block.column_alignments.len() != block.columns.len()
                || block
                    .column_alignments
                    .iter()
                    .any(|kind| !matches!(*kind, "left" | "right" | "center")))
        || rows.iter().zip(&slots).any(|(row, slots)| {
            let used = slots.iter().flatten().copied().collect::<Vec<_>>();
            slots.len() != row.children.len()
                || slots.is_empty()
                || used.iter().any(|&i| i >= block.columns.len())
                || used.windows(2).any(|pair| pair[0] >= pair[1])
                || row.column_slots.is_empty() && slots.len() != block.columns.len()
        })
    {
        return Some("rows differ from their typed column plan");
    }
    if block.layout == "visual-grid"
        && (block.primary_ref.is_some()
            || !block.source_refs.is_empty()
            || block.column_prefix_refs.iter().any(Option::is_some)
            || rows
                .iter()
                .any(|row| row.layout != "visual-row" || row.primary_ref.is_some() || !row.source_refs.is_empty())
            || rows
                .iter()
                .flat_map(|row| &row.children)
                .any(|&child| inline_unit_refs(child, plan).is_none()))
    {
        return Some("visual grid lacks complete independently rendered units");
    }
    for (index, prefix) in block.column_prefix_refs.iter().enumerate() {
        let refs: Vec<_> = rows
            .iter()
            .zip(&slots)
            .flat_map(|(row, indices)| {
                row.children.iter().zip(indices).filter_map(|(&child, slot)| {
                    (*slot == Some(index)).then(|| plan.block(child).and_then(|block| block.primary_ref))
                })
            })
            .collect();
        if refs.is_empty()
            || prefix.is_some_and(|prefix| {
                !refs.contains(&Some(prefix))
                    || refs
                        .iter()
                        .any(|reference| !reference.is_some_and(|id| same_prefix(context, id, prefix)))
            })
        {
            return Some("column prefix differs from original member roles or states");
        }
    }
    None
}

pub(super) fn layout_issues(plan: &GeometricPlan, context: &GeometricContext<'_>) -> Vec<(BlockId, &'static str)> {
    let mut issues = pattern_issues(plan, context);
    for key in plan.active_ids() {
        let block = plan.block(key).unwrap();
        let children: Vec<_> = block.children.iter().map(|&child| plan.block(child).unwrap()).collect();
        let mut reason = None;
        if block
            .shared_prefix_ref
            .is_some_and(|id| !plan.evidence.contains_key(&id))
        {
            reason = Some("shared prefix has no captured source");
        } else if block.layout == "source-only" {
            if block.primary_ref.is_none()
                || block.source_refs.as_slice() != block.primary_ref.as_slice()
                || !block.children.is_empty()
                || block.primary_ref.is_none_or(|id| !transparent_group(plan, context, id))
            {
                reason = Some("source-only wrapper lacks complete passive source evidence");
            }
        } else if block.layout == "compound-control" {
            reason = compound::issue(block, plan, context);
        } else if block.kind == "field" && block.field_target_ref.is_none() {
            let labels: Vec<_> = children
                .iter()
                .filter(|member| member.primary_ref == block.label_ref)
                .collect();
            if block.primary_ref.is_some()
                || !matches!(block.layout, "fields" | "field-inline")
                || block.label_ref.is_none()
                || labels.len() != 1
                || !labels[0].children.is_empty()
                || children.len() < 2
            {
                reason = Some("visual field lacks its original atomic label and independent members");
            }
        } else if block.layout == "control-unit" {
            if !block.visual_unit || block.field_target_ref.is_none() || block.kind != "field" {
                reason = Some("control unit lacks its original control target and membership evidence");
            }
        } else if matches!(block.layout, "chain" | "record-path") {
            let pair = match (block.primary_ref, children.as_slice()) {
                (Some(parent), [child]) => child.primary_ref.map(|id| (parent, id)),
                _ => None,
            };
            match pair
                .and_then(|(parent, child)| Some((context.projection.node(parent)?, context.projection.node(child)?)))
            {
                Some((parent, child))
                    if parent.children == [child.id] && plan.projected_parents.get(&child.id) == Some(&parent.id) =>
                {
                    if block.layout == "chain" && field_text(parent, "visualChainChild") != Some(&child.reference) {
                        reason = Some("visual chain has no source evidence");
                    } else if block.layout == "record-path" && child.parent != Some(parent.id) {
                        reason = Some("record path crosses original native parents");
                    }
                }
                _ => reason = Some("path differs from its unique original source edge"),
            }
        } else if block.layout == "compact-list" {
            if block.primary_ref.is_some()
                || block.shared_prefix_ref.is_none()
                || children.len() < 3
                || children
                    .iter()
                    .any(|member| member.primary_ref.is_none() || !member.children.is_empty())
                || children.first().and_then(|member| member.primary_ref) != block.shared_prefix_ref
            {
                reason = Some("compact list lacks its independent leaf targets");
            }
        } else if matches!(block.layout, "records" | "visual-grid") {
            reason = column_issue(plan, context, block, &children);
        } else if block.layout == "record-blocks" {
            let refs: Vec<_> = children.iter().map(|child| child.primary_ref).collect();
            if block.kind != "collection"
                || block.primary_ref.is_some()
                || children.len() < 2
                || refs.iter().any(|id| {
                    id.and_then(|id| context.projection.node(id))
                        .is_none_or(|node| !matches!(role(node), "group" | "row"))
                })
                || refs
                    .iter()
                    .flatten()
                    .filter_map(|id| plan.evidence.get(id).map(|node| node.scope))
                    .collect::<HashSet<_>>()
                    .len()
                    != 1
            {
                reason = Some("record blocks lack separate original rows in one scope");
            }
        } else if block.layout == "visual-row" {
            if block.children.iter().zip(&children).any(|(&key, child)| {
                inline_unit_refs(key, plan).is_none() || child.visual_unit && child.layout != "control-unit"
            }) {
                reason = Some("visual row lacks complete single-line units");
            }
        } else if block.layout == "inline" {
            if children
                .iter()
                .any(|member| member.primary_ref.is_none() || !member.children.is_empty())
            {
                reason = Some("inline layout has a non-atomic member");
            }
        } else if block.layout == "rows"
            && (block.shared_prefix_ref.is_none() || children.iter().any(|child| child.layout != "inline"))
        {
            reason = Some("row group lacks its typed inline children");
        }
        if let Some(prefix) = block.shared_prefix_ref.filter(|_| reason.is_none()) {
            let refs = match block.layout {
                "inline" | "compact-list" | "record-blocks" => {
                    children.iter().map(|member| member.primary_ref).collect::<Vec<_>>()
                }
                "rows" => children
                    .iter()
                    .flat_map(|row| {
                        row.children
                            .iter()
                            .map(|&id| plan.block(id).and_then(|block| block.primary_ref))
                    })
                    .collect(),
                _ => Vec::new(),
            };
            if refs
                .iter()
                .any(|reference| !reference.is_some_and(|id| same_prefix(context, id, prefix)))
            {
                reason = Some("shared prefix differs from original member roles or states");
            }
        }
        if let Some(reason) = reason {
            issues.push((key, reason));
        }
    }
    issues
}

fn source_members(context: &GeometricContext<'_>, root: NodeId) -> Result<HashSet<NodeId>> {
    let mut found = HashSet::new();
    let mut remaining = vec![root];
    while let Some(id) = remaining.pop() {
        if !found.insert(id) {
            return Err(GeometryError::Invalid("original source hierarchy is not a tree"));
        }
        let node = context
            .projection
            .node(id)
            .ok_or(GeometryError::Invalid("original recovery source is absent"))?;
        remaining.extend(node.children.iter().copied());
    }
    Ok(found)
}

fn source_forest(
    plan: &GeometricPlan,
    context: &GeometricContext<'_>,
    refs: &HashSet<NodeId>,
) -> Result<Option<Vec<NodeId>>> {
    let roots: Vec<_> = plan
        .target_refs
        .iter()
        .copied()
        .filter(|id| {
            refs.contains(id)
                && plan
                    .projected_parents
                    .get(id)
                    .is_none_or(|parent| !refs.contains(parent))
        })
        .collect();
    let mut covered = HashSet::new();
    for &id in &roots {
        let members = source_members(context, id)?;
        if !members.is_subset(refs) {
            return Ok(None);
        }
        if !members.is_disjoint(&covered) {
            return Err(GeometryError::Invalid("original recovery roots overlap"));
        }
        covered.extend(members);
    }
    Ok((!roots.is_empty() && &covered == refs).then_some(roots))
}

#[derive(Default)]
struct Subtree {
    keys: HashSet<BlockId>,
    refs: HashSet<NodeId>,
}

fn subtrees(plan: &GeometricPlan) -> HashMap<BlockId, Subtree> {
    let mut output = HashMap::<BlockId, Subtree>::new();
    let mut pending: Vec<_> = plan.roots.iter().rev().map(|&id| (id, false)).collect();
    while let Some((key, finish)) = pending.pop() {
        let block = plan.block(key).expect("validated source");
        if finish {
            let mut tree = Subtree {
                keys: HashSet::from([key]),
                refs: block.source_refs.iter().copied().collect(),
            };
            for child in &block.children {
                let child = &output[child];
                tree.keys.extend(child.keys.iter().copied());
                tree.refs.extend(child.refs.iter().copied());
            }
            output.insert(key, tree);
        } else {
            pending.push((key, true));
            pending.extend(block.children.iter().rev().map(|&id| (id, false)));
        }
    }
    output
}

fn recover(plan: &mut GeometricPlan, context: &GeometricContext<'_>, issues: &[(BlockId, &'static str)]) -> Result<()> {
    let parents: HashMap<_, _> = plan
        .active_ids()
        .flat_map(|id| plan.block(id).unwrap().children.iter().map(move |&child| (child, id)))
        .collect();
    let subtrees = subtrees(plan);
    let mut selected = Vec::<(BlockId, Vec<&str>, Vec<NodeId>)>::new();
    for &(mut key, reason) in issues {
        loop {
            let primary = plan.block(key).ok_or(GeometryError::MissingBlock(key))?.primary_ref;
            if let Some(roots) = source_forest(plan, context, &subtrees[&key].refs)?
                .filter(|roots| primary.is_none_or(|primary| roots == &[primary]))
            {
                if let Some((_, reasons, _)) = selected.iter_mut().find(|(id, _, _)| *id == key) {
                    reasons.push(reason);
                } else {
                    selected.push((key, vec![reason], roots));
                }
                break;
            }
            key = *parents.get(&key).ok_or(GeometryError::Invalid(
                "invalid layout has no closed original recovery scope",
            ))?;
        }
    }
    let mut replacements = HashMap::new();
    let mut restored_keys = HashSet::new();
    let mut restored_refs = HashSet::new();
    for (key, _, roots) in &selected {
        let key = *key;
        if selected
            .iter()
            .any(|(other, _, _)| *other != key && subtrees[other].keys.contains(&key))
        {
            continue;
        }
        let tree = &subtrees[&key];
        let previous = plan.block(key).unwrap().clone();
        restored_keys.extend(tree.keys.iter().copied());
        restored_refs.extend(tree.refs.iter().copied());
        for member in &tree.keys {
            plan.remove(*member);
        }
        let ids: Vec<_> = plan
            .target_refs
            .iter()
            .copied()
            .filter(|id| tree.refs.contains(id))
            .collect();
        for id in &ids {
            let node = context.projection.node(*id).expect("validated source");
            let key = format!("element:{}", node.reference);
            if plan.by_key(&key).is_some() {
                return Err(GeometryError::Invalid(
                    "recovered source block would replace an unrelated block",
                ));
            }
            let block = plan.insert(Block::new(key, "tree", Some(*id), Vec::new()));
            plan.source_blocks.insert(*id, block);
        }
        for id in ids {
            let node = context.projection.node(id).unwrap();
            let key = plan.source_blocks[&id];
            let children = node.children.iter().map(|child| plan.source_blocks[child]).collect();
            plan.block_mut(key).unwrap().children = children;
        }
        let replacement = if let Some(reference) = previous.primary_ref {
            plan.source_blocks[&reference]
        } else {
            let region = previous.kind == "region";
            let mut block = Block::new(
                previous.key.clone(),
                if region { "region" } else { "tree" },
                None,
                roots.iter().map(|id| plan.source_blocks[id]).collect(),
            );
            block.origin = previous.origin;
            if region {
                block.label_ref = previous.label_ref.filter(|id| tree.refs.contains(id));
                block.position = previous.position;
                block.region_role = previous.region_role;
            }
            plan.insert(block)
        };
        replacements.insert(key, replacement);
        let reasons = selected
            .iter()
            .filter(|(id, _, _)| tree.keys.contains(id))
            .flat_map(|(_, reasons, _)| reasons.iter().copied())
            .collect::<BTreeSet<_>>();
        let owner = previous
            .primary_ref
            .and_then(|id| context.projection.node(id).map(|node| node.reference.as_str()))
            .unwrap_or(&previous.key);
        plan.decisions.push(format!(
            "local-layout-fallback:{owner}: {}",
            reasons.into_iter().collect::<Vec<_>>().join("; ")
        ));
    }
    for root in &mut plan.roots {
        if let Some(&replacement) = replacements.get(root) {
            *root = replacement;
        }
    }
    for id in plan.active_ids().collect::<Vec<_>>() {
        for child in &mut plan.block_mut(id).unwrap().children {
            if let Some(&replacement) = replacements.get(child) {
                *child = replacement;
            }
        }
    }
    plan.geometric_patterns
        .retain(|pattern| !restored_keys.contains(&pattern.owner) && !restored_refs.contains(&pattern.owner_ref));
    Ok(())
}

pub(super) fn finish(mut plan: GeometricPlan, context: &GeometricContext<'_>) -> Result<GeometricPlan> {
    plan.validate_sources()?;
    let issues = layout_issues(&plan, context);
    if !issues.is_empty() {
        recover(&mut plan, context, &issues)?;
        plan.validate_sources()?;
        if !layout_issues(&plan, context).is_empty() {
            return Err(GeometryError::Invalid(
                "geometric recovery did not preserve a valid local layout",
            ));
        }
    }
    plan.freeze()
}
