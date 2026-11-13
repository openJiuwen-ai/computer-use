// Copyright (c) Huawei Technologies Co., Ltd. 2026. All rights reserved.

//! Source-preserving visual control units and passive layout frontiers.

use std::collections::{HashMap, HashSet};

use super::{
    block_key, composition::{form_control, member_span}, evidence::{node_evidence, original_parent, original_role},
    field_text, ordered_ancestry, reading_scope, role, scopes::{external_flow, native_edges, owners, stable_order},
    seq, truth, Block, BlockId, GeometricContext, GeometricPlan, GeometryError, ParentReport, Result,
};
use crate::{numbering::NodeId, value::Value};

const PASSIVE_CAPABILITIES: &[&str] = &["click", "typeText", "replaceText", "focus"];
const CONTENT_FIELDS: &[&str] = &[
    "name", "description", "value", "help", "placeholder", "selectedText", "documentText", "controlValue",
    "rangeValue", "url", "fullDescription", "valueDescription", "rangeDescription", "shortcut", "landmarkType",
    "localizedLandmarkType",
];
const OWNED_STATE: &[&str] = &[
    "focused", "selected", "selectable", "editable", "pressable", "focusable", "busy", "invalid", "modal",
    "expandable", "expanded", "submenu", "sensitive", "protected", "preserveGroup", "incompleteLabelRelation",
    "actionsUnavailable", "linkedLabel", "titleRef", "visualLabelRef", "labelTargets", "errorRelations",
    "toggleState", "menuMark", "required", "unsaved", "readOnly", "textReadOnly", "rangeReadOnly", "disclosedBy",
];
const STRUCTURAL_FIELDS: &[&str] = &[
    "headingLevel", "rowIndex", "columnIndex", "rowRange", "columnRange", "rowCount", "columnCount",
    "cellPosition", "cellRow", "cellColumn", "cellRowSpan", "cellColumnSpan", "selectedChildCount",
    "selectionRange", "documentReports", "documentUrl", "disclosureLevel", "layoutGroups",
];

fn exact_parent(plan: &GeometricPlan, node: NodeId, parent: NodeId) -> bool {
    matches!(plan.parent_reports.get(&node), Some(ParentReport::Confirmed(id) | ParentReport::Semantic(Some(id))) if *id == parent)
}

fn allowed_text(value: Option<&Value>, allowed: &[&str]) -> bool {
    match value {
        None | Some(Value::Null) => true,
        Some(Value::Text(text)) => allowed.contains(&text.as_str()),
        _ => false,
    }
}

pub(super) fn transparent_group(plan: &GeometricPlan, context: &GeometricContext<'_>, reference: NodeId) -> bool {
    let Some(node) = context.projection.node(reference) else { return false; };
    let fields = |name| node.field(name);
    let evidence = node_evidence(node);
    let caps = fields("capabilities").and_then(Value::as_map);
    let passive = caps.is_some_and(|caps| {
        !caps.is_empty() && PASSIVE_CAPABILITIES.iter().all(|key| {
            caps.iter().any(|(name, value)| name == key && value.as_str() == Some("unavailable"))
        }) && caps.iter().all(|(_, value)| value.as_str() == Some("unavailable"))
    });
    if role(node) != "group" || evidence.incomplete || evidence.hidden || !passive
        || fields("actions").is_none_or(|actions| truth(Some(actions)))
        || fields("enabled") == Some(&Value::Bool(false))
        || !allowed_text(fields("subrole"), &["", "hostingView"])
        || !allowed_text(fields("roleDescription"), &["", "group", "组", "container"])
        || CONTENT_FIELDS.iter().any(|key| fields(key).is_some_and(|value| !matches!(value, Value::Null) && value.as_str() != Some("")))
        || OWNED_STATE.iter().any(|key| truth(fields(key)))
        || STRUCTURAL_FIELDS.iter().any(|key| fields(key).is_some())
    {
        return false;
    }
    if node.native_children.is_empty() || node.native_children.iter().any(|child| !plan.evidence.contains_key(child)) {
        return false;
    }
    let members: Vec<_> = std::iter::once(reference).chain(node.native_children.iter().copied()).collect();
    plan.same_scope(&members, context)
        && matches!(plan.parent_reports.get(&reference), Some(ParentReport::Confirmed(_) | ParentReport::Semantic(_)))
        && node.native_children.iter().all(|child| exact_parent(plan, *child, reference))
        && !plan.relations.iter().any(|relation| {
            !matches!(relation.kind.as_str(), "children" | "parent" | "window")
                && (relation.source == reference && !relation.targets.is_empty() || relation.targets.contains(&reference))
        })
}

pub(super) fn expand_transparent_frontier(plan: &mut GeometricPlan, context: &GeometricContext<'_>) -> Result<()> {
    if !plan.enabled { return Ok(()); }
    let mut order = Vec::new();
    let mut seen = HashSet::new();
    let mut pending: Vec<_> = plan.roots.iter().rev().copied().collect();
    while let Some(key) = pending.pop() {
        let block = plan.block(key).ok_or(GeometryError::MissingBlock(key))?;
        if !seen.insert(key) { return Err(GeometryError::Invalid("frontier block occurrence")); }
        order.push(key);
        pending.extend(block.children.iter().rev().copied());
    }
    let mut protected = HashSet::new();
    let mut pending: Vec<_> = plan.active_ids().filter(|id| plan.block(*id).is_some_and(|block| block.visual_unit)).collect();
    while let Some(key) = pending.pop() {
        if protected.insert(key) {
            let block = plan.block(key).ok_or(GeometryError::MissingBlock(key))?;
            pending.extend(&block.children);
        }
    }
    for key in order.into_iter().rev() {
        let parent = plan.block(key).ok_or(GeometryError::MissingBlock(key))?;
        let parent_role = parent.primary_ref.and_then(|reference| context.projection.node(reference)).map(role);
        if protected.contains(&key) || matches!(parent.kind, "control" | "field" | "collection" | "table" | "record")
            || matches!(parent_role, Some("list" | "outline" | "table" | "row" | "cell" | "column" | "menu" | "menuBar" | "document"))
        {
            continue;
        }
        let original = parent.children.clone();
        let mut children = Vec::new();
        let mut retained = Vec::new();
        for child in original {
            let block = plan.block(child).ok_or(GeometryError::MissingBlock(child))?;
            let reference = block.primary_ref.filter(|reference| {
                block.source_refs == [*reference] && !block.children.is_empty() && block.layout == "tree"
                    && transparent_group(plan, context, *reference)
            });
            if let Some(reference) = reference {
                children.extend(&block.children);
                retained.push(child);
                let block = plan.block_mut(child).ok_or(GeometryError::MissingBlock(child))?;
                block.children.clear();
                block.layout = "source-only";
                let node = context.projection.node(reference).ok_or(GeometryError::Invalid("transparent frontier source"))?;
                plan.decisions.push(format!("geometric-transparent-frontier:{}", node.reference));
            } else {
                children.push(child);
            }
        }
        if !retained.is_empty() {
            children.extend(retained);
            plan.block_mut(key).ok_or(GeometryError::MissingBlock(key))?.children = children;
        }
    }
    Ok(())
}

fn native_record<'a>(context: &GeometricContext<'a>, reference: NodeId) -> Option<(&'a str, &'a Value)> {
    let (token, node) = context.facts?.observation.get("nodes")?.as_map()?.get(reference.0)?;
    Some((token.as_str(), node))
}

fn local_boundary(plan: &GeometricPlan, context: &GeometricContext<'_>, target: NodeId) -> Option<NodeId> {
    let mut current = Some(target);
    let mut seen = HashSet::new();
    while let Some(id) = current.filter(|id| seen.insert(*id)) {
        let role = original_role(context, id)?;
        let node = context.projection.node(id);
        let native = native_record(context, id).and_then(|(_, node)| node.get("states"));
        let field = |key| match node {
            Some(node) => node.native_fields.iter().find(|(name, _)| name == key).map(|(_, value)| value),
            None => native.and_then(|states| states.get(key)),
        };
        if field("visible") == Some(&Value::Bool(false)) || field("showing") == Some(&Value::Bool(false))
            || field("offscreen") == Some(&Value::Bool(true))
        {
            return None;
        }
        if matches!(role.as_str(), "list" | "outline" | "table" | "row" | "cell" | "column" | "document"
            | "webArea" | "menu" | "menuBar" | "toolbar" | "tabGroup" | "window" | "sheet" | "scrollArea")
        {
            return Some(id);
        }
        let (parent, known) = original_parent(plan, id);
        if !known { return None; }
        current = parent;
    }
    None
}

fn contained_caption_path(plan: &GeometricPlan, context: &GeometricContext<'_>, target: NodeId) -> Option<Vec<NodeId>> {
    let mut path = vec![target];
    let mut visited = HashSet::from([target]);
    let mut reference = target;
    loop {
        let node = context.projection.node(reference)?;
        let [child] = node.children.as_slice() else { return None; };
        if node.native_children != [*child] || !visited.insert(*child)
            || plan.projected_parents.get(child) != Some(&reference) || !exact_parent(plan, *child, reference)
        {
            return None;
        }
        let next = context.projection.node(*child)?;
        if next.parent != Some(reference) { return None; }
        path.push(*child);
        if role(next) == "staticText" && next.children.is_empty() { return Some(path); }
        if !transparent_group(plan, context, *child) { return None; }
        reference = *child;
    }
}

fn conflicts(plan: &GeometricPlan, context: &GeometricContext<'_>, target: NodeId, label: NodeId) -> bool {
    let Some(target_node) = context.projection.node(target) else { return true; };
    let Some(label_node) = context.projection.node(label) else { return true; };
    if ["titleRef", "visualLabelRef"].iter().any(|name| {
        field_text(target_node, name).is_some_and(|reference| reference != label_node.reference)
    }) || seq(label_node.field("labelTargets")).iter().any(|linked| linked.as_str() != Some(&target_node.reference))
        || truth(target_node.field("incompleteLabelRelation")) || truth(label_node.field("incompleteLabelRelation"))
    {
        return true;
    }
    plan.relations.iter().filter(|relation| matches!(relation.kind.as_str(), "labels" | "labelled_by")).any(|relation| {
        if relation.targets.is_empty() {
            return (relation.source == target || relation.source == label) && (!relation.complete || !relation.confirmed);
        }
        relation.targets.iter().any(|endpoint| {
            let (unit, caption) = if relation.kind == "labels" { (*endpoint, relation.source) } else { (relation.source, *endpoint) };
            (unit == target || caption == label) && (!relation.complete || !relation.confirmed || unit != target || caption != label)
        })
    })
}

fn same_visual_control(plan: &GeometricPlan, context: &GeometricContext<'_>, target: NodeId, label: NodeId) -> bool {
    let (Some(control), Some(caption)) = (context.projection.node(target), context.projection.node(label)) else { return false; };
    if target == label { return false; }
    let role_name = role(control);
    let selection_row = matches!(role_name, "row" | "cell") && ["selectable", "selected"].iter()
        .any(|name| control.field(name) == Some(&Value::Bool(true)));
    let field_like = form_control(control, false);
    if !matches!(role_name, "button" | "menuButton" | "link") && !field_like && !selection_row
        || role(caption) != "staticText" || !caption.children.is_empty()
        || !["name", "value", "description"].iter().any(|name| field_text(caption, name).is_some_and(|text| {
            !text.trim_matches(|character: char| character.is_whitespace() || matches!(character, '\u{1c}'..='\u{1f}')).is_empty()
        }))
        || [target, label].iter().any(|id| plan.evidence.get(id).is_none_or(|evidence| {
            evidence.hidden || evidence.incomplete || evidence.boundary && !(selection_row && *id == target)
        }))
        || !plan.same_scope(&[target, label], context)
    {
        return false;
    }
    let scope = reading_scope(plan, context, target);
    if scope != reading_scope(plan, context, label) || scope.iter().any(|(_, resolved)| !resolved) { return false; }
    let boundary = local_boundary(plan, context, target);
    let label_boundary = local_boundary(plan, context, label);
    if boundary.is_none() || boundary != label_boundary
        && !(selection_row && contained_caption_path(plan, context, target).is_some_and(|path| path.contains(&label)))
    {
        return false;
    }
    if conflicts(plan, context, target, label) { return false; }
    let (Some(control), Some(caption)) = (context.geometry.get(&target), context.geometry.get(&label)) else { return false; };
    if control.contains(caption) { return true; }
    let gap = caption.x - control.right();
    field_like && matches!(role_name, "checkBox" | "radioButton" | "switch") && control.comparable(caption)
        && control.intersection_area(caption) == 0.0 && caption.vertical_overlap(control) >= 0.75
        && 0.0 <= gap && gap <= 2.0 * caption.height
}

fn duplicate_child_caption(plan: &GeometricPlan, context: &GeometricContext<'_>, target: NodeId) -> Option<NodeId> {
    let node = context.projection.node(target)?;
    let atoms = context.atoms?;
    if !matches!(role(node), "button" | "link")
        || node.field("capabilities").and_then(|caps| caps.get("click")).and_then(Value::as_str) != Some("available")
    {
        return None;
    }
    let path = contained_caption_path(plan, context, target)?;
    let child = *path.last()?;
    if context.facts.is_some() {
        let boundary = local_boundary(plan, context, target).and_then(|id| native_record(context, id)).map(|(token, _)| token);
        for reference in [target, child] {
            let Some((_, native)) = native_record(context, reference) else { continue; };
            if native.get("relations").and_then(Value::as_map).is_some_and(|relations| relations.iter().any(|(_, relation)| {
                let targets = seq(relation.get("targets"));
                relation.get("kind").and_then(Value::as_str) == Some("window") && !targets.is_empty()
                    && (targets.len() != 1 || boundary.is_none() || targets[0].as_str() != boundary)
            })) {
                return None;
            }
        }
    }
    let mut captions = atoms.get(&target)?.slots.iter().filter(|slot| slot.caption);
    let caption = captions.next()?.value.as_str().filter(|caption| !caption.is_empty())?;
    let [slot] = atoms.get(&child)?.slots.as_slice() else { return None; };
    if captions.next().is_some() || !matches!(slot.key.as_str(), "name" | "description" | "value")
        || slot.format != "text" || slot.protected || slot.value.as_str() != Some(caption) || caption.contains(['\r', '\n', '\t'])
    {
        return None;
    }
    same_visual_control(plan, context, target, child).then_some(child)
}

fn unit_order(plan: &GeometricPlan, context: &GeometricContext<'_>, children: Vec<BlockId>, target: NodeId) -> Vec<BlockId> {
    let owners = owners(plan, &children);
    if children.len() < 2 || owners.keys().any(|id| ordered_ancestry(plan, context, *id, true, true))
        || external_flow(&owners, &plan.relations)
    {
        return children;
    }
    let Some(edges) = native_edges(&children, &owners, &plan.relations) else { return children; };
    let mut canonical = children.clone();
    canonical.sort_by(|a, b| (Some(a) != owners.get(&target)).cmp(&(Some(b) != owners.get(&target))).then_with(|| {
        let key = |id| plan.block(id).map(|block| block.key.as_str());
        key(*a).cmp(&key(*b))
    }));
    stable_order(&canonical, &edges).unwrap_or(children)
}

pub(super) fn compose_control_units(plan: &mut GeometricPlan, context: &GeometricContext<'_>) -> Result<()> {
    if !plan.enabled { return Ok(()); }
    let mut labels = HashMap::new();
    let mut users = HashMap::<_, usize>::new();
    for &target in &plan.target_refs {
        let node = context.projection.node(target).ok_or(GeometryError::Invalid("control unit target"))?;
        let label = match field_text(node, "visualLabelRef") {
            Some(reference) => context.projection.by_reference(reference).map(|label| label.id),
            None => duplicate_child_caption(plan, context, target),
        };
        if let Some(label) = label {
            labels.insert(target, label);
            *users.entry(label).or_default() += 1;
        }
    }
    let mut used = HashSet::new();
    for target in plan.target_refs.clone() {
        let Some(&label) = labels.get(&target) else { continue; };
        if users[&label] != 1 || used.contains(&target) || used.contains(&label)
            || !same_visual_control(plan, context, target, label)
        {
            continue;
        }
        let Some(span) = member_span(plan, context, &[target, label]) else { continue; };
        if plan.block(span.parent).ok_or(GeometryError::MissingBlock(span.parent))?.kind == "field" { continue; }
        let children = unit_order(plan, context, span.children, target);
        let key = block_key(plan, "control-unit", span.parent, &children)?;
        let mut block = Block::new(key, "field", None, children);
        block.layout = "fields";
        block.origin = "visual";
        block.label_ref = Some(label);
        block.field_target_ref = Some(target);
        block.member_roles = vec![(target, "control"), (label, "label")];
        block.member_roles.extend(block.children.iter().flat_map(|child| plan.block_sources(*child))
            .filter(|id| *id != target && *id != label).map(|id| (id, "content")));
        block.visual_unit = true;
        let key = plan.insert(block);
        plan.block_mut(span.parent).ok_or(GeometryError::MissingBlock(span.parent))?.children.splice(span.start..span.stop, [key]);
        used.extend([target, label]);
        let node = context.projection.node(target).ok_or(GeometryError::Invalid("control unit target"))?;
        plan.decisions.push(format!("geometric-control-unit:{}", node.reference));
    }
    Ok(())
}

#[cfg(test)]
mod tests {
    use super::*;
    use crate::{
        geometry::{Box, Geometry},
        projection::{Projection, ProjectionOptions},
        wire::decode_json,
    };

    fn projection(source: &str, count: usize) -> Projection {
        let aliases = (0..count).map(|id| (id.to_string(), NodeId(id))).collect();
        Projection::prepare(decode_json(source.as_bytes()).unwrap(), &aliases, ProjectionOptions::default()).unwrap()
    }

    #[test]
    fn visual_unit_keeps_caption_reference_and_canonicalizes_only_unordered_siblings() {
        let projection = projection(r#"{"ref":"0","role":"window","children":[
            {"ref":"1","role":"staticText","name":"Enable","children":[]},
            {"ref":"2","role":"checkBox","visualLabelRef":"1","children":[]}] }"#, 3);
        let geometry = Geometry::from_iter([
            (NodeId(1), Box { space: "screen".to_owned(), x: 12.0, y: 2.0, width: 20.0, height: 12.0 }),
            (NodeId(2), Box { space: "screen".to_owned(), x: 0.0, y: 0.0, width: 50.0, height: 18.0 }),
        ]);
        let context = GeometricContext { projection: &projection, geometry: &geometry, facts: None, atoms: None };
        let mut plan = GeometricPlan::seed(&context, true).unwrap();
        compose_control_units(&mut plan, &context).unwrap();
        let parent = plan.block(plan.roots[0]).unwrap();
        let [unit] = parent.children.as_slice() else { panic!("one complete visual unit") };
        let unit = plan.block(*unit).unwrap();
        assert!(unit.visual_unit);
        assert_eq!(unit.field_target_ref, Some(NodeId(2)));
        assert_eq!(unit.label_ref, Some(NodeId(1)));
        assert_eq!(unit.children, [plan.source_block(NodeId(2)).unwrap(), plan.source_block(NodeId(1)).unwrap()]);
        assert_eq!(unit.member_roles, [(NodeId(2), "control"), (NodeId(1), "label")]);
    }

    #[test]
    fn frontier_lifts_only_proven_passive_groups_and_keeps_their_source_blocks() {
        let projection = projection(r#"{"ref":"0","role":"window","children":[
          {"ref":"1","role":"group","capabilities":{"click":"unavailable","focus":"unavailable",
          "typeText":"unavailable","replaceText":"unavailable"},"actions":[],"children":[
            {"ref":"2","role":"button","name":"Run","children":[]}]},
          {"ref":"3","role":"group","capabilities":{"click":"unavailable"},"actions":[],"children":[
            {"ref":"4","role":"button","name":"Cancel","children":[]}]}] }"#, 5);
        let geometry = Geometry::new();
        let context = GeometricContext { projection: &projection, geometry: &geometry, facts: None, atoms: None };
        let mut plan = GeometricPlan::seed(&context, true).unwrap();
        let ids: Vec<_> = (0..5).map(|id| plan.source_block(NodeId(id)).unwrap()).collect();
        expand_transparent_frontier(&mut plan, &context).unwrap();
        assert_eq!(plan.block(ids[0]).unwrap().children, [ids[2], ids[3], ids[1]]);
        let retained = plan.block(ids[1]).unwrap();
        assert_eq!(retained.primary_ref, Some(NodeId(1)));
        assert_eq!(retained.layout, "source-only");
        assert!(retained.children.is_empty());
        assert_eq!(plan.block(ids[3]).unwrap().children, [ids[4]]);
    }
}
