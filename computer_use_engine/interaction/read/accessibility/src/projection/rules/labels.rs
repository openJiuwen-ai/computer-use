// Copyright (c) Huawei Technologies Co., Ltd. 2026. All rights reserved.

use super::{put, role, short_value, text_sources, truth, HashMap, HashSet, NodeId, Projection, ProjectionError, Result, Value};

fn add(targets: &mut Vec<(String, Vec<String>)>, label: &str, target: &str) {
    if let Some((_, items)) = targets.iter_mut().find(|(key, _)| key == label) {
        if !items.iter().any(|item| item == target) {
            items.push(target.to_owned());
        }
    } else {
        targets.push((label.to_owned(), vec![target.to_owned()]));
    }
}

impl Projection {
    /// Associate native label edges in source order before local compression.
    pub(crate) fn associate_labels(&mut self) -> Result<()> {
        let order = self.display_order.clone();
        let mut targets = Vec::new();
        for &id in &order {
            let node = self.display_node(id)?;
            if role(node) == "staticText" {
                if let Some(values) = node.field("labelTargets") {
                    let refs = values.as_seq().ok_or(ProjectionError::Invalid("label targets must be ordered"))?;
                    for target in refs {
                        let target = target.as_str().ok_or(ProjectionError::Invalid("label target must be text"))?;
                        add(&mut targets, &node.reference, target);
                    }
                }
            }
            if let Some(title) = node.field("titleRef").and_then(Value::as_str) {
                if self.by_reference(title).is_some_and(|title| !title.fields.is_empty() && role(title) == "staticText") {
                    add(&mut targets, title, &node.reference);
                }
            }
        }
        let mut labels: HashMap<NodeId, Vec<NodeId>> = HashMap::new();
        for (label, ids) in &targets {
            let Some(source) = self.by_reference(label).filter(|node| !node.fields.is_empty()) else { continue };
            let source_id = source.id;
            let source_scope = source.scope;
            let refs = Value::Seq(ids.iter().cloned().map(Value::Text).collect());
            put(&mut self.node_mut(source_id)?.fields, "labelTargets", refs);
            for target in ids {
                let Some(node) = self.by_reference(target).filter(|node| !node.fields.is_empty()) else { continue };
                if node.id != source_id && node.scope == source_scope
                    && !matches!(role(node), "staticText" | "application" | "window" | "sheet" | "menuBar")
                {
                    labels.entry(node.id).or_default().push(source_id);
                }
            }
        }
        self.targets = targets;
        let mut consumed = HashSet::new();
        let mut additions: HashMap<NodeId, Vec<NodeId>> = HashMap::new();
        for id in order {
            let Some(linked) = labels.get(&id) else { continue };
            let target = self.display_node(id)?;
            let primary = target.field("titleRef").and_then(Value::as_str)
                .and_then(|title| self.by_reference(title)).map(|title| title.id)
                .filter(|title| linked.contains(title)).unwrap_or(linked[0]);
            let primary = self.display_node(primary)?;
            if !truth(target, "name") && short_value(primary)? {
                self.adopt(id, &text_sources(primary), "name", " ", false)?;
            }
            for &label in linked {
                let node = self.display_node(label)?;
                if short_value(node)? {
                    let target = self.display_node(id)?;
                    let parts: Vec<_> = text_sources(node).into_iter().filter(|part| {
                        !target.text_sources.iter().any(|known| known.text == part.text)
                    }).collect();
                    if !parts.is_empty() {
                        self.adopt(id, &parts, "description", " ", false)?;
                    }
                }
            }
            put(&mut self.node_mut(id)?.fields, "linkedLabel", Value::Bool(true));
            for &label in linked {
                let source = self.display_node(label)?;
                let target = self.display_node(id)?;
                let exclusive = self.targets.iter().find(|(reference, _)| reference == &source.reference)
                    .is_some_and(|(_, ids)| ids.len() == 1);
                let parts = text_sources(source);
                if exclusive && !self.ancestor_of(label, id) && self.passive_caption(source, target, &parts)?
                    && parts.iter().all(|part| target.text_sources.iter().any(|known| known.text == part.text))
                    && source.children.iter().all(|&child| {
                        child != id && self.node(child).is_some_and(|node| role(node) == "link")
                    })
                {
                    consumed.insert(label);
                    additions.entry(id).or_default().extend_from_slice(&source.children);
                    self.fold(label, id, "associate_labels", "label")?;
                }
            }
        }
        let mut pending = vec![self.root];
        let mut seen = HashSet::new();
        while let Some(id) = pending.pop() {
            if !seen.insert(id) {
                return Err(ProjectionError::Invalid("label arrangement produced repeated containment"));
            }
            let mut children: Vec<_> = self.display_node(id)?.children.iter().copied()
                .filter(|child| !consumed.contains(child)).collect();
            children.extend(additions.remove(&id).unwrap_or_default());
            pending.extend(children.iter().rev().copied());
            self.node_mut(id)?.children = children;
        }
        Ok(())
    }
}
