// Copyright (c) Huawei Technologies Co., Ltd. 2026. All rights reserved.

use super::{
    capabilities, disclosure, explicit_false, incomplete, layout, ordinary_control, role, same_blocking,
    short_value, text_sources, truth, NodeId, Projection, ProjectionNode, Result, TextSource, Value, TABLE_FIELDS,
};
use crate::projection::remove;

const PRESSABLE_FIELDS: &[&str] = &[
    "pressable", "selectable", "focusable", "valueExpected", "incompleteLabelRelation", "labelTargets",
];

fn control_members(node: &ProjectionNode) -> &[Value] {
    node.field("control").and_then(|value| value.get("members")).and_then(Value::as_seq).unwrap_or(&[])
}

fn member_part<'a>(node: &ProjectionNode, owner: &'a ProjectionNode) -> Option<&'a str> {
    control_members(owner).iter().find_map(|member| {
        if member.get("ref").and_then(Value::as_str) == Some(node.reference.as_str()) {
            member.get("part").and_then(Value::as_str)
        } else {
            None
        }
    })
}

fn represented(source: &[TextSource], owner: &ProjectionNode) -> bool {
    source.iter().all(|source| owner.text_sources.iter().any(|known| known.text == source.text))
}

fn optional(value: Option<&Value>) -> &Value {
    value.unwrap_or(&Value::Null)
}

impl Projection {
    pub(in crate::projection) fn ancestor_of(&self, ancestor: NodeId, child: NodeId) -> bool {
        let mut parent = self.node(child).and_then(|node| node.parent);
        while let Some(id) = parent {
            if id == ancestor {
                return true;
            }
            parent = self.node(id).and_then(|node| node.parent);
        }
        false
    }

    fn member_layout(
        &self, node: &ProjectionNode, owner: &ProjectionNode, ignore_focus: bool, ignore_selected: bool,
    ) -> Result<bool> {
        if !(truth(node, "_factBacked") && role(node) == "cell" && role(owner) == "row" && Self::member_of(node, owner)) {
            return layout(node, false, ignore_focus, ignore_selected);
        }
        for name in ["rowRange", "columnRange"] {
            if node.field(name).and_then(|value| value.get("length")).is_some_and(|value| value.as_i64() != Some(1)) {
                return Ok(false);
            }
        }
        let mut candidate = node.clone();
        for name in ["rowRange", "columnRange", "index"] {
            remove(&mut candidate.fields, name);
        }
        let title = node.field("titleRef").and_then(Value::as_str).and_then(|reference| self.by_reference(reference));
        if title.is_some_and(|title| Self::member_of(title, owner)) {
            remove(&mut candidate.fields, "titleRef");
        }
        layout(&candidate, false, ignore_focus, ignore_selected)
    }

    fn label_parts(&self, id: NodeId, owner: &ProjectionNode) -> Result<Option<Vec<TextSource>>> {
        let node = self.display_node(id)?;
        let title = node.field("titleRef").and_then(Value::as_str).and_then(|reference| self.by_reference(reference));
        if let Some(title) = title.filter(|title| {
            role(owner) == "row" && role(node) == "cell" && self.ancestor_of(id, title.id)
                && !truth(node, "incompleteLabelRelation") && !truth(node, "valueExpected")
                && node.field("value").is_none() && same_blocking(node, owner) && !truth(node, "modal")
                && !matches!(node.field("subrole").and_then(Value::as_str), Some("dialog" | "systemDialog"))
        }) {
            let targets = self.targets.iter().find(|(label, _)| label == &title.reference).map(|(_, targets)| targets);
            if !title.fields.is_empty() && !truth(title, "incompleteLabelRelation") && same_blocking(title, owner)
                && targets.is_some_and(|targets| targets.as_slice() == [node.reference.as_str()])
                && short_value(title)?
            {
                return Ok(Some(text_sources(title)));
            }
        }
        if owner.field("capabilities").is_some() && !Self::member_of(node, owner) {
            return Ok(None);
        }
        if role(node) == "staticText" && !PRESSABLE_FIELDS.iter().any(|name| truth(node, name))
            && node.children.is_empty() && node.field("partialReason").is_none() && short_value(node)?
        {
            return Ok(Some(text_sources(node)));
        }
        if role(node) == "image" && !["pressable", "selectable", "focusable", "actions", "actionsUnavailable"].iter()
            .any(|name| truth(node, name))
            && node.children.is_empty() && node.field("partialReason").is_none()
        {
            return Ok(Some(Vec::new()));
        }
        if !self.member_layout(node, owner, true, true)? {
            return Ok(None);
        }
        let mut parts = Vec::new();
        for &child in &node.children {
            let Some(child_parts) = self.label_parts(child, owner)? else { return Ok(None) };
            parts.extend(child_parts);
        }
        Ok(Some(parts))
    }

    fn compact_label(&mut self, id: NodeId, owner_id: NodeId) -> Result<Vec<NodeId>> {
        let node = self.display_node(id)?;
        let owner = self.display_node(owner_id)?;
        if owner.field("capabilities").is_some() && !Self::member_of(node, owner) {
            return Ok(vec![id]);
        }
        let parts = text_sources(node);
        if self.passive_caption(node, owner, &parts)? && !truth(node, "labelTargets")
            && node.children.is_empty() && represented(&parts, owner)
        {
            self.fold(id, owner_id, "merge_control_labels", "label")?;
            return Ok(Vec::new());
        }
        if (member_part(node, owner) != Some("layout") && self.label_parts(id, owner)?.is_none())
            || !self.member_layout(node, owner, true, true)?
            || self.keep(id)
            || !same_blocking(node, owner)
        {
            return Ok(vec![id]);
        }
        let cell = role(node) == "cell";
        let repeated_selection = cell && role(owner) == "row"
            && node.field("selected").and_then(Value::as_bool) == Some(true)
            && owner.field("selected").and_then(Value::as_bool) == Some(true);
        let internal_focus = cell && ordinary_control(owner)
            && !(Some(id) == self.options.focused && explicit_false(node, "focused"));
        let mut retained = Vec::new();
        for child in node.children.clone() {
            retained.extend(self.compact_label(child, owner_id)?);
        }
        self.node_mut(id)?.children = retained;
        let node = self.display_node(id)?;
        let owner = self.display_node(owner_id)?;
        if self.options.compact && truth(node, "_factBacked") && cell && role(owner) == "row"
            && (node.field("focused").and_then(Value::as_bool) == Some(true)
                || ["rowRange", "columnRange", "index"].iter().any(|name| node.field(name).is_some()))
        {
            return Ok(vec![id]);
        }
        if self.member_layout(node, owner, internal_focus, repeated_selection)?
            && (Some(id) != self.options.focused || internal_focus)
        {
            let replacements = node.children.clone();
            self.fold(id, owner_id, "merge_control_labels", "layout")?;
            return Ok(replacements);
        }
        Ok(vec![id])
    }

    pub(in crate::projection) fn merge_control_labels(&mut self, id: NodeId, in_table: bool) -> Result<()> {
        let node = self.display_node(id)?;
        if in_table || !ordinary_control(node) {
            return Ok(());
        }
        let children = node.children.clone();
        if !truth(node, "name") {
            let mut parts = Vec::new();
            if node.field("control").is_some() {
                for member in control_members(node) {
                    if member.get("part").and_then(Value::as_str) != Some("label") {
                        continue;
                    }
                    let source = member.get("ref").and_then(Value::as_str).and_then(|ref_| self.by_reference(ref_));
                    if let Some(source) = source.filter(|source| !source.fields.is_empty()) {
                        if short_value(source)? {
                            parts.extend(text_sources(source));
                        }
                    }
                }
            } else {
                for &child in &children {
                    if let Some(child_parts) = self.label_parts(child, node)? {
                        parts.extend(child_parts);
                    }
                }
            }
            if !parts.is_empty() {
                self.adopt(id, &parts, "name", ", ", false)?;
            }
        }
        let mut retained = Vec::new();
        for child in children {
            retained.extend(self.compact_label(child, id)?);
        }
        self.node_mut(id)?.children = retained;
        Ok(())
    }

    fn compact_input(&mut self, id: NodeId, owner_id: NodeId) -> Result<Vec<NodeId>> {
        let node = self.display_node(id)?;
        let owner = self.display_node(owner_id)?;
        let Some(part) = member_part(node, owner) else { return Ok(vec![id]) };
        let editor = part == "editor";
        if node.field("control").is_some() || self.keep(id) || !same_blocking(node, owner) {
            return Ok(vec![id]);
        }
        let mut retained = Vec::new();
        for child in node.children.clone() {
            retained.extend(self.compact_input(child, owner_id)?);
        }
        self.node_mut(id)?.children = retained;
        let node = self.display_node(id)?;
        let owner = self.display_node(owner_id)?;
        let owner_text = ["name", "description", "value"].map(|name| optional(owner.field(name)));
        let value = node.field("value");
        let owner_value = optional(owner.field("value"));
        let node_caps = capabilities(node).and_then(Value::as_map).unwrap_or(&[]);
        let owner_caps = capabilities(owner);
        let same_field = |name| optional(node.field(name)) == optional(owner.field(name));
        if ["name", "description"].iter().any(|name| {
            truth(node, name) && !owner_text.contains(&optional(node.field(name)))
        }) || value.is_some_and(|value| value != owner_value || truth(node, "valueIsNumber") != truth(owner, "valueIsNumber"))
            || (truth(node, "valueExpected") && value.is_none())
            || (truth(node, "selectable") && !truth(owner, "selectable"))
            || (truth(node, "pressable") && !truth(owner, "pressable")
                && capabilities(node).and_then(|caps| caps.get("click")) != owner_caps.and_then(|caps| caps.get("click")))
            || node_caps.iter().any(|(name, value)| value.as_str() == Some("available")
                && owner_caps.and_then(|caps| caps.get(name)).and_then(Value::as_str) != Some("available"))
            || ["actions", "actionsUnavailable", "help", "url"].iter().any(|name| truth(node, name))
            || ["valueDescription", "rangeDescription"].iter().any(|name| node.field(name).is_some()
                && !node.shared_description_destinations.iter().any(|(attribute, _)| attribute == name))
            || ["headingLevel", "landmarkType", "localizedLandmarkType", "fullDescription"].iter().any(|name| truth(node, name))
            || (truth(node, "placeholder") && !same_field("placeholder"))
            || incomplete(node)?
            || ["labelTargets", "incompleteLabelRelation", "preserveGroup", "subrole", "modal"].iter().any(|name| truth(node, name))
            || (node.field("selected").is_some() && !same_field("selected"))
            || (explicit_false(node, "enabled") && !explicit_false(owner, "enabled"))
            || truth(node, "expandable") || truth(node, "submenu") || disclosure(node)
            || ["shortcut", "menuMark", "toggleState"].iter().any(|name| node.field(name).is_some())
            || TABLE_FIELDS.iter().any(|name| node.field(name).is_some())
            || (Some(id) == self.options.focused && explicit_false(node, "focused"))
        {
            return Ok(vec![id]);
        }
        let selected = ["selectionRange", "selectedText"].map(|name| (name, node.field(name).is_some()));
        if selected.iter().any(|(_, present)| *present) {
            if owner.field("value").is_none() || !same_field("value")
                || selected.iter().any(|(name, _)| owner.field(name).is_some() && !same_field(name))
            {
                return Ok(vec![id]);
            }
            for (name, present) in selected {
                if present {
                    self.copy_field(owner_id, id, name)?;
                }
            }
        }
        let children = self.display_node(id)?.children.clone();
        self.fold(id, owner_id, "merge_editors", if editor { "editor" } else { "layout" })?;
        Ok(children)
    }

    pub(in crate::projection) fn merge_editors(&mut self, id: NodeId) -> Result<()> {
        let node = self.display_node(id)?;
        if node.field("control").and_then(|value| value.get("kind")).and_then(Value::as_str) == Some("textInput") {
            let mut retained = Vec::new();
            for child in node.children.clone() {
                retained.extend(self.compact_input(child, id)?);
            }
            self.node_mut(id)?.children = retained;
        }
        Ok(())
    }
}
