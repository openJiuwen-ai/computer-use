// Copyright (c) Huawei Technologies Co., Ltd. 2026. All rights reserved.

//! Complex record blocks with retained native members and independent controls.

use std::collections::{HashMap, HashSet};

use super::super::{
    field_text, role, same_prefix, synthetic_key, truth, Block, BlockId, GeometricContext, GeometricPlan,
    GeometryError, Relation, Result,
};
use crate::{geometry::Box, numbering::NodeId, projection::ProjectionNode, value::Value};

#[derive(PartialEq, Eq)]
enum ContentShape {
    Named(&'static str, Vec<ContentShape>),
    Children(Vec<ContentShape>),
}

struct RecordCandidate<'a> {
    key: BlockId,
    reference: NodeId,
    parent: NodeId,
    shape: Vec<ContentShape>,
    bounds: &'a Box,
    complex: bool,
}

pub(in crate::geometric) fn child_relations(relations: &[Relation]) -> HashMap<NodeId, Vec<usize>> {
    let mut children = HashMap::<_, Vec<_>>::new();
    for (index, relation) in relations.iter().enumerate() {
        if relation.kind == "children" {
            children.entry(relation.source).or_default().push(index);
        }
    }
    children
}

pub(in crate::geometric) fn confirmed_children(
    relations: &[Relation],
    index: &HashMap<NodeId, Vec<usize>>,
    parent: NodeId,
    children: &[NodeId],
) -> bool {
    index.get(&parent).is_some_and(|matches| {
        !matches.is_empty()
            && matches.iter().all(|&index| {
                let relation = &relations[index];
                relation.complete && relation.confirmed && relation.targets == children
            })
    })
}

fn boundary(role: &str) -> bool {
    matches!(
        role,
        "window" | "sheet" | "table" | "outline" | "list" | "row" | "cell" | "column"
            | "document" | "webArea" | "scrollArea" | "menu" | "menuBar"
    )
}

pub(in crate::geometric) fn known_control(context: &GeometricContext<'_>, reference: NodeId) -> bool {
    let Some(node) = context.projection.node(reference) else { return false };
    let Some(atom) = context.atoms.and_then(|atoms| atoms.get(&reference)) else { return false };
    let capabilities = node.field("capabilities");
    let reported = ["click", "typeText", "replaceText"].into_iter().any(|key| {
        capabilities.and_then(|value| value.get(key)).and_then(Value::as_str) == Some("available")
    });
    let role = role(node);
    let control_role = matches!(
        role,
        "button" | "menuButton" | "checkBox" | "radioButton" | "switch" | "popUpButton" | "comboBox"
            | "slider" | "incrementor" | "closeButton" | "minimizeButton" | "zoomButton" | "textField" | "textArea"
    );
    (control_role && (atom.has_actions || reported))
        || (node.children.is_empty() && !boundary(role) && role != "link" && reported)
}

fn native_role(node: &ProjectionNode) -> Option<&str> {
    node.native_fields.iter().find(|(key, _)| key == "role").and_then(|(_, value)| value.as_str())
}

fn record_candidate<'a>(
    plan: &GeometricPlan,
    context: &GeometricContext<'a>,
    key: BlockId,
    child_index: &HashMap<NodeId, Vec<usize>>,
) -> Option<RecordCandidate<'a>> {
    let atoms = context.atoms?;
    let block = plan.block(key)?;
    let reference = block.primary_ref?;
    let node = context.projection.node(reference)?;
    if block.origin != "native"
        || block.layout != "tree"
        || !matches!(block.kind, "tree" | "record")
        || !matches!(role(node), "group" | "row")
        || block.children.is_empty()
    {
        return None;
    }
    let parent = node.parent?;
    let outer_node = context.projection.node(parent)?;
    if plan.projected_parents.get(&reference) != Some(&parent)
        || matches!(native_role(outer_node), Some("table" | "outline"))
        || outer_node.native_children.iter().filter(|&&child| {
            matches!(context.projection.node(child).and_then(native_role), Some("group" | "row"))
        }).take(2).count() < 2
        || !confirmed_children(&plan.relations, child_index, parent, &outer_node.native_children)
    {
        return None;
    }
    let bounds = context.geometry.get(&reference)?;
    let outer_bounds = context.geometry.get(&parent)?;
    if !outer_bounds.contains(bounds) {
        return None;
    }

    let mut members = vec![parent];
    let mut seen = HashSet::new();
    let mut controls = HashSet::new();
    let mut complex = false;
    let mut pending = vec![reference];
    while let Some(current) = pending.pop() {
        if !seen.insert(current) {
            return None;
        }
        members.push(current);
        let node = context.projection.node(current)?;
        let evidence = plan.evidence.get(&current)?;
        let member_bounds = context.geometry.get(&current)?;
        if evidence.incomplete || evidence.hidden || !bounds.contains(member_bounds)
            || (current != reference && boundary(role(node)))
            || node.field("modal") == Some(&Value::Bool(true))
            || truth(node.field("blockedBy"))
        {
            return None;
        }
        if !node.native_children.is_empty()
            && !confirmed_children(&plan.relations, child_index, current, &node.native_children)
        {
            return None;
        }
        if known_control(context, current) {
            controls.insert(current);
        }
        let atom = atoms.get(&current)?;
        complex |= (current != reference && !node.children.is_empty())
            || atom.slots.iter().any(|slot| slot.value.as_str().is_some_and(|value| {
                value.contains(['\n', '\r']) || value.chars().nth(160).is_some()
            }));
        pending.extend(node.children.iter().rev().copied());
    }
    if controls.is_empty() || !plan.same_scope(&members, context) {
        return None;
    }

    let mut content = HashMap::new();
    // The record's own name is excluded. Its direct children supply its shape.
    for &member in members[2..].iter().rev() {
        let member_node = context.projection.node(member)?;
        let children = member_node.children.iter().filter_map(|child| content.remove(child)).collect::<Vec<_>>();
        if controls.contains(&member) {
            continue;
        }
        let named = ["name", "value", "description"].into_iter().any(|key| {
            field_text(member_node, key).is_some_and(|value| !value.is_empty())
        });
        let tag = if named {
            match role(member_node) {
                "staticText" => Some("staticText"),
                "heading" => Some("heading"),
                "image" => Some("image"),
                "group" => Some("caption"),
                _ => None,
            }
        } else {
            None
        };
        if let Some(tag) = tag {
            content.insert(member, ContentShape::Named(tag, children));
        } else if !children.is_empty() {
            content.insert(member, ContentShape::Children(children));
        }
    }
    let shape = node.children.iter().filter_map(|child| content.remove(child)).collect::<Vec<_>>();
    if shape.is_empty() {
        return None;
    }
    Some(RecordCandidate { key, reference, parent, shape, bounds, complex })
}

#[derive(Clone, Copy, PartialEq, Eq)]
enum Axis {
    Vertical,
    Horizontal,
}

fn peer_axis(left: &RecordCandidate<'_>, right: &RecordCandidate<'_>) -> Option<Axis> {
    let (left_bounds, right_bounds) = (left.bounds, right.bounds);
    if left.parent != right.parent || left.shape != right.shape
        || !left_bounds.comparable(right_bounds) || left_bounds.intersection_area(right_bounds) > 0.0
    {
        return None;
    }
    if right_bounds.y >= left_bounds.bottom() && left_bounds.horizontal_overlap(right_bounds) >= 0.6
        && right_bounds.y - left_bounds.bottom() <= left_bounds.height.max(right_bounds.height)
    {
        return Some(Axis::Vertical);
    }
    if right_bounds.x >= left_bounds.right() && left_bounds.vertical_overlap(right_bounds) >= 0.6
        && right_bounds.x - left_bounds.right() <= left_bounds.width.max(right_bounds.width)
    {
        return Some(Axis::Horizontal);
    }
    None
}

pub(in crate::geometric) fn build_record_blocks(
    plan: &mut GeometricPlan,
    context: &GeometricContext<'_>,
) -> Result<()> {
    if !plan.enabled || context.atoms.is_none() {
        return Ok(());
    }
    let child_index = child_relations(&plan.relations);
    let candidates: HashMap<_, _> = plan.active_ids().filter_map(|key| {
        record_candidate(plan, context, key, &child_index).map(|candidate| (key, candidate))
    }).collect();
    // The pass visits original parent blocks in order. Earlier grouping can
    // change a member's kind without changing that original parent decision.
    let parents: Vec<_> = plan.active_ids().filter_map(|key| {
        let block = plan.block(key)?;
        (block.layout == "tree" && !matches!(block.kind, "table" | "record" | "field"))
            .then(|| (key, block.kind, block.children.clone()))
    }).collect();
    for (parent, kind, children) in parents {
        let mut result = Vec::new();
        let mut index = 0;
        while index < children.len() {
            let Some(first) = candidates.get(&children[index]) else {
                result.push(children[index]);
                index += 1;
                continue;
            };
            let mut members = vec![first];
            let mut axis = None;
            let mut end = index + 1;
            while end < children.len() {
                let Some(candidate) = candidates.get(&children[end]) else { break };
                let Some(next_axis) = peer_axis(members[members.len() - 1], candidate) else { break };
                if axis.is_some_and(|axis| axis != next_axis) {
                    break;
                }
                let references: Vec<_> = members.iter().copied().chain(std::iter::once(candidate))
                    .flat_map(|member| plan.block_sources(member.key)).collect();
                if !plan.same_region(&references) {
                    break;
                }
                axis = Some(next_axis);
                members.push(candidate);
                end += 1;
            }
            if members.len() < 2 || !members.iter().any(|member| member.complex) {
                result.extend_from_slice(&children[index..end]);
                index = end;
                continue;
            }
            let refs = members.iter().map(|member| {
                context.projection.node(member.reference).map(|node| node.reference.as_str())
                    .ok_or(GeometryError::Invalid("missing record source"))
            }).collect::<Result<Vec<_>>>()?;
            let parent_key = &plan.block(parent).ok_or(GeometryError::MissingBlock(parent))?.key;
            let key = synthetic_key("record-blocks", parent_key, &refs);
            let mut collection = Block::new(
                key, "collection", None, members.iter().map(|member| member.key).collect(),
            );
            collection.layout = "record-blocks";
            collection.origin = "visual";
            collection.shared_prefix_ref = members.iter()
                .all(|member| same_prefix(context, first.reference, member.reference)).then_some(first.reference);
            let collection = plan.insert(collection);
            for member in members {
                plan.block_mut(member.key).ok_or(GeometryError::MissingBlock(member.key))?.kind = "record";
            }
            result.push(collection);
            plan.decisions.push("complex-records:".to_owned() + &refs.join(","));
            index = end;
        }
        if result != children {
            let block = plan.block_mut(parent).ok_or(GeometryError::MissingBlock(parent))?;
            block.kind = kind;
            block.children = result;
        }
    }
    Ok(())
}

#[cfg(test)]
mod tests {
    use super::*;
    use crate::{
        geometric::{AtomLayoutEvidence, AtomSlot},
        geometry::Geometry,
        projection::{Projection, ProjectionOptions},
    };

    fn node(reference: &str, role: &str, name: &str, children: Vec<Value>) -> Value {
        Value::Map(vec![
            ("ref".to_owned(), Value::Text(reference.to_owned())),
            ("role".to_owned(), Value::Text(role.to_owned())),
            ("name".to_owned(), Value::Text(name.to_owned())),
            ("children".to_owned(), Value::Seq(children)),
        ])
    }

    fn atom(id: usize) -> AtomLayoutEvidence {
        AtomLayoutEvidence {
            role_label: "Group".to_owned(),
            source_description: None,
            state_labels: Vec::new(),
            has_actions: matches!(id, 3 | 6 | 7),
            actions_unavailable: false,
            declarations: false,
            partial: false,
            focused: false,
            unexpanded_menu: false,
            action_cost: 0,
            slots: (id == 2).then(|| AtomSlot {
                key: "name".to_owned(),
                format: "text".to_owned(),
                value: Value::Text("表".repeat(161)),
                block: false,
                caption: false,
                protected: false,
                preview: false,
                label: None,
            }).into_iter().collect(),
        }
    }

    fn bounds(x: f64, y: f64, width: f64, height: f64) -> Box {
        Box { space: "desktop".to_owned(), x, y, width, height }
    }

    fn children(source: usize, targets: &[usize], complete: bool) -> Relation {
        Relation {
            source: NodeId(source),
            property: "children".to_owned(),
            kind: "children".to_owned(),
            targets: targets.iter().copied().map(NodeId).collect(),
            complete,
            confirmed: true,
        }
    }

    #[test]
    fn records_keep_optional_controls_and_require_uncontested_coverage() {
        let first = node("2", "group", "A", vec![
            node("3", "staticText", "Summary A", vec![]),
            node("4", "button", "Open", vec![]),
        ]);
        let second = node("5", "group", "B", vec![
            node("6", "staticText", "Summary B", vec![]),
            node("7", "button", "Open", vec![]),
            node("8", "button", "Save", vec![]),
        ]);
        let source = node("1", "window", "Window", vec![first, second]);
        let aliases = (0..8).map(|id| ((id + 1).to_string(), NodeId(id))).collect();
        let projection = Projection::prepare(source, &aliases, ProjectionOptions::default()).unwrap();
        let geometry = Geometry::from([
            (NodeId(0), bounds(0.0, 0.0, 140.0, 120.0)),
            (NodeId(1), bounds(10.0, 10.0, 100.0, 40.0)),
            (NodeId(2), bounds(11.0, 11.0, 20.0, 10.0)),
            (NodeId(3), bounds(11.0, 26.0, 20.0, 10.0)),
            (NodeId(4), bounds(10.0, 60.0, 100.0, 40.0)),
            (NodeId(5), bounds(11.0, 61.0, 20.0, 10.0)),
            (NodeId(6), bounds(11.0, 76.0, 20.0, 10.0)),
            (NodeId(7), bounds(40.0, 76.0, 20.0, 10.0)),
        ]);
        let atoms = (0..8).map(|id| (NodeId(id), atom(id))).collect();
        let context = GeometricContext { projection: &projection, geometry: &geometry, facts: None, atoms: Some(&atoms) };
        let initial = || {
            let mut plan = GeometricPlan::seed(&context, true).unwrap();
            plan.relations = vec![children(0, &[1, 4], true), children(1, &[2, 3], true), children(4, &[5, 6, 7], true)];
            plan
        };

        let mut plan = initial();
        let root = plan.roots[0];
        let original_children = plan.block(root).unwrap().children.clone();
        build_record_blocks(&mut plan, &context).unwrap();
        let group = plan.block(plan.block(root).unwrap().children[0]).unwrap();
        assert_eq!(group.layout, "record-blocks");
        assert_eq!(group.origin, "visual");
        assert_eq!(group.children, original_children);
        assert_eq!(group.shared_prefix_ref, Some(NodeId(1)));
        assert_eq!(plan.decisions, ["complex-records:2,5"]);
        for (index, &key) in group.children.iter().enumerate() {
            let record = plan.block(key).unwrap();
            assert_eq!(record.kind, "record");
            assert_eq!(record.children.len(), 2 + index);
        }

        let mut plan = initial();
        plan.relations.push(children(0, &[1], false));
        build_record_blocks(&mut plan, &context).unwrap();
        assert_eq!(plan.block(root).unwrap().children, original_children);
        assert!(plan.decisions.is_empty());

        let mut plan = initial();
        plan.regions.push((root, HashMap::from([(NodeId(1), vec![0]), (NodeId(4), vec![1])])));
        build_record_blocks(&mut plan, &context).unwrap();
        assert_eq!(plan.block(root).unwrap().children, original_children);
        assert!(plan.decisions.is_empty());
    }
}
