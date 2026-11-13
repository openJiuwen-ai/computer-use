// Copyright (c) Huawei Technologies Co., Ltd. 2026. All rights reserved.

//! Field and option structure over the retained geometric block graph.

use std::collections::{HashMap, HashSet};

use super::{
    alignment::{analyze, cmp, Alignment, LayoutItem}, block_key, field_text, ordered_ancestry, reading_scope,
    role, rows::{inline_unit_refs, local_parent, row_geometry}, scopes::{native_edges, owners, stable_order},
    seq, synthetic_key, truth, Block, BlockId, Box, GeometricContext, GeometricPlan, GeometryError, Result,
};
use crate::{numbering::NodeId, projection::{spatial::referenceable_caption, ProjectionNode}, value::Value};

mod records;

pub(super) use records::{build_record_blocks, child_relations, confirmed_children, known_control};

fn control(node: &ProjectionNode) -> bool {
    matches!(role(node), "button" | "menuButton" | "link" | "checkBox" | "radioButton" | "switch" | "popUpButton"
        | "comboBox" | "slider" | "incrementor" | "closeButton" | "minimizeButton" | "zoomButton" | "textField" | "textArea")
        || ["click", "typeText", "replaceText"].iter().any(|name| {
            node.field("capabilities").and_then(|capabilities| capabilities.get(name)).and_then(Value::as_str) == Some("available")
        })
}

fn nonempty_caption(node: &ProjectionNode) -> bool {
    ["name", "value", "description"].iter().any(|name| field_text(node, name).is_some_and(|text| {
        !text.trim_matches(|character: char| character.is_whitespace() || matches!(character, '\u{1c}'..='\u{1f}')).is_empty()
    }))
}

fn referenceable(node: &ProjectionNode) -> Result<bool> {
    referenceable_caption(node).map_err(|_| GeometryError::Invalid("invalid visual caption facts"))
}

#[derive(Default)]
struct LabelEvidence {
    native: HashSet<NodeId>,
    visual: bool,
    conflict: bool,
}

fn label_evidence(plan: &GeometricPlan, context: &GeometricContext<'_>, label: NodeId, targets: &[NodeId]) -> Result<LabelEvidence> {
    let caption = context.projection.node(label).ok_or(GeometryError::Invalid("missing field label"))?;
    let selected: HashSet<_> = targets.iter().copied().collect();
    let mut evidence = LabelEvidence::default();
    for &target in targets {
        let node = context.projection.node(target).ok_or(GeometryError::Invalid("missing field target"))?;
        if let Some(title) = field_text(node, "titleRef") {
            if title == caption.reference { evidence.native.insert(target); }
            else { evidence.conflict = true; }
        }
        if let Some(visual) = field_text(node, "visualLabelRef") {
            if visual == caption.reference { evidence.visual = true; }
            else { evidence.conflict = true; }
        }
        evidence.conflict |= truth(node.field("incompleteLabelRelation"));
    }
    evidence.conflict |= truth(caption.field("incompleteLabelRelation"));
    for linked in seq(caption.field("labelTargets")) {
        let target = linked.as_str().and_then(|linked| context.projection.by_reference(linked)).map(|node| node.id);
        if let Some(target) = target.filter(|target| selected.contains(target)) { evidence.native.insert(target); }
        else { evidence.conflict = true; }
    }
    for relation in &plan.relations {
        if !matches!(relation.kind.as_str(), "labelled_by" | "labels") { continue; }
        let complete = relation.complete && relation.confirmed;
        for endpoint in &relation.targets {
            let (target, caption) = if relation.kind == "labels" { (*endpoint, relation.source) } else { (relation.source, *endpoint) };
            if !selected.contains(&target) && caption != label { continue; }
            if complete && selected.contains(&target) && caption == label { evidence.native.insert(target); }
            else { evidence.conflict = true; }
        }
        if relation.targets.is_empty() && (relation.source == label || selected.contains(&relation.source)) {
            evidence.conflict |= !complete;
        }
    }
    Ok(evidence)
}

struct Subfield {
    key: BlockId,
    label: NodeId,
    controls: Vec<NodeId>,
    content: Vec<NodeId>,
    confirmed: bool,
}

fn subfield(plan: &GeometricPlan, context: &GeometricContext<'_>, key: BlockId) -> Result<Option<Subfield>> {
    let row = plan.block(key).ok_or(GeometryError::MissingBlock(key))?;
    if row.layout != "visual-row" || row.primary_ref.is_some() || !row.source_refs.is_empty() || row.children.len() < 2
        || !row_geometry(&row.children, plan, context, true)
    {
        return Ok(None);
    }
    let members: Option<Vec<_>> = row.children.iter().map(|child| plan.block(*child).and_then(|block| block.primary_ref)).collect();
    let Some(members) = members else { return Ok(None); };
    let label = members[0];
    let node = context.projection.node(label).ok_or(GeometryError::Invalid("missing subfield label"))?;
    if !matches!(role(node), "staticText" | "heading") || !nonempty_caption(node) { return Ok(None); }
    let mut controls = Vec::new();
    let mut content = Vec::new();
    for &member in &members[1..] {
        let node = context.projection.node(member).ok_or(GeometryError::Invalid("missing subfield member"))?;
        if control(node) { controls.push(member); }
        else if role(node) == "image" && plan.evidence.get(&member).is_some_and(|evidence| !evidence.independent) {
            content.push(member);
        } else { return Ok(None); }
    }
    if controls.is_empty() { return Ok(None); }
    let evidence = label_evidence(plan, context, label, &controls)?;
    let confirmed = !evidence.native.is_empty() || evidence.visual;
    if evidence.conflict || !confirmed && !referenceable(node)? { return Ok(None); }
    Ok(Some(Subfield { key, label, controls, content, confirmed }))
}

fn local_alignment(parent: &Block, plan: &GeometricPlan, context: &GeometricContext<'_>) -> Result<HashMap<String, Alignment>> {
    let mut by_space = HashMap::<_, Vec<LayoutItem>>::new();
    let mut pending = parent.children.clone();
    let mut seen = HashSet::new();
    while let Some(key) = pending.pop() {
        if !seen.insert(key) { continue; }
        let block = plan.block(key).ok_or(GeometryError::MissingBlock(key))?;
        if block.layout == "visual-row" && block.primary_ref.is_none() {
            pending.extend(&block.children);
        } else if block.children.is_empty() {
            if let Some(bounds) = block.primary_ref.and_then(|reference| context.geometry.get(&reference)) {
                by_space.entry(bounds.space.clone()).or_default().push(LayoutItem { key, bounds: bounds.clone() });
            }
        }
    }
    by_space.into_iter().map(|(space, items)| {
        let alignment = analyze(&items, plan).ok_or(GeometryError::Invalid("invalid subfield alignment"))?;
        Ok((space, alignment))
    }).collect()
}

fn shared_anchor(plan: &GeometricPlan, alignment: &Alignment, first: NodeId, second: NodeId) -> bool {
    if first == second { return false; }
    let (Some(first), Some(second)) = (plan.source_block(first), plan.source_block(second)) else { return false; };
    alignment.anchors.iter().any(|anchor| anchor.members.contains(&first) && anchor.members.contains(&second))
}

fn repeated_subfield(plan: &GeometricPlan, alignment: &Alignment, candidate: &Subfield, others: &[Subfield]) -> bool {
    others.iter().any(|other| {
        if other.key == candidate.key { return false; }
        let refs: Vec<_> = std::iter::once(candidate.label).chain(candidate.controls.iter().copied())
            .chain(std::iter::once(other.label)).chain(other.controls.iter().copied()).collect();
        plan.same_region(&refs) && shared_anchor(plan, alignment, candidate.label, other.label)
            && shared_anchor(plan, alignment, *candidate.controls.last().expect("controls"), *other.controls.last().expect("controls"))
    })
}

pub(super) fn organize_subfields(plan: &mut GeometricPlan, context: &GeometricContext<'_>) -> Result<()> {
    if !plan.enabled { return Ok(()); }
    let mut selected = Vec::new();
    for id in plan.active_ids() {
        let parent = plan.block(id).ok_or(GeometryError::MissingBlock(id))?;
        if !local_parent(parent, plan, context, 1) { continue; }
        let candidates: Vec<_> = parent.children.iter().map(|key| subfield(plan, context, *key)).collect::<Result<Vec<_>>>()?
            .into_iter().flatten().collect();
        if candidates.is_empty() { continue; }
        let alignments = local_alignment(parent, plan, context)?;
        let mut proven = Vec::new();
        for candidate in &candidates {
            let alignment = context.geometry.get(&candidate.label).and_then(|bounds| alignments.get(&bounds.space))
                .ok_or(GeometryError::Invalid("missing subfield source alignment"))?;
            if candidate.confirmed || repeated_subfield(plan, alignment, candidate, &candidates) { proven.push(candidate.key); }
        }
        selected.extend(candidates.into_iter().filter(|candidate| proven.contains(&candidate.key)));
    }
    for candidate in selected {
        let row = plan.block(candidate.key).ok_or(GeometryError::MissingBlock(candidate.key))?;
        let children = if row.children.len() > 2 {
            let first = row.children[0];
            let key = block_key(plan, "field-members", candidate.key, &row.children[1..])?;
            let mut content = Block::new(key, "content", None, row.children[1..].to_vec());
            content.layout = "visual-row";
            content.origin = "visual";
            vec![first, plan.insert(content)]
        } else { row.children.clone() };
        let row = plan.block_mut(candidate.key).ok_or(GeometryError::MissingBlock(candidate.key))?;
        row.kind = "field";
        row.layout = "field-inline";
        row.children = children;
        row.label_ref = Some(candidate.label);
        row.member_roles = std::iter::once((candidate.label, "label"))
            .chain(candidate.controls.into_iter().map(|id| (id, "control")))
            .chain(candidate.content.into_iter().map(|id| (id, "content"))).collect();
        let label = context.projection.node(candidate.label).ok_or(GeometryError::Invalid("missing subfield label"))?;
        plan.decisions.push(format!("geometric-subfield:{}", label.reference));
    }
    Ok(())
}

fn radio_targets(plan: &GeometricPlan, context: &GeometricContext<'_>, key: BlockId) -> Option<Vec<NodeId>> {
    let mut pending = vec![key];
    let mut targets = Vec::new();
    while let Some(key) = pending.pop() {
        let block = plan.block(key)?;
        let target = if block.visual_unit { block.field_target_ref } else { block.primary_ref };
        if let Some(target) = target.filter(|target| context.projection.node(*target).is_some_and(|node| role(node) == "radioButton")) {
            inline_unit_refs(key, plan)?;
            targets.push(target);
            continue;
        }
        if block.primary_ref.is_some() || !block.source_refs.is_empty() || block.layout != "visual-row" || block.children.is_empty() {
            return None;
        }
        pending.extend(block.children.iter().rev().copied());
    }
    Some(targets)
}

fn question_start(plan: &GeometricPlan, context: &GeometricContext<'_>, key: BlockId) -> Option<(BlockId, Option<BlockId>)> {
    let block = plan.block(key)?;
    if block.primary_ref.is_some() { return Some((key, None)); }
    let (target, label) = (block.field_target_ref?, block.label_ref?);
    if block.kind != "field" || block.visual_unit || block.origin != "visual" || !block.source_refs.is_empty()
        || role(context.projection.node(target)?) != "radioButton" || block.children.len() != 2
    {
        return None;
    }
    let members: HashMap<_, _> = block.children.iter().filter_map(|child| {
        let member = plan.block(*child)?;
        member.children.is_empty().then_some((member.primary_ref, *child))
    }).collect();
    let roles: HashMap<_, _> = block.member_roles.iter().copied().collect();
    if members.len() != 2 || roles != HashMap::from([(target, "control"), (label, "label")]) { return None; }
    Some((*members.get(&Some(label))?, Some(*members.get(&Some(target))?)))
}

fn nearby_question(label: &Box, options: &mut [&Box]) -> bool {
    if options.iter().any(|option| !label.comparable(option)) { return false; }
    let scale = options.iter().fold(label.height, |scale, bounds| scale.min(bounds.height));
    if scale <= 0.0 { return false; }
    let left = options.iter().map(|option| option.x).reduce(f64::min).expect("at least two options");
    let top = options.iter().map(|option| option.y).reduce(f64::min).expect("at least two options");
    options.sort_by(|a, b| cmp(a.y, b.y).then_with(|| cmp(a.x, b.x)));
    if options.windows(2).any(|pair| {
        let (before, after) = (pair[0], pair[1]);
        after.y - before.bottom() > 1.5 * scale || before.vertical_overlap(after) >= 0.6 && after.x - before.right() > 3.0 * scale
    }) { return false; }
    let above = (0.0..=1.5 * scale).contains(&(top - label.bottom())) && (label.x - left).abs() <= 0.75 * scale;
    let beside = (0.0..=2.0 * scale).contains(&(left - label.right()))
        && label.vertical_overlap(options[0]) >= 0.6 && label.y <= top + 0.5 * scale;
    above || beside
}

fn safe_radio_group(plan: &GeometricPlan, context: &GeometricContext<'_>, label_key: BlockId, children: &[BlockId], targets: &[NodeId]) -> Result<bool> {
    let block = plan.block(label_key).ok_or(GeometryError::MissingBlock(label_key))?;
    let Some(label) = block.primary_ref.filter(|_| block.children.is_empty()) else { return Ok(false); };
    let keys: Vec<_> = std::iter::once(label_key).chain(children.iter().copied()).collect();
    let refs: Vec<_> = keys.iter().flat_map(|key| plan.block_sources(*key)).collect();
    let selected: HashSet<_> = refs.iter().copied().collect();
    if refs.len() != selected.len() || !plan.same_scope(&refs, context) || !plan.same_region(&refs) { return Ok(false); }
    let scope = reading_scope(plan, context, label);
    if refs.iter().any(|reference| reading_scope(plan, context, *reference) != scope
        || plan.evidence.get(reference).is_none_or(|evidence| evidence.boundary || evidence.hidden || evidence.incomplete)
        || ordered_ancestry(plan, context, *reference, true, true))
    {
        return Ok(false);
    }
    if plan.relations.iter().filter(|relation| matches!(relation.kind.as_str(), "flows_to" | "flows_from")).any(|relation| {
        let endpoints = std::iter::once(&relation.source).chain(&relation.targets);
        endpoints.clone().any(|source| selected.contains(source))
            && (!relation.complete || !relation.confirmed || !endpoints.clone().all(|source| selected.contains(source)))
    }) { return Ok(false); }
    let owners = owners(plan, &keys);
    let Some(edges) = native_edges(&keys, &owners, &plan.relations) else { return Ok(false); };
    if stable_order(&keys, &edges).as_ref() != Some(&keys) { return Ok(false); }
    let evidence = label_evidence(plan, context, label, targets)?;
    let targets_set: HashSet<_> = targets.iter().copied().collect();
    let confirmed = evidence.native == targets_set;
    if evidence.conflict || !evidence.native.is_empty() && !confirmed { return Ok(false); }
    let node = context.projection.node(label).ok_or(GeometryError::Invalid("missing radio question"))?;
    if !nonempty_caption(node) { return Ok(false); }
    if confirmed { return Ok(matches!(role(node), "staticText" | "heading")); }
    if !referenceable(node)? { return Ok(false); }
    let Some(label) = plan.block_bounds(label_key, context.geometry) else { return Ok(false); };
    let bounds: Option<Vec<_>> = targets.iter().map(|target| context.geometry.get(target)).collect();
    Ok(bounds.is_some_and(|mut bounds| nearby_question(&label, &mut bounds)))
}

struct RadioGroup {
    start: usize,
    stop: usize,
    label_key: BlockId,
    label: NodeId,
    options: Vec<NodeId>,
    members: Vec<BlockId>,
    pack_row: bool,
}

fn radio_groups(plan: &GeometricPlan, context: &GeometricContext<'_>, parent: &Block) -> Result<Vec<RadioGroup>> {
    let children = &parent.children;
    let mut groups = Vec::new();
    let mut index = 0;
    while index < children.len() {
        let Some((label_key, leading)) = question_start(plan, context, children[index]) else { index += 1; continue; };
        let label = plan.block(label_key).and_then(|label| label.primary_ref);
        let mut stop = index + 1;
        let mut options = leading.and_then(|key| radio_targets(plan, context, key)).unwrap_or_default();
        while stop < children.len() {
            let Some(targets) = radio_targets(plan, context, children[stop]) else { break; };
            options.extend(targets);
            stop += 1;
        }
        let members: Vec<_> = leading.into_iter().chain(children[index + 1..stop].iter().copied()).collect();
        let Some(label) = label.filter(|label| Some(*label) != parent.label_ref && options.len() >= 2) else { index += 1; continue; };
        if !safe_radio_group(plan, context, label_key, &members, &options)? { index += 1; continue; }
        let pack_row = members.len() >= 2 && row_geometry(&members, plan, context, true);
        groups.push(RadioGroup { start: index, stop, label_key, label, options, members, pack_row });
        index = stop;
    }
    Ok(groups)
}

pub(super) fn group_radio_options(plan: &mut GeometricPlan, context: &GeometricContext<'_>) -> Result<()> {
    if !plan.enabled { return Ok(()); }
    let mut selected = Vec::new();
    for key in plan.active_ids() {
        let parent = plan.block(key).ok_or(GeometryError::MissingBlock(key))?;
        if local_parent(parent, plan, context, 2) {
            let groups = radio_groups(plan, context, parent)?;
            if !groups.is_empty() { selected.push((key, parent.children.clone(), groups)); }
        }
    }
    for (parent, original, groups) in selected {
        let mut output = Vec::new();
        let mut cursor = 0;
        for group in groups {
            output.extend(&original[cursor..group.start]);
            let mut members = vec![group.label_key];
            members.extend(&group.members);
            let key = block_key(plan, "radio-question", parent, &members)?;
            if group.pack_row {
                let keys = group.members.iter().map(|id| plan.block(*id).map(|block| block.key.as_str()).ok_or(GeometryError::MissingBlock(*id)))
                    .collect::<Result<Vec<_>>>()?;
                let row_key = synthetic_key("radio-options", &key, &keys);
                let mut row = Block::new(row_key, "content", None, group.members);
                row.layout = "visual-row";
                row.origin = "visual";
                members = vec![group.label_key, plan.insert(row)];
            }
            let mut field = Block::new(key, "field", None, members);
            field.layout = "fields";
            field.origin = "visual";
            field.label_ref = Some(group.label);
            field.member_roles = std::iter::once((group.label, "label")).chain(group.options.into_iter().map(|id| (id, "control"))).collect();
            if original[group.start] != group.label_key { plan.remove(original[group.start]); }
            output.push(plan.insert(field));
            let node = context.projection.node(group.label).ok_or(GeometryError::Invalid("missing radio question"))?;
            plan.decisions.push(format!("geometric-radio-question:{}", node.reference));
            cursor = group.stop;
        }
        output.extend(&original[cursor..]);
        plan.block_mut(parent).ok_or(GeometryError::MissingBlock(parent))?.children = output;
    }
    Ok(())
}

#[cfg(test)]
mod tests {
    use super::*;
    use crate::{geometry::Geometry, projection::{Projection, ProjectionOptions}, wire::decode_json};

    fn projection(source: &str, count: usize) -> Projection {
        let aliases = (0..count).map(|id| (id.to_string(), NodeId(id))).collect();
        Projection::prepare(decode_json(source.as_bytes()).unwrap(), &aliases, ProjectionOptions::default()).unwrap()
    }

    fn bounds(x: f64, y: f64) -> Box {
        Box { space: "window".to_owned(), x, y, width: 20.0, height: 12.0 }
    }

    #[test]
    fn subfield_keeps_independent_control_and_image_members_of_a_confirmed_row() {
        let projection = projection(r#"{"ref":"0","role":"window","children":[
            {"ref":"1","role":"staticText","name":"Status","children":[]},
            {"ref":"2","role":"checkBox","titleRef":"1","children":[]},
            {"ref":"3","role":"image","children":[]}]}"#, 4);
        let geometry = Geometry::from_iter([(NodeId(1), bounds(0.0, 0.0)), (NodeId(2), bounds(28.0, 0.0)), (NodeId(3), bounds(56.0, 0.0))]);
        let context = GeometricContext { projection: &projection, geometry: &geometry, facts: None, atoms: None };
        let mut plan = GeometricPlan::seed(&context, true).unwrap();
        let parent = plan.roots[0];
        let children = plan.block(parent).unwrap().children.clone();
        let mut row = Block::new("test-row".to_owned(), "content", None, children.clone());
        row.layout = "visual-row";
        row.origin = "visual";
        let row_id = plan.insert(row);
        plan.block_mut(parent).unwrap().children = vec![row_id];
        organize_subfields(&mut plan, &context).unwrap();
        let field = plan.block(row_id).unwrap();
        assert_eq!(field.layout, "field-inline");
        assert_eq!(field.member_roles, [(NodeId(1), "label"), (NodeId(2), "control"), (NodeId(3), "content")]);
        assert_eq!(field.children[0], children[0]);
        assert_eq!(plan.block(field.children[1]).unwrap().children, children[1..]);
        assert!(plan.validate_sources().is_ok());
    }

    #[test]
    fn radio_question_uses_all_native_labels_and_rejects_partial_confirmation() {
        for (second, grouped) in [("titleRef", true), ("visualLabelRef", false)] {
            let input = format!(r#"{{"ref":"0","role":"window","children":[
                {{"ref":"1","role":"staticText","name":"Mode","value":"Mode","_completeTextValues":["value"],"children":[]}},
                {{"ref":"2","role":"radioButton","titleRef":"1","children":[]}},
                {{"ref":"3","role":"radioButton","{second}":"1","children":[]}}]}}"#);
            let projection = projection(&input, 4);
            let geometry = Geometry::from_iter([(NodeId(1), bounds(0.0, 0.0)), (NodeId(2), bounds(0.0, 20.0)), (NodeId(3), bounds(25.0, 20.0))]);
            let context = GeometricContext { projection: &projection, geometry: &geometry, facts: None, atoms: None };
            let mut plan = GeometricPlan::seed(&context, true).unwrap();
            let parent = plan.roots[0];
            let children = plan.block(parent).unwrap().children.clone();
            group_radio_options(&mut plan, &context).unwrap();
            let current = &plan.block(parent).unwrap().children;
            if grouped {
                assert_eq!(current.len(), 1);
                let field = plan.block(current[0]).unwrap();
                assert_eq!(field.label_ref, Some(NodeId(1)));
                assert_eq!(field.field_target_ref, None);
                assert_eq!(field.member_roles, [(NodeId(1), "label"), (NodeId(2), "control"), (NodeId(3), "control")]);
                assert_eq!(plan.block(field.children[1]).unwrap().children, children[1..]);
            } else {
                assert_eq!(*current, children);
            }
            assert!(plan.validate_sources().is_ok());
        }
    }

    #[test]
    fn deep_visual_rows_keep_radio_order_and_geometry_on_an_sdk_worker_stack() {
        let projection = projection(r#"{"ref":"0","role":"window","children":[
            {"ref":"1","role":"radioButton","children":[]},
            {"ref":"2","role":"radioButton","children":[]}]}"#, 3);
        let left = bounds(0.0, 0.0);
        let right = bounds(25.0, 0.0);
        let expected = super::super::union([Some(left.clone()), Some(right.clone())]);
        let geometry = Geometry::from_iter([(NodeId(1), left), (NodeId(2), right)]);
        let context = GeometricContext { projection: &projection, geometry: &geometry, facts: None, atoms: None };
        let mut plan = GeometricPlan::seed(&context, true).unwrap();
        let mut children = plan.block(plan.roots[0]).unwrap().children.clone();
        for level in 0..2_000 {
            let mut row = Block::new(format!("test-row-{level}"), "content", None, children);
            row.layout = "visual-row";
            children = vec![plan.insert(row)];
        }
        std::thread::scope(|scope| {
            std::thread::Builder::new().stack_size(512 * 1024).spawn_scoped(scope, || {
                assert_eq!(radio_targets(&plan, &context, children[0]), Some(vec![NodeId(1), NodeId(2)]));
                assert_eq!(plan.block_bounds(children[0], &geometry), expected);
            }).unwrap().join().unwrap();
        });
    }
}
