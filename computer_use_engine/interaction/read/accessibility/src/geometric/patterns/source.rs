// Copyright (c) Huawei Technologies Co., Ltd. 2026. All rights reserved.

//! Source evidence for native collection rows and visual item patterns.

use std::collections::HashSet;

use super::super::{role, Block, BlockId, GeometricContext, GeometricPlan};
use crate::{geometry::Box, numbering::NodeId, projection::ProjectionNode, value::Value};

fn nonnegative_integer(value: &Value) -> bool {
    match value {
        Value::Int(value) => *value >= 0,
        Value::BigInt(value) => !value.starts_with('-'),
        _ => false,
    }
}

fn native_range<'a>(node: &'a ProjectionNode, key: &str) -> Option<(&'a Value, &'a Value)> {
    let range = node.field(key)?;
    let start = range.get("location")?;
    let length = range.get("length")?;
    (nonnegative_integer(start) && nonnegative_integer(length) && length != &Value::Int(0))
        .then_some((start, length))
}

fn original_edge(plan: &GeometricPlan, context: &GeometricContext<'_>, parent: NodeId, child: NodeId) -> bool {
    plan.projected_parents.get(&child) == Some(&parent)
        && context.projection.node(child).is_some_and(|node| node.parent == Some(parent))
        && context.projection.node(parent).is_some_and(|node| node.native_children == [child])
}

pub(super) fn wrapper_chain(
    plan: &GeometricPlan,
    context: &GeometricContext<'_>,
    key: BlockId,
) -> Vec<BlockId> {
    let mut chain = vec![key];
    let mut seen = HashSet::from([key]);
    let mut key = key;
    while let Some(block) = plan.block(key) {
        let Some(reference) = block.primary_ref else { break };
        let [child] = block.children.as_slice() else { break };
        if block.source_refs != [reference]
            || !matches!(block.layout, "tree" | "record-path" | "chain")
            || !context.projection.node(reference).is_some_and(|node| matches!(role(node), "group" | "row" | "cell"))
            || !plan.evidence.get(&reference).is_some_and(|evidence| !evidence.incomplete)
        {
            break;
        }
        let Some(child_ref) = plan.block(*child).and_then(|block| block.primary_ref) else { break };
        if !original_edge(plan, context, reference, child_ref) || !seen.insert(*child) {
            break;
        }
        chain.push(*child);
        key = *child;
    }
    chain
}

pub(super) fn source_keys(plan: &GeometricPlan, key: BlockId) -> Vec<NodeId> {
    plan.block_sources(key)
}

pub(super) fn item_box<'a>(
    plan: &GeometricPlan,
    context: &GeometricContext<'a>,
    key: BlockId,
) -> Option<&'a Box> {
    let reference = plan.block(key)?.primary_ref?;
    context.geometry.get(&reference)
}

pub(super) fn inline_chain(
    plan: &GeometricPlan,
    context: &GeometricContext<'_>,
    key: BlockId,
) -> Option<Vec<BlockId>> {
    let chain = wrapper_chain(plan, context, key);
    if !plan.block(*chain.last()?)?.children.is_empty() {
        return None;
    }
    let first = plan.block(key)?.primary_ref?;
    let scope = context.projection.node(first)?.scope;
    for &member in &chain {
        let reference = plan.block(member)?.primary_ref?;
        let node = context.projection.node(reference)?;
        let evidence = plan.evidence.get(&reference)?;
        if node.scope != scope || evidence.hidden || evidence.incomplete
            || matches!(role(node), "window" | "sheet" | "webArea" | "document" | "scrollArea" | "table" | "outline" | "list")
        {
            return None;
        }
        if let Some(atoms) = context.atoms {
            let atom = atoms.get(&reference)?;
            if atom.partial || atom.slots.iter().any(|slot| {
                slot.block || slot.value.as_str().is_some_and(|value| value.contains(['\r', '\n']))
            }) {
                return None;
            }
        }
    }
    Some(chain)
}

pub(super) fn complete_row_cells(
    plan: &GeometricPlan,
    context: &GeometricContext<'_>,
    block: &Block,
) -> Option<Vec<(BlockId, usize)>> {
    let reference = block.primary_ref?;
    let row = context.projection.node(reference)?;
    if role(row) != "row" || block.children.is_empty() || plan.evidence.get(&reference)?.incomplete {
        return None;
    }
    let mut cells = Vec::with_capacity(block.children.len());
    let mut common_row = None;
    for &key in &block.children {
        let cell_ref = plan.block(key)?.primary_ref?;
        let node = context.projection.node(cell_ref)?;
        let (row_start, row_length) = native_range(node, "rowRange")?;
        let (column_start, column_length) = native_range(node, "columnRange")?;
        if !matches!(role(node), "cell" | "columnHeader")
            || row_length != &Value::Int(1)
            || column_length != &Value::Int(1)
            || plan.evidence.get(&cell_ref)?.incomplete
            || plan.projected_parents.get(&cell_ref) != Some(&reference)
            || node.scope != row.scope
            || common_row.is_some_and(|index| index != row_start)
        {
            return None;
        }
        let column = match column_start {
            Value::Int(index) => usize::try_from(*index).ok()?,
            Value::BigInt(index) => index.parse().ok()?,
            _ => return None,
        };
        if cells.last().is_some_and(|(_, previous)| *previous >= column) {
            return None;
        }
        common_row = Some(row_start);
        cells.push((key, column));
    }
    Some(cells)
}

#[cfg(test)]
mod tests {
    use std::collections::HashMap;

    use super::*;
    use crate::{
        geometric::{AtomLayoutEvidence, AtomSlot},
        geometry::Geometry,
        projection::{Projection, ProjectionOptions},
    };

    fn node(reference: &str, role: &str, children: Vec<Value>, fields: Vec<(&str, Value)>) -> Value {
        Value::Map(
            [
                ("ref", Value::Text(reference.to_owned())),
                ("role", Value::Text(role.to_owned())),
                ("children", Value::Seq(children)),
            ]
            .into_iter()
            .chain(fields)
            .map(|(key, value)| (key.to_owned(), value))
            .collect(),
        )
    }

    fn projection(root: Value, references: &[&str]) -> Projection {
        let aliases = references
            .iter()
            .enumerate()
            .map(|(index, reference)| (reference.to_string(), NodeId(index)))
            .collect();
        Projection::prepare(root, &aliases, ProjectionOptions::default()).unwrap()
    }

    fn atom(value: &str) -> AtomLayoutEvidence {
        AtomLayoutEvidence {
            role_label: String::new(),
            source_description: None,
            state_labels: Vec::new(),
            has_actions: false,
            actions_unavailable: false,
            declarations: false,
            partial: false,
            focused: false,
            unexpanded_menu: false,
            action_cost: 0,
            slots: vec![AtomSlot {
                key: "name".to_owned(),
                format: "text".to_owned(),
                value: Value::Text(value.to_owned()),
                block: false,
                caption: false,
                protected: false,
                preview: false,
                label: None,
            }],
        }
    }

    #[test]
    fn inline_path_requires_original_edges_and_single_line_atoms() {
        let text = node("2", "staticText", vec![], vec![]);
        let projection = projection(node("1", "group", vec![text], vec![]), &["1", "2"]);
        let bounds = Box { space: "window".to_owned(), x: 10.0, y: 20.0, width: 30.0, height: 40.0 };
        let geometry = Geometry::from([(NodeId(0), bounds)]);
        let context = GeometricContext { projection: &projection, geometry: &geometry, facts: None, atoms: None };
        let mut plan = GeometricPlan::seed(&context, true).unwrap();
        let root = plan.source_block(NodeId(0)).unwrap();
        let leaf = plan.source_block(NodeId(1)).unwrap();

        assert_eq!(inline_chain(&plan, &context, root), Some(vec![root, leaf]));
        assert_eq!(source_keys(&plan, root), [NodeId(0), NodeId(1)]);
        assert_eq!(item_box(&plan, &context, root), geometry.get(&NodeId(0)));
        assert!(item_box(&plan, &context, leaf).is_none());

        let atoms = HashMap::from([(NodeId(0), atom("container")), (NodeId(1), atom("two\rlines"))]);
        let atom_context = GeometricContext { atoms: Some(&atoms), ..context };
        assert_eq!(inline_chain(&plan, &atom_context, root), None);

        plan.projected_parents.remove(&NodeId(1));
        assert_eq!(wrapper_chain(&plan, &context, root), [root]);
        assert_eq!(inline_chain(&plan, &context, root), None);
    }

    fn range(start: Value, length: Value) -> Value {
        Value::Map(vec![("location".to_owned(), start), ("length".to_owned(), length)])
    }

    #[test]
    fn row_requires_one_logical_index_and_native_column_order() {
        let index = Value::BigInt("9223372036854775808".to_owned());
        let cell = |reference, role, column| {
            node(reference, role, vec![], vec![
                ("rowRange", range(index.clone(), Value::Int(1))),
                ("columnRange", range(Value::Int(column), Value::Int(1))),
            ])
        };
        let row = node("1", "row", vec![cell("2", "columnHeader", 0), cell("3", "cell", 2)], vec![]);
        let projection = projection(row, &["1", "2", "3"]);
        let geometry = Geometry::new();
        let context = GeometricContext { projection: &projection, geometry: &geometry, facts: None, atoms: None };
        let mut plan = GeometricPlan::seed(&context, true).unwrap();
        let root = plan.source_block(NodeId(0)).unwrap();
        let children = plan.block(root).unwrap().children.clone();
        assert_eq!(
            complete_row_cells(&plan, &context, plan.block(root).unwrap()),
            Some(vec![(children[0], 0), (children[1], 2)]),
        );
        plan.block_mut(root).unwrap().children.reverse();
        assert_eq!(complete_row_cells(&plan, &context, plan.block(root).unwrap()), None);
    }
}
