// Copyright (c) Huawei Technologies Co., Ltd. 2026. All rights reserved.

//! Retain derived names on the original, still-visible source fields.

use std::collections::HashSet;

use super::{field, rules, truthy, CompressionEvidence, NodeId, PresentedValue, Projection, ProjectionNode, Result, Value};

fn original<'a>(node: &'a ProjectionNode, name: &str) -> Option<&'a Value> {
    field(&node.native_fields, name)
}

fn presented<'a>(node: &'a ProjectionNode, name: &str) -> Option<&'a PresentedValue> {
    node.provenance.iter().find(|(attribute, _)| attribute == name).map(|(_, value)| value)
}

impl Projection {
    fn complete_path(&self, owner: &ProjectionNode, source: NodeId, evidence: &CompressionEvidence) -> Result<bool> {
        if !evidence.complete_children.contains_key(&owner.id) {
            return Ok(false);
        }
        let mut seen = HashSet::new();
        let mut current = source;
        while seen.insert(current) {
            let Some(node) = self.node(current).filter(|node| !node.fields.is_empty() && node.scope == owner.scope) else {
                return Ok(false);
            };
            if evidence.incomplete_children.contains(&current) || rules::incomplete(node)?
                || rules::incomplete_fields(&node.native_fields, node.native_children.len())?
            {
                return Ok(false);
            }
            if current == owner.id {
                return Ok(true);
            }
            let Some(parent) = node.parent.filter(|parent| {
                evidence.complete_children.get(parent).is_some_and(|children| children.contains(&current))
            }) else {
                return Ok(false);
            };
            current = parent;
        }
        Ok(false)
    }

    fn primary_targets(&self, owner: &ProjectionNode, evidence: &CompressionEvidence) -> Result<Vec<NodeId>> {
        let mut pending: Vec<_> = owner.children.iter().rev().copied().collect();
        let mut targets = Vec::new();
        while let Some(id) = pending.pop() {
            if !self.complete_path(owner, id, evidence)? {
                continue;
            }
            let node = self.display_node(id)?;
            let click = original(node, "capabilities").and_then(|caps| caps.get("click")).and_then(Value::as_str);
            if click == Some("available")
                && original(node, "enabled").and_then(Value::as_bool) != Some(false)
                && !original(node, "blockedBy").is_some_and(truthy)
                && !original(node, "actionsUnavailable").is_some_and(truthy)
                && original(node, "actions").and_then(Value::as_seq).is_some_and(|actions| {
                    actions.iter().any(|action| matches!(
                        action.get("kind").and_then(Value::as_str), Some("invoke" | "select" | "toggle")
                    ))
                })
            {
                targets.push(id);
            } else {
                pending.extend(node.children.iter().rev().copied());
            }
        }
        Ok(targets)
    }

    fn retained_caption_sources(&self, owner: &ProjectionNode, evidence: &CompressionEvidence) -> Result<Vec<Value>> {
        let Some(caption) = presented(owner, "name").filter(|caption| caption.separator.is_some()) else {
            return Ok(Vec::new());
        };
        let mut pending = owner.children.clone();
        let mut retained = HashSet::new();
        while let Some(id) = pending.pop() {
            let node = self.display_node(id)?;
            retained.insert(id);
            pending.extend_from_slice(&node.children);
        }
        let mut result = Vec::new();
        let mut seen = HashSet::new();
        for part in &caption.parts {
            let Some(text) = part.value.as_str().filter(|text| !text.is_empty()) else {
                return Ok(Vec::new());
            };
            if part.facts.is_empty() || text.encode_utf16().count() > 1200 || text.to_lowercase().contains(";base64,") {
                return Ok(Vec::new());
            }
            for fact in &part.facts {
                let source = self.display_node(fact.source)?;
                if !retained.contains(&fact.source) || !matches!(rules::role(source), "staticText" | "heading")
                    || !source.children.is_empty() || !self.complete_path(owner, fact.source, evidence)?
                    || !matches!(fact.attribute.as_str(), "name" | "description" | "value")
                    || !evidence.complete_text.contains(fact)
                    || source.field(&fact.attribute).and_then(Value::as_str) != Some(text)
                    || original(source, &fact.attribute).and_then(Value::as_str) != Some(text)
                    || rules::truth(source, "valueIsNumber")
                    || source.field("valueKind").and_then(Value::as_str) == Some("document")
                {
                    return Ok(Vec::new());
                }
                if !presented(source, &fact.attribute).is_some_and(|value| {
                    matches!(value.parts.as_slice(), [part] if part.value.as_str() == Some(text) && part.facts.contains(fact))
                }) {
                    return Ok(Vec::new());
                }
                if seen.insert(fact) {
                    let reference = Value::Text(source.reference.clone());
                    let attribute = Value::Text(fact.attribute.clone());
                    result.push(Value::Map(vec![
                        ("sourceRef".to_owned(), reference.clone()),
                        ("attribute".to_owned(), attribute.clone()),
                        ("value".to_owned(), Value::Text(text.to_owned())),
                        ("targetRef".to_owned(), reference),
                        ("field".to_owned(), attribute),
                    ]));
                }
            }
        }
        Ok(result)
    }

    fn single_child_value_caption(
        &self, owner: &ProjectionNode, targets: &[NodeId], evidence: &CompressionEvidence,
    ) -> Result<bool> {
        if !self.options.public_api || !self.options.share_descriptions {
            return Ok(false);
        }
        let ([child], [original_child]) = (owner.children.as_slice(), owner.native_children.as_slice()) else {
            return Ok(false);
        };
        let child = self.display_node(*child)?;
        if rules::role(child) != "staticText" || !child.children.is_empty() || *original_child != child.id
            || original(child, "role").and_then(Value::as_str) != Some("staticText") || !child.native_children.is_empty()
            || evidence.complete_children.get(&owner.id).is_none_or(|children| children.as_slice() != [child.id])
            || !evidence.complete_children.get(&child.id).is_some_and(Vec::is_empty)
            || child.parent != Some(owner.id) || targets != [child.id] || !self.complete_path(owner, child.id, evidence)?
            || rules::display_boundaries(owner, evidence, self.options.focused, false)? != rules::ACTION_BOUNDARY
            || rules::display_boundaries(child, evidence, self.options.focused, false)? != rules::ACTION_BOUNDARY
        {
            return Ok(false);
        }
        let Some(caption) = presented(owner, "name").filter(|caption| caption.separator.is_some()) else {
            return Ok(false);
        };
        let Some(retained) = presented(child, "value") else { return Ok(false) };
        let ([caption], [retained]) = (caption.parts.as_slice(), retained.parts.as_slice()) else { return Ok(false) };
        let Some(text) = caption.value.as_str().filter(|text| !text.is_empty()) else { return Ok(false) };
        Ok(owner.field("name").and_then(Value::as_str) == Some(text)
            && child.field("value").and_then(Value::as_str) == Some(text)
            && retained.value.as_str() == Some(text) && caption.facts == retained.facts
            && matches!(caption.facts.as_slice(), [fact] if fact.source == child.id && fact.attribute == "value"))
    }

    pub(crate) fn deduplicate_derived_captions(&mut self, evidence: &CompressionEvidence) -> Result<()> {
        if !self.options.compact || self.options.subtree.is_some() {
            return Ok(());
        }
        let mut pending = self.roots.clone();
        while let Some(id) = pending.pop() {
            let owner = self.display_node(id)?;
            pending.extend_from_slice(&owner.children);
            if rules::role(owner) != "group" || !rules::truth(owner, "_factBacked") || !rules::truth(owner, "name")
                || !evidence.unlabelled_groups.contains(&id)
                || original(owner, "control").and_then(|control| control.get("kind")).and_then(Value::as_str) != Some("button")
                || ["name", "description"].iter().any(|name| {
                    original(owner, name).and_then(Value::as_str) != Some("")
                        || !evidence.complete_text.contains(&super::FactKey { source: id, attribute: name.to_string() })
                })
                || ["linkedLabel", "titleRef", "labelTargets", "incompleteLabelRelation"].iter()
                    .any(|key| rules::truth(owner, key))
                || self.targets.iter().any(|(_, targets)| targets.contains(&owner.reference))
                || !self.complete_path(owner, id, evidence)?
            {
                continue;
            }
            let targets = self.primary_targets(owner, evidence)?;
            let single = targets.len() < 2 && self.single_child_value_caption(owner, &targets, evidence)?;
            if targets.len() < 2 && !single {
                continue;
            }
            let sources = self.retained_caption_sources(owner, evidence)?;
            if sources.is_empty() {
                continue;
            }
            let kind = if single { "repeatedDerivedSingleChildName" } else { "repeatedDerivedContainerName" };
            let targets = targets.into_iter().map(|id| {
                self.display_node(id).map(|node| Value::Text(node.reference.clone()))
            }).collect::<Result<_>>()?;
            let event = Value::Map(vec![
                ("sourceRef".to_owned(), Value::Text(owner.reference.clone())),
                ("kind".to_owned(), Value::Text(kind.to_owned())),
                ("field".to_owned(), Value::Text("name".to_owned())),
                ("previous".to_owned(), owner.field("name").expect("admitted nonempty caption").clone()),
                ("primaryTargets".to_owned(), Value::Seq(targets)),
                ("destinations".to_owned(), Value::Seq(sources)),
            ]);
            self.suppress(id, "name")?;
            self.changes.push(event);
        }
        Ok(())
    }
}
