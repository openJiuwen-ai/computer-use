// Copyright (c) Huawei Technologies Co., Ltd. 2026. All rights reserved.

use std::collections::HashSet;

use super::{
    field, rules, truthy, value_lines::covered_value_lines, CompressionEvidence, NodeId, Projection,
    ProjectionError, ProjectionNode, Result, TextSource, Value,
};

const TEXT_FIELDS: [&str; 3] = ["name", "description", "value"];

fn text_part(part: &TextSource) -> &str {
    part.text.as_str().expect("validated caption part")
}

impl Projection {
    fn original_path(
        &self, owner: &ProjectionNode, root: NodeId, evidence: &CompressionEvidence,
    ) -> Result<bool> {
        let mut current = root;
        let mut seen = HashSet::new();
        while seen.insert(current) {
            let Some(source) = self.node(current).filter(|node| node.scope == owner.scope) else {
                return Ok(false);
            };
            if evidence.incomplete_children.contains(&current) || evidence.contested_sources.contains(&current)
                || rules::incomplete_fields(&source.native_fields, source.native_children.len())?
            {
                return Ok(false);
            }
            if current == owner.id {
                return Ok(true);
            }
            let Some(parent) = source.parent.filter(|parent| {
                evidence.complete_children.get(parent).is_some_and(|children| children.contains(&current))
            }) else {
                return Ok(false);
            };
            current = parent;
        }
        Ok(false)
    }

    fn field_parts(&self, node: &ProjectionNode, name: &str, evidence: &CompressionEvidence) -> Option<Vec<TextSource>> {
        let (_, presented) = node.provenance.iter().find(|(key, _)| key == name)?;
        if presented.parts.is_empty() {
            return None;
        }
        let mut parts = Vec::new();
        for part in &presented.parts {
            let text = part.value.as_str()?;
            if part.facts.is_empty() {
                return None;
            }
            for fact in &part.facts {
                if !TEXT_FIELDS.contains(&fact.attribute.as_str()) || !evidence.complete_text.contains(fact)
                    || self.node(fact.source).and_then(|source| field(&source.native_fields, &fact.attribute))
                        .and_then(Value::as_str) != Some(text)
                {
                    return None;
                }
                parts.push(TextSource { fact: fact.clone(), field: name.to_owned(), text: part.value.clone() });
            }
        }
        Some(parts)
    }

    fn owned_text(&self, node: &ProjectionNode, evidence: &CompressionEvidence) -> Option<Vec<TextSource>> {
        let mut result = Vec::new();
        let mut values = HashSet::new();
        for name in TEXT_FIELDS {
            let value = node.field(name);
            if rules::empty_content(value) {
                continue;
            }
            let text = value?.as_str()?;
            if values.insert(text) {
                result.extend(self.field_parts(node, name, evidence)?);
            }
        }
        Some(result)
    }

    fn branch_caption(
        &self, owner: &ProjectionNode, root: NodeId, evidence: &CompressionEvidence,
        visual_labels: &HashSet<String>, nodes: &mut Vec<NodeId>,
    ) -> Result<Option<Vec<TextSource>>> {
        enum Visit {
            Enter(NodeId),
            Leave { own: Vec<TextSource>, descendants_start: usize },
        }
        let mut pending = vec![Visit::Enter(root)];
        let mut parts = Vec::new();
        while let Some(visit) = pending.pop() {
            match visit {
                Visit::Enter(root) => {
                    let node = self.display_node(root)?;
                    let role = rules::role(node);
                    if visual_labels.contains(&node.reference) || !matches!(role, "group" | "staticText" | "image")
                        || self.keep(root)
                        || rules::display_boundaries(node, evidence, self.options.focused, false)? != 0
                        || !rules::same_blocking(node, owner) || !self.original_path(owner, root, evidence)?
                        || rules::incomplete(node)?
                        || rules::truth(node, "valueIsNumber") || rules::truth(node, "incompleteLabelRelation")
                        || node.field("labelTargets").and_then(Value::as_seq).is_some_and(|targets| {
                            targets.iter().any(|target| target.as_str() != Some(owner.reference.as_str()))
                        })
                        || (rules::truth(node, "url") && role != "image")
                        || ["documentText", "controlValue", "rangeValue"].iter().any(|name| node.field(name).is_some())
                    {
                        return Ok(None);
                    }
                    let Some(own) = self.owned_text(node, evidence) else { return Ok(None) };
                    nodes.push(root);
                    pending.push(Visit::Leave { own, descendants_start: parts.len() });
                    pending.extend(node.children.iter().rev().copied().map(Visit::Enter));
                }
                Visit::Leave { own, descendants_start } => {
                    if parts.len() == descendants_start {
                        parts.extend(own);
                    }
                }
            }
        }
        Ok(Some(parts))
    }

    fn captions(&self, owner: &ProjectionNode, evidence: &CompressionEvidence) -> (HashSet<String>, HashSet<String>) {
        let mut whole = HashSet::new();
        let mut represented = HashSet::new();
        for name in TEXT_FIELDS {
            if name == "value" && (rules::truth(owner, "valueIsNumber")
                || matches!(rules::role(owner), "checkBox" | "switch" | "radioButton"))
            {
                continue;
            }
            let Some(value) = owner.field(name).and_then(Value::as_str).filter(|text| !text.is_empty()) else { continue };
            if let Some(parts) = self.field_parts(owner, name, evidence) {
                whole.insert(value.to_owned());
                represented.insert(value.to_owned());
                represented.extend(parts.iter().map(|part| text_part(part).to_owned()));
            }
        }
        (whole, represented)
    }

    pub(crate) fn merge_owned_passive_content(&mut self, evidence: &CompressionEvidence) -> Result<()> {
        if !self.options.compact || !self.options.native_facts || self.options.subtree.is_some() {
            return Ok(());
        }
        let visual_labels: HashSet<_> = self.display_order.iter().filter_map(|&id| {
            self.node(id)?.field("visualLabelRef")?.as_str().map(str::to_owned)
        }).collect();
        let mut pending: Vec<_> = self.roots.iter().rev().copied().collect();
        while let Some(id) = pending.pop() {
            let owner = self.display_node(id)?;
            pending.extend(owner.children.iter().rev().copied());
            if self.folded_into.iter().any(|(source, _)| *source == id) || !rules::ordinary_control(owner)
                || owner.children.is_empty() || matches!(rules::role(owner), "row" | "cell")
                || rules::incomplete(owner)? || evidence.incomplete_content.contains(&id)
            {
                continue;
            }
            let mut candidates = Vec::new();
            let mut ordered = Vec::new();
            let mut seen = HashSet::new();
            for &child in &owner.children {
                let mut nodes = Vec::new();
                let branch = self.branch_caption(owner, child, evidence, &visual_labels, &mut nodes)?;
                if let Some(parts) = branch {
                    for part in parts {
                        if seen.insert(part.fact.clone()) {
                            ordered.push(part);
                        }
                    }
                    candidates.push((child, Some(nodes)));
                } else {
                    candidates.push((child, None));
                }
            }
            let (mut whole, mut represented) = self.captions(owner, evidence);
            let values: Vec<_> = ordered.iter().map(text_part).collect();
            if !values.is_empty() && [" ", ", ", ""].iter().any(|separator| whole.contains(&values.join(separator))) {
                represented.extend(values.iter().map(|value| value.to_string()));
            }
            let mut whole_represented = represented.clone();
            let uncovered: Vec<_> = ordered.iter().filter(|part| !represented.contains(text_part(part))).cloned().collect();
            let lines = covered_value_lines(self, owner, &uncovered, evidence)?.unwrap_or_default();
            let mut line_sources = HashSet::new();
            for line in &lines {
                let source = line.get("sourceRef").and_then(Value::as_str).and_then(|ref_| self.by_reference(ref_))
                    .ok_or(ProjectionError::Invalid("unknown value-line source"))?;
                let text = line.get("attribute").and_then(Value::as_str).and_then(|key| field(&source.native_fields, key))
                    .and_then(Value::as_str).ok_or(ProjectionError::Invalid("unknown value-line field"))?;
                line_sources.insert(source.id);
                represented.insert(text.to_owned());
            }
            let derived = TEXT_FIELDS.iter().all(|name| rules::empty_content(owner.field(name)));
            if derived {
                let distinct: HashSet<_> = values.iter().copied().collect();
                if values.is_empty() || values.len() != distinct.len() || candidates.iter().any(|(_, branch)| branch.is_none()) {
                    continue;
                }
                let proposed = values.join(" ");
                let mut allowed = true;
                for node in candidates.iter().flat_map(|(_, branch)| branch.iter().flatten()) {
                    let node = self.display_node(*node)?;
                    allowed &= TEXT_FIELDS.iter().all(|name| {
                        let value = node.field(name);
                        rules::empty_content(value) || value.and_then(Value::as_str).is_some_and(|text| {
                            text == proposed || distinct.contains(text)
                        })
                    });
                }
                if !allowed {
                    continue;
                }
                self.adopt(id, &ordered, "name", " ", false)?;
                (whole, represented) = self.captions(self.display_node(id)?, evidence);
                whole_represented = represented.clone();
            }
            if whole.is_empty() {
                continue;
            }
            let mut covered = Vec::new();
            for (_, branch) in &candidates {
                let mut accepted = branch.is_some();
                for id in branch.iter().flatten() {
                    let node = self.display_node(*id)?;
                    accepted &= TEXT_FIELDS.iter().all(|name| {
                        let value = node.field(name);
                        rules::empty_content(value) || value.and_then(Value::as_str).is_some_and(|text| {
                            whole_represented.contains(text) || (line_sources.contains(id) && represented.contains(text))
                        })
                    });
                }
                covered.push(accepted);
            }
            let complete = covered.iter().all(|accepted| *accepted);
            let mut retained = Vec::new();
            let mut folded = Vec::new();
            let mut positions = Vec::new();
            for ((child, branch), accepted) in candidates.into_iter().zip(covered) {
                let Some(branch) = branch.filter(|_| accepted) else {
                    retained.push(child);
                    continue;
                };
                if !complete && branch.iter().any(|&id| self.node(id).is_some_and(|node| rules::truth(node, "spatialPosition"))) {
                    retained.push(child);
                    continue;
                }
                for source in branch {
                    let node = self.display_node(source)?;
                    let reference = Value::Text(node.reference.clone());
                    if let Some(position) = node.field("spatialPosition").filter(|value| truthy(value)) {
                        positions.push(Value::Map(vec![
                            ("sourceRef".to_owned(), reference.clone()),
                            ("position".to_owned(), position.clone()),
                        ]));
                    }
                    self.fold(source, id, "merge_owned_passive_content", "owned-content")?;
                    folded.push(reference);
                }
            }
            if folded.is_empty() {
                continue;
            }
            let covered_lines: Vec<_> = lines.into_iter().filter(|line| {
                line.get("sourceRef").is_some_and(|reference| folded.contains(reference))
            }).collect();
            let owner = self.node_mut(id)?;
            owner.children = retained;
            let mut event = vec![
                ("sourceRef".to_owned(), Value::Text(owner.reference.clone())),
                ("kind".to_owned(), Value::Text("owned-passive-content".to_owned())),
                ("members".to_owned(), Value::Seq(folded)),
                ("derivedCaption".to_owned(), Value::Bool(derived)),
            ];
            if !covered_lines.is_empty() {
                event.push(("valueLines".to_owned(), Value::Seq(covered_lines)));
            }
            if owner.children.is_empty() && !positions.is_empty() {
                event.push(("internalPositions".to_owned(), Value::Seq(positions)));
            }
            self.changes.push(Value::Map(event));
        }
        Ok(())
    }
}
