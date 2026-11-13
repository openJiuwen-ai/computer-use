// Copyright (c) Huawei Technologies Co., Ltd. 2026. All rights reserved.

//! Geometry-assisted organization of an already scoped projection.

use std::cmp::Ordering;
use std::collections::{HashMap, HashSet};

use crate::geometry::{Box, Geometry};

use super::{field, put, truthy, CompressionEvidence, Fields, NodeId, Projection, ProjectionError, ProjectionNode, Result, Value};

mod association;
mod rows;
pub(crate) use association::referenceable_caption;
pub(crate) use rows::atomic_rows;
#[cfg(test)]
mod tests;

const REGIONS: &[&str] = &["group", "webArea", "document", "scrollArea", "window", "splitGroup"];
const FORM_FIELDS: &[&str] = &["textField", "textArea", "comboBox", "popUpButton", "checkBox", "radioButton", "switch"];
const COLLECTIONS: &[&str] = &["list", "outline", "table", "row", "cell", "column"];
const TABLE_FIELDS: &[&str] = &[
    "rowRange", "columnRange", "rowCount", "columnCount", "index", "cellPosition", "selectedChildCount",
    "cellRow", "cellColumn", "cellRowSpan", "cellColumnSpan",
];

fn role(fields: &Fields) -> &str {
    field(fields, "role").and_then(Value::as_str).unwrap_or("")
}

fn any(fields: &Fields, names: &[&str]) -> bool {
    names.iter().any(|name| field(fields, name).is_some_and(truthy))
}

fn hidden(fields: &Fields) -> bool {
    matches!(field(fields, "visible"), Some(Value::Bool(false)))
        || matches!(field(fields, "showing"), Some(Value::Bool(false)))
        || matches!(field(fields, "offscreen"), Some(Value::Bool(true)))
}

fn incomplete_children(fields: &Fields, children: &[NodeId]) -> Result<bool> {
    if ["partialReason", "nextChildOffset", "childrenStart", "childrenEnd"]
        .iter().any(|name| field(fields, name).is_some())
        || any(fields, &["childOmissions"])
    {
        return Ok(true);
    }
    let Some(total) = field(fields, "childCount") else { return Ok(false) };
    let count = |value: &Value| match value {
        Value::Int(value) => Ok(*value),
        Value::Bool(value) => Ok(i64::from(*value)),
        _ => Err(ProjectionError::Invalid("spatial child coverage must use integer counts")),
    };
    let observed = field(fields, "childrenRead").map(count).transpose()?.unwrap_or(children.len() as i64);
    Ok(observed < count(total)?)
}

fn complete_visible(fields: &Fields, children: &[NodeId]) -> Result<bool> {
    Ok(!hidden(fields) && !incomplete_children(fields, children)?)
}

fn available_region(node: &ProjectionNode) -> Result<bool> {
    let fields = &node.fields;
    Ok(REGIONS.contains(&role(fields))
        && complete_visible(fields, &node.children)?
        && !any(fields, &["modal"])
        && !matches!(field(fields, "subrole").and_then(Value::as_str), Some("dialog" | "systemDialog")))
}

fn logical_structure(fields: &Fields) -> bool {
    let role = role(fields);
    COLLECTIONS.contains(&role)
        || matches!(role, "menu" | "menuBar" | "menuItem" | "menuBarItem")
        || TABLE_FIELDS.iter().chain(["disclosureLevel", "disclosedBy", "incompleteDisclosureRelation"].iter())
            .any(|name| field(fields, name).is_some())
}

fn native_path_matches(
    projection: &Projection,
    node: NodeId,
    predicate: impl Fn(&Fields) -> bool,
) -> Result<bool> {
    let mut current = Some(node);
    while let Some(id) = current {
        let node = projection.node(id).ok_or(ProjectionError::Invalid("unknown spatial source"))?;
        if predicate(&node.native_fields) {
            return Ok(true);
        }
        current = node.parent;
    }
    Ok(false)
}

fn visible_source_path(projection: &Projection, id: NodeId) -> Result<bool> {
    Ok(!native_path_matches(projection, id, hidden)?)
}

fn source_region(projection: &Projection, parent: NodeId, leaf: NodeId) -> Result<Option<Vec<NodeId>>> {
    if parent == leaf {
        return Ok(Some(Vec::new()));
    }
    let leaf = projection.node(leaf).ok_or(ProjectionError::Invalid("unknown spatial child"))?;
    let mut current = leaf.parent;
    let mut regions = Vec::new();
    while let Some(id) = current {
        if id == parent {
            return Ok(Some(regions));
        }
        let node = projection.node(id).ok_or(ProjectionError::Invalid("unknown spatial ancestor"))?;
        if !complete_visible(&node.native_fields, &node.native_children)? {
            return Ok(None);
        }
        if node.native_children.len() > 1 {
            regions.push(id);
        }
        current = node.parent;
    }
    Ok(None)
}

fn same_blocking(left: &ProjectionNode, right: &ProjectionNode) -> Result<bool> {
    fn blocking(node: &ProjectionNode) -> Result<String> {
        node.field("blockedBy").map(|value| {
            let values = value.as_seq().ok_or(ProjectionError::Invalid("spatial blocking path must be ordered"))?;
            let refs = values.iter().map(|value| {
                value.as_str().ok_or(ProjectionError::Invalid("spatial blocking reference must be text"))
            }).collect::<Result<Vec<_>>>()?;
            Ok(refs.join(","))
        }).transpose().map(Option::unwrap_or_default)
    }
    Ok(blocking(left)? == blocking(right)?)
}

fn parallel_regions(projection: &Projection, id: NodeId, geometry: &Geometry) -> Result<Vec<NodeId>> {
    let parent = projection.display_node(id)?;
    if !available_region(parent)? || !visible_source_path(projection, id)?
        || native_path_matches(projection, id, |fields| COLLECTIONS.contains(&role(fields)))?
    {
        return Ok(Vec::new());
    }
    let mut regions = Vec::new();
    let mut dividers = Vec::new();
    let mut space = None;
    let mut partition = None;
    for &child_id in &parent.children {
        let child = projection.display_node(child_id)?;
        if child.scope != parent.scope || !same_blocking(parent, child)?
            || !complete_visible(&child.fields, &child.children)? || !visible_source_path(projection, child_id)?
        {
            return Ok(Vec::new());
        }
        let Some(box_) = geometry.get(&child_id) else { return Ok(Vec::new()) };
        if available_region(child)? && !child.children.is_empty() {
            regions.push((child_id, box_));
        } else if matches!(role(&child.fields), "separator" | "growArea") {
            dividers.push((child_id, box_));
        } else {
            return Ok(Vec::new());
        }
        if let Some(space) = space {
            if space != &box_.space {
                return Ok(Vec::new());
            }
        } else {
            space = Some(&box_.space);
        }
        let Some(source) = source_region(projection, id, child_id)? else { return Ok(Vec::new()) };
        if let Some(partition) = &partition {
            if *partition != source {
                return Ok(Vec::new());
            }
        } else {
            partition = Some(source);
        }
    }
    if !matches!(regions.len(), 2 | 3) {
        return Ok(Vec::new());
    }
    regions.sort_by(|(_, left), (_, right)| left.x.partial_cmp(&right.x).unwrap_or(Ordering::Equal));
    if regions.windows(2).any(|pair| pair[0].1.right() > pair[1].1.x || pair[0].1.vertical_overlap(pair[1].1) < 0.75) {
        return Ok(Vec::new());
    }
    if geometry.get(&id).is_some_and(|parent| {
        regions.iter().chain(&dividers).any(|(_, child)| !parent.contains(child))
    }) {
        return Ok(Vec::new());
    }
    for (_, divider) in dividers {
        if !regions.windows(2).any(|pair| {
            let (left, right) = (pair[0].1, pair[1].1);
            left.right() <= divider.x && divider.right() <= right.x
                && divider.width <= left.width.min(right.width) * 0.08
                && divider.vertical_overlap(left) >= 0.75 && divider.vertical_overlap(right) >= 0.75
        }) {
            return Ok(Vec::new());
        }
    }
    Ok(regions.into_iter().map(|(id, _)| id).collect())
}

fn annotate(projection: &mut Projection, id: NodeId, name: &str, value: Value) -> Result<()> {
    put(&mut projection.node_mut(id)?.fields, name, value);
    Ok(())
}

fn add_context(projection: &mut Projection, id: NodeId, values: &[Value]) -> Result<()> {
    let node = projection.node_mut(id)?;
    let prior = node.field("_contextRefs").and_then(Value::as_seq).unwrap_or_default();
    let mut contexts = Vec::new();
    for value in prior.iter().chain(values) {
        if !contexts.contains(value) {
            contexts.push(value.clone());
        }
    }
    put(&mut node.fields, "_contextRefs", Value::Seq(contexts));
    Ok(())
}

fn reference(projection: &Projection, id: NodeId) -> Result<Value> {
    Ok(Value::Text(projection.display_node(id)?.reference.clone()))
}

fn event(
    projection: &mut Projection,
    source: NodeId,
    kind: &str,
    fields: impl IntoIterator<Item = (&'static str, Value)>,
) -> Result<()> {
    let mut change = vec![
        ("sourceRef".to_owned(), reference(projection, source)?),
        ("kind".to_owned(), Value::Text(kind.to_owned())),
    ];
    change.extend(fields.into_iter().map(|(name, value)| (name.to_owned(), value)));
    projection.changes.push(Value::Map(change));
    Ok(())
}

fn describe_regions(projection: &mut Projection, geometry: &Geometry, protect: bool) -> Result<()> {
    for parent in projection.visible_order()? {
        let ordered = parallel_regions(projection, parent, geometry)?;
        for (index, &child) in ordered.iter().enumerate() {
            if protect && !any(&projection.display_node(child)?.fields, &["preserveGroup"]) {
                annotate(projection, child, "preserveGroup", Value::Bool(true))?;
                let sources = ordered.iter().map(|&id| reference(projection, id)).collect::<Result<Vec<_>>>()?;
                event(projection, child, "spatial-region", [("geometrySources", Value::Seq(sources))])?;
            }
            if !any(&projection.display_node(child)?.fields, &["name", "description"]) {
                let position = if index == 0 { "left" } else if index == ordered.len() - 1 { "right" } else { "middle" };
                let position = Value::Text(position.to_owned());
                annotate(projection, child, "spatialPosition", position.clone())?;
                event(projection, child, "spatial-position", [("position", position)])?;
            }
        }
    }
    Ok(())
}

pub(super) fn protect_regions(projection: &mut Projection, geometry: &Geometry) -> Result<()> {
    if projection.options.compact {
        describe_regions(projection, geometry, true)?;
    }
    Ok(())
}

fn contained_caption(role: &str) -> bool {
    FORM_FIELDS.contains(&role) || matches!(role, "button" | "menuButton" | "link")
}

fn tight_containment(parent: &Box, child: &Box) -> bool {
    parent.contains(child) && child.width >= parent.width * 0.8 && child.height >= parent.height * 0.8
        && (child.x - parent.x).max(child.y - parent.y)
            .max(parent.right() - child.right()).max(parent.bottom() - child.bottom())
            <= child.width.min(child.height) / 4.0
}

fn mark_visual_chains(projection: &mut Projection, geometry: &Geometry) -> Result<()> {
    let protected = ["layoutGroups", "spatialPosition", "blockedBy", "modal"];
    for id in projection.visible_order()? {
        let parent = projection.display_node(id)?;
        let [child_id] = parent.children.as_slice() else { continue };
        if role(&parent.fields) != "group" || parent.native_children.len() != 1
            || any(&parent.fields, &protected)
            || !complete_visible(&parent.fields, &parent.children)? || !visible_source_path(projection, id)?
        {
            continue;
        }
        let child = projection.display_node(*child_id)?;
        let child_role = role(&child.fields);
        if (child_role != "group" && !contained_caption(child_role)) || any(&child.fields, &protected)
            || !complete_visible(&child.fields, &child.children)? || !visible_source_path(projection, *child_id)?
            || parent.scope != child.scope
        {
            continue;
        }
        let (Some(outer), Some(inner)) = (geometry.get(&id), geometry.get(child_id)) else { continue };
        if tight_containment(outer, inner) {
            let child_ref = reference(projection, *child_id)?;
            annotate(projection, id, "visualChainChild", child_ref.clone())?;
            event(projection, id, "visual-chain", [("childRef", child_ref)])?;
        }
    }
    Ok(())
}

pub(super) fn organize(projection: &mut Projection, geometry: &Geometry, evidence: &CompressionEvidence) -> Result<()> {
    projection.run_rule("associate_spatial_labels", |projection| association::associate_labels(projection, geometry, evidence))?;
    projection.run_rule("group_spatial_rows", |projection| rows::group_rows(projection, geometry))?;
    projection.run_rule("describe_spatial_regions", |projection| describe_regions(projection, geometry, false))?;
    projection.run_rule("mark_visual_chains", |projection| mark_visual_chains(projection, geometry))
}
