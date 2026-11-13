// Copyright (c) Huawei Technologies Co., Ltd. 2026. All rights reserved.

use std::collections::HashSet;

use super::{
    field, rules, CompressionEvidence, FactKey, FieldDeclaration, NodeId, PresentedValue, Projection,
    ProjectionError, ProjectionNode, Result, Value,
};

const DESCRIPTIONS: [(&str, &str); 2] = [
    ("valueDescription", "Value description"),
    ("rangeDescription", "Range description"),
];

fn aliases_length(projection: &Projection, facts: &[FactKey]) -> Result<usize> {
    let mut values = facts.iter().map(|fact| {
        projection.display_node(fact.source)?.reference.parse::<usize>()
            .map_err(|_| ProjectionError::Invalid("display alias must be numeric"))
    }).collect::<Result<Vec<_>>>()?;
    values.sort_unstable();
    values.dedup();
    let mut index = 0;
    let mut length = 0;
    while index < values.len() {
        let mut end = index + 1;
        while end < values.len() && values[end - 1].checked_add(1) == Some(values[end]) {
            end += 1;
        }
        if length > 0 {
            length += 2;
        }
        length += values[index].to_string().len();
        if end - index == 2 {
            length += 2 + values[end - 1].to_string().len();
        } else if end - index > 2 {
            length += 1 + values[end - 1].to_string().len();
        }
        index = end;
    }
    Ok(length)
}

fn caption_sources(node: &ProjectionNode) -> Option<(&str, HashSet<FactKey>)> {
    let name = node.field("name")?.as_str()?;
    let (_, value) = node.provenance.iter().find(|(name, _)| name == "name")?;
    let [part] = value.parts.as_slice() else { return None };
    (!name.is_empty() && part.value.as_str() == Some(name))
        .then(|| (name, part.facts.iter().cloned().collect()))
}

impl Projection {
    pub(crate) fn share_empty_descriptions(&mut self, evidence: &CompressionEvidence) -> Result<()> {
        if !self.options.compact || !self.options.share_descriptions || self.options.subtree.is_some() {
            return Ok(());
        }
        let mut groups: Vec<(NodeId, &str, &str, Vec<FactKey>)> = Vec::new();
        for &id in &self.display_order {
            let node = self.display_node(id)?;
            let scope = self.display_node(node.scope)?;
            if !matches!(rules::role(scope), "window" | "sheet" | "menuBar") {
                continue;
            }
            for (attribute, label) in DESCRIPTIONS {
                let fact = FactKey { source: id, attribute: attribute.to_owned() };
                if node.field(attribute).and_then(Value::as_str) != Some("") || !evidence.complete_descriptions.contains(&fact) {
                    continue;
                }
                if let Some((_, _, _, facts)) = groups.iter_mut().find(|(id, key, _, _)| *id == node.scope && *key == attribute) {
                    facts.push(fact);
                } else {
                    groups.push((node.scope, attribute, label, vec![fact]));
                }
            }
        }
        for (scope, attribute, label, facts) in groups {
            if facts.len() < 2 || label.len() + 16 + aliases_length(self, &facts)? >= facts.len() * (label.len() + 4) {
                continue;
            }
            let scope_ref = self.display_node(scope)?.reference.clone();
            for fact in &facts {
                let source = self.node_mut(fact.source)?;
                if field(&source.native_fields, attribute).and_then(Value::as_str) != Some("")
                    || source.shared_description_destinations.iter().any(|(key, _)| key == attribute)
                {
                    return Err(ProjectionError::Invalid("shared description has no unique matching scoped source"));
                }
                source.shared_description_destinations.push((attribute.to_owned(), scope));
                let source_ref = source.reference.clone();
                self.changes.push(Value::Map(vec![
                    ("sourceRef".to_owned(), Value::Text(source_ref)),
                    ("kind".to_owned(), Value::Text("shared-description".to_owned())),
                    ("targetRef".to_owned(), Value::Text(scope_ref.clone())),
                    ("field".to_owned(), Value::Text(attribute.to_owned())),
                ]));
            }
            self.node_mut(scope)?.shared_descriptions.push(FieldDeclaration {
                attribute: attribute.to_owned(), value: String::new(), facts,
            });
        }
        Ok(())
    }

    pub(crate) fn deduplicate_row_captions(&mut self, evidence: &CompressionEvidence) -> Result<()> {
        if !self.options.compact || self.options.subtree.is_some() {
            return Ok(());
        }
        let mut pending = self.roots.clone();
        while let Some(id) = pending.pop() {
            let row = self.display_node(id)?;
            pending.extend_from_slice(&row.children);
            if rules::role(row) != "row" || field(&row.native_fields, "name").is_some_and(|value| !matches!(value, Value::Null)) {
                continue;
            }
            let ([cell], [native_child]) = (row.children.as_slice(), row.native_children.as_slice()) else { continue };
            if cell != native_child || rules::incomplete(row)? || rules::incomplete_fields(&row.native_fields, 1)? {
                continue;
            }
            let cell = self.display_node(*cell)?;
            if rules::role(cell) != "cell" || cell.parent != Some(id) || row.scope != cell.scope
                || rules::incomplete(cell)? || row.field("name") != cell.field("name")
            {
                continue;
            }
            let Some((caption, sources)) = caption_sources(row) else { continue };
            if sources.is_empty() || caption_sources(cell) != Some((caption, sources.clone())) {
                continue;
            }
            if sources.iter().any(|fact| fact.source == id || !evidence.complete_text.contains(fact)
                || self.node(fact.source).is_none_or(|source| source.scope != row.scope))
            {
                continue;
            }
            let mut sources: Vec<_> = sources.into_iter().map(|fact| {
                let reference = self.node(fact.source).expect("proved caption source").reference.clone();
                (reference, fact.attribute)
            }).collect();
            sources.sort_unstable();
            let caption = Value::Text(caption.to_owned());
            let source_values = sources.into_iter().map(|(reference, attribute)| Value::Map(vec![
                ("sourceRef".to_owned(), Value::Text(reference)),
                ("attribute".to_owned(), Value::Text(attribute)),
                ("value".to_owned(), caption.clone()),
            ])).collect();
            let change = Value::Map(vec![
                ("sourceRef".to_owned(), Value::Text(row.reference.clone())),
                ("kind".to_owned(), Value::Text("repeatedDerivedName".to_owned())),
                ("targetRef".to_owned(), Value::Text(cell.reference.clone())),
                ("field".to_owned(), Value::Text("name".to_owned())),
                ("sources".to_owned(), Value::Seq(source_values)),
            ]);
            self.suppress(id, "name")?;
            self.changes.push(change);
        }
        Ok(())
    }

    pub(crate) fn finalize_sources(&mut self) -> Result<()> {
        let visible = self.visible_order()?;
        for &id in &visible {
            let node = self.display_node(id)?;
            let actions = omitted_actions(node)?;
            if !actions.is_empty() {
                self.changes.push(Value::Map(vec![
                    ("sourceRef".to_owned(), Value::Text(node.reference.clone())),
                    ("kind".to_owned(), Value::Text("actions".to_owned())),
                    ("actions".to_owned(), Value::Seq(actions)),
                ]));
            }
        }
        self.finalize_folds()?;
        let visible: HashSet<_> = visible.into_iter().collect();
        let mut groups: Vec<(NodeId, FieldDeclaration)> = Vec::new();
        for &id in &self.display_order {
            let node = self.display_node(id)?;
            if visible.contains(&id) || node.shared_description_destinations.is_empty() {
                continue;
            }
            let attribute = if field(&node.native_fields, "roleDescription").is_some_and(super::truthy) { "roleDescription" } else { "role" };
            let Some(value) = field(&node.native_fields, attribute).and_then(Value::as_str) else { continue };
            let fact = FactKey { source: id, attribute: attribute.to_owned() };
            if let Some((_, group)) = groups.iter_mut().find(|(scope, group)| {
                *scope == node.scope && group.attribute == attribute && group.value == value
            }) {
                group.facts.push(fact);
            } else {
                groups.push((node.scope, FieldDeclaration {
                    attribute: attribute.to_owned(), value: value.to_owned(), facts: vec![fact],
                }));
            }
        }
        for (scope, declaration) in groups {
            let value = Value::Text(declaration.value.clone());
            for fact in &declaration.facts {
                if !self.elisions.iter().any(|elision| elision.source == fact.source) {
                    return Err(ProjectionError::Invalid("source role has no matching folded scoped field"));
                }
                self.node_mut(fact.source)?.provenance.push((
                    fact.attribute.clone(), PresentedValue::original(&value, fact.source, &fact.attribute),
                ));
            }
            self.node_mut(scope)?.source_roles.push(declaration);
        }
        Ok(())
    }

    pub(crate) fn validate(&self) -> Result<()> {
        let visible: HashSet<_> = self.visible_order()?.into_iter().collect();
        let mut shared = HashSet::new();
        let mut roles = HashSet::new();
        for &id in &self.display_order {
            let node = self.display_node(id)?;
            let omitted_app = self.options.app_view && self.options.subtree.is_none() && id == self.root
                && rules::role(node) == "application";
            if !visible.contains(&id) && !omitted_app && !self.elisions.iter().any(|elision| elision.source == id) {
                return Err(ProjectionError::Invalid("projection removed an element without recording its source"));
            }
            if visible.contains(&id) {
                self.validate_occurrence(node, &visible)?;
            }
            for group in &node.shared_descriptions {
                if !visible.contains(&id) || group.value != "" {
                    return Err(ProjectionError::Invalid("shared description scope must remain visible"));
                }
                for fact in &group.facts {
                    let source = self.display_node(fact.source)?;
                    if !shared.insert(fact.clone()) || fact.attribute != group.attribute || source.scope != id
                        || field(&source.native_fields, &fact.attribute).and_then(Value::as_str) != Some("")
                        || !source.shared_description_destinations.contains(&(fact.attribute.clone(), id))
                    {
                        return Err(ProjectionError::Invalid("shared description source or destination mismatch"));
                    }
                }
            }
            for group in &node.source_roles {
                if !visible.contains(&id) {
                    return Err(ProjectionError::Invalid("source role scope must remain visible"));
                }
                for fact in &group.facts {
                    let source = self.display_node(fact.source)?;
                    if !roles.insert(fact.clone()) || visible.contains(&fact.source) || fact.attribute != group.attribute
                        || source.scope != id || !self.elisions.iter().any(|elision| elision.source == fact.source)
                        || field(&source.native_fields, &fact.attribute).and_then(Value::as_str) != Some(group.value.as_str())
                    {
                        return Err(ProjectionError::Invalid("source role must retain an exact folded field"));
                    }
                }
            }
        }
        for &id in &self.display_order {
            let node = self.display_node(id)?;
            for (attribute, _) in &node.shared_description_destinations {
                if !shared.contains(&FactKey { source: id, attribute: attribute.clone() }) {
                    return Err(ProjectionError::Invalid("shared field lacks a public scope declaration"));
                }
            }
        }
        for proof in &self.caption_equivalences {
            let owner = self.display_node(proof.owner)?;
            let display = owner.provenance.iter().find(|(name, _)| name == &proof.display_field).map(|(_, value)| value);
            let Some(parts) = proof.composition.parts.iter().map(|part| part.value.as_str()).collect::<Option<Vec<_>>>() else {
                return Err(ProjectionError::Invalid("native caption composition has non-text components"));
            };
            let composed = parts.join(proof.composition.separator.as_deref().unwrap_or(""));
            if !visible.contains(&proof.owner) || owner.scope != proof.scope
                || owner.field(&proof.display_field).and_then(Value::as_str) != Some(&composed)
                || !display.is_some_and(|value| matches!(
                    value.parts.as_slice(), [part] if part.facts.as_slice() == std::slice::from_ref(&proof.anchor)
                ))
            {
                return Err(ProjectionError::Invalid("native caption composition lost its visible owner"));
            }
            for part in &proof.composition.parts {
                let [fact] = part.facts.as_slice() else {
                    return Err(ProjectionError::Invalid("native caption component must retain one exact source"));
                };
                let original = self.display_node(fact.source)?;
                if visible.contains(&fact.source) || !self.folded_into.contains(&(fact.source, proof.owner))
                    || field(&original.native_fields, &fact.attribute) != Some(&part.value)
                {
                    return Err(ProjectionError::Invalid("native caption composition lost its source or destination"));
                }
            }
        }
        if let Some(detail) = &self.export_recursion {
            return Err(ProjectionError::Recursion(detail.clone()));
        }
        Ok(())
    }

    fn validate_occurrence(&self, node: &ProjectionNode, visible: &HashSet<NodeId>) -> Result<()> {
        if node.source_refs.iter().any(|&id| self.node(id).is_none()) {
            return Err(ProjectionError::Invalid("projection contains an unknown source reference"));
        }
        for part in &node.text_sources {
            let value = self.node(part.fact.source).and_then(|source| field(&source.native_fields, &part.fact.attribute));
            if value != Some(&part.text) {
                return Err(ProjectionError::Invalid("projection caption has no matching source field"));
            }
        }
        if let Some(groups) = node.field("layoutGroups").and_then(Value::as_seq) {
            if !groups.is_empty() {
                let mut members = Vec::new();
                for group in groups {
                    let row = group.get("members").and_then(Value::as_seq).filter(|row| row.len() >= 2)
                        .ok_or(ProjectionError::Invalid("layout groups need at least two members"))?;
                    if group.get("kind").and_then(Value::as_str) != Some("row") {
                        return Err(ProjectionError::Invalid("layout groups must be rows"));
                    }
                    for member in row {
                        let member = member.as_str().and_then(|reference| self.by_reference(reference))
                            .filter(|child| child.scope == node.scope)
                            .ok_or(ProjectionError::Invalid("layout groups must share source scope"))?;
                        members.push(member.id);
                    }
                }
                if members != node.children {
                    return Err(ProjectionError::Invalid("layout groups must partition scoped references"));
                }
            }
        }
        if node.field("_contextRefs").and_then(Value::as_seq).is_some_and(|refs| {
            refs.iter().any(|reference| reference.as_str().and_then(|ref_| self.by_reference(ref_)).is_none())
        }) {
            return Err(ProjectionError::Invalid("layout context has an unknown source reference"));
        }
        if let Some(label) = node.field("visualLabelRef").filter(|value| !matches!(value, Value::Null)) {
            if label.as_str().and_then(|ref_| self.by_reference(ref_)).is_none_or(|label| {
                label.id == node.id || label.scope != node.scope || !visible.contains(&label.id)
            }) {
                return Err(ProjectionError::Invalid("visual label must retain a distinct scoped reference"));
            }
        }
        if let Some(child) = node.field("visualChainChild").filter(|value| !matches!(value, Value::Null)) {
            if child.as_str().and_then(|ref_| self.by_reference(ref_)).is_none_or(|child| {
                node.children.as_slice() != [child.id] || child.scope != node.scope
            }) {
                return Err(ProjectionError::Invalid("visual chain must retain one direct scoped child"));
            }
        }
        Ok(())
    }
}

fn omitted_actions(node: &ProjectionNode) -> Result<Vec<Value>> {
    let Some(actions) = node.field("actions").and_then(Value::as_seq) else { return Ok(Vec::new()) };
    let caps = rules::capabilities(node);
    let role = rules::role(node);
    let selectable = rules::truth(node, "selectable");
    let menu_item = matches!(role, "menuItem" | "menuBarItem");
    let mut result = Vec::new();
    for action in actions {
        let kind = rules::action_kind(action)?;
        let invoke = kind == "invoke" && !selectable && if caps.is_some() {
            rules::click_available(node)
        } else {
            rules::ordinary_control(node) || menu_item
        };
        let select = kind == "select" && !selectable && caps.is_none() && menu_item;
        let show_menu = kind == "show_menu" && !selectable && rules::truth(node, "pressable")
            && matches!(role, "popUpButton" | "menuButton" | "comboBox")
            && (caps.is_none() || rules::click_available(node)
                && !actions.iter().map(rules::action_kind).collect::<Result<Vec<_>>>()?.contains(&"invoke"));
        let disclose = matches!(kind, "expand" | "collapse") && rules::truth(node, "expandable") && caps.is_none();
        if invoke || select || show_menu || disclose {
            result.push(action.get("name").expect("validated action name").clone());
        }
    }
    Ok(result)
}
