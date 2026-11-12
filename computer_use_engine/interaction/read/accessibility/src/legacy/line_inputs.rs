// Copyright (c) Huawei Technologies Co., Ltd. 2026. All rights reserved.

//! Producer evidence consumed by the reversible legacy layout passes.

use std::collections::HashMap;

use crate::{
    line_layout::{EmptyValueLine, LayoutNode, TextPartLine},
    numbering::NodeId,
    projection::{Projection, ProjectionNode},
    render::{field, lines::LineSeed, quoted},
    semantic::literal,
    value::Value,
};

const TEXT_ROLES: &[&str] = &["textField", "textArea", "comboBox"];
const CONTAINER_ROLES: &[&str] = &[
    "application", "window", "sheet", "group", "splitGroup", "column", "scrollArea", "toolbar",
    "tabGroup", "outline", "list", "button", "image", "closeButton", "zoomButton", "minimizeButton",
];
const ORDINARY_CONTROLS: &[&str] = &[
    "button", "popUpButton", "menuButton", "checkBox", "radioButton", "switch", "link", "textField",
    "textArea", "comboBox",
];
const ANONYMOUS_FIELDS: &[&str] = &[
    "name", "description", "identifier", "help", "url", "preserveGroup", "modal", "subrole",
];
const TEXT_PART_PROPERTIES: &[&str] = &[
    "name", "description", "identifier", "help", "url", "placeholder", "focused", "focusable",
    "selected", "selectable", "pressable", "selectedText", "valueExpected", "valueIsNumber",
    "titleRef", "labelTargets", "incompleteLabelRelation", "linkedLabel", "subrole", "modal",
    "ownedModals", "blockedBy", "preserveGroup", "expandable", "expanded", "disclosing", "submenu",
    "disclosedBy", "incompleteDisclosureRelation", "actionsUnavailable",
];

fn text<'a>(node: &'a ProjectionNode, name: &str) -> Option<&'a str> {
    node.field(name).and_then(Value::as_str)
}

fn role(node: &ProjectionNode) -> &str { text(node, "role").unwrap_or("unknown") }

fn truth(value: &Value) -> bool {
    match value {
        Value::Null => false,
        Value::Bool(value) => *value,
        Value::Int(value) => *value != 0,
        Value::BigInt(value) => value != "0",
        Value::Float(value) => *value != 0.0,
        Value::Text(value) => !value.is_empty(),
        Value::Bytes(value) => !value.is_empty(),
        Value::Seq(value) => !value.is_empty(),
        Value::Map(value) => !value.is_empty(),
    }
}

fn any(node: &ProjectionNode, names: &[&str]) -> bool {
    names.iter().any(|name| node.field(name).is_some_and(truth))
}

fn present(node: &ProjectionNode, names: &[&str]) -> bool {
    names.iter().any(|name| node.field(name).is_some())
}

fn capability<'a>(node: &'a ProjectionNode, name: &str) -> Option<&'a str> {
    node.field("capabilities")?.get(name)?.as_str()
}

fn editing(node: &ProjectionNode) -> bool {
    TEXT_ROLES.contains(&role(node)) || role(node) == "document"
        || ["typeText", "replaceText"].iter().any(|name| capability(node, name) == Some("available"))
}

fn has_display_value(node: &ProjectionNode) -> bool {
    let role = role(node);
    any(node, &["valueExpected"]) || TEXT_ROLES.contains(&role)
        || matches!(role, "slider" | "checkBox" | "switch" | "radioButton" | "progressIndicator" | "incrementor")
        || role == "cell" && any(node, &["name"])
        || node.field("value").is_some_and(|value| value.as_str() != Some("")
            || !CONTAINER_ROLES.contains(&role)
            || capability(node, "typeText") == Some("available") || capability(node, "replaceText") == Some("available")
            || any(node, &["focused", "selectedText"]) || present(node, &["selectionRange"]))
}

fn enrich_layout(layout: &mut LayoutNode, node: &ProjectionNode) {
    let string = |name| text(node, name).map(str::to_owned);
    layout.identifier = string("identifier");
    layout.name = string("name");
    layout.value = string("value");
    layout.description = string("description");
    layout.url = string("url");
    layout.role_description = string("roleDescription");
    layout.visual_chain_child = string("visualChainChild");
    layout.enabled = node.field("enabled").and_then(Value::as_bool);
    layout.blocked = any(node, &["blocked"]);
    layout.unknown_input_pair = capability(node, "click") == Some("unknown")
        && capability(node, "typeText") == Some("unknown")
        && capability(node, "replaceText") != Some("available");
    layout.anonymous_group = layout.role == "group" && !any(node, ANONYMOUS_FIELDS);
    layout.fact_backed_group_wrapper = layout.role == "group" && any(node, &["_factBacked"])
        && !any(node, &["preserveGroup", "modal", "subrole", "blockedBy"]);
    layout.anonymous_control_wrapper = layout.anonymous_group && !has_display_value(node)
        && !present(node, &["control"])
        && !any(node, &["titleRef", "labelTargets", "incompleteLabelRelation", "blockedBy"]);
    layout.ordinary_control = ORDINARY_CONTROLS.contains(&layout.role.as_str())
        && !any(node, &["modal", "subrole", "blockedBy"]);
}

fn text_part(
    layout: &LayoutNode,
    node: &ProjectionNode,
    parent: &ProjectionNode,
    parent_ordinal: usize,
    sibling_position: usize,
    focused: Option<&str>,
) -> Option<TextPartLine> {
    let value = text(node, "value").filter(|value| !value.is_empty() && value.encode_utf16().count() <= 160)?;
    let generic_actions_only = node.field("actions").and_then(Value::as_seq).is_none_or(|actions| {
        actions.iter().all(|action| matches!(
            action.get("kind").or_else(|| action.get("name")).and_then(Value::as_str),
            Some("show_menu" | "scroll_into_view")
        ))
    });
    if layout.role != "staticText" || !layout.children.is_empty() || layout.incomplete_children
        || focused == Some(node.reference.as_str()) || any(node, TEXT_PART_PROPERTIES)
        || node.field("enabled").and_then(Value::as_bool) == Some(false)
        || present(node, &["selectionRange", "control", "disclosureLevel", "shortcut", "menuMark"])
        || value.chars().any(|character| matches!(character, '\0'..='\u{1f}' | '\u{7f}'..='\u{9f}' | '\u{2028}' | '\u{2029}'))
        || !generic_actions_only
        || node.field("capabilities").and_then(Value::as_map).is_some_and(|items|
            items.iter().any(|(_, value)| value.as_str() == Some("available")))
        || parent.reference.as_str() == focused.unwrap_or_default()
    {
        return None;
    }
    let role_label = text(node, "roleDescription").filter(|value| !value.is_empty())
        .map_or_else(|| "static text".to_owned(), field);
    let key = ["capabilities", "actions"].iter()
        .map(|name| node.field(name).cloned().unwrap_or(Value::Null)).collect();
    Some(TextPartLine {
        parent_ordinal, sibling_position, role_label, formatted_value: field(value), quoted_value: quoted(value),
        action_and_capability_key: literal(&Value::Seq(key)).ok()?,
    })
}

#[derive(Clone, Copy)]
struct TextPartParent<'a> {
    node: &'a ProjectionNode,
    ordinal: usize,
    siblings: &'a [NodeId],
}

pub(super) fn populate_seed(seed: &mut LineSeed, projection: &Projection, focused: Option<&str>) {
    let mut ordinal = 0;
    let mut pending: Vec<_> = seed
        .roots
        .iter_mut()
        .rev()
        .map(|node| (node, false, None))
        .collect();
    while let Some((layout, in_table, parent)) = pending.pop() {
        let Some(node) = projection.by_reference(&layout.reference) else {
            continue;
        };
        if let Some(TextPartParent {
            node: parent,
            ordinal,
            siblings,
        }) = parent
        {
            if let Some(position) = siblings.iter().rposition(|id| *id == node.id) {
                if let Some(part) = text_part(layout, node, parent, ordinal, position, focused) {
                    seed.text_parts.insert(layout.reference.clone(), part);
                }
            }
        }
        enrich_layout(layout, node);
        let in_table = in_table
            || matches!(
                layout.role.as_str(),
                "table" | "row" | "cell" | "column" | "outline" | "list"
            );
        let fact_backed = any(node, &["_factBacked"]);
        let container = layout.role == "group"
            || fact_backed && matches!(layout.role.as_str(), "webArea" | "scrollArea");
        let eligible = !in_table
            && container
            && !layout.incomplete_children
            && !present(node, &["control"])
            && !any(
                node,
                &[
                    "preserveGroup",
                    "subrole",
                    "selectedText",
                    "selected",
                    "focused",
                ],
            )
            && !editing(node);
        let siblings = if fact_backed {
            &node.children
        } else {
            &node.native_children
        };
        let parent = eligible.then_some(TextPartParent {
            node,
            ordinal,
            siblings,
        });
        ordinal += 1;
        pending.extend(
            layout
                .children
                .iter_mut()
                .rev()
                .map(|child| (child, in_table, parent)),
        );
    }
}

const SAFE_INTEGER: u64 = 9_007_199_254_740_991;

fn address(name: &str) -> Option<(&str, &str, u64, u64)> {
    let (caption, address) = if let Some((caption, address)) = name.rsplit_once(' ') {
        if caption.is_empty() || caption.contains('\n') { return None; }
        (caption, address)
    } else {
        ("", name)
    };
    let split = address.bytes().take_while(u8::is_ascii_uppercase).count();
    if split == 0 { return None; }
    let (column, row) = address.split_at(split);
    if row.is_empty() || row.len() > 16 || row.starts_with('0') || !row.bytes().all(|value| value.is_ascii_digit()) {
        return None;
    }
    let column = column.bytes().try_fold(0u64, |number, value|
        number.checked_mul(26)?.checked_add(u64::from(value - b'A' + 1)).filter(|value| *value <= SAFE_INTEGER))?;
    let row = row.parse().ok().filter(|value| *value <= SAFE_INTEGER)?;
    Some((caption, address, column, row))
}

fn collect_empty_value(
    output: &mut Vec<EmptyValueLine>,
    seed: &LineSeed,
    projection: &Projection,
    slots: &HashMap<String, Vec<String>>,
    focused: Option<&str>,
    layout: &LayoutNode,
    parent: Option<&LayoutNode>,
    in_table: bool,
) {
    if in_table && matches!(layout.role.as_str(), "cell" | "textField") && layout.children.is_empty()
        && focused != Some(layout.reference.as_str())
    {
        if let Some((parent, node, fields, span, prefix)) = parent
            .filter(|parent| matches!(parent.role.as_str(), "table" | "row" | "column"))
            .zip(projection.by_reference(&layout.reference))
            .zip(slots.get(&layout.reference))
            .zip(seed.node_ranges.get(&layout.reference))
            .zip(seed.node_prefixes.get(&layout.reference))
            .map(|((((parent, node), fields), span), prefix)| (parent, node, fields, span, prefix))
        {
            if text(node, "value") == Some("")
                && !present(node, &["selectionRange", "partialReason"])
                && !any(node, &["focused", "selected", "selectedText", "labelTargets", "incompleteLabelRelation", "blockedBy"])
            {
                if let Some((caption, address, column, row)) = text(node, "name").and_then(address) {
                    if let Some([line]) = seed.lines.get(span.clone()) {
                        let properties = if caption.is_empty() { prefix.clone() } else { format!("{prefix} {caption}") };
                        output.push(EmptyValueLine {
                            reference: layout.reference.clone(), parent: parent.reference.clone(), address: address.to_owned(),
                            column, row, indent: line.chars().take_while(|value| *value == '\t').collect(),
                            properties, fields: fields.clone(), text: line.clone(),
                        });
                    }
                }
            }
        }
    }
}

/// The producer already verified that each saved slot belongs to an unchanged
/// single-line caption with its value emitted.
pub(super) fn populate_empty_values(
    seed: &mut LineSeed,
    projection: &Projection,
    focused: Option<&str>,
    slots: &HashMap<String, Vec<String>>,
) {
    let mut output = Vec::new();
    let mut pending: Vec<_> = seed.roots.iter().rev().map(|node| (node, None, false)).collect();
    while let Some((layout, parent, in_table)) = pending.pop() {
        let in_table = in_table || layout.role == "table";
        collect_empty_value(&mut output, seed, projection, slots, focused, layout, parent, in_table);
        pending.extend(layout.children.iter().rev().map(|child| (child, Some(layout), in_table)));
    }
    seed.empty_values = output;
}

#[cfg(test)]
mod tests {
    use super::*;
    use crate::{
        line_layout::{self, LayoutFrame, LayoutMode, TableEvidence},
        projection::ProjectionOptions,
        render::lines::{render_projection, LineOptions},
    };

    fn node(reference: usize, role: &str, children: Vec<Value>, fields: &[(&str, &str)]) -> Value {
        let mut node = vec![
            ("ref".to_owned(), Value::Text(reference.to_string())),
            ("role".to_owned(), Value::Text(role.to_owned())),
            ("children".to_owned(), Value::Seq(children)),
        ];
        node.extend(
            fields
                .iter()
                .map(|(name, value)| (name.to_string(), Value::Text(value.to_string()))),
        );
        Value::Map(node)
    }

    #[test]
    fn deep_legacy_tree_preserves_layout_evidence_on_a_read_worker_stack() {
        const LEVELS: usize = 512;
        let caption = node(
            LEVELS + 1,
            "staticText",
            vec![],
            &[("value", "Deep caption")],
        );
        let cell = node(LEVELS + 4, "cell", vec![], &[("name", "A1"), ("value", "")]);
        let row = node(LEVELS + 3, "row", vec![cell], &[]);
        let table = node(LEVELS + 2, "table", vec![row], &[]);
        let mut children = vec![caption, table];
        for reference in (1..=LEVELS).rev() {
            let role = if reference == 1 { "window" } else { "group" };
            children = vec![node(reference, role, children, &[("name", "Layer")])];
        }
        let root = children.pop().unwrap();
        let tokens: HashMap<_, _> = (1..=LEVELS + 4)
            .map(|id| (id.to_string(), NodeId(id)))
            .collect();
        std::thread::scope(|scope| {
            std::thread::Builder::new()
                .stack_size(512 * 1024)
                .spawn_scoped(scope, || {
                    let projection =
                        Projection::prepare(&root, &tokens, ProjectionOptions::default()).unwrap();
                    let mut seed = render_projection(
                        &projection,
                        &LineOptions {
                            compact: true,
                            public_api: true,
                            record_fields: true,
                            ..LineOptions::default()
                        },
                    )
                    .unwrap();
                    assert_eq!(
                        seed.section
                            .nodes
                            .iter()
                            .map(|node| node.reference.clone())
                            .collect::<Vec<_>>(),
                        (1..=LEVELS + 4)
                            .map(|id| id.to_string())
                            .collect::<Vec<_>>()
                    );
                    populate_seed(&mut seed, &projection, None);
                    let caption = &seed.text_parts[&(LEVELS + 1).to_string()];
                    assert_eq!(
                        (caption.parent_ordinal, caption.sibling_position),
                        (LEVELS - 1, 0)
                    );
                    assert_eq!(caption.formatted_value, "Deep caption");
                    let slots = std::mem::take(&mut seed.empty_value_slots);
                    populate_empty_values(&mut seed, &projection, None, &slots);
                    assert_eq!(seed.empty_values.len(), 1);
                    assert_eq!(seed.empty_values[0].reference, (LEVELS + 4).to_string());
                    assert_eq!(seed.empty_values[0].address, "A1");
                    let packed = line_layout::pack(LayoutFrame {
                        lines: std::mem::take(&mut seed.lines),
                        mode: LayoutMode::Compact,
                        public_api: true,
                        record_stages: true,
                        roots: &seed.roots,
                        rendered_nodes: &seed.section.nodes,
                        node_prefixes: &seed.node_prefixes,
                        unexpanded_menus: &seed.unexpanded_menus,
                        node_line_ranges: &seed.node_ranges,
                        child_hints: &seed.child_hints,
                        empty_value_lines: &seed.empty_values,
                        action_sources: &seed.action_sources,
                        text_parts: &seed.text_parts,
                        identifier_sources: &HashMap::new(),
                        text_slice_sources: &HashMap::new(),
                        table_evidence: &TableEvidence::default(),
                        native_tables: &[],
                        field_prefix_sources: &[],
                        role_sources: &[],
                    })
                    .unwrap();
                    let rendered = seed.finish(packed.lines);
                    assert!(rendered.text.contains("Deep caption"));
                    assert!(rendered.text.contains("A1"));
                })
                .unwrap()
                .join()
                .unwrap();
        });
    }
}
