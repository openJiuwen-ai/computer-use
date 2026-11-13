// Copyright (c) Huawei Technologies Co., Ltd. 2026. All rights reserved.

//! Share equivalent initial coverage at a displayed ancestor while preserving
//! the original per-target evidence for details and comparison.

use std::collections::{HashMap, HashSet};
use std::hash::Hash;

use super::super::{numbering::NodeId, projection::Projection, render::atoms::{AtomPlan, Coverage}, value::Value};
use super::has_value;

pub(crate) struct CoverageBlock<'a, B> {
    pub(crate) primary: Option<NodeId>,
    pub(crate) source_refs: &'a [NodeId],
    pub(crate) children: &'a [B],
}

#[derive(Debug, Eq, PartialEq)]
pub(crate) struct SharedCoverage {
    pub(crate) refs: Vec<NodeId>,
    pub(crate) total: i64,
    pub(crate) reason: String,
    pub(crate) scope: NodeId,
}

#[derive(Debug)]
pub(crate) struct CoveragePlan<B> {
    pub(crate) by_block: Vec<(B, Vec<SharedCoverage>)>,
    pub(crate) omitted_refs: HashSet<NodeId>,
}

#[derive(Debug, Eq, PartialEq)]
pub(crate) enum CoverageError {
    MissingSource,
    MissingAtom,
    MissingBlock,
    InvalidForest,
}

impl<B> Default for CoveragePlan<B> {
    fn default() -> Self {
        Self { by_block: Vec::new(), omitted_refs: HashSet::new() }
    }
}

fn boundary(role: Option<&Value>) -> bool {
    matches!(role.and_then(Value::as_str), Some("window" | "sheet" | "menuBar" | "menu"))
}

fn original_scope(source: &Projection, id: NodeId) -> Result<NodeId, CoverageError> {
    let mut current = Some(id);
    let mut visited = HashSet::new();
    while let Some(id) = current {
        if !visited.insert(id) { return Err(CoverageError::InvalidForest); }
        let node = source.node(id).ok_or(CoverageError::MissingSource)?;
        let role = node.native_fields.iter().find(|(key, _)| key == "role").map(|(_, value)| value);
        if boundary(role) { return Ok(id); }
        current = node.parent;
    }
    source.node(id).map(|node| node.scope).ok_or(CoverageError::MissingSource)
}

fn empty_initial<'a>(source: &Projection, coverage: &'a Coverage, id: NodeId) -> Option<(i64, &'a str)> {
    let total = coverage.total.filter(|total| *total > 0)?;
    let reason = coverage.reason.as_deref().filter(|reason| !reason.is_empty())?;
    if coverage.unexpanded_menu || coverage.start != Some(0) || coverage.end != Some(0)
        || coverage.read.is_some_and(|read| read != 0) || coverage.next.is_some() || !coverage.omissions.is_empty()
    {
        return None;
    }
    let node = source.node(id)?;
    let check = |fields: &[(String, Value)], children: &[NodeId]| {
        let field = |key: &str| fields.iter().find(|(name, _)| name == key).map(|(_, value)| value);
        let zero = Some(&Value::Int(0));
        field("childrenStart") == zero && field("childrenEnd") == zero
            && field("childCount") == Some(&Value::Int(total)) && children.is_empty()
            && field("partialReason").and_then(Value::as_str) == Some(reason)
            && !has_value(field("childOmissions")) && field("nextChildOffset").is_none()
            && field("childrenRead").is_none_or(|read| matches!(read, Value::Int(0) | Value::Bool(false)))
    };
    if !check(&node.fields, &node.children) || !check(&node.native_fields, &node.native_children) { return None; }
    Some((total, reason))
}

fn common_prefix<B: Eq>(shared: &mut Vec<B>, next: &[B]) {
    let common = shared.iter().zip(next).take_while(|(left, right)| left == right).count();
    shared.truncate(common);
}

pub(crate) fn plan<'a, B: Copy + Eq + Hash + 'a>(
    source: &Projection,
    atoms: &AtomPlan,
    roots: &[B],
    target_refs: &[NodeId],
    block: impl Fn(B) -> Option<CoverageBlock<'a, B>>,
) -> Result<CoveragePlan<B>, CoverageError> {
    let mut paths = HashMap::<NodeId, Vec<B>>::new();
    let mut scopes = HashMap::new();
    let mut visited = HashSet::new();
    let mut pending: Vec<_> = roots.iter().rev().map(|id| (*id, Vec::new(), *id)).collect();
    while let Some((id, mut path, mut scope)) = pending.pop() {
        if !visited.insert(id) { return Err(CoverageError::InvalidForest); }
        let entry = block(id).ok_or(CoverageError::MissingBlock)?;
        if let Some(reference) = entry.primary {
            let node = source.node(reference).ok_or(CoverageError::MissingSource)?;
            if boundary(node.field("role")) { scope = id; }
        }
        path.push(id);
        for reference in entry.source_refs {
            paths.insert(*reference, path.clone());
            scopes.insert(*reference, scope);
        }
        pending.extend(entry.children.iter().rev().map(|child| (*child, path.clone(), scope)));
    }
    let mut keys = HashMap::new();
    let mut candidates: Vec<(NodeId, i64, &str, Vec<NodeId>)> = Vec::new();
    for reference in target_refs {
        let coverage = &atoms.atoms.get(reference).ok_or(CoverageError::MissingAtom)?.coverage;
        let Some((total, reason)) = empty_initial(source, coverage, *reference) else { continue };
        let scope = original_scope(source, *reference)?;
        let shown_scope = scopes.get(reference).ok_or(CoverageError::MissingBlock)?;
        let index = *keys.entry((scope, *shown_scope, total, reason)).or_insert_with(|| {
            candidates.push((scope, total, reason, Vec::new()));
            candidates.len() - 1
        });
        candidates[index].3.push(*reference);
    }
    let mut plan = CoveragePlan::default();
    let mut owner_indexes = HashMap::new();
    for (scope, total, reason, refs) in candidates {
        if refs.len() < 2 { continue; }
        let mut common = paths.get(&refs[0]).ok_or(CoverageError::MissingBlock)?.clone();
        let mut references_cost = 0usize;
        for reference in &refs {
            common_prefix(&mut common, paths.get(reference).ok_or(CoverageError::MissingBlock)?);
            let reference = &source.node(*reference).ok_or(CoverageError::MissingSource)?.reference;
            references_cost = references_cost.saturating_add(reference.chars().count() + 2);
        }
        let Some(owner) = common.last().copied() else { continue };
        let total_width = total.to_string().len();
        let separate = refs.len().saturating_mul(42 + total_width);
        let shared = 75usize.saturating_add(total_width).saturating_add(reason.chars().count()).saturating_add(references_cost);
        if shared >= separate { continue; }
        let index = *owner_indexes.entry(owner).or_insert_with(|| {
            plan.by_block.push((owner, Vec::new()));
            plan.by_block.len() - 1
        });
        plan.omitted_refs.extend(refs.iter().copied());
        plan.by_block[index].1.push(SharedCoverage { refs, total, reason: reason.to_owned(), scope });
    }
    Ok(plan)
}

#[cfg(test)]
mod tests {
    use super::*;

    fn node(reference: &str, role: &str, children: Vec<Value>, empty_page: bool) -> Value {
        let mut data = vec![
            ("ref".to_owned(), Value::Text(reference.to_owned())),
            ("role".to_owned(), Value::Text(role.to_owned())),
            ("children".to_owned(), Value::Seq(children)),
        ];
        if empty_page {
            data.extend([
                ("childrenStart".to_owned(), Value::Int(0)),
                ("childrenEnd".to_owned(), Value::Int(0)),
                ("childCount".to_owned(), Value::Int(20)),
                ("partialReason".to_owned(), Value::Text("gap".to_owned())),
            ]);
        }
        Value::Map(data)
    }

    #[test]
    fn nearest_common_ancestor_receives_equivalent_hints_without_menu_boundary_leaks() {
        let aliases = (0..4).map(|id| (id.to_string(), NodeId(id))).collect();
        let source = Projection::prepare(node("0", "window", vec![
            node("1", "menuItem", vec![], true),
            node("2", "menuItem", vec![], true),
            node("3", "menuBarItem", vec![], true),
        ], false), &aliases, Default::default()).unwrap();
        let refs = [NodeId(0), NodeId(1), NodeId(2), NodeId(3)];
        let blocks = [1, 2, 3];
        let atoms = crate::render::atoms::plan_atoms(&source, &Default::default()).unwrap();
        let plan = plan(&source, &atoms, &[0], &refs, |id| {
            refs.get(id).map(|reference| CoverageBlock {
                primary: Some(*reference),
                source_refs: std::slice::from_ref(reference),
                children: if id == 0 { &blocks } else { &[] },
            })
        }).unwrap();
        assert_eq!(plan.by_block.len(), 1);
        assert_eq!(plan.by_block[0].0, 0);
        assert_eq!(plan.by_block[0].1[0].refs, [NodeId(1), NodeId(2)]);
        assert_eq!(plan.omitted_refs, HashSet::from([NodeId(1), NodeId(2)]));
    }
}
