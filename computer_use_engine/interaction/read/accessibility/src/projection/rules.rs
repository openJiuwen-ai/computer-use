// Copyright (c) Huawei Technologies Co., Ltd. 2026. All rights reserved.

//! Node-local rules over the source-accounted projection.

use std::collections::{HashMap, HashSet};

use crate::geometry::Geometry;

mod members;
mod labels;

use super::{
    put, truthy, CompressionEvidence, ElisionKind, FactKey, NodeId, Projection, ProjectionError, ProjectionNode,
    Result, TextSource, Value,
};

const WRAPPERS: &[&str] = &["group", "cell", "column"];
const CONTROLS: &[&str] = &[
    "button", "checkBox", "radioButton", "switch", "textField", "textArea", "comboBox", "popUpButton", "menuButton",
    "link", "row",
];
const REGIONS: &[&str] = &[
    "window", "sheet", "group", "splitGroup", "scrollArea", "toolbar", "tabGroup", "outline", "list", "table",
    "webArea", "document",
];
const TABLE_FIELDS: &[&str] = &[
    "rowRange", "columnRange", "rowCount", "columnCount", "index", "cellPosition", "selectedChildCount", "cellRow",
    "cellColumn", "cellRowSpan", "cellColumnSpan",
];

pub(super) fn role(node: &ProjectionNode) -> &str {
    node.field("role").and_then(Value::as_str).expect("admitted node role")
}

pub(super) fn truth(node: &ProjectionNode, field: &str) -> bool {
    node.field(field).is_some_and(truthy)
}

fn explicit_false(node: &ProjectionNode, field: &str) -> bool {
    matches!(node.field(field), Some(Value::Bool(false)))
}

pub(super) fn capabilities(node: &ProjectionNode) -> Option<&Value> {
    node.field("capabilities").filter(|value| !matches!(value, Value::Null))
}

pub(super) fn click_available(node: &ProjectionNode) -> bool {
    capabilities(node).and_then(|value| value.get("click")).and_then(Value::as_str) == Some("available")
}

pub(super) fn ordinary_control(node: &ProjectionNode) -> bool {
    CONTROLS.contains(&role(node)) || truth(node, "pressable") || (truth(node, "_factBacked") && click_available(node))
}

pub(super) fn action_kind(action: &Value) -> Result<&str> {
    let name = action.get("name").ok_or(ProjectionError::Invalid("semantic action has no name"))?;
    action.get("kind").unwrap_or(name).as_str().ok_or(ProjectionError::Invalid("semantic action kind must be text"))
}

pub(super) fn independent_actions(node: &ProjectionNode) -> Result<bool> {
    if let Some(actions) = node.field("actions") {
        for action in actions.as_seq().ok_or(ProjectionError::Invalid("semantic actions must be ordered"))? {
            if !matches!(action_kind(action)?, "show_menu" | "scroll_into_view") {
                return Ok(true);
            }
        }
    }
    Ok(false)
}

pub(super) fn incomplete_fields(fields: &super::Fields, children: usize) -> Result<bool> {
    if ["partialReason", "nextChildOffset", "childrenStart", "childrenEnd"]
        .iter()
        .any(|name| super::field(fields, name).is_some())
        || super::field(fields, "childOmissions").is_some_and(truthy)
    {
        return Ok(true);
    }
    let Some(total) = super::field(fields, "childCount") else { return Ok(false) };
    let as_count = |value: &Value| match value {
        Value::Int(value) => Ok(*value),
        Value::Bool(value) => Ok(i64::from(*value)),
        _ => Err(ProjectionError::Invalid("semantic child coverage must use integer counts")),
    };
    let read = super::field(fields, "childrenRead").map(as_count).transpose()?.unwrap_or(children as i64);
    Ok(read < as_count(total)?)
}

pub(super) fn incomplete(node: &ProjectionNode) -> Result<bool> {
    incomplete_fields(&node.fields, node.children.len())
}

fn disclosure(node: &ProjectionNode) -> bool {
    let expandable = truth(node, "expandable");
    if matches!(node.field("expanded"), Some(Value::Bool(true))) || expandable || matches!(role(node), "row" | "disclosureTriangle") {
        if let Some(expanded) = node.field("expanded") {
            return !matches!(expanded, Value::Null);
        }
        if expandable || matches!(node.field("disclosing"), Some(Value::Bool(true))) {
            return node.field("disclosing").is_some_and(|value| !matches!(value, Value::Null));
        }
    }
    false
}

pub(super) fn protected_content(node: &ProjectionNode) -> Result<bool> {
    if node.field("selectionRange").is_some()
        || node.field("selectedText").is_some_and(|value| !matches!(value, Value::Null))
        || truth(node, "actionsUnavailable")
        || truth(node, "placeholder")
        || node.field("documentUrl").is_some()
        || node.field("documentReports").is_some()
        || ["readOnly", "textReadOnly", "rangeReadOnly", "unsaved", "invalid", "errorRelations"]
            .iter()
            .any(|key| node.field(key).is_some_and(|value| !matches!(value, Value::Bool(false))))
        || ["valueDescription", "rangeDescription"].iter().any(|key| {
            node.field(key).is_some()
                && !node.shared_description_destinations.iter().any(|(attribute, _)| attribute == key)
        })
        || ["documentText", "controlValue", "rangeValue", "headingLevel", "landmarkType", "localizedLandmarkType", "fullDescription"]
            .iter()
            .any(|key| truth(node, key))
        || if truth(node, "_factBacked") { independent_actions(node)? } else { truth(node, "actions") }
    {
        return Ok(true);
    }
    Ok(matches!(node.field("busy"), Some(Value::Bool(true)))
        || incomplete(node)?
        || ["shortcut", "menuMark", "toggleState", "disclosureLevel"].iter().any(|key| node.field(key).is_some())
        || truth(node, "disclosedBy")
        || truth(node, "incompleteDisclosureRelation")
        || TABLE_FIELDS.iter().any(|key| node.field(key).is_some()))
}

fn carries_value(node: &ProjectionNode) -> bool {
    truth(node, "valueExpected")
        || matches!(role(node), "textField" | "textArea" | "comboBox" | "slider" | "checkBox" | "switch" | "radioButton" | "progressIndicator" | "incrementor")
        || (role(node) == "cell" && truth(node, "name"))
}

fn independent(node: &ProjectionNode, ignore_preserve_group: bool) -> Result<bool> {
    let role = role(node);
    let fact_backed = truth(node, "_factBacked");
    let caps = capabilities(node).and_then(Value::as_map).unwrap_or(&[]);
    let available = caps.iter().any(|(key, value)| {
        value.as_str() == Some("available") && !(fact_backed && WRAPPERS.contains(&role) && key == "focus")
    });
    Ok(available
        || (truth(node, "focusable") && !WRAPPERS.contains(&role))
        || truth(node, "labelTargets")
        || truth(node, "incompleteLabelRelation")
        || (!ignore_preserve_group && truth(node, "preserveGroup"))
        || (truth(node, "subrole") && node.field("subrole").and_then(Value::as_str) != Some("hostingView"))
        || node.field("control").is_some()
        || truth(node, "titleRef")
        || carries_value(node)
        || truth(node, "help")
        || truth(node, "url")
        || (truth(node, "identifier") && !truth(node, "name") && !truth(node, "description") && !truth(node, "value") && node.children.is_empty())
        || protected_content(node)?
        || role == "sheet"
        || matches!(node.field("subrole").and_then(Value::as_str), Some("dialog" | "systemDialog"))
        || truth(node, "modal"))
}

fn cap(node: &ProjectionNode, name: &str) -> bool {
    capabilities(node).and_then(|value| value.get(name)).and_then(Value::as_str) == Some("available")
}

fn has_display_value(node: &ProjectionNode) -> bool {
    carries_value(node) || node.field("value").is_some_and(|value| {
        value.as_str() != Some("")
            || !matches!(
                role(node),
                "application" | "window" | "sheet" | "group" | "splitGroup" | "column" | "scrollArea" | "toolbar"
                    | "tabGroup" | "outline" | "list" | "button" | "image" | "closeButton" | "zoomButton" | "minimizeButton"
            )
            || cap(node, "typeText")
            || cap(node, "replaceText")
            || truth(node, "focused")
            || node.field("selectionRange").is_some()
            || truth(node, "selectedText")
    })
}

fn layout(node: &ProjectionNode, ignore_preserve_group: bool, ignore_focus: bool, ignore_selected: bool) -> Result<bool> {
    Ok(WRAPPERS.contains(&role(node))
        && !truth(node, "name")
        && !truth(node, "description")
        && !has_display_value(node)
        && !independent(node, ignore_preserve_group)?
        && !truth(node, "pressable")
        && !truth(node, "selectable")
        && !explicit_false(node, "enabled")
        && (ignore_selected || !truth(node, "selected"))
        && (ignore_focus || !truth(node, "focused"))
        && !truth(node, "expandable")
        && !truth(node, "submenu")
        && !disclosure(node))
}

fn anonymous(node: &ProjectionNode, ignore_preserve_group: bool) -> Result<bool> {
    Ok(layout(node, ignore_preserve_group, false, false)?
        && !truth(node, "identifier")
        && !explicit_false(node, "visible")
        && !explicit_false(node, "showing")
        && (!truth(node, "roleDescription")
            || role(node) == "group"
                && matches!(node.field("roleDescription").and_then(Value::as_str), Some("group" | "组"))))
}

pub(super) fn text_sources(node: &ProjectionNode) -> Vec<TextSource> {
    let mut result = Vec::new();
    for attribute in ["name", "description", "value"] {
        let Some(value) = node.field(attribute).filter(|value| truthy(value)) else { continue };
        let before = result.len();
        result.extend(node.text_sources.iter().filter(|part| part.field == attribute).cloned());
        if attribute == "value" && before == result.len() {
            result.push(TextSource {
                fact: FactKey { source: node.id, attribute: attribute.to_owned() },
                field: "name".to_owned(),
                text: value.clone(),
            });
        }
    }
    result
}

pub(super) fn same_blocking(left: &ProjectionNode, right: &ProjectionNode) -> bool {
    // Interaction annotation publishes sorted lists of canonical display aliases.
    left.field("blockedBy") == right.field("blockedBy")
}

fn short_value(node: &ProjectionNode) -> Result<bool> {
    node.field("value").map_or(Ok(true), |value| {
        value.as_str().map(|value| value.encode_utf16().count() <= 1200)
            .ok_or(ProjectionError::Invalid("passive caption value must be text"))
    })
}

pub(super) fn empty_content(value: Option<&Value>) -> bool {
    matches!(value, None | Some(Value::Null)) || value.and_then(Value::as_str) == Some("")
}

fn neutral_selection(value: &Value) -> bool {
    value.as_map().is_some_and(|values| values.len() == 2)
        && value.get("location").and_then(Value::as_i64) == Some(0)
        && value.get("length").and_then(Value::as_i64) == Some(0)
}

pub(super) const ACTION_BOUNDARY: u8 = 1 << 1;
const SCOPE_BOUNDARY: u8 = 1 << 4;

pub(super) fn display_boundaries(
    node: &ProjectionNode, evidence: &CompressionEvidence, focused: Option<NodeId>, retained_help: bool,
) -> Result<u8> {
    let input = evidence.concrete_inputs.contains(&node.id)
        || matches!(role(node), "textField" | "textArea" | "comboBox" | "document")
        || node.field("control").and_then(|value| value.get("kind")).and_then(Value::as_str) == Some("textInput")
        || truth(node, "editorOwnerRef");
    let action = ["actionsUnavailable", "pressable", "selectable"].iter().any(|key| truth(node, key))
        || (truth(node, "_factBacked") && click_available(node))
        || independent_actions(node)?
        || truth(node, "shortcut")
        || truth(node, "submenu");
    let state = focused == Some(node.id)
        || ["focused", "selected", "focusable", "offscreen", "busy", "modal"]
            .iter().any(|name| node.field(name).and_then(Value::as_bool) == Some(true))
        || ["enabled", "visible", "showing"].iter().any(|name| explicit_false(node, name))
        || ["readOnly", "textReadOnly", "rangeReadOnly", "unsaved", "invalid", "errorRelations"].iter()
            .any(|key| node.field(key).is_some_and(|value| !matches!(value, Value::Bool(false))))
        || !empty_content(node.field("selectedText"))
        || node.field("selectionRange").is_some_and(|value| !neutral_selection(value))
        || node.field("toggleState").is_some() || truth(node, "expandable") || disclosure(node) || truth(node, "menuMark");
    let region = evidence.semantic_regions.contains(&node.id)
        || matches!(
            role(node),
            "application" | "window" | "sheet" | "popover" | "webArea" | "document" | "scrollArea" | "splitGroup"
                | "toolbar" | "tabGroup" | "tabPanel" | "list" | "outline" | "table" | "row" | "cell"
                | "column" | "menu" | "menuBar" | "heading"
        )
        || matches!(node.field("subrole").and_then(Value::as_str), Some("dialog" | "systemDialog"))
        || TABLE_FIELDS.iter().any(|name| node.field(name).is_some())
        || (!retained_help && !empty_content(node.field("help")))
        || ["placeholder", "fullDescription", "landmarkType", "localizedLandmarkType", "valueDescription", "rangeDescription"]
            .iter().any(|key| !empty_content(node.field(key)))
        || ["headingLevel", "documentUrl", "documentReports"].iter().any(|name| node.field(name).is_some())
        || node.field("disclosureLevel").is_some_and(|value| !matches!(value, Value::Int(0) | Value::Bool(false)))
        || truth(node, "disclosedBy") || truth(node, "incompleteDisclosureRelation")
        || !(empty_content(node.field("subrole")) || node.field("subrole").and_then(Value::as_str) == Some("hostingView"));
    Ok(u8::from(input)
        | (u8::from(action) * ACTION_BOUNDARY)
        | (u8::from(state) << 2)
        | (u8::from(region) << 3)
        | (u8::from(evidence.contested_sources.contains(&node.id)) * SCOPE_BOUNDARY)
        | (u8::from(evidence.incomplete_content.contains(&node.id)) << 5))
}

impl Projection {
    /// The original before/after-children rule order. Whole-tree rules own
    /// their own stages and supply the completed source/spatial evidence.
    pub(crate) fn compress_local(&mut self, evidence: &CompressionEvidence, geometry: &Geometry) -> Result<()> {
        let root = self.display_node(self.root)?;
        let input = if self.options.app_view && role(root) == "application" && self.options.subtree.is_none() {
            root.children.clone()
        } else {
            vec![self.root]
        };
        let mut roots = self.visit(input, evidence, geometry)?;
        if self.options.app_view {
            roots.sort_by_key(|&node| match role(self.node(node).expect("visited display root")) {
                "window" | "sheet" => 0,
                "menuBar" => 2,
                _ => 1,
            });
        }
        self.roots = roots;
        Ok(())
    }

    fn visit(
        &mut self, roots: Vec<NodeId>, evidence: &CompressionEvidence, geometry: &Geometry,
    ) -> Result<Vec<NodeId>> {
        enum Visit {
            Enter { id: NodeId, parent: Option<NodeId>, parent_table: bool },
            Leave { id: NodeId, parent: Option<NodeId>, in_table: bool, start: usize },
        }
        let mut pending: Vec<_> = roots.into_iter().rev().map(|id|
            Visit::Enter { id, parent: None, parent_table: false }).collect();
        let mut retained = Vec::new();
        while let Some(visit) = pending.pop() {
            match visit {
                Visit::Enter { id, parent, parent_table } => {
                    let node = self.display_node(id)?;
                    let role_ = role(node);
                    let in_table = role_ == "table"
                        || (role_ == "row" && node.children.iter().filter(|&&id| {
                            self.node(id).is_some_and(|child| role(child) == "cell")
                        }).take(2).count() > 1)
                        || (parent_table && role_ != "outline");
                    self.run_rule("promote_description", |projection| projection.promote_description(id))?;
                    self.run_rule("merge_editors", |projection| projection.merge_editors(id))?;
                    self.run_rule("merge_control_labels", |projection| projection.merge_control_labels(id, in_table))?;
                    pending.push(Visit::Leave { id, parent, in_table, start: retained.len() });
                    pending.extend(self.display_node(id)?.children.iter().rev().map(|&child|
                        Visit::Enter { id: child, parent: Some(id), parent_table: in_table }));
                }
                Visit::Leave { id, parent, in_table, start } => {
                    self.node_mut(id)?.children = retained.split_off(start);
                    self.run_rule("merge_repeated_text", |projection| projection.merge_repeated_text(id, in_table, evidence))?;
                    self.run_rule("merge_region_labels", |projection| projection.merge_region_labels(id, in_table))?;
                    self.run_rule("flatten_redundant_hierarchy", |projection| projection.flatten_redundant_hierarchy(id, in_table))?;
                    if let Some(replacements) = self.run_rule("prune_layout", |projection| {
                        projection.prune_layout(id, parent, in_table, evidence)
                    })? {
                        retained.extend(replacements);
                        continue;
                    }
                    if self.run_rule("prune_empty_rows", |projection| projection.prune_empty_row(id, in_table))? {
                        continue;
                    }
                    let child = self.run_rule("collapse_transparent_groups", |projection| {
                        projection.collapse_group(id, in_table, evidence, geometry)
                    })?;
                    retained.push(child.unwrap_or(id));
                }
            }
        }
        Ok(retained)
    }

    pub(super) fn keep(&self, id: NodeId) -> bool {
        let Some(subtree) = self.options.subtree else { return false };
        if subtree == id {
            return true;
        }
        let Some(target) = self.node(id) else { return false };
        self.node(subtree)
            .and_then(|node| super::field(&node.native_fields, "control"))
            .and_then(|control| control.get("members"))
            .and_then(Value::as_seq)
            .is_some_and(|members| members.iter().any(|member| {
                member.get("ref").and_then(Value::as_str) == Some(&target.reference)
            }))
    }

    pub(super) fn promote_description(&mut self, id: NodeId) -> Result<()> {
        let node = self.display_node(id)?;
        if !truth(node, "name") && truth(node, "description") && (ordinary_control(node) || REGIONS.contains(&role(node)) || role(node) == "heading") {
            let parts: Vec<_> = node.text_sources.iter().filter(|part| part.field == "description").cloned().collect();
            self.adopt(id, &parts, "name", " ", false)?;
            self.suppress(id, "description")?;
        }
        Ok(())
    }

    pub(super) fn passive_caption(&self, node: &ProjectionNode, target: &ProjectionNode, parts: &[TextSource]) -> Result<bool> {
        Ok(matches!(role(node), "staticText" | "heading")
            && !parts.is_empty()
            && !self.keep(node.id)
            && Some(node.id) != self.options.focused
            && !["focused", "pressable", "selectable", "focusable", "selected"].iter().any(|key| truth(node, key))
            && !explicit_false(node, "enabled")
            && !disclosure(node)
            && !truth(node, "expandable")
            && !truth(node, "submenu")
            && !truth(node, "valueExpected")
            && !protected_content(node)?
            && !truth(node, "incompleteLabelRelation")
            && !capabilities(node).and_then(Value::as_map).is_some_and(|caps| {
                caps.iter().any(|(_, value)| value.as_str() == Some("available"))
            })
            && !["preserveGroup", "subrole", "help", "url", "modal"].iter().any(|key| truth(node, key))
            && node.field("control").is_none()
            && !matches!(node.field("subrole").and_then(Value::as_str), Some("dialog" | "systemDialog"))
            && short_value(node)?
            && same_blocking(node, target))
    }

    fn member_of(node: &ProjectionNode, owner: &ProjectionNode) -> bool {
        owner.field("control").and_then(|control| control.get("members")).and_then(Value::as_seq)
            .is_some_and(|members| members.iter().any(|member| {
                member.get("ref").and_then(Value::as_str) == Some(node.reference.as_str())
            }))
    }

    pub(super) fn merge_region_labels(&mut self, id: NodeId, in_table: bool) -> Result<()> {
        let node = self.display_node(id)?;
        if in_table || !REGIONS.contains(&role(node)) {
            return Ok(());
        }
        let children = node.children.clone();
        if !truth(node, "name") {
            let headings = children.iter().filter_map(|id| self.node(*id))
                .filter(|child| role(child) == "heading" && truth(child, "name")).collect::<Vec<_>>();
            if let [heading] = headings.as_slice() {
                if children.first() == Some(&heading.id) {
                    self.adopt(id, &text_sources(heading), "name", ", ", false)?;
                }
            }
        }
        let mut retained = Vec::new();
        for child_id in children {
            let node = self.display_node(id)?;
            let child = self.display_node(child_id)?;
            let parts = text_sources(child);
            if self.passive_caption(child, node, &parts)?
                && (node.field("capabilities").is_none() || Self::member_of(child, node))
                && !truth(child, "labelTargets")
                && child.children.is_empty()
                && parts.iter().all(|part| node.text_sources.iter().any(|own| own.text == part.text))
            {
                self.fold(child_id, id, "merge_region_labels", "label")?;
            } else {
                retained.push(child_id);
            }
        }
        self.node_mut(id)?.children = retained;
        Ok(())
    }

    pub(super) fn merge_repeated_text(
        &mut self,
        id: NodeId,
        in_table: bool,
        evidence: &CompressionEvidence,
    ) -> Result<()> {
        let node = self.display_node(id)?;
        let node_role = role(node);
        let editing = matches!(node_role, "textField" | "textArea" | "comboBox" | "document")
            || cap(node, "typeText") || cap(node, "replaceText");
        if !truth(node, "_factBacked") || in_table || editing
            || (!REGIONS.contains(&node_role) && !CONTROLS.contains(&node_role) && !matches!(node_role, "heading" | "staticText"))
            || (truth(node, "preserveGroup") && !self.options.compact)
            || incomplete(node)?
        {
            return Ok(());
        }
        let mut destinations = HashMap::new();
        for name in ["name", "description", "value"] {
            let parts: Vec<_> = node.text_sources.iter().filter(|part| part.field == name).collect();
            if truth(node, "preserveGroup") {
                let complete = if parts.is_empty() {
                    evidence.complete_text.contains(&FactKey { source: id, attribute: name.to_owned() })
                } else {
                    parts.iter().all(|part| evidence.complete_text.contains(&part.fact))
                };
                if !complete {
                    continue;
                }
            }
            if name == "value" && (truth(node, "valueIsNumber") || matches!(node_role, "checkBox" | "switch" | "radioButton")) {
                continue;
            }
            if let Some(value) = node.field(name).and_then(Value::as_str) {
                if !value.is_empty() && value.encode_utf16().count() <= 1200
                    && parts.iter().all(|part| part.text.as_str() == Some(value))
                {
                    destinations.entry(value.to_owned()).or_insert(name);
                }
            }
        }
        let children = node.children.clone();
        let mut retained = Vec::new();
        for child_id in children {
            let node = self.display_node(id)?;
            let child = self.display_node(child_id)?;
            let parts = text_sources(child);
            if role(child) != "staticText" || !child.children.is_empty()
                || !self.passive_caption(child, node, &parts)?
                || ["labelTargets", "titleRef", "valueIsNumber"].iter().any(|key| truth(child, key))
                || parts.is_empty()
                || parts.iter().any(|part| !part.text.as_str().is_some_and(|text| destinations.contains_key(text)))
            {
                retained.push(child_id);
                continue;
            }
            for part in parts {
                let name = destinations[part.text.as_str().expect("admitted destination text")];
                let node = self.display_node(id)?;
                if !node.text_sources.iter().any(|existing| existing.field == name) {
                    let original = node.field(name).expect("existing destination field").clone();
                    self.text_source(id, TextSource {
                        fact: FactKey { source: id, attribute: name.to_owned() },
                        field: name.to_owned(),
                        text: original,
                    })?;
                }
                self.adopt(id, &[part], name, ", ", false)?;
            }
            self.fold(child_id, id, "merge_repeated_text", "label")?;
        }
        self.node_mut(id)?.children = retained;
        Ok(())
    }

    pub(super) fn flatten_redundant_hierarchy(&mut self, id: NodeId, in_table: bool) -> Result<()> {
        let node = self.display_node(id)?;
        if !self.options.compact || in_table || role(node) != "group"
            || self.keep(id) || Some(id) == self.options.focused || !anonymous(node, false)?
        {
            return Ok(());
        }
        let children = node.children.clone();
        let mut retained = Vec::new();
        for child_id in children {
            let child = self.display_node(child_id)?;
            if role(child) == "group" && anonymous(child, false)?
                && same_blocking(self.display_node(id)?, child)
                && !self.keep(child_id) && Some(child_id) != self.options.focused
            {
                let grandchildren = child.children.clone();
                retained.extend_from_slice(&grandchildren);
                self.elide(child_id, ElisionKind::Splice, grandchildren, "flatten_redundant_hierarchy", "layout", Some(id))?;
            } else {
                retained.push(child_id);
            }
        }
        self.node_mut(id)?.children = retained;
        Ok(())
    }

    pub(super) fn prune_layout(
        &mut self,
        id: NodeId,
        parent: Option<NodeId>,
        in_table: bool,
        evidence: &CompressionEvidence,
    ) -> Result<Option<Vec<NodeId>>> {
        let node = self.display_node(id)?;
        let sole_wrapper = parent.and_then(|id| self.node(id)).is_some_and(|parent| {
            parent.children.len() == 1 && REGIONS.contains(&role(parent))
                && (!WRAPPERS.contains(&role(parent)) || truth(parent, "name") || truth(parent, "description"))
        });
        let compact = self.options.compact;
        let mut removable = if compact { anonymous(node, false)? } else { layout(node, false, false, false)? };
        if !removable && compact && evidence.provider_wrappers.contains(&id)
            && node.children.len() == 1 && !truth(node, "spatialPosition")
        {
            removable = anonymous(node, true)?;
        }
        let raw_layout = truth(node, "_factBacked") && role(node) == "group"
            && explicit_false(node, "isControlElement") && explicit_false(node, "isContentElement");
        if !self.keep(id) && Some(id) != self.options.focused && removable
            && (!in_table || !matches!(role(node), "cell" | "column"))
            && (node.children.len() <= 1 || sole_wrapper || raw_layout)
        {
            let children = node.children.clone();
            self.elide(id, ElisionKind::Splice, children.clone(), "prune_layout", "layout", None)?;
            return Ok(Some(children));
        }
        Ok(None)
    }

    fn complete_caption<'a>(
        &self, node: &'a ProjectionNode, name: &str, evidence: &CompressionEvidence,
    ) -> Option<&'a str> {
        let (_, presented) = node.provenance.iter().find(|(key, _)| key == name)?;
        let text = node.field(name)?.as_str()?;
        if text.is_empty() || presented.parts.is_empty() {
            return None;
        }
        for part in &presented.parts {
            let value = part.value.as_str()?;
            if part.facts.is_empty() {
                return None;
            }
            for fact in &part.facts {
                if !matches!(fact.attribute.as_str(), "name" | "description" | "value")
                    || !evidence.complete_text.contains(fact)
                    || self.node(fact.source).and_then(|source| super::field(&source.native_fields, &fact.attribute))
                        .and_then(Value::as_str) != Some(value)
                {
                    return None;
                }
            }
        }
        Some(text)
    }

    fn covered_caption(
        &self, node: &ProjectionNode, child: &ProjectionNode, evidence: &CompressionEvidence, geometry: &Geometry,
    ) -> bool {
        if !self.options.native_facts
            || ["linkedLabel", "titleRef", "incompleteLabelRelation"].iter().any(|key| truth(node, key))
            || self.targets.iter().any(|(_, targets)| targets.contains(&node.reference))
            || evidence.incomplete_content.contains(&child.id)
            || !geometry.get(&node.id).is_some_and(|bounds| geometry.get(&child.id) == Some(bounds))
        {
            return false;
        }
        let mut current = node.id;
        loop {
            if evidence.contested_sources.contains(&current) {
                return false;
            }
            if current == child.id {
                break;
            }
            current = evidence.complete_children[&current][0];
        }
        let captions = ["name", "description", "value"].map(|name| self.complete_caption(child, name, evidence));
        let mut nonempty = false;
        for name in ["name", "description"] {
            let Some(value) = super::field(&node.native_fields, name).and_then(Value::as_str) else { return false };
            if !evidence.complete_text.contains(&FactKey { source: node.id, attribute: name.to_owned() }) {
                return false;
            }
            if !value.is_empty() {
                nonempty = true;
                if !captions.contains(&Some(value)) {
                    return false;
                }
            }
            if !empty_content(node.field(name))
                && !self.complete_caption(node, name, evidence).is_some_and(|caption| captions.contains(&Some(caption)))
            {
                return false;
            }
        }
        nonempty
    }

    pub(super) fn collapse_group(
        &mut self, id: NodeId, in_table: bool, evidence: &CompressionEvidence, geometry: &Geometry,
    ) -> Result<Option<NodeId>> {
        let node = self.display_node(id)?;
        if !self.options.compact || self.options.subtree.is_some() || !truth(node, "_factBacked")
            || role(node) != "group" || in_table || self.keep(id) || node.children.len() != 1 || incomplete(node)?
            || truth(node, "spatialPosition") || truth(node, "labelTargets")
            || ["value", "controlValue", "documentText", "rangeValue", "url"].iter().any(|key| !empty_content(node.field(key)))
            || !(empty_content(node.field("roleDescription"))
                || matches!(node.field("roleDescription").and_then(Value::as_str), Some("group" | "组" | "container")))
        {
            return Ok(None);
        }
        let boundaries = display_boundaries(node, evidence, self.options.focused, false)?;
        let boundaries = if evidence.splice_conflicts.contains(&id) { boundaries } else { boundaries & !SCOPE_BOUNDARY };
        if boundaries != 0 {
            return Ok(None);
        }
        let child = self.display_node(node.children[0])?;
        if !same_blocking(node, child) {
            return Ok(None);
        }
        let mut current = id;
        let mut seen = HashSet::new();
        while current != child.id {
            if !seen.insert(current) || evidence.splice_conflicts.contains(&current) {
                return Ok(None);
            }
            let Some([following]) = evidence.complete_children.get(&current).map(Vec::as_slice) else { return Ok(None) };
            if !self.node(*following).is_some_and(|source| source.parent == Some(current) && source.scope == node.scope) {
                return Ok(None);
            }
            current = *following;
        }
        let named = ["name", "description"].iter().any(|key| !empty_content(node.field(key)));
        if named && !self.covered_caption(node, child, evidence, geometry) {
            return Ok(None);
        }
        let child = child.id;
        self.elide(
            id, ElisionKind::Splice, vec![child], "collapse_transparent_groups",
            if named { "covered-caption-group" } else { "transparent-group" }, Some(child),
        )?;
        Ok(Some(child))
    }

    pub(super) fn prune_empty_row(&mut self, id: NodeId, in_table: bool) -> Result<bool> {
        let node = self.display_node(id)?;
        let focused = self.options.focused;
        if in_table
            || self.keep(id)
            || role(node) != "row"
            || truth(node, "name")
            || truth(node, "description")
            || node.field("value").is_some()
            || !node.children.is_empty()
            || ["selected", "selectable", "focused", "pressable"].iter().any(|key| truth(node, key))
            || explicit_false(node, "enabled")
            || disclosure(node)
            || independent(node, false)?
            || Some(id) == focused
            || node.source_refs.iter().any(|&source| {
                Some(source) == focused
                    || self.node(source).and_then(|node| super::field(&node.native_fields, "focused")).is_some_and(truthy)
            })
        {
            return Ok(false);
        }
        self.elide(id, ElisionKind::Empty, Vec::new(), "prune_empty_rows", "layout", None)?;
        Ok(true)
    }

    /// Reconstruct outline nesting in display containment from confirmed,
    /// acyclic disclosure edges. Original parent and scope stay untouched.
    pub(super) fn reconstruct_outlines(&mut self) -> Result<()> {
        let mut pending: Vec<_> = self.roots.iter().rev().map(|&id| (id, false)).collect();
        while let Some((id, visited)) = pending.pop() {
            if visited {
                self.outline(id)?;
            } else {
                pending.push((id, true));
                pending.extend(self.display_node(id)?.children.iter().rev().map(|&child| (child, false)));
            }
        }
        Ok(())
    }

    fn outline(&mut self, id: NodeId) -> Result<()> {
        if role(self.display_node(id)?) != "outline" {
            return Ok(());
        }
        let children = self.display_node(id)?.children.clone();
        let mut rows = HashMap::new();
        for &child in &children {
            let row = self.display_node(child)?;
            if role(row) == "row" {
                rows.insert(row.reference.clone(), child);
            }
        }
        let mut retained = Vec::new();
        let mut stack: Vec<(String, NodeId)> = Vec::new();
        let mut acyclic_results = HashMap::new();
        for child in children {
            let row = self.display_node(child)?;
            let row_role = role(row) == "row";
            let row_reference = row.reference.clone();
            let parent_index = row.field("disclosedBy").and_then(Value::as_str).and_then(|parent| {
                stack.iter().position(|(reference, _)| reference == parent)
            });
            let mut attach = None;
            if let Some(index) = parent_index.filter(|_| row_role) {
                let parent = self.display_node(stack[index].1)?;
                let row_level = row.field("disclosureLevel");
                let parent_level = parent.field("disclosureLevel");
                let compatible_level = match (row_level, parent_level) {
                    (Some(row_level), Some(parent_level)) => {
                        let level = parent_level.as_i64().ok_or(ProjectionError::Invalid("outline parent level must be an integer"))?;
                        level.checked_add(1).is_some_and(|level| row_level.as_i64() == Some(level))
                    }
                    _ => true,
                };
                let depth = parent.field("outlineDepth").and_then(Value::as_i64).unwrap_or(0);
                if acyclic(self, child, &rows, &mut acyclic_results)?
                    && !truth(row, "incompleteDisclosureRelation")
                    && compatible_level
                    && !explicit_false(parent, "expanded")
                    && !explicit_false(parent, "disclosing")
                    && same_blocking(row, parent)
                    && depth < 128
                {
                    attach = Some((index, parent.id, parent.reference.clone(), depth + 1));
                }
            }
            if let Some((index, parent, parent_ref, depth)) = attach {
                self.node_mut(parent)?.children.push(child);
                put(&mut self.node_mut(child)?.fields, "outlineDepth", Value::Int(depth));
                self.changes.push(Value::Map(vec![
                    ("sourceRef".to_owned(), Value::Text(row_reference.clone())),
                    ("kind".to_owned(), Value::Text("outline".to_owned())),
                    ("targetRef".to_owned(), Value::Text(parent_ref)),
                ]));
                stack.truncate(index + 1);
            } else {
                retained.push(child);
                stack.clear();
                if row_role {
                    put(&mut self.node_mut(child)?.fields, "outlineDepth", Value::Int(0));
                }
            }
            if row_role {
                stack.push((row_reference, child));
            }
        }
        self.node_mut(id)?.children = retained;
        Ok(())
    }
}

fn acyclic(
    projection: &Projection,
    start: NodeId,
    rows: &HashMap<String, NodeId>,
    known: &mut HashMap<NodeId, bool>,
) -> Result<bool> {
    let mut seen = HashSet::new();
    let mut current = Some(start);
    let mut valid = true;
    while let Some(id) = current {
        if let Some(result) = known.get(&id) {
            valid = *result;
            break;
        }
        if !seen.insert(id) {
            valid = false;
            break;
        }
        current = projection.display_node(id)?.field("disclosedBy").and_then(Value::as_str)
            .and_then(|reference| rows.get(reference)).copied();
    }
    for id in seen {
        known.insert(id, valid);
    }
    Ok(valid)
}
