// Copyright (c) Huawei Technologies Co., Ltd. 2026. All rights reserved.

//! Compose only complete source text in a single established visual band.

use crate::geometry::{Box, Geometry};

use super::{field, rules, CompressionEvidence, FactKey, Projection, ProjectionNode, Result, TextSource, Value};

fn caption(node: &ProjectionNode, evidence: &CompressionEvidence) -> Option<TextSource> {
    let names = ["name", "description", "value"];
    let mut values = names.iter().filter_map(|name| node.field(name).and_then(Value::as_str)).filter(|text| !text.is_empty());
    let text = values.next()?;
    if values.any(|value| value != text) || text.chars().any(|c| matches!(c, '\r' | '\n' | '\u{85}' | '\u{2028}' | '\u{2029}')) {
        return None;
    }
    for name in names {
        let fact = FactKey { source: node.id, attribute: name.to_owned() };
        if node.field(name).and_then(Value::as_str) != Some(text) || !evidence.complete_text.contains(&fact) {
            continue;
        }
        if let Some((_, value)) = node.provenance.iter().find(|(attribute, _)| attribute == name) {
            if let [part] = value.parts.as_slice() {
                if part.facts.as_slice() == std::slice::from_ref(&fact)
                    && part.value.as_str() == Some(text)
                    && field(&node.native_fields, name).and_then(Value::as_str) == Some(text)
                {
                    return Some(TextSource { fact, field: "name".to_owned(), text: Value::Text(text.to_owned()) });
                }
            }
        }
    }
    None
}

fn one_visual_line(owner: &Box, boxes: &[&Box]) -> bool {
    if boxes.iter().any(|bounds| !owner.comparable(bounds)) {
        return false;
    }
    let height = boxes.iter().map(|bounds| bounds.height).fold(f64::NEG_INFINITY, f64::max);
    let edge = 0.1 * boxes.iter().map(|bounds| bounds.height).fold(f64::INFINITY, f64::min);
    if boxes.iter().any(|bounds| {
        bounds.x < owner.x - edge || bounds.y < owner.y - edge
            || bounds.right() > owner.right() + edge || bounds.bottom() > owner.bottom() + edge
    }) {
        return false;
    }
    let bottom = boxes.iter().map(|bounds| bounds.bottom()).fold(f64::INFINITY, f64::min);
    let top = boxes.iter().map(|bounds| bounds.y).fold(f64::NEG_INFINITY, f64::max);
    if bottom - top < 0.8 * height || owner.height > 1.5 * height {
        return false;
    }
    boxes.windows(2).all(|pair| {
        let left = pair[0];
        let right = pair[1];
        let gap = right.x - left.right();
        left.center().0 < right.center().0
            && left.x <= right.x
            && -edge.min(0.25 * left.width.min(right.width)) <= gap
            && gap <= height
    })
}

impl Projection {
    pub(crate) fn compose_passive_text_lines(&mut self, evidence: &CompressionEvidence, geometry: &Geometry) -> Result<()> {
        if !self.options.compact || !self.options.native_facts || self.options.subtree.is_some() {
            return Ok(());
        }
        let mut pending: Vec<_> = self.roots.iter().rev().copied().collect();
        while let Some(id) = pending.pop() {
            let owner = self.display_node(id)?;
            pending.extend(owner.children.iter().rev().copied());
            if self.folded_into.iter().any(|(folded, _)| *folded == id)
                || rules::role(owner) != "group" || !rules::truth(owner, "_factBacked")
                || self.keep(id) || owner.children.len() < 2
            {
                continue;
            }
            if rules::display_boundaries(owner, evidence, self.options.focused, false)? != 0 {
                let help = owner.field("help").and_then(Value::as_str);
                if help.is_none_or(str::is_empty)
                    || field(&owner.native_fields, "help").and_then(Value::as_str) != help
                    || rules::display_boundaries(owner, evidence, self.options.focused, true)? != 0
                {
                    continue;
                }
            }
            if rules::incomplete(owner)? || rules::truth(owner, "layoutGroups")
                || rules::truth(owner, "labelTargets") || rules::truth(owner, "incompleteLabelRelation")
                || ["name", "description", "value", "url", "controlValue", "rangeValue", "documentText"]
                    .iter().any(|name| !rules::empty_content(owner.field(name)))
                || evidence.complete_children.get(&id) != Some(&owner.children)
            {
                continue;
            }
            let Some(owner_box) = geometry.get(&id) else { continue };
            let children = owner.children.clone();
            let mut parts = Vec::new();
            let mut boxes = Vec::new();
            let mut members = Vec::new();
            let mut positions = Vec::new();
            for &child_id in &children {
                let child = self.display_node(child_id)?;
                if rules::role(child) != "staticText" || !child.children.is_empty() || self.keep(child_id)
                    || rules::display_boundaries(child, evidence, self.options.focused, false)? != 0
                    || rules::incomplete(child)?
                    || ["valueIsNumber", "labelTargets", "incompleteLabelRelation"].iter().any(|name| rules::truth(child, name))
                    || rules::truth(child, "url")
                    || ["documentText", "controlValue", "rangeValue"].iter().any(|name| child.field(name).is_some())
                    || child.parent != Some(id) || child.scope != owner.scope || !rules::same_blocking(owner, child)
                    || !evidence.complete_children.get(&child_id).is_some_and(Vec::is_empty)
                {
                    break;
                }
                let (Some(part), Some(bounds)) = (caption(child, evidence), geometry.get(&child_id)) else { break };
                parts.push(part);
                boxes.push(bounds);
                let reference = Value::Text(child.reference.clone());
                members.push(reference.clone());
                if let Some(position) = child.field("spatialPosition").filter(|value| super::truthy(value)) {
                    positions.push(Value::Map(vec![
                        ("sourceRef".to_owned(), reference),
                        ("position".to_owned(), position.clone()),
                    ]));
                }
            }
            if parts.len() != children.len() || !one_visual_line(owner_box, &boxes) {
                continue;
            }
            let owner_ref = owner.reference.clone();
            self.adopt(id, &parts, "name", " ", true)?;
            for child in children {
                self.fold(child, id, "compose_passive_text_lines", "passive-text-line")?;
            }
            self.node_mut(id)?.children.clear();
            let mut event = vec![
                ("sourceRef".to_owned(), Value::Text(owner_ref)),
                ("kind".to_owned(), Value::Text("composed-passive-text-line".to_owned())),
                ("members".to_owned(), Value::Seq(members)),
            ];
            if !positions.is_empty() {
                event.push(("internalPositions".to_owned(), Value::Seq(positions)));
            }
            self.changes.push(Value::Map(event));
        }
        Ok(())
    }
}
