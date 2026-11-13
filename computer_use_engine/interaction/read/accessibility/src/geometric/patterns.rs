// Copyright (c) Huawei Technologies Co., Ltd. 2026. All rights reserved.

//! Compare native collection explanations and materialize only their display blocks.

use std::collections::{HashMap, HashSet};
use std::hash::Hash;

use super::{block_key, role, synthetic_key, Block, BlockId, GeometricContext, GeometricPlan, GeometryError, Result};
use crate::{numbering::NodeId, value::Value};

mod grid;
mod source;
mod validation;

#[cfg(test)]
mod tests;

pub(crate) use validation::pattern_issues;

fn key(plan: &GeometricPlan, id: BlockId) -> Result<&str> {
    plan.block(id).map(|block| block.key.as_str()).ok_or(GeometryError::MissingBlock(id))
}

#[derive(Clone, Debug, PartialEq, Eq)]
pub(crate) struct PatternRow {
    pub key: String,
    pub reference: Option<NodeId>,
    pub members: Vec<BlockId>,
    pub slots: Vec<usize>,
}

#[derive(Clone, Debug, PartialEq, Eq)]
pub(crate) struct PatternCandidate {
    pub kind: &'static str,
    pub supported: bool,
    pub reason: String,
    pub evidence: &'static [&'static str],
}

#[derive(Clone, Debug, PartialEq, Eq)]
pub(crate) struct PatternPlan {
    pub owner: BlockId,
    pub owner_ref: NodeId,
    pub kind: &'static str,
    pub rows: Vec<PatternRow>,
    pub remaining_keys: Vec<BlockId>,
    pub column_count: usize,
    pub column_alignments: Vec<&'static str>,
    pub wrapper_chains: Vec<Vec<BlockId>>,
    pub evidence: &'static [&'static str],
    pub candidates: Vec<PatternCandidate>,
    pub input_keys: Vec<BlockId>,
}

impl PatternPlan {
    fn new(owner: BlockId, owner_ref: NodeId, block: &Block, kind: &'static str, evidence: &'static [&'static str]) -> Self {
        Self {
            owner, owner_ref, kind, rows: Vec::new(), remaining_keys: Vec::new(), column_count: 0,
            column_alignments: Vec::new(), wrapper_chains: Vec::new(), evidence, candidates: Vec::new(),
            input_keys: block.children.clone(),
        }
    }

    pub(crate) fn to_value(&self, plan: &GeometricPlan, context: &GeometricContext<'_>) -> Result<Value> {
        let text = |value: &str| Value::Text(value.to_owned());
        let record = |items: Vec<(&str, Value)>| Value::Map(items.into_iter().map(|(key, value)| (key.to_owned(), value)).collect());
        let reference = |id| -> Result<Value> {
            context.projection.node(id).map(|node| text(&node.reference)).ok_or(GeometryError::Invalid("pattern reference"))
        };
        let keys = |ids: &[BlockId]| -> Result<Value> {
            Ok(Value::Seq(ids.iter().map(|id| key(plan, *id).map(text)).collect::<Result<_>>()?))
        };
        let number = |value: usize| -> Result<Value> {
            i64::try_from(value).map(Value::Int).map_err(|_| GeometryError::Invalid("pattern column outside native range"))
        };
        let rows = self.rows.iter().map(|row| Ok(record(vec![
            ("key", text(&row.key)),
            ("ref", row.reference.map(reference).transpose()?.unwrap_or(Value::Null)),
            ("members", keys(&row.members)?),
            ("slots", Value::Seq(row.slots.iter().map(|slot| number(*slot)).collect::<Result<_>>()?)),
        ]))).collect::<Result<Vec<_>>>()?;
        let candidates = self.candidates.iter().map(|candidate| record(vec![
            ("kind", text(candidate.kind)),
            ("supported", Value::Bool(candidate.supported)),
            ("reason", text(&candidate.reason)),
            ("evidence", Value::Seq(candidate.evidence.iter().map(|item| text(item)).collect())),
        ])).collect();
        Ok(record(vec![
            ("owner_key", text(key(plan, self.owner)?)), ("owner_ref", reference(self.owner_ref)?),
            ("kind", text(self.kind)), ("rows", Value::Seq(rows)), ("remaining_keys", keys(&self.remaining_keys)?),
            ("column_count", number(self.column_count)?),
            ("column_alignments", Value::Seq(self.column_alignments.iter().map(|item| text(item)).collect())),
            ("wrapper_chains", Value::Seq(self.wrapper_chains.iter().map(|path| keys(path)).collect::<Result<_>>()?)),
            ("evidence", Value::Seq(self.evidence.iter().map(|item| text(item)).collect())),
            ("candidates", Value::Seq(candidates)), ("input_keys", keys(&self.input_keys)?),
        ]))
    }
}

fn dominant<'a, T: Eq + Hash + 'a>(items: impl IntoIterator<Item = &'a T>) -> Option<(&'a T, usize)> {
    let mut counts = HashMap::<&T, usize>::new();
    let mut order = Vec::new();
    for item in items {
        if let std::collections::hash_map::Entry::Vacant(entry) = counts.entry(item) {
            entry.insert(1);
            order.push(item);
        } else {
            *counts.get_mut(item).expect("previously counted shape") += 1;
        }
    }
    order.into_iter().map(|item| (item, counts[item])).reduce(|left, right| if right.1 > left.1 { right } else { left })
}

fn table(plan: &GeometricPlan, context: &GeometricContext<'_>, owner: BlockId, block: &Block) -> Option<PatternPlan> {
    let owner_ref = block.primary_ref?;
    let mut rows = Vec::new();
    for child in &block.children {
        let child = plan.block(*child)?;
        if let Some(cells) = source::complete_row_cells(plan, context, child).filter(|cells| cells.len() >= 2) {
            let (members, slots) = cells.into_iter().unzip();
            rows.push(PatternRow { key: child.key.clone(), reference: child.primary_ref, members, slots });
        }
    }
    let (slots, count) = dominant(rows.iter().map(|row| &row.slots))?;
    if count < 2 || count * 4 < rows.len() * 3 {
        return None;
    }
    let slots = slots.clone();
    let columns = slots.iter().max()?.checked_add(1)?;
    let mut result = PatternPlan::new(owner, owner_ref, block, "table", &["native-row-and-cell-identities", "repeated-logical-columns"]);
    result.rows = rows.into_iter().filter(|row| row.slots == slots).collect();
    let selected: HashSet<_> = result.rows.iter().map(|row| row.key.as_str()).collect();
    result.remaining_keys = block.children.iter().copied().filter(|child| {
        plan.block(*child).is_some_and(|child| !selected.contains(child.key.as_str()))
    }).collect();
    result.column_count = columns;
    result.column_alignments = vec!["left"; columns];
    Some(result)
}

fn card_row<'a>(plan: &GeometricPlan, context: &'a GeometricContext<'_>, child: BlockId) -> Option<(Vec<BlockId>, Vec<&'a str>)> {
    let row = plan.block(child)?;
    if source::complete_row_cells(plan, context, row)?.len() != 1 {
        return None;
    }
    let chain = source::wrapper_chain(plan, context, child);
    let refs = source::source_keys(plan, child);
    if refs.iter().any(|reference| plan.evidence.get(reference).is_none_or(|evidence| evidence.incomplete)) {
        return None;
    }
    let mut shape = Vec::new();
    for reference in refs {
        let node = context.projection.node(reference)?;
        if matches!(role(node), "staticText" | "heading") && ["name", "value", "description"].iter().any(|key| {
            node.field(key).and_then(Value::as_str).is_some_and(|value| !value.trim_matches(|character: char| {
                character.is_whitespace() || matches!(character, '\u{1c}'..='\u{1f}')
            }).is_empty())
        }) {
            shape.push(role(node));
        }
    }
    (shape.len() >= 2).then_some((chain, shape))
}

fn cards(plan: &GeometricPlan, context: &GeometricContext<'_>, owner: BlockId, block: &Block) -> Option<PatternPlan> {
    let owner_ref = block.primary_ref?;
    let candidates: Vec<_> = block.children.iter().filter_map(|child| {
        card_row(plan, context, *child).map(|(chain, shape)| (*child, chain, shape))
    }).collect();
    let (shape, count) = dominant(candidates.iter().map(|(_, _, shape)| shape))?;
    if count < 2 {
        return None;
    }
    let mut result = PatternPlan::new(owner, owner_ref, block, "cards", &["native-single-cell-items", "repeated-multifield-content"]);
    let mut selected = HashSet::new();
    for (key, chain, _) in candidates.iter().filter(|(_, _, candidate)| candidate == shape) {
        let row = plan.block(*key)?;
        selected.insert(*key);
        result.rows.push(PatternRow { key: row.key.clone(), reference: row.primary_ref, members: row.children.clone(), slots: Vec::new() });
        if chain.len() > 1 {
            result.wrapper_chains.push(chain.clone());
        }
    }
    result.remaining_keys = block.children.iter().copied().filter(|child| !selected.contains(child)).collect();
    Some(result)
}

fn select(plan: &GeometricPlan, context: &GeometricContext<'_>) -> Result<Vec<PatternPlan>> {
    if !plan.enabled {
        return Ok(Vec::new());
    }
    let mut selected = Vec::new();
    for owner in plan.active_ids() {
        let block = plan.block(owner).ok_or(GeometryError::MissingBlock(owner))?;
        let Some(owner_ref) = block.primary_ref else { continue; };
        let node = context.projection.node(owner_ref).ok_or(GeometryError::Invalid("pattern owner"))?;
        if block.origin != "native" || block.layout != "tree" || block.children.is_empty()
            || !matches!(role(node), "list" | "outline" | "table")
        {
            continue;
        }
        let alternatives = [
            ("table", table(plan, context, owner, block), "requires two complete native rows with repeated logical columns"),
            ("grid", grid::select(plan, context, owner, block)?, "requires a measured whole-item run with repeated rows and columns and compatible native order"),
            ("cards", cards(plan, context, owner, block), "requires two native single-cell rows with repeated multifield content"),
        ];
        let candidates = alternatives.iter().map(|(kind, pattern, reason)| PatternCandidate {
            kind,
            supported: pattern.is_some(),
            reason: pattern.as_ref().map_or_else(|| (*reason).to_owned(), |pattern| format!("supported by {}", pattern.evidence.join(", "))),
            evidence: pattern.as_ref().map_or(&[][..], |pattern| pattern.evidence),
        }).collect();
        // The alternatives are already in decreasing native priority.
        if let Some(mut pattern) = alternatives.into_iter().find_map(|(_, pattern, _)| pattern) {
            pattern.candidates = candidates;
            selected.push(pattern);
        }
    }
    Ok(selected)
}

fn apply_grid(plan: &mut GeometricPlan, owner: &Block, pattern: &PatternPlan) -> Result<Vec<BlockId>> {
    let mut row_ids = Vec::with_capacity(pattern.rows.len());
    for row in &pattern.rows {
        let mut block = Block::new(row.key.clone(), "content", None, row.members.clone());
        block.layout = "visual-row";
        block.origin = "visual";
        block.column_slots = row.slots.iter().copied().map(Some).collect();
        row_ids.push(plan.insert(block));
    }
    let key = block_key(plan, "collection-grid", pattern.owner, &row_ids)?;
    let mut block = Block::new(key, "content", None, row_ids);
    block.layout = "visual-grid";
    block.origin = "visual";
    block.columns = (0..pattern.column_count).map(|index| index.to_string()).collect();
    block.column_prefix_refs = vec![None; pattern.column_count];
    block.column_alignments.clone_from(&pattern.column_alignments);
    let grid = plan.insert(block);
    let selected: HashSet<_> = pattern.rows.iter().flat_map(|row| row.members.iter().copied()).collect();
    let first = owner.children.iter().find(|child| selected.contains(child))
        .ok_or(GeometryError::Invalid("selected grid lost its first item"))?;
    Ok(owner.children.iter().filter_map(|child| {
        if child == first { Some(grid) } else { (!selected.contains(child)).then_some(*child) }
    }).collect())
}

fn flush_cards(plan: &mut GeometricPlan, owner: &Block, pending: &mut Vec<BlockId>, output: &mut Vec<BlockId>) -> Result<()> {
    if pending.len() >= 2 {
        let keys = pending.iter().map(|id| key(plan, *id)).collect::<Result<Vec<_>>>()?;
        let key = synthetic_key("native-card-list", &owner.key, &keys);
        let mut block = Block::new(key, "collection", None, std::mem::take(pending));
        block.layout = "record-blocks";
        block.origin = "visual";
        output.push(plan.insert(block));
    } else {
        output.append(pending);
    }
    Ok(())
}

fn apply_cards(plan: &mut GeometricPlan, owner: &Block, pattern: &PatternPlan) -> Result<Vec<BlockId>> {
    let mut selected = HashSet::new();
    for row in &pattern.rows {
        let id = plan.by_key(&row.key).ok_or(GeometryError::Invalid("selected card block"))?;
        plan.block_mut(id).ok_or(GeometryError::MissingBlock(id))?.kind = "record";
        selected.insert(id);
    }
    let mut output = Vec::new();
    let mut pending = Vec::new();
    for child in &owner.children {
        if selected.contains(child) {
            pending.push(*child);
        } else {
            flush_cards(plan, owner, &mut pending, &mut output)?;
            output.push(*child);
        }
    }
    flush_cards(plan, owner, &mut pending, &mut output)?;
    Ok(output)
}

pub(crate) fn apply_patterns(plan: &mut GeometricPlan, context: &GeometricContext<'_>) -> Result<()> {
    let selected = select(plan, context)?;
    for pattern in &selected {
        let owner = plan.block(pattern.owner).ok_or(GeometryError::MissingBlock(pattern.owner))?.clone();
        for path in &pattern.wrapper_chains {
            for key in path.iter().take(path.len().saturating_sub(1)) {
                plan.block_mut(*key).ok_or(GeometryError::MissingBlock(*key))?.layout = "record-path";
            }
        }
        let children = match pattern.kind {
            "grid" => Some(apply_grid(plan, &owner, pattern)?),
            "cards" => Some(apply_cards(plan, &owner, pattern)?),
            _ => None,
        };
        if let Some(children) = children {
            plan.block_mut(pattern.owner).ok_or(GeometryError::MissingBlock(pattern.owner))?.children = children;
        }
        let reference = context.projection.node(pattern.owner_ref).ok_or(GeometryError::Invalid("pattern owner"))?;
        plan.decisions.push(format!("geometric-pattern:{}:{}", pattern.kind, reference.reference));
    }
    plan.geometric_patterns = selected;
    Ok(())
}
