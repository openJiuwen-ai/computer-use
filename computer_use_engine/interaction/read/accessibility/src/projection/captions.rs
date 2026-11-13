// Copyright (c) Huawei Technologies Co., Ltd. 2026. All rights reserved.

use std::collections::HashSet;

use super::{
    field, remove, rules, truthy, CaptionEquivalence, CompressionEvidence, FactKey, NodeId, PresentedValue, Projection,
    ProjectionError, ProjectionNode, Result, Value, ValuePart,
};

const INDEPENDENT_CAPTION_FIELDS: &[&str] = &[
    "name", "description", "help", "url", "identifier", "titleRef", "labelTargets", "linkedLabel",
    "incompleteLabelRelation", "preserveGroup", "subrole", "placeholder", "documentUrl", "documentText", "controlValue",
    "rangeValue", "valueExpected", "headingLevel", "landmarkType", "fullDescription", "shortcut", "menuMark",
    "toggleState", "disclosedBy", "incompleteDisclosureRelation",
];
const ACTIVE_CAPTION_FIELDS: &[&str] = &[
    "focused", "selected", "pressable", "focusable", "selectable", "expandable", "submenu", "modal",
];

fn complete_text(value: &str) -> bool {
    !value.is_empty() && value.encode_utf16().count() <= 1200
        && !value.to_lowercase().contains(";base64,")
        && !value.chars().any(|ch| matches!(ch, '\r' | '\n' | '\u{85}' | '\u{2028}' | '\u{2029}'))
}

fn original_action(node: &ProjectionNode) -> Result<bool> {
    let Some(actions) = field(&node.native_fields, "actions").and_then(Value::as_seq) else { return Ok(false) };
    for action in actions {
        if !matches!(rules::action_kind(action)?, "show_menu" | "scroll_into_view") {
            return Ok(true);
        }
    }
    Ok(false)
}

struct Candidate<'a> {
    projection: &'a Projection,
    owner: &'a ProjectionNode,
    evidence: &'a CompressionEvidence,
    visible: &'a HashSet<NodeId>,
    referenced: &'a HashSet<String>,
    visited: HashSet<NodeId>,
    parts: Vec<ValuePart>,
}

impl Candidate<'_> {
    fn visit(&mut self, id: NodeId, parent: NodeId) -> Result<bool> {
        let mut pending = vec![(id, parent)];
        while let Some((id, parent)) = pending.pop() {
            let Some(native) = self.projection.node(id) else { return Ok(false) };
            if !self.visited.insert(id) || native.parent != Some(parent) || native.scope != self.owner.scope
                || self.evidence.incomplete_children.contains(&id)
                || rules::incomplete_fields(&native.native_fields, native.native_children.len())?
            {
                return Ok(false);
            }
            let role = field(&native.native_fields, "role").and_then(Value::as_str);
            let node = self.visible.contains(&id).then_some(native);
            if role == Some("staticText") {
                let (Some(node), Some(source)) = (node, self.evidence.caption_sources.get(&id)) else {
                    return Ok(false);
                };
                let Some(through_owner) = source.ancestors.iter().position(|ancestor| *ancestor == self.owner.id) else {
                    return Ok(false);
                };
                let fact = FactKey { source: id, attribute: "value".to_owned() };
                if !node.children.is_empty() || rules::incomplete(node)? || self.projection.keep(id)
                    || self.projection.options.focused == Some(id)
                    || rules::truth(node, "valueIsNumber") || node.field("valueKind").and_then(Value::as_str) == Some("document")
                    || !rules::same_blocking(node, self.owner) || !self.evidence.complete_text.contains(&fact)
                    || !complete_text(&source.value)
                    || INDEPENDENT_CAPTION_FIELDS.iter().any(|key| rules::truth(node, key))
                    || self.referenced.contains(&node.reference)
                    || rules::independent_actions(node)?
                    || source.reported_windows.iter().any(|(member, target)| {
                        (*member == id || source.ancestors[..=through_owner].contains(member)) && *target != Some(self.owner.scope)
                    })
                {
                    return Ok(false);
                }
                let mut protected = node.clone();
                if node.field("selectionRange").is_some_and(|range| {
                    range.as_map().is_some_and(|values| values.len() == 2)
                        && range.get("location").and_then(Value::as_i64) == Some(0)
                        && range.get("length").and_then(Value::as_i64) == Some(0)
                }) {
                    remove(&mut protected.fields, "selectionRange");
                }
                if node.field("selectedText").and_then(Value::as_str) == Some("") {
                    remove(&mut protected.fields, "selectedText");
                }
                if rules::protected_content(&protected)? || ACTIVE_CAPTION_FIELDS.iter().any(|key| rules::truth(node, key))
                    || rules::capabilities(node).and_then(Value::as_map).is_some_and(|caps| {
                        caps.iter().any(|(_, value)| value.as_str() == Some("available"))
                    })
                {
                    return Ok(false);
                }
                if node.field("value").and_then(Value::as_str) != Some(&source.value)
                    || field(&native.native_fields, "value").and_then(Value::as_str) != Some(&source.value)
                    || !node.provenance.iter().find(|(key, _)| key == "value").is_some_and(|(_, value)| {
                        matches!(value.parts.as_slice(), [part] if part.facts.as_slice() == std::slice::from_ref(&fact))
                    })
                {
                    return Ok(false);
                }
                self.parts.push(ValuePart { value: Value::Text(source.value.clone()), facts: vec![fact] });
                continue;
            }
            if role == Some("image") {
                if node.is_none() || !native.native_children.is_empty() || original_action(native)? {
                    return Ok(false);
                }
                continue;
            }
            let Some(children) = self.evidence.complete_children.get(&id) else { return Ok(false) };
            if node.is_some() || role != Some("group")
                || ["name", "description", "value", "help", "url", "titleRef"].iter()
                    .any(|name| field(&native.native_fields, name).is_some_and(truthy))
                || original_action(native)?
            {
                return Ok(false);
            }
            pending.extend(children.iter().rev().map(|&child| (child, id)));
        }
        Ok(true)
    }
}

impl Projection {
    fn caption_equivalence(
        &self, owner: &ProjectionNode, evidence: &CompressionEvidence, visible: &HashSet<NodeId>, referenced: &HashSet<String>,
    ) -> Result<Option<CaptionEquivalence>> {
        let role = rules::role(owner);
        if !matches!(role, "link" | "button")
            || field(&owner.native_fields, "role").and_then(Value::as_str) != Some(role)
            || !rules::click_available(owner) || rules::truth(owner, "actionsUnavailable") || rules::truth(owner, "incompleteLabelRelation")
            || rules::incomplete(owner)? || rules::incomplete_fields(&owner.native_fields, owner.native_children.len())?
        {
            return Ok(None);
        }
        let Some(children) = evidence.complete_children.get(&owner.id) else { return Ok(None) };
        let display_field = if rules::truth(owner, "name") { "name" } else { "description" };
        let Some(caption) = owner.field(display_field).and_then(Value::as_str).filter(|text| complete_text(text)) else {
            return Ok(None);
        };
        let Some((_, value)) = owner.provenance.iter().find(|(key, _)| key == display_field) else { return Ok(None) };
        let [part] = value.parts.as_slice() else { return Ok(None) };
        let [anchor] = part.facts.as_slice() else { return Ok(None) };
        if anchor.source != owner.id || !matches!(anchor.attribute.as_str(), "name" | "description")
            || field(&owner.native_fields, &anchor.attribute).and_then(Value::as_str) != Some(caption)
            || !evidence.complete_text.contains(anchor)
            || ["name", "description"].iter().any(|key| {
                let field = field(&owner.native_fields, key);
                !rules::empty_content(field) && field.and_then(Value::as_str) != Some(caption)
            })
        {
            return Ok(None);
        }
        let mut candidate = Candidate {
            projection: self, owner, evidence, visible, referenced, visited: HashSet::new(), parts: Vec::new(),
        };
        for &child in children {
            if !candidate.visit(child, owner.id)? {
                return Ok(None);
            }
        }
        if candidate.parts.len() < 2 || candidate.parts.iter()
            .map(|part| part.value.as_str().expect("complete caption part")).collect::<Vec<_>>().join(" ") != caption
        {
            return Ok(None);
        }
        Ok(Some(CaptionEquivalence {
            owner: owner.id,
            scope: owner.scope,
            display_field: display_field.to_owned(),
            anchor: anchor.clone(),
            composition: PresentedValue { parts: candidate.parts, separator: Some(" ".to_owned()) },
        }))
    }

    pub(crate) fn fold_native_caption_components(&mut self, evidence: &CompressionEvidence) -> Result<()> {
        if !self.options.compact || !self.options.native_facts || self.options.subtree.is_some() {
            return Ok(());
        }
        let mut pending = self.roots.clone();
        let mut order = Vec::new();
        while let Some(id) = pending.pop() {
            order.push(id);
            pending.extend_from_slice(&self.display_node(id)?.children);
        }
        let mut visible: HashSet<_> = order.iter().copied().collect();
        let referenced: HashSet<_> = self.targets.iter().flat_map(|(_, refs)| refs.iter().cloned()).collect();
        for id in order {
            let owner = self.display_node(id)?;
            let Some(proof) = self.caption_equivalence(owner, evidence, &visible, &referenced)? else { continue };
            let refs: Vec<_> = proof.composition.parts.iter().map(|part| part.facts[0].source).collect();
            if owner.children.iter().copied().filter(|id| refs.contains(id)).collect::<Vec<_>>() != refs {
                continue;
            }
            let children: Vec<_> = owner.children.iter().copied().filter(|id| !refs.contains(id)).collect();
            let components = proof.composition.parts.iter().map(|part| {
                let fact = &part.facts[0];
                let source = self.display_node(fact.source)?;
                Ok(Value::Map(vec![
                    ("sourceRef".to_owned(), Value::Text(source.reference.clone())),
                    ("attribute".to_owned(), Value::Text(fact.attribute.clone())),
                    ("value".to_owned(), part.value.clone()),
                    ("detailField".to_owned(), Value::Text("control_value.value".to_owned())),
                ]))
            }).collect::<Result<Vec<_>>>()?;
            let caption = owner.field(&proof.display_field).cloned()
                .ok_or(ProjectionError::Invalid("caption anchor disappeared"))?;
            let reference = Value::Text(owner.reference.clone());
            let event = Value::Map(vec![
                ("sourceRef".to_owned(), reference.clone()),
                ("kind".to_owned(), Value::Text("native-caption-components".to_owned())),
                ("scope".to_owned(), Value::Text(self.display_node(proof.scope)?.reference.clone())),
                ("field".to_owned(), Value::Text(proof.display_field.clone())),
                ("anchor".to_owned(), Value::Map(vec![
                    ("sourceRef".to_owned(), reference),
                    ("attribute".to_owned(), Value::Text(proof.anchor.attribute.clone())),
                    ("value".to_owned(), caption),
                ])),
                ("separator".to_owned(), Value::Text(" ".to_owned())),
                ("components".to_owned(), Value::Seq(components)),
            ]);
            for source in refs {
                self.fold(source, id, "fold_native_caption_components", "caption-component")?;
                visible.remove(&source);
            }
            self.node_mut(id)?.children = children;
            self.caption_equivalences.push(proof);
            self.changes.push(event);
        }
        Ok(())
    }
}
