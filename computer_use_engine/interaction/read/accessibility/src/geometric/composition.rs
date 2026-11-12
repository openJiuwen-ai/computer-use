// Copyright (c) Huawei Technologies Co., Ltd. 2026. All rights reserved.

//! Retain complete source blocks while organizing labelled fields.

use std::collections::{HashMap, HashSet};

use super::{
    alignment::{self, LayoutItem}, block_key, field_text, ordered_ancestry, reading_scope, role, same_prefix,
    scopes::{self, ScopePlan}, seq, synthetic_key, truth, Block, BlockId, GeometricContext, GeometricPlan, GeometryError, Result,
};
use crate::{
    numbering::NodeId, projection::{spatial::{atomic_rows, referenceable_caption}, ProjectionNode}, value::Value,
};

pub(super) fn classify_units(plan: &mut GeometricPlan, context: &GeometricContext<'_>) -> Result<()> {
    for reference in plan.target_refs.clone() {
        let node = context.projection.node(reference).ok_or(GeometryError::Invalid("missing projected unit"))?;
        let kind = match role(node) {
            "textField" | "textArea" | "comboBox" | "button" | "menuButton" | "link" | "checkBox" | "radioButton"
            | "switch" | "popUpButton" | "slider" | "incrementor" | "closeButton" | "minimizeButton" | "zoomButton"
                => "control",
            "row" => "record",
            "staticText" | "heading" | "image" => "content",
            _ => continue,
        };
        let id = plan.source_block(reference).ok_or(GeometryError::Invalid("missing source unit"))?;
        plan.block_mut(id).ok_or(GeometryError::MissingBlock(id))?.kind = kind;
    }
    Ok(())
}

pub(super) fn form_control(node: &ProjectionNode, related: bool) -> bool {
    let role = role(node);
    if matches!(role, "textField" | "textArea" | "comboBox" | "popUpButton" | "checkBox" | "radioButton" | "switch") {
        return true;
    }
    matches!(role, "menuButton" | "incrementor" | "slider") && (related
        || ["value", "controlValue", "valueDescription"].into_iter()
            .any(|name| node.field(name).is_some_and(|value| !matches!(value, Value::Null)))
        || role == "menuButton" && field_text(node, "description").is_some_and(|value| !value.trim().is_empty()))
}

fn caption(node: &ProjectionNode) -> bool {
    matches!(role(node), "staticText" | "heading" | "image")
        && ["name", "value", "description"].into_iter().any(|key| field_text(node, key).is_some_and(|value| !value.is_empty()))
}

#[derive(Clone, Copy, Eq, Hash, PartialEq)]
enum Endpoint<'a> {
    Native(NodeId),
    Detached(&'a str),
}

#[derive(Default)]
struct Members<'a> {
    members: HashMap<Endpoint<'a>, HashSet<Endpoint<'a>>>,
    users: HashMap<Endpoint<'a>, HashSet<Endpoint<'a>>>,
    uncertain: HashSet<Endpoint<'a>>,
}

impl<'a> Members<'a> {
    fn link(&mut self, target: Endpoint<'a>, member: Endpoint<'a>) {
        self.members.entry(target).or_default().insert(member);
        self.users.entry(member).or_default().insert(target);
    }

    fn single_user(&self, member: Endpoint<'a>, target: Endpoint<'a>) -> bool {
        self.users.get(&member).is_some_and(|users| users.len() == 1 && users.contains(&target))
    }
}

struct Membership {
    target: NodeId,
    label: Option<NodeId>,
    descriptions: Vec<NodeId>,
    origin: &'static str,
}

fn memberships(plan: &GeometricPlan, context: &GeometricContext<'_>) -> Vec<Membership> {
    let projection = context.projection;
    let positions: HashMap<_, _> = plan.target_refs.iter().enumerate().map(|(i, id)| (*id, i)).collect();
    let named: HashMap<_, _> = projection.source_order.iter().filter_map(|&id| {
        projection.node(id).map(|node| (node.reference.as_str(), Endpoint::Native(id)))
    }).collect();
    let named = |reference| named.get(reference).copied().unwrap_or(Endpoint::Detached(reference));
    let mut labels = Members::default();
    let mut descriptions = Members::default();
    let mut visual = Members::default();
    for &id in &plan.target_refs {
        let Some(node) = projection.node(id) else { continue; };
        let target = Endpoint::Native(id);
        if let Some(label) = field_text(node, "titleRef") { labels.link(target, named(label)); }
        let targets = seq(node.field("labelTargets")).iter().filter_map(Value::as_str).map(named);
        let uncertain = truth(node.field("incompleteLabelRelation"));
        if uncertain { labels.uncertain.insert(target); }
        for other in targets {
            labels.link(other, target);
            if uncertain { labels.uncertain.insert(other); }
        }
        if let Some(label) = field_text(node, "visualLabelRef") { visual.link(target, named(label)); }
    }
    for relation in &plan.relations {
        let members = match relation.kind.as_str() {
            "labelled_by" | "labels" => &mut labels,
            "described_by" | "describes" => &mut descriptions,
            _ => continue,
        };
        let inverse = matches!(relation.kind.as_str(), "labels" | "describes");
        let source = Endpoint::Native(relation.source);
        if (!relation.complete || !relation.confirmed) && !inverse { members.uncertain.insert(source); }
        for &endpoint in &relation.targets {
            let endpoint = Endpoint::Native(endpoint);
            let (target, member) = if inverse { (endpoint, source) } else { (source, endpoint) };
            members.link(target, member);
            if (!relation.complete || !relation.confirmed) && inverse { members.uncertain.insert(target); }
        }
    }
    plan.target_refs.iter().filter_map(|&id| {
        let target = Endpoint::Native(id);
        let node = projection.node(id)?;
        let native = labels.members.get(&target);
        let related = descriptions.members.get(&target);
        if !form_control(node, native.is_some() || related.is_some()) { return None; }
        let mut origin = "native";
        let candidate = if labels.uncertain.contains(&target) {
            None
        } else if let Some(native) = native {
            native.iter().next().copied().filter(|label| native.len() == 1 && labels.single_user(*label, target))
        } else {
            field_text(node, "visualLabelRef").map(named).filter(|label| visual.single_user(*label, target)
                && labels.users.get(label).is_none_or(|users| users.iter().all(|&user| user == target)))
                .inspect(|_| origin = "visual")
        };
        let label = candidate.and_then(|candidate| match candidate {
            Endpoint::Native(label) if positions.contains_key(&label) && projection.node(label).is_some_and(caption) => Some(label),
            _ => None,
        });
        let mut descriptions = if descriptions.uncertain.contains(&target) {
            Vec::new()
        } else {
            related.into_iter().flatten().filter_map(|&member| match member {
                Endpoint::Native(member_id) if positions.contains_key(&member_id) && descriptions.single_user(member, target) => Some(member_id),
                _ => None,
            }).collect()
        };
        descriptions.sort_by_key(|ref_id| positions[ref_id]);
        (label.is_some() || !descriptions.is_empty()).then_some(Membership { target: id, label, descriptions, origin })
    }).collect()
}

fn transparent(plan: &GeometricPlan, context: &GeometricContext<'_>, reference: NodeId) -> bool {
    let (Some(node), Some(evidence)) = (context.projection.node(reference), plan.evidence.get(&reference)) else {
        return false;
    };
    let content = ["name", "description", "value", "help", "placeholder", "selectedText", "modal", "busy", "invalid", "preserveGroup"];
    role(node) == "group" && !evidence.incomplete && !evidence.hidden && !evidence.independent
        && node.field("capabilities").and_then(Value::as_map).is_some_and(|caps| !caps.is_empty()
            && caps.iter().all(|(_, state)| state.as_str() == Some("unavailable")))
        && node.field("actions").is_some_and(|actions| !truth(Some(actions)))
        && !truth(node.field("actionsUnavailable")) && !content.into_iter().any(|name| truth(node.field(name)))
}

pub(super) struct MemberSpan {
    pub(super) parent: BlockId,
    pub(super) start: usize,
    pub(super) stop: usize,
    pub(super) children: Vec<BlockId>,
}

pub(super) fn member_span(plan: &GeometricPlan, context: &GeometricContext<'_>, refs: &[NodeId]) -> Option<MemberSpan> {
    let parents: HashMap<_, _> = plan.active_ids().flat_map(|id| plan.block(id).into_iter()
        .flat_map(move |block| block.children.iter().map(move |&child| (child, id)))).collect();
    let mut paths = Vec::with_capacity(refs.len());
    for &reference in refs {
        let mut key = plan.source_block(reference)?;
        let mut path = vec![key];
        while let Some(&parent) = parents.get(&key) {
            path.push(parent);
            key = parent;
        }
        paths.push(path);
    }
    let parent = paths.first()?[1..].iter().copied().find(|id| paths.iter().all(|path| path[1..].contains(id)))?;
    let child_roots: HashSet<_> = paths.iter().map(|path| path.iter().position(|id| *id == parent).map(|index| path[index - 1]))
        .collect::<Option<_>>()?;
    let children = &plan.block(parent)?.children;
    let indices: Vec<_> = children.iter().enumerate().filter_map(|(i, child)| child_roots.contains(child).then_some(i)).collect();
    let (start, stop) = (*indices.first()?, *indices.last()? + 1);
    if stop - start != child_roots.len() { return None; }
    let mut pending = children[start..stop].to_vec();
    let wanted: HashSet<_> = refs.iter().copied().collect();
    let mut encountered = HashSet::new();
    while let Some(id) = pending.pop() {
        let block = plan.block(id)?;
        block.primary_ref?;
        for &source in &block.source_refs {
            if wanted.contains(&source) { encountered.insert(source); }
            else if !transparent(plan, context, source) { return None; }
        }
        pending.extend(block.children.iter().copied());
    }
    (wanted == encountered).then(|| MemberSpan { parent, start, stop, children: children[start..stop].to_vec() })
}

pub(super) fn associate_field_labels(plan: &mut GeometricPlan, context: &GeometricContext<'_>) -> Result<()> {
    if !plan.enabled { return Ok(()); }
    for member in memberships(plan, context) {
        let descriptions: Vec<_> = member.descriptions.into_iter()
            .filter(|ref_id| *ref_id != member.target && Some(*ref_id) != member.label).collect();
        let mut refs = vec![member.target];
        refs.extend(member.label.filter(|label| *label != member.target));
        refs.extend(descriptions.iter().copied());
        if refs.len() < 2 || !plan.same_scope(&refs, context) || refs.iter().any(|id| {
            plan.evidence.get(id).is_none_or(|evidence| evidence.incomplete || evidence.hidden)
        }) { continue; }
        let Some(span) = member_span(plan, context, &refs) else { continue; };
        let parent = plan.block(span.parent).ok_or(GeometryError::MissingBlock(span.parent))?;
        if parent.kind == "field" { continue; }
        let children = span.children.iter().map(|id| plan.block(*id).ok_or(GeometryError::MissingBlock(*id)))
            .collect::<Result<Vec<_>>>()?;
        let key = block_key(plan, "field-row", span.parent, &span.children)?;
        let simple = children.len() == 2 && children.iter().all(|child| child.children.is_empty());
        let mut block = Block::new(key, "field", None, span.children);
        block.layout = if simple && descriptions.is_empty() { "inline" } else { "fields" };
        block.origin = member.origin;
        block.label_ref = member.label;
        block.field_target_ref = Some(member.target);
        block.member_roles.push((member.target, "control"));
        block.member_roles.extend(member.label.map(|label| (label, "label")));
        block.member_roles.extend(descriptions.into_iter().map(|description| (description, "help")));
        let block = plan.insert(block);
        plan.block_mut(span.parent).ok_or(GeometryError::MissingBlock(span.parent))?
            .children.splice(span.start..span.stop, [block]);
        let target = context.projection.node(member.target).ok_or(GeometryError::Invalid("missing field target"))?;
        let decision = if member.label.is_some() { "field-label" } else { "field-description" };
        plan.decisions.push(format!("{decision}:{}", target.reference));
    }
    Ok(())
}

#[derive(Clone, Copy)]
enum HeadingChild {
    Block(BlockId),
    Section(usize),
}

struct HeadingSection {
    heading: BlockId,
    children: Vec<HeadingChild>,
}

struct OpenSection {
    level: i64,
    children: Vec<HeadingChild>,
}

fn close_section(stack: &mut Vec<OpenSection>, sections: &mut Vec<HeadingSection>) {
    let completed = stack.pop().expect("open heading section");
    let parent = &mut stack.last_mut().expect("section has parent").children;
    if completed.children.len() > 1 {
        let HeadingChild::Block(heading) = completed.children[0] else { unreachable!("a section begins with its heading") };
        parent.push(HeadingChild::Section(sections.len()));
        sections.push(HeadingSection { heading, children: completed.children });
    } else {
        parent.extend(completed.children);
    }
}

fn heading_sections(children: &[(BlockId, Option<&ProjectionNode>)]) -> (Vec<HeadingChild>, Vec<HeadingSection>) {
    let levels_known = children.iter().filter_map(|(_, node)| *node).filter(|node| role(node) == "heading")
        .all(|node| node.field("headingLevel").and_then(Value::as_i64).is_some_and(|level| level > 0));
    let mut stack = vec![OpenSection { level: -1, children: Vec::new() }];
    let mut sections = Vec::new();
    for &(id, node) in children {
        let node_role = node.map(role);
        let subrole = node.and_then(|node| field_text(node, "subrole"));
        let modal = node.and_then(|node| node.field("modal")).and_then(Value::as_bool) == Some(true);
        if matches!(node_role, Some("toolbar" | "menuBar" | "closeButton" | "zoomButton" | "minimizeButton" | "window" | "sheet"))
            || node_role == Some("button") && matches!(subrole, Some("close" | "minimize" | "zoom" | "fullscreen"))
            || modal || matches!(subrole, Some("dialog" | "alert" | "systemDialog" | "popover"))
        {
            while stack.len() > 1 { close_section(&mut stack, &mut sections); }
            stack[0].children.push(HeadingChild::Block(id));
        } else if node_role == Some("heading") {
            let level = if levels_known {
                node.and_then(|node| node.field("headingLevel")).and_then(Value::as_i64).unwrap_or(0)
            } else {
                0
            };
            while stack.len() > 1 && stack.last().is_some_and(|open| open.level >= level) {
                close_section(&mut stack, &mut sections);
            }
            stack.push(OpenSection { level, children: vec![HeadingChild::Block(id)] });
        } else {
            stack.last_mut().expect("root section").children.push(HeadingChild::Block(id));
        }
    }
    while stack.len() > 1 { close_section(&mut stack, &mut sections); }
    (stack.pop().expect("root section").children, sections)
}

fn insert_section(plan: &mut GeometricPlan, heading: BlockId, owner_ref: &str, members: Vec<BlockId>) -> Result<BlockId> {
    let keys = members.iter().map(|&id| plan.block(id).map(|block| block.key.as_str()).ok_or(GeometryError::MissingBlock(id)))
        .collect::<Result<Vec<_>>>()?;
    let key = synthetic_key("section", owner_ref, &keys);
    let mut block = Block::new(key, "region", None, members);
    block.label_ref = plan.block(heading).and_then(|heading| heading.primary_ref);
    block.region_role = Some("region");
    Ok(plan.insert(block))
}

fn materialize_sections(
    plan: &mut GeometricPlan, context: &GeometricContext<'_>, roots: &[HeadingChild], sections: &[HeadingSection],
    owner: &ProjectionNode, siblings: &[BlockId],
) -> Result<Vec<BlockId>> {
    struct Frame<'a> {
        heading: Option<BlockId>,
        children: std::slice::Iter<'a, HeadingChild>,
        members: Vec<BlockId>,
    }

    let mut frames = vec![Frame { heading: None, children: roots.iter(), members: Vec::new() }];
    while let Some(frame) = frames.last_mut() {
        match frame.children.next() {
            Some(HeadingChild::Block(id)) => frame.members.push(*id),
            Some(HeadingChild::Section(index)) => {
                let section = &sections[*index];
                let mut pending = section.children.clone();
                let mut descendants = HashSet::new();
                let mut sources = vec![owner.id];
                while let Some(next) = pending.pop() {
                    match next {
                        HeadingChild::Block(id) => {
                            descendants.insert(id);
                            sources.extend(plan.block_sources(id));
                        }
                        HeadingChild::Section(index) => pending.extend_from_slice(&sections[index].children),
                    }
                }
                if plan.same_scope(&sources, context) {
                    frames.push(Frame {
                        heading: Some(section.heading), children: section.children.iter(), members: Vec::new(),
                    });
                } else {
                    frame.members.extend(siblings.iter().copied().filter(|id| descendants.contains(id)));
                }
            }
            None => {
                let frame = frames.pop().expect("active heading section");
                let Some(parent) = frames.last_mut() else { return Ok(frame.members); };
                let heading = frame.heading.expect("nested section has heading");
                parent.members.push(insert_section(plan, heading, &owner.reference, frame.members)?);
            }
        }
    }
    unreachable!("root heading section returns its members")
}

pub(super) fn group_heading_sections(plan: &mut GeometricPlan, context: &GeometricContext<'_>) -> Result<()> {
    if !plan.enabled { return Ok(()); }
    for reference in plan.target_refs.clone() {
        let Some(owner) = context.projection.node(reference) else { continue; };
        if truth(owner.field("layoutGroups"))
            || !matches!(role(owner), "group" | "window" | "sheet" | "scrollArea" | "webArea" | "document" | "tabPanel")
            || plan.evidence.get(&reference).is_none_or(|evidence| evidence.hidden)
        { continue; }
        let Some(parent) = plan.source_block(reference) else { continue; };
        let children = plan.block(parent).ok_or(GeometryError::MissingBlock(parent))?.children.clone();
        let source_children: Vec<_> = children.iter().map(|&id| {
            (id, plan.block(id).and_then(|block| block.primary_ref).and_then(|ref_id| context.projection.node(ref_id)))
        }).collect();
        if !source_children.iter().any(|(_, node)| node.is_some_and(|node| role(node) == "heading")) { continue; }
        let (roots, sections) = heading_sections(&source_children);
        let replacement = materialize_sections(plan, context, &roots, &sections, owner, &children)?;
        if replacement != children {
            plan.block_mut(parent).ok_or(GeometryError::MissingBlock(parent))?.children = replacement;
            plan.decisions.push(format!("heading-sections:{}", owner.reference));
        }
    }
    Ok(())
}

fn single_line(node: &ProjectionNode) -> bool {
    ["name", "description", "value", "help", "placeholder", "selectedText", "fullDescription", "valueDescription", "rangeDescription"]
        .into_iter().all(|name| field_text(node, name).is_none_or(|text| !text.contains(['\n', '\r', '\t', '|'])))
}

fn control_role(role: &str) -> bool {
    matches!(role, "button" | "menuButton" | "link" | "checkBox" | "radioButton" | "switch" | "popUpButton"
        | "comboBox" | "slider" | "incrementor" | "closeButton" | "minimizeButton" | "zoomButton")
}

fn row_members(node: &ProjectionNode, context: &GeometricContext<'_>) -> Option<Vec<Vec<NodeId>>> {
    let groups = seq(node.field("layoutGroups"));
    let refs: HashMap<_, _> = node.children.iter().filter_map(|&id| {
        context.projection.node(id).map(|child| (child.reference.as_str(), id))
    }).collect();
    groups.iter().map(|group| {
        if group.get("kind").and_then(Value::as_str) != Some("row") { return None; }
        seq(group.get("members")).iter().map(|member| refs.get(member.as_str()?).copied()).collect()
    }).collect()
}

pub(super) fn build_records(plan: &mut GeometricPlan, context: &GeometricContext<'_>) -> Result<()> {
    if !plan.enabled { return Ok(()); }
    for reference in plan.target_refs.clone() {
        let node = context.projection.node(reference).ok_or(GeometryError::Invalid("missing record source"))?;
        let parent_id = plan.source_block(reference).ok_or(GeometryError::Invalid("missing record parent"))?;
        let parent = plan.block(parent_id).ok_or(GeometryError::MissingBlock(parent_id))?;
        let expected = node.children.iter().map(|&child| plan.source_block(child)).collect::<Option<Vec<_>>>();
        if expected.as_deref() != Some(parent.children.as_slice()) { continue; }
        let Some(mut rows) = row_members(node, context) else { continue; };
        // Atomic rows need at least two children, before any source-ancestry evidence.
        if rows.is_empty() && node.children.len() >= 2
            && matches!(role(node), "window" | "sheet" | "group" | "splitGroup" | "tabPanel" | "scrollArea")
            && plan.evidence.get(&reference).is_some_and(|item| !item.incomplete)
            && !ordered_ancestry(plan, context, reference, true, true)
            && node.children.iter().all(|child| {
                context.projection.node(*child).is_some_and(|child| child.parent == Some(reference))
                    && plan.evidence.get(child).is_some_and(|item| !item.boundary)
            })
            && scopes::comparable_members(&parent.children, plan, context).is_some()
        {
            rows = atomic_rows(context.projection, &node.children, context.geometry, 1)
                .map_err(|_| GeometryError::Invalid("invalid atomic rows"))?;
        }
        let Some(width) = rows.first().map(Vec::len) else { continue; };
        if !(2..=8).contains(&width) || rows.iter().any(|row| row.len() != width) { continue; }
        let members: Vec<_> = rows.iter().flatten().copied().collect();
        if members != node.children || members.iter().collect::<HashSet<_>>().len() != members.len() { continue; }
        let scopes: Vec<_> = std::iter::once(reference).chain(members.iter().copied()).collect();
        if !plan.same_scope(&scopes, context) || !plan.same_region(&members) || members.iter().any(|member| {
            plan.evidence.get(member).is_none_or(|item| item.incomplete || item.hidden)
                || context.projection.node(*member).is_none_or(|node| !node.children.is_empty() || !single_line(node))
        }) { continue; }
        let Some(space) = context.geometry.get(&members[0]).map(|bounds| bounds.space.as_str()) else { continue; };
        if members.iter().any(|member| context.geometry.get(member).is_none_or(|bounds| bounds.space != space)) { continue; }
        let mut columns = Vec::with_capacity(width);
        let mut prefixes = Vec::with_capacity(width);
        for column in 0..width {
            let first = rows[0][column];
            let actions = rows.iter().all(|row| context.projection.node(row[column]).is_some_and(|node| control_role(role(node))));
            columns.push(if actions { "Actions".into() } else { format!("Content {}", column + 1) });
            prefixes.push(rows.iter().all(|row| same_prefix(context, first, row[column])).then_some(first));
        }
        let row_count = rows.len();
        let mut records = Vec::with_capacity(row_count);
        for row in rows {
            let sources = row.iter().map(|id| context.projection.node(*id).map(|node| node.reference.as_str()))
                .collect::<Option<Vec<_>>>().ok_or(GeometryError::Invalid("missing record member"))?;
            let children = row.iter().map(|&id| plan.source_block(id)).collect::<Option<Vec<_>>>()
                .ok_or(GeometryError::Invalid("missing record block"))?;
            let key = synthetic_key("record", &node.reference, &sources);
            let mut record = Block::new(key, "record", None, children);
            record.layout = "inline";
            record.origin = "visual";
            records.push(plan.insert(record));
        }
        let decision = if row_count == 1 {
            plan.block_mut(parent_id).ok_or(GeometryError::MissingBlock(parent_id))?.children = records;
            "visual-row"
        } else {
            let refs = members.iter().map(|&id| context.projection.node(id).map(|node| node.reference.as_str()))
                .collect::<Option<Vec<_>>>().ok_or(GeometryError::Invalid("missing record member"))?;
            let mut collection = Block::new(synthetic_key("records", &node.reference, &refs), "collection", None, records);
            collection.layout = "records";
            collection.origin = "visual";
            collection.columns = columns;
            collection.column_prefix_refs = prefixes;
            let collection_id = plan.insert(collection);
            plan.block_mut(parent_id).ok_or(GeometryError::MissingBlock(parent_id))?.children = vec![collection_id];
            "visual-records"
        };
        plan.decisions.push(format!("{decision}:{}", node.reference));
    }
    Ok(())
}

struct FormBand {
    label: NodeId,
    children: Vec<BlockId>,
}

fn form_bands(plan: &GeometricPlan, context: &GeometricContext<'_>, parent: &Block, scope: Option<&ScopePlan>) -> Vec<FormBand> {
    let Some(root) = parent.primary_ref.and_then(|id| context.projection.node(id)) else { return Vec::new(); };
    if parent.layout != "tree" || !matches!(role(root), "window" | "sheet" | "group" | "splitGroup" | "tabPanel" | "scrollArea")
        || ordered_ancestry(plan, context, root.id, true, true)
        || plan.evidence.get(&root.id).is_none_or(|item| item.hidden || item.incomplete)
    { return Vec::new(); }
    let primary = |id| plan.block(id).and_then(|block| block.primary_ref);
    let children = &parent.children;
    let candidates: Vec<_> = children.windows(2).enumerate().filter_map(|(index, pair)| {
        let label = plan.block(pair[0])?;
        let id = label.primary_ref?;
        let node = context.projection.node(id)?;
        if !label.children.is_empty() || !referenceable_caption(node).ok()? { return None; }
        let (label, content) = (context.geometry.get(&id)?, context.geometry.get(&primary(pair[1])?)?);
        let gap = content.x - label.right();
        (label.comparable(content) && gap >= 0.0 && gap <= 2.0 * label.height
            && label.y - 0.5 * label.height <= content.y && content.y <= label.bottom() + label.height)
            .then_some((pair[0], index, label))
    }).collect();
    let fallback;
    let alignment = if let Some(scope) = scope {
        Some(&scope.alignment)
    } else {
        let items: Vec<_> = candidates.iter().map(|(key, _, bounds)| LayoutItem { key: *key, bounds: (*bounds).clone() }).collect();
        fallback = alignment::analyze(&items, plan);
        fallback.as_ref()
    };
    let Some(alignment) = alignment else { return Vec::new(); };
    let positions: HashMap<_, _> = candidates.iter().map(|(key, index, _)| (*key, *index)).collect();
    let rank = |kind| match kind { "left" => 0, "right" => 1, _ => 2 };
    let mut anchors: Vec<_> = alignment.anchors.iter().collect();
    anchors.sort_by(|left, right| {
        let count = |anchor: &alignment::Anchor| anchor.members.iter().filter(|key| positions.contains_key(key)).count();
        count(right).cmp(&count(left)).then_with(|| rank(left.kind).cmp(&rank(right.kind)))
            .then_with(|| alignment::cmp(left.position, right.position))
    });
    let mut claimed = HashSet::new();
    let mut output = Vec::new();
    for anchor in anchors {
        let mut column: Vec<_> = anchor.members.iter().filter_map(|key| positions.get(key).copied())
            .filter(|index| !claimed.contains(index)).collect();
        if column.len() < 3 { continue; }
        column.sort_unstable();
        claimed.extend(column.iter().copied());
        let mut proposed = Vec::new();
        let mut control_rows = 0;
        for (offset, &start) in column.iter().enumerate() {
            let Some(label_ref) = primary(children[start]) else { continue; };
            let Some(label) = context.geometry.get(&label_ref) else { continue; };
            let stop = column.get(offset + 1).copied().unwrap_or(children.len());
            let next = children.get(stop).and_then(|&key| primary(key)).and_then(|id| context.geometry.get(&id));
            if next.is_some_and(|next| next.y < label.bottom()) { continue; }
            let mut refs = vec![label_ref];
            let mut members = vec![children[start]];
            let mut bottom = label.bottom();
            let scope = reading_scope(plan, context, label_ref);
            let parent = context.projection.node(label_ref).and_then(|node| node.parent);
            for &key in &children[start + 1..stop] {
                let Some(id) = primary(key) else { break; };
                let Some(bounds) = context.geometry.get(&id) else { break; };
                let available = |source| plan.evidence.get(&source).is_some_and(|item| !item.boundary && !item.incomplete && !item.hidden);
                if !available(id) || !plan.same_scope(&[label_ref, id], context)
                    || plan.block_sources(key).into_iter().any(|source| !available(source) || reading_scope(plan, context, source) != scope)
                    || reading_scope(plan, context, id) != scope
                    || context.projection.node(id).and_then(|node| node.parent) != parent
                    || !bounds.comparable(label) || bounds.x < label.right() || bounds.y < label.y - 0.5 * label.height
                    || next.is_some_and(|next| bounds.bottom() > next.y) || bounds.y - bottom > 3.0 * label.height
                    || bounds.bottom() < bottom - label.height
                { break; }
                refs.push(id);
                members.push(key);
                bottom = bottom.max(bounds.bottom());
            }
            if members.len() < 2 { continue; }
            let related: HashSet<_> = refs.iter().copied().collect();
            if plan.relations.iter().any(|relation| {
                matches!(relation.kind.as_str(), "labelled_by" | "labels" | "described_by" | "describes")
                    && (related.contains(&relation.source) || relation.targets.iter().any(|target| related.contains(target)))
                    && (!relation.complete || !relation.confirmed || !related.contains(&relation.source)
                        || relation.targets.iter().any(|target| !related.contains(target)))
            }) { continue; }
            proposed.push(FormBand { label: label_ref, children: members });
            if refs[1..].iter().any(|&id| context.projection.node(id).is_some_and(|node| form_control(node, false))) {
                control_rows += 1;
            }
        }
        if proposed.len() >= 3 && control_rows >= 2 { output.extend(proposed); }
    }
    let mut counts = HashMap::<_, usize>::new();
    for &child in output.iter().flat_map(|band| &band.children) { *counts.entry(child).or_default() += 1; }
    output.retain(|band| band.children.iter().all(|child| counts[child] == 1));
    output
}

pub(super) fn group_form_bands(
    plan: &mut GeometricPlan, context: &GeometricContext<'_>, alignment: &HashMap<BlockId, ScopePlan>,
) -> Result<()> {
    if !plan.enabled { return Ok(()); }
    let bands: Vec<_> = plan.active_ids().filter_map(|parent| {
        let block = plan.block(parent)?;
        let bands = form_bands(plan, context, block, alignment.get(&parent));
        (!bands.is_empty()).then_some((parent, block.children.clone(), bands))
    }).collect();
    for (parent, siblings, bands) in bands {
        let starts: HashMap<_, _> = bands.into_iter().map(|band| (band.children[0], band)).collect();
        let mut children = Vec::new();
        let mut index = 0;
        while let Some(&child) = siblings.get(index) {
            let Some(band) = starts.get(&child) else { children.push(child); index += 1; continue; };
            let key = block_key(plan, "form-band", parent, &band.children)?;
            let mut block = Block::new(key, "field", None, band.children.clone());
            block.layout = "fields";
            block.origin = "visual";
            block.label_ref = Some(band.label);
            children.push(plan.insert(block));
            let label = context.projection.node(band.label).ok_or(GeometryError::Invalid("missing form label"))?;
            plan.decisions.push(format!("form-band:{}", label.reference));
            index += band.children.len();
        }
        plan.block_mut(parent).ok_or(GeometryError::MissingBlock(parent))?.children = children;
    }
    Ok(())
}

fn readonly_value(plan: &GeometricPlan, context: &GeometricContext<'_>, id: BlockId) -> Option<NodeId> {
    let block = plan.block(id)?;
    let (target, label) = (block.field_target_ref?, block.label_ref?);
    if block.kind != "field" || block.visual_unit || block.children.len() != 2
        || block.children.iter().any(|&child| plan.block(child).is_none_or(|block| !block.children.is_empty()))
        || plan.block_sources(id).into_iter().collect::<HashSet<_>>() != HashSet::from([target, label])
    { return None; }
    let node = context.projection.node(target)?;
    (matches!(role(node), "textField" | "textArea") && node.field("value").is_some()
        && (node.field("enabled").and_then(Value::as_bool) == Some(false)
            || ["readOnly", "textReadOnly"].iter().any(|name| node.field(name).and_then(Value::as_bool) == Some(true))))
        .then_some(target)
}

fn selector(plan: &GeometricPlan, context: &GeometricContext<'_>, id: BlockId) -> Option<NodeId> {
    let block = plan.block(id)?;
    let reference = block.primary_ref?;
    let node = context.projection.node(reference)?;
    (block.children.is_empty() && matches!(role(node), "button" | "menuButton" | "popUpButton")
        && node.field("enabled").and_then(Value::as_bool) != Some(false)
        && !matches!(field_text(node, "subrole"), Some("close" | "minimize" | "zoom" | "fullscreen"))
        && node.field("capabilities").and_then(|caps| caps.get("click")).and_then(Value::as_str) == Some("available"))
        .then_some(reference)
}

fn adjacent_selector(
    plan: &GeometricPlan, context: &GeometricContext<'_>, siblings: &[BlockId], index: usize,
) -> Option<(NodeId, NodeId)> {
    let keys = [siblings[index], *siblings.get(index + 1)?];
    let (value, trigger) = (readonly_value(plan, context, keys[0])?, selector(plan, context, keys[1])?);
    let node = context.projection.node(trigger)?;
    let label = plan.block(keys[0])?.label_ref.and_then(|id| context.projection.node(id));
    let refs: Vec<_> = plan.block_sources(keys[0]).into_iter().chain([trigger]).collect();
    scopes::comparable_members(&keys, plan, context)?;
    let (value_box, trigger_box) = (context.geometry.get(&value)?, context.geometry.get(&trigger)?);
    if !plan.same_scope(&refs, context) || !plan.same_region(&refs) || refs.iter().any(|&id| {
        plan.evidence.get(&id).is_none_or(|item| item.incomplete || item.hidden || item.boundary)
            || ordered_ancestry(plan, context, id, true, true)
    }) || ["titleRef", "visualLabelRef"].into_iter().filter_map(|name| field_text(node, name))
        .any(|caption| !caption.is_empty() && label.is_none_or(|label| label.reference != caption))
        || truth(node.field("incompleteLabelRelation"))
    { return None; }
    let owners = scopes::owners(plan, &keys);
    let edges = scopes::native_edges(&keys, &owners, &plan.relations)?;
    if scopes::stable_order(&keys, &edges)?.as_slice() != keys || scopes::external_flow(&owners, &plan.relations) {
        return None;
    }
    let other_box = siblings.get(index + 2).and_then(|&id| selector(plan, context, id))
        .and_then(|id| context.geometry.get(&id));
    let competing = other_box.is_some_and(|other| {
        value_box.comparable(other) && value_box.vertical_overlap(other) >= 0.75
            && (0.0..=value_box.height).contains(&(other.x - trigger_box.right()))
    });
    (!competing && value_box.comparable(trigger_box) && value_box.vertical_overlap(trigger_box) >= 0.75
        && (0.0..=value_box.height).contains(&(trigger_box.x - value_box.right()))
        && trigger_box.width <= 2.0 * value_box.height && trigger_box.height <= 1.5 * value_box.height)
        .then_some((value, trigger))
}

pub(super) fn associate_value_controls(plan: &mut GeometricPlan, context: &GeometricContext<'_>) -> Result<()> {
    if !plan.enabled { return Ok(()); }
    let mut replacements = Vec::new();
    for id in plan.active_ids() {
        let parent = plan.block(id).ok_or(GeometryError::MissingBlock(id))?;
        let Some(source) = parent.primary_ref else { continue; };
        if matches!(parent.kind, "field" | "table" | "collection" | "record") || parent.layout != "tree"
            || ordered_ancestry(plan, context, source, true, true)
            || plan.evidence.get(&source).is_none_or(|item| item.incomplete || item.hidden)
        { continue; }
        let mut index = 0;
        let mut children = Vec::new();
        let mut paired = Vec::new();
        while index < parent.children.len() {
            let key = parent.children[index];
            children.push(key);
            if let Some((value, trigger)) = adjacent_selector(plan, context, &parent.children, index) {
                paired.push((key, parent.children[index + 1], value, trigger));
                index += 2;
            } else {
                index += 1;
            }
        }
        if !paired.is_empty() { replacements.push((id, children, paired)); }
    }
    for (parent, children, pairs) in replacements {
        for (field_id, child, value, trigger) in pairs {
            let field = plan.block_mut(field_id).ok_or(GeometryError::MissingBlock(field_id))?;
            field.children.push(child);
            field.layout = "fields";
            field.origin = "visual";
            field.field_target_ref = None;
            field.member_roles.push((trigger, "control"));
            let value = &context.projection.node(value).ok_or(GeometryError::Invalid("missing readonly value"))?.reference;
            let trigger = &context.projection.node(trigger).ok_or(GeometryError::Invalid("missing value selector"))?.reference;
            plan.decisions.push(format!("geometric-value-control:{value}:{trigger}"));
        }
        plan.block_mut(parent).ok_or(GeometryError::MissingBlock(parent))?.children = children;
    }
    Ok(())
}
