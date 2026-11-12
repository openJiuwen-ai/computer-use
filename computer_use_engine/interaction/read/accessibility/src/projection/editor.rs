// Copyright (c) Huawei Technologies Co., Ltd. 2026. All rights reserved.

use std::collections::{HashMap, HashSet};

use super::{
    field, put, text, Elision, ElisionKind, FactKey, NodeId, PresentedValue, Projection, ProjectionError, Result,
    TextSource, Value, ValuePart, DISPLAY_FIELDS,
};

impl Projection {
    pub(super) fn display_node(&self, id: NodeId) -> Result<&super::ProjectionNode> {
        self.node(id).filter(|node| !node.fields.is_empty())
            .ok_or(ProjectionError::Invalid("unknown or unavailable display source"))
    }

    pub(super) fn admit_node(&self, id: NodeId) -> Result<()> {
        self.display_node(id).map(|_| ())
    }

    fn set_presented(&mut self, id: NodeId, name: &str, value: Value, provenance: PresentedValue) -> Result<()> {
        self.admit_node(id)?;
        if !DISPLAY_FIELDS.contains(&name) {
            return Err(ProjectionError::Invalid("not an editable presentation field"));
        }
        let node = self.node_mut(id)?;
        if let Some((_, current)) = node.fields.iter_mut().find(|(key, _)| key == name) {
            *current = value;
        } else {
            // Source fields retain their insertion order, followed by the
            // editor's new fields and then its independently added annotations.
            let added_fields = node.fields[node.source_display_size..].iter()
                .take_while(|(key, _)| DISPLAY_FIELDS.contains(&key.as_str())).count();
            node.fields.insert(node.source_display_size + added_fields, (name.to_owned(), value));
        }
        if let Some((_, current)) = node.provenance.iter_mut().find(|(key, _)| key == name) {
            *current = provenance;
        } else {
            node.provenance.push((name.to_owned(), provenance));
        }
        Ok(())
    }

    pub(super) fn suppress(&mut self, id: NodeId, name: &str) -> Result<()> {
        self.set_presented(
            id,
            name,
            Value::Text(String::new()),
            PresentedValue { parts: Vec::new(), separator: Some(String::new()) },
        )
    }

    pub(super) fn copy_field(&mut self, target: NodeId, source: NodeId, name: &str) -> Result<()> {
        let displayed = self.display_node(source)?.field(name);
        let original = self.node(source).and_then(|node| field(&node.native_fields, name));
        if displayed != original {
            return Err(ProjectionError::Invalid("presentation value has no matching source field"));
        }
        let value = original.cloned().ok_or(ProjectionError::Invalid("presentation source has no field"))?;
        self.set_presented(target, name, value.clone(), PresentedValue::original(&value, source, name))
    }

    pub(super) fn text_source(&mut self, id: NodeId, part: TextSource) -> Result<()> {
        let original = self.node(part.fact.source).and_then(|node| field(&node.native_fields, &part.fact.attribute));
        if original != Some(&part.text) {
            return Err(ProjectionError::Invalid("projection caption has no matching source field"));
        }
        self.node_mut(id)?.text_sources.push(part);
        Ok(())
    }

    pub(super) fn adopt(
        &mut self,
        id: NodeId,
        parts: &[TextSource],
        name: &str,
        separator: &str,
        preserve_occurrences: bool,
    ) -> Result<()> {
        self.admit_node(id)?;
        if !DISPLAY_FIELDS.contains(&name) {
            return Err(ProjectionError::Invalid("not an editable presentation field"));
        }
        let original = self.node(id).expect("admitted display source");
        for part in parts {
            let duplicate = original.text_sources.iter().any(|existing| {
                existing.fact == part.fact && existing.field == name && existing.text == part.text
            });
            if !duplicate {
                let source = self.node(part.fact.source)
                    .and_then(|node| field(&node.native_fields, &part.fact.attribute));
                if source != Some(&part.text) {
                    return Err(ProjectionError::Invalid("projection caption has no matching source field"));
                }
            }
        }
        let node = self.node_mut(id)?;
        for part in parts {
            if !node.text_sources.iter().any(|existing| {
                existing.fact == part.fact && existing.field == name && existing.text == part.text
            }) {
                let mut part = part.clone();
                part.field = name.to_owned();
                node.text_sources.push(part);
            }
            if !node.source_refs.contains(&part.fact.source) {
                node.source_refs.push(part.fact.source);
            }
        }
        let sources = if preserve_occurrences {
            parts.iter().map(|part| ValuePart { value: part.text.clone(), facts: vec![part.fact.clone()] }).collect()
        } else {
            let mut grouped: Vec<ValuePart> = Vec::new();
            for part in node.text_sources.iter().filter(|part| part.field == name) {
                if let Some(existing) = grouped.iter_mut().find(|existing| existing.value == part.text) {
                    existing.facts.push(part.fact.clone());
                } else {
                    grouped.push(ValuePart { value: part.text.clone(), facts: vec![part.fact.clone()] });
                }
            }
            grouped
        };
        let values = sources.iter().map(|part| {
            text(&part.value).ok_or(ProjectionError::Invalid("presentation text composition requires string values"))
        }).collect::<Result<Vec<_>>>()?;
        let value = Value::Text(values.join(separator));
        if let Some(index) = self.active_stage {
            let current = self.display_node(id)?;
            if current.field(name) != Some(&value) {
                let reference = current.reference.clone();
                self.stages[index].affected_refs.insert(reference);
            }
        }
        self.set_presented(id, name, value, PresentedValue { parts: sources, separator: Some(separator.to_owned()) })
    }

    pub(super) fn elide(
        &mut self,
        source: NodeId,
        kind: ElisionKind,
        destinations: Vec<NodeId>,
        rule: &'static str,
        event: &str,
        target: Option<NodeId>,
    ) -> Result<()> {
        self.admit_node(source)?;
        if (kind == ElisionKind::Fold && destinations.len() != 1)
            || (kind == ElisionKind::Empty && !destinations.is_empty())
        {
            return Err(ProjectionError::Invalid("invalid presentation elision destinations"));
        }
        for &destination in &destinations {
            self.admit_node(destination)?;
        }
        let mut change = self.source_change(source, event)?;
        if let Some(target) = target {
            let target = self.node(target).ok_or(ProjectionError::Invalid("unknown presentation target"))?;
            put(&mut change, "targetRef", Value::Text(target.reference.clone()));
        }
        let next = Elision {
            source,
            kind,
            destinations,
            rule,
            evidence: vec![FactKey { source, attribute: "children".to_owned() }],
        };
        if let Some(old) = self.elisions.iter_mut().find(|old| old.source == source) {
            *old = next;
        } else {
            self.elisions.push(next);
        }
        self.changes.push(Value::Map(change));
        Ok(())
    }

    fn source_change(&self, id: NodeId, kind: &str) -> Result<super::Fields> {
        let source = self.node(id).ok_or(ProjectionError::Invalid("unknown presentation source"))?;
        Ok(vec![
            ("sourceRef".to_owned(), Value::Text(source.reference.clone())),
            ("kind".to_owned(), Value::Text(kind.to_owned())),
        ])
    }

    pub(super) fn fold(&mut self, source: NodeId, owner: NodeId, rule: &'static str, event: &str) -> Result<()> {
        self.admit_node(source)?;
        self.admit_node(owner)?;
        let source_node = self.node(source).expect("admitted display source");
        let mut sources = source_node.source_refs.clone();
        sources.insert(0, source);
        let actions = if event == "label" {
            source_node.field("actions").and_then(Value::as_seq).filter(|actions| !actions.is_empty()).map(|actions| {
                actions.iter().map(|action| {
                    action.get("name").cloned().ok_or(ProjectionError::Invalid("semantic action has no name"))
                }).collect::<Result<Vec<_>>>()
            }).transpose()?
        } else {
            None
        };
        if let Some((_, current)) = self.folded_into.iter_mut().find(|(id, _)| *id == source) {
            *current = owner;
        } else {
            self.folded_into.push((source, owner));
        }
        let target = self.node_mut(owner)?;
        for id in sources {
            if !target.source_refs.contains(&id) {
                target.source_refs.push(id);
            }
        }
        self.elide(source, ElisionKind::Fold, vec![owner], rule, event, Some(owner))?;
        if let Some(actions) = actions {
            let mut change = self.source_change(source, "actions")?;
            put(&mut change, "actions", Value::Seq(actions));
            self.changes.push(Value::Map(change));
        }
        Ok(())
    }

    pub(super) fn finalize_folds(&mut self) -> Result<()> {
        let mut folds: HashMap<_, _> = self.folded_into.iter().copied().collect();
        for (source, owner) in &mut self.folded_into {
            let mut seen = HashSet::from([*source]);
            let mut target = *owner;
            while let Some(next) = folds.get(&target) {
                if !seen.insert(target) {
                    return Err(ProjectionError::Invalid("projection fold contains a cycle"));
                }
                target = *next;
            }
            *owner = target;
            folds.insert(*source, target);
        }
        let elisions = self.elisions.iter().map(|elision| (elision.source, elision)).collect();
        let mut resolved = HashMap::new();
        let mut result = Vec::new();
        let mut active = HashSet::new();
        for elision in &self.elisions {
            resolve(elision.source, &elisions, &mut resolved, &mut result, &mut active)?;
        }
        self.resolved_elisions = result;
        Ok(())
    }
}

fn resolve(
    source: NodeId,
    elisions: &HashMap<NodeId, &Elision>,
    resolved: &mut HashMap<NodeId, Vec<NodeId>>,
    order: &mut Vec<(NodeId, Vec<NodeId>)>,
    active: &mut HashSet<NodeId>,
) -> Result<()> {
    if resolved.contains_key(&source) || !elisions.contains_key(&source) {
        return Ok(());
    }
    active.insert(source);
    let mut pending = vec![(source, 0usize, Vec::new())];
    while let Some((source, position, targets)) = pending.last_mut() {
        let elision = elisions[source];
        if let Some(&child) = elision.destinations.get(*position) {
            if let Some(children) = resolved.get(&child) {
                for &target in children {
                    if !targets.contains(&target) {
                        targets.push(target);
                    }
                }
            } else if elisions.contains_key(&child) {
                if !active.insert(child) {
                    return Err(ProjectionError::Invalid("projection elision contains a cycle"));
                }
                pending.push((child, 0, Vec::new()));
                continue;
            } else if !targets.contains(&child) {
                targets.push(child);
            }
            *position += 1;
        } else {
            let (source, _, targets) = pending.pop().expect("current elision frame");
            active.remove(&source);
            resolved.insert(source, targets.clone());
            order.push((source, targets));
        }
    }
    Ok(())
}
