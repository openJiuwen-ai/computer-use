// Copyright (c) Huawei Technologies Co., Ltd. 2026. All rights reserved.

use super::*;
use crate::projection::{rules, value_lines::covered_value_lines};

const VALUE_PREVIEW_LIMIT: usize = 1200;

fn nonempty_text(value: &str) -> bool {
    value.chars().any(|character| !character.is_whitespace() && !matches!(character, '\u{1c}'..='\u{1f}'))
}

fn form_control(node: &ProjectionNode) -> bool {
    let role = role(&node.fields);
    FORM_FIELDS.contains(&role)
        || matches!(role, "menuButton" | "incrementor" | "slider") && (
            ["value", "controlValue", "valueDescription"].iter().any(|name| {
                node.field(name).is_some_and(|value| !matches!(value, Value::Null))
            })
            || role == "menuButton" && node.field("description").and_then(Value::as_str).is_some_and(nonempty_text)
        )
}

fn caption_target(node: &ProjectionNode) -> bool {
    contained_caption(role(&node.fields)) || form_control(node)
}

fn caption_competitor(node: &ProjectionNode) -> Result<bool> {
    let role = role(&node.fields);
    if matches!(role, "window" | "application") {
        return Ok(false);
    }
    Ok(contained_caption(role)
        || matches!(role, "slider" | "scrollBar" | "incrementor" | "tab" | "closeButton"
            | "zoomButton" | "minimizeButton" | "disclosureTriangle")
        || rules::ordinary_control(node)
        || rules::independent_actions(node)?
        || node.field("capabilities").and_then(Value::as_map).is_some_and(|caps| {
            caps.iter().any(|(_, value)| value.as_str() == Some("available"))
        })
        || any(&node.fields, &["focusable", "selectable", "expandable", "submenu"]))
}

pub(crate) fn referenceable_caption(node: &ProjectionNode) -> Result<bool> {
    Ok(role(&node.fields) == "staticText"
        && node.children.is_empty()
        && node.field("value").and_then(Value::as_str).is_some_and(|value| {
            nonempty_text(value) && value.encode_utf16().count() <= VALUE_PREVIEW_LIMIT
        })
        && node.field("_completeTextValues").and_then(Value::as_seq).is_some_and(|values| {
            values.iter().any(|value| value.as_str() == Some("value"))
        })
        && !caption_competitor(node)?
        && !any(&node.fields, &["focused", "selected", "actionsUnavailable", "valueExpected", "incompleteLabelRelation", "modal"])
        && node.field("control").is_none())
}

fn relation(label: &Box, target: &Box, role: &str, competing_only: bool, field_like: bool) -> Option<&'static str> {
    if !label.comparable(target) {
        return None;
    }
    if target.contains(label) {
        return Some("inside");
    }
    if (!field_like && !competing_only) || label.intersection_area(target) > 0.0 {
        return None;
    }
    if label.vertical_overlap(target) >= 0.75 {
        if (0.0..=2.0 * label.height).contains(&(target.x - label.right())) {
            return Some("left");
        }
        if (matches!(role, "checkBox" | "radioButton" | "switch") || competing_only)
            && (0.0..=2.0 * label.height).contains(&(label.x - target.right()))
        {
            return Some("right");
        }
    }
    if (0.0..=0.75 * label.height).contains(&(target.y - label.bottom()))
        && (target.x - label.x).abs() <= 0.5 * label.height
        && label.horizontal_overlap(target) >= 0.9
    {
        return Some("above");
    }
    None
}

#[derive(Clone, Copy)]
struct LabelMatch {
    label: NodeId,
    target: NodeId,
    relation: &'static str,
}

fn unique_matches(
    projection: &Projection, labels: &[NodeId], targets: &[NodeId], geometry: &Geometry,
) -> Result<Vec<LabelMatch>> {
    let mut by_label = Vec::new();
    let mut target_counts = HashMap::new();
    for &label in labels {
        let Some(label_box) = geometry.get(&label) else { continue };
        let mut candidates = Vec::new();
        for &id in targets {
            let Some(target_box) = geometry.get(&id) else { continue };
            let target = projection.display_node(id)?;
            if let Some(relation) = relation(label_box, target_box, role(&target.fields), !caption_target(target), form_control(target)) {
                candidates.push(LabelMatch { label, target: id, relation });
            }
        }
        if candidates.iter().any(|candidate| candidate.relation == "inside") {
            candidates.retain(|candidate| candidate.relation == "inside");
        }
        for candidate in &candidates {
            *target_counts.entry(candidate.target).or_insert(0) += 1;
        }
        by_label.push(candidates);
    }
    Ok(by_label.into_iter().filter_map(|candidates| {
        let [candidate] = candidates.as_slice() else { return None };
        (target_counts[&candidate.target] == 1).then_some(*candidate)
    }).collect())
}

fn label_event(projection: &mut Projection, candidate: LabelMatch, kind: &str) -> Result<()> {
    let label = reference(projection, candidate.label)?;
    let target = reference(projection, candidate.target)?;
    projection.changes.push(Value::Map(vec![
        ("sourceRef".to_owned(), label.clone()),
        ("targetRef".to_owned(), target.clone()),
        ("kind".to_owned(), Value::Text(kind.to_owned())),
        ("relation".to_owned(), Value::Text(candidate.relation.to_owned())),
        ("geometrySources".to_owned(), Value::Seq(vec![label, target])),
    ]));
    Ok(())
}

pub(super) fn associate_labels(projection: &mut Projection, geometry: &Geometry, evidence: &CompressionEvidence) -> Result<()> {
    let mut pending: Vec<_> = projection.roots.iter().rev().copied().collect();
    while let Some(id) = pending.pop() {
        let parent = projection.display_node(id)?;
        let mut consumed = HashSet::new();
        if visible_source_path(projection, id)?
            && (available_region(parent)? || caption_target(parent) && complete_visible(&parent.fields, &parent.children)?)
        {
            let mut labels = Vec::new();
            let mut targets = Vec::new();
            for &child in &parent.children {
                let child = projection.display_node(child)?;
                if caption_competitor(child)? && !hidden(&child.fields) {
                    targets.push(child.id);
                }
                if role(&child.fields) == "staticText" && child.children.is_empty() && !hidden(&child.fields)
                    && !any(&child.fields, &["labelTargets"])
                    && !projection.targets.iter().any(|(source, targets)| *source == child.reference && !targets.is_empty())
                {
                    labels.push(child.id);
                }
            }
            if caption_competitor(parent)? && !hidden(&parent.fields) {
                targets.push(id);
            }
            for candidate in unique_matches(projection, &labels, &targets, geometry)? {
                let LabelMatch { label, target, .. } = candidate;
                let label_node = projection.display_node(label)?;
                let target_node = projection.display_node(target)?;
                let label_region = source_region(projection, id, label)?;
                if !caption_target(target_node)
                    || !complete_visible(&target_node.fields, &target_node.children)?
                    || !complete_visible(&label_node.fields, &label_node.children)?
                    || any(&target_node.fields, &["titleRef", "linkedLabel", "incompleteLabelRelation"])
                    || label_region.is_none() || label_region != source_region(projection, id, target)?
                    || label_node.scope != target_node.scope || !same_blocking(label_node, target_node)?
                {
                    continue;
                }
                let label_value = label_node.field("value");
                let own_value = target == id && label_value.is_some_and(|value| {
                    !matches!(value, Value::Null) && value.as_str() != Some("")
                }) && label_value == target_node.field("value");
                let parts = rules::text_sources(label_node);
                if own_value || covered_value_lines(projection, target_node, &parts, evidence)?.is_some() {
                    continue;
                }
                if !projection.options.defer_visual_composition
                    && !any(&target_node.fields, &["name", "description", "preserveGroup"])
                    && projection.passive_caption(label_node, target_node, &parts)?
                {
                    projection.adopt(target, &parts, "name", " ", false)?;
                    projection.fold(label, target, "associate_spatial_labels", "label")?;
                    label_event(projection, candidate, "spatial-label")?;
                    consumed.insert(label);
                } else if referenceable_caption(label_node)?
                    && !parts.iter().all(|part| target_node.text_sources.iter().any(|own| own.text == part.text))
                {
                    let label_ref = reference(projection, label)?;
                    annotate(projection, target, "visualLabelRef", label_ref.clone())?;
                    add_context(projection, target, std::slice::from_ref(&label_ref))?;
                    label_event(projection, candidate, "spatial-label-reference")?;
                }
            }
        }
        let parent = projection.node_mut(id)?;
        if !consumed.is_empty() {
            parent.children.retain(|child| !consumed.contains(child));
        }
        pending.extend(parent.children.iter().rev().copied());
    }
    Ok(())
}
