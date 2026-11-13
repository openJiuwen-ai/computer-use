// Copyright (c) Huawei Technologies Co., Ltd. 2026. All rights reserved.

//! Complete same-capture evidence for terminal atom description policies.

use std::collections::{HashMap, HashSet};

use crate::{
    facts::FactSnapshot,
    numbering::NodeId,
    projection::{CompressionEvidence, FactKey, Projection},
    render::lines::Fields,
    value::Value,
};

use super::{Result, RoleAlias, TextError};

type NodeRows<'a> = HashMap<&'a str, &'a Value>;

pub(crate) struct AtomDetailPolicy {
    pub(crate) role_aliases: HashMap<NodeId, RoleAlias>,
    pub(crate) deferred: HashSet<FactKey>,
    pub(crate) destinations: Vec<(FactKey, Value)>,
}

impl AtomDetailPolicy {
    fn new() -> Self {
        Self {
            role_aliases: HashMap::new(),
            deferred: HashSet::new(),
            destinations: Vec::new(),
        }
    }
}

struct Source<'a> {
    snapshot: &'a FactSnapshot,
    projection: &'a Projection,
    refs: HashMap<&'a str, &'a str>,
    counts: HashMap<&'a str, usize>,
    native: NodeRows<'a>,
    facts: NodeRows<'a>,
    fields: HashMap<(&'a str, &'a str), &'a Value>,
}

impl<'a> Source<'a> {
    fn new(snapshot: &'a FactSnapshot, semantic: &'a Value, projection: &'a Projection) -> Result<Self> {
        let refs = semantic
            .get("refs")
            .and_then(Value::as_map)
            .ok_or(TextError::Invalid("atom policy refs"))?;
        let refs: HashMap<_, _> = refs
            .iter()
            .filter_map(|(reference, token)| Some((reference.as_str(), token.as_str()?)))
            .collect();
        let mut counts = HashMap::new();
        for token in refs.values() {
            *counts.entry(*token).or_insert(0) += 1;
        }
        let native = snapshot
            .observation
            .get("nodes")
            .and_then(Value::as_map)
            .ok_or(TextError::Invalid("atom policy native nodes"))?
            .iter()
            .map(|(token, node)| (token.as_str(), node))
            .collect();
        let facts = snapshot
            .facts
            .seq("nodes")
            .iter()
            .filter_map(|node| Some((node.text("node")?, node)))
            .collect();
        let fields = semantic
            .seq("fields")
            .iter()
            .filter_map(|row| Some(((row.text("ref")?, row.text("key")?), row.get("value")?)))
            .collect();
        Ok(Self {
            snapshot,
            projection,
            refs,
            counts,
            native,
            facts,
            fields,
        })
    }

    fn original(&self, reference: &str) -> Option<(&'a str, &'a Value, &'a Value)> {
        let token = *self.refs.get(reference)?;
        if self.counts.get(token) != Some(&1) {
            return None;
        }
        Some((token, *self.native.get(token)?, *self.facts.get(token)?))
    }

    fn exact_field(
        &self,
        reference: &str,
        token: &str,
        property: &str,
        path: &[&str],
        attribute: Option<&str>,
        representation: &str,
        normalized: Option<&str>,
    ) -> bool {
        self.fields.get(&(reference, property)).is_some_and(|row| {
            row.text("representation") == Some(representation)
                && row.text("node") == Some(token)
                && row.text("field") == attribute
                && path_matches(row.seq("path"), path)
                && normalized.is_none_or(|normalized| row.text("normalized") == Some(normalized))
        })
    }

    fn destination(
        &self,
        output: &mut AtomDetailPolicy,
        id: NodeId,
        token: &str,
        property: &str,
        attribute: &str,
        path: &[&str],
        value: &str,
    ) -> Result<()> {
        let node = self
            .projection
            .node(id)
            .ok_or(TextError::Invalid("native detail source"))?;
        let scope = self
            .projection
            .node(node.scope)
            .ok_or(TextError::Invalid("native detail scope"))?;
        let capture = &self.snapshot.observation;
        let session = capture
            .text("session")
            .ok_or(TextError::Invalid("native detail session"))?;
        let observation = capture
            .text("id")
            .ok_or(TextError::Invalid("native detail observation"))?;
        let fact = FactKey {
            source: id,
            attribute: attribute.to_owned(),
        };
        let text = |value: &str| Value::Text(value.to_owned());
        let destination = [
            ("ref", text(&node.reference)),
            ("native", text(token)),
            (
                "scope",
                Value::Seq(vec![text(session), text(observation), text(&scope.reference)]),
            ),
            ("property", text(property)),
            ("field", text(attribute)),
            ("path", Value::Seq(path.iter().map(|part| text(part)).collect())),
            ("value", text(value)),
            ("readState", text("complete")),
            ("status", Value::Null),
        ];
        let destination = Value::Map(
            destination
                .into_iter()
                .map(|(key, value)| (key.to_owned(), value))
                .collect(),
        );
        if output.deferred.insert(fact.clone()) {
            output.destinations.push((fact, destination));
        } else if let Some((_, current)) = output.destinations.iter_mut().find(|(key, _)| *key == fact) {
            *current = destination;
        }
        Ok(())
    }
}

fn path_matches(actual: &[Value], expected: &[&str]) -> bool {
    actual.len() == expected.len()
        && actual
            .iter()
            .zip(expected)
            .all(|(left, right)| left.as_str() == Some(right))
}

fn path_overlaps(actual: &[Value], expected: &[&str]) -> bool {
    actual
        .iter()
        .zip(expected)
        .all(|(left, right)| left.as_str() == Some(right))
}

fn provider_subrole(native: &Value) -> bool {
    native
        .seq("supplements")
        .iter()
        .any(|record| record.text("key") == Some("provider.role_detail"))
}

fn common_label(role: &str, detail: Option<&str>, description: &str) -> Option<&'static str> {
    Some(match (role, detail, description) {
        ("button", None, "按钮") => "button",
        ("static_text", None, "文本") => "text",
        ("window", Some("standard"), "标准窗口") => "standard window",
        ("button", Some("close"), "关闭按钮") => "close button",
        ("button", Some("minimize"), "最小化按钮") => "minimize button",
        ("button", Some("zoom"), "缩放按钮") => "zoom button",
        ("button", Some("fullscreen"), "全屏幕按钮") => "full screen button",
        ("menu_bar", None, "菜单栏") => "menu bar",
        ("menu_bar_item", None, "菜单栏项") => "menu bar item",
        _ => return None,
    })
}

fn role_aliases(source: &Source<'_>, result: &mut AtomDetailPolicy) -> Result<()> {
    if source.snapshot.observation.text("backend") != Some("macos") {
        return Ok(());
    }
    let mut blocked = HashSet::new();
    for issue in source.snapshot.observation.seq("issues") {
        let path = issue.seq("path");
        if path_matches(path, &["nodes"]) && issue.text("state") == Some("partial") {
            continue;
        }
        if path.len() < 2 || path[0].as_str() != Some("nodes") {
            return Ok(());
        }
        if path.len() > 2
            && !matches!(
                path[2].as_str(),
                Some("role" | "role_detail" | "role_description" | "supplements")
            )
        {
            continue;
        }
        if path.len() > 3
            && path[2].as_str() == Some("supplements")
            && !matches!(path[3].as_str(), Some("AXSubrole" | "provider.role_detail"))
        {
            continue;
        }
        let Some(token) = path[1].as_str() else { continue };
        let optional_detail =
            path_matches(&path[2..], &["role_detail"]) || path_matches(&path[2..], &["supplements", "AXSubrole"]);
        if optional_detail
            && matches!(issue.text("state"), Some("absent" | "unsupported"))
            && issue.seq("alternatives").is_empty()
            && source
                .native
                .get(token)
                .is_some_and(|native| native.text("role_detail").is_none() && !provider_subrole(native))
        {
            continue;
        }
        blocked.insert(token);
    }
    let projection = source.projection;
    let mut pending = projection.roots.clone();
    while let Some(id) = pending.pop() {
        let node = projection
            .node(id)
            .ok_or(TextError::Invalid("role alias projection node"))?;
        pending.extend(&node.children);
        let Some((token, native, facts)) = source.original(&node.reference) else {
            continue;
        };
        if blocked.contains(token) || provider_subrole(native) {
            continue;
        }
        let (Some(native_role), Some(description)) = (native.text("role"), native.text("role_description")) else {
            continue;
        };
        let detail = native.text("role_detail");
        let Some(label) = common_label(native_role, detail, description) else {
            continue;
        };
        let Some(displayed_role) = node.text("role") else {
            continue;
        };
        let Some(values) = facts.get("values") else { continue };
        let field_check = |property, attribute, raw, displayed| {
            values.text(property) == Some(raw)
                && source.exact_field(
                    &node.reference,
                    token,
                    property,
                    &[property],
                    Some(attribute),
                    "field",
                    Some(displayed),
                )
        };
        if !field_check("role", "role", native_role, displayed_role)
            || node.text("roleDescription") != Some(description)
            || !field_check("role_description", "roleDescription", description, description)
            || detail.is_some_and(|detail| !field_check("role_detail", "subrole", detail, detail))
            || node.text("subrole") != detail
            || node.native_fields.text("roleDescription") != Some(description)
            || node.native_fields.text("role") != Some(displayed_role)
            || node.native_fields.text("subrole") != detail
        {
            continue;
        }
        source.destination(
            result,
            id,
            token,
            "role_description",
            "roleDescription",
            &["role_description"],
            description,
        )?;
        result.role_aliases.insert(
            id,
            RoleAlias {
                role: displayed_role.to_owned(),
                subrole: detail.map(str::to_owned),
                description: description.to_owned(),
                label: label.to_owned(),
            },
        );
    }
    Ok(())
}

fn has_status(facts: &Value, property: &str) -> bool {
    facts.get("fieldStatus").and_then(Value::as_map).is_some_and(|status| {
        status.iter().any(|(key, _)| {
            key == property
                || key.strip_prefix(property).is_some_and(|tail| tail.starts_with('.'))
                || property.strip_prefix(key).is_some_and(|tail| tail.starts_with('.'))
        })
    })
}

fn diagnostic_fields(source: &Source<'_>, result: &mut AtomDetailPolicy) -> Result<()> {
    let mut issues = HashMap::<_, Vec<_>>::new();
    for issue in source.snapshot.observation.seq("issues") {
        let path = issue.seq("path");
        if path.is_empty() || path_matches(path, &["nodes"]) && issue.text("state") != Some("partial") {
            return Ok(());
        }
        if path.len() < 2 || path[0].as_str() != Some("nodes") {
            continue;
        }
        if let Some(token) = path[1].as_str() {
            issues.entry(token).or_default().push(&path[2..]);
        }
    }
    let projection = source.projection;
    let visible = projection
        .visible_order()
        .map_err(|_| TextError::Invalid("native detail visible inventory"))?;
    let visible: HashSet<_> = visible.into_iter().collect();
    let role_facts: HashSet<_> = visible
        .iter()
        .filter_map(|id| projection.node(*id))
        .flat_map(|node| node.source_roles.iter().flat_map(|declaration| &declaration.facts))
        .collect();
    for id in &projection.source_order {
        let node = projection
            .node(*id)
            .ok_or(TextError::Invalid("native detail source node"))?;
        let Some((token, native, facts)) = source.original(&node.reference) else {
            continue;
        };
        let Some(values) = facts.get("values") else { continue };
        for (attribute, property, path) in [
            (
                "valueDescription",
                "control_value.description",
                &["control_value", "description"][..],
            ),
            (
                "rangeDescription",
                "numeric_value.description",
                &["numeric_value", "description"][..],
            ),
            ("role", "role", &["role"][..]),
            ("roleDescription", "role_description", &["role_description"][..]),
        ] {
            let role = matches!(attribute, "role" | "roleDescription");
            let key = FactKey {
                source: *id,
                attribute: attribute.to_owned(),
            };
            if role && (visible.contains(id) || !role_facts.contains(&key)) {
                continue;
            }
            let Some(value) = values.text(property) else { continue };
            if !role && !value.is_empty() {
                continue;
            }
            let actual = path
                .iter()
                .try_fold(native, |node, key| node.get(key))
                .and_then(Value::as_str);
            let normalized = if attribute == "role" {
                node.native_fields.text(attribute)
            } else {
                Some(value)
            };
            if actual != Some(value)
                || normalized.is_none()
                || node.native_fields.text(attribute) != normalized
                || !source.exact_field(
                    &node.reference,
                    token,
                    property,
                    path,
                    Some(attribute),
                    "field",
                    normalized,
                )
                || has_status(facts, property)
                || issues
                    .get(token)
                    .is_some_and(|paths| paths.iter().any(|issue| path_overlaps(issue, path)))
            {
                continue;
            }
            source.destination(result, *id, token, property, attribute, path, value)?;
        }
    }
    Ok(())
}

pub(crate) fn plan(
    snapshot: &FactSnapshot,
    semantic: &Value,
    projection: &Projection,
    evidence: &CompressionEvidence,
    summarized: &HashSet<NodeId>,
) -> Result<AtomDetailPolicy> {
    let mut result = AtomDetailPolicy::new();
    if !projection.options.compact
        || !projection.options.public_api
        || !projection.options.share_descriptions
        || projection.options.subtree.is_some()
    {
        return Ok(result);
    }
    let source = Source::new(snapshot, semantic, projection)?;
    diagnostic_fields(&source, &mut result)?;
    code_descriptions(&source, evidence, &mut result)?;
    empty_selected_text(&source, summarized, &mut result)?;
    role_aliases(&source, &mut result)?;
    Ok(result)
}

mod alpha;

fn qualified_code(value: &str) -> bool {
    let bytes = value.as_bytes();
    bytes.first().is_some_and(u8::is_ascii_lowercase)
        && bytes
            .iter()
            .all(|byte| byte.is_ascii_lowercase() || byte.is_ascii_digit() || *byte == b'_')
        && bytes
            .windows(3)
            .filter(|item| item[0] == b'_' && item[1] == b'_' && item[2].is_ascii_lowercase())
            .take(2)
            .count()
            == 2
}

fn ordinary_caption(node: &impl Fields) -> Option<(&'static str, &str)> {
    for attribute in ["name", "value"] {
        let Some(value) = node.text(attribute) else { continue };
        let trimmed = value.trim_matches(|value: char| value.is_whitespace() || matches!(value, '\u{1c}'..='\u{1f}'));
        if trimmed.is_empty() || value.chars().count() > 256 || qualified_code(value) {
            continue;
        }
        let mut characters = value.chars();
        if value.chars().any(alpha::alphabetic)
            || characters.next().is_some_and(|value| value.is_ascii_alphabetic())
                && characters.all(|value| {
                    value.is_ascii_alphabetic() || matches!(value, ' ' | '\'' | '’' | '.' | '?' | '!' | '…' | '-')
                })
        {
            return Some((attribute, value));
        }
    }
    None
}

fn code_descriptions(source: &Source<'_>, evidence: &CompressionEvidence, result: &mut AtomDetailPolicy) -> Result<()> {
    if source.snapshot.observation.seq("issues").iter().any(|issue| {
        let path = issue.seq("path");
        path.is_empty() || path_matches(path, &["nodes"]) && issue.text("state") != Some("partial")
    }) {
        return Ok(());
    }
    let projection = source.projection;
    let mut errors = HashSet::new();
    for (token, native) in &source.native {
        if let Some(relations) = native.get("relations").and_then(Value::as_map) {
            for (_, relation) in relations
                .iter()
                .filter(|(_, relation)| relation.text("kind") == Some("error"))
            {
                errors.insert(*token);
                errors.extend(relation.seq("targets").iter().filter_map(Value::as_str));
            }
        }
    }
    let mut peers = HashMap::<_, HashSet<_>>::new();
    for id in &projection.source_order {
        let node = projection
            .node(*id)
            .ok_or(TextError::Invalid("code description node"))?;
        let native = &node.native_fields;
        if let (Some(role), Some((_, caption))) = (native.text("role"), ordinary_caption(native)) {
            peers
                .entry((node.scope, role, caption))
                .or_default()
                .insert(native.text("description"));
        }
    }
    for id in &projection.source_order {
        let node = projection
            .node(*id)
            .ok_or(TextError::Invalid("code description source"))?;
        let original = &node.native_fields;
        let Some((caption_key, caption)) = ordinary_caption(original) else {
            continue;
        };
        let Some(value) = original.text("description").filter(|value| qualified_code(value)) else {
            continue;
        };
        let Some(role) = original.text("role") else { continue };
        let Some((token, native, facts)) = source.original(&node.reference) else {
            continue;
        };
        if !matches!(
            native.text("role"),
            Some(
                "button"
                    | "checkbox"
                    | "radio_button"
                    | "switch"
                    | "popup_button"
                    | "menu_button"
                    | "static_text"
                    | "image"
                    | "link"
            )
        ) || errors.contains(token)
            || native.text("description") != Some(value)
            || !source.exact_field(
                &node.reference,
                token,
                "description",
                &["description"],
                Some("description"),
                "field",
                Some(value),
            )
            || facts.false_flag("observed")
            || facts.get("values").and_then(|values| values.text("description")) != Some(value)
            || facts
                .get("fieldStatus")
                .and_then(|values| values.get("description"))
                .is_some()
            || !evidence.complete_text.contains(&FactKey {
                source: *id,
                attribute: "description".to_owned(),
            })
            || !evidence.complete_text.contains(&FactKey {
                source: *id,
                attribute: caption_key.to_owned(),
            })
            || peers
                .get(&(node.scope, role, caption))
                .is_none_or(|peers| peers.len() != 1)
            || native
                .get("states")
                .is_some_and(|states| states.flag("editable") || states.flag("protected"))
            || ["errorRelations", "invalid", "error", "documentText"]
                .into_iter()
                .any(|key| super::super::super::geometric::truth(original.value(key)))
            || matches!(role, "textField" | "textArea" | "comboBox" | "document")
        {
            continue;
        }
        source.destination(
            result,
            *id,
            token,
            "description",
            "description",
            &["description"],
            value,
        )?;
    }
    Ok(())
}

const SELECTION_PROPERTIES: [&str; 4] = ["text.source", "text.unit", "text.selections", "text.selected_text"];

fn empty_selected_text(source: &Source<'_>, selected: &HashSet<NodeId>, result: &mut AtomDetailPolicy) -> Result<()> {
    let mut blocked = HashSet::new();
    for issue in source.snapshot.observation.seq("issues") {
        let path = issue.seq("path");
        if path.is_empty() || path_matches(path, &["nodes"]) && issue.text("state") != Some("partial") {
            return Ok(());
        }
        if path.len() < 2 || path[0].as_str() != Some("nodes") {
            continue;
        }
        if path.len() == 2
            || path[2].as_str() == Some("text")
                && (path.len() == 3
                    || matches!(
                        path[3].as_str(),
                        Some("source" | "unit" | "selections" | "selected_text")
                    ))
        {
            if let Some(token) = path[1].as_str() {
                blocked.insert(token);
            }
        }
    }
    for id in &source.projection.source_order {
        if !selected.contains(id) {
            continue;
        }
        let node = source
            .projection
            .node(*id)
            .ok_or(TextError::Invalid("empty selection node"))?;
        let Some((token, native, facts)) = source.original(&node.reference) else {
            continue;
        };
        let Some(text) = native.get("text") else { continue };
        let Some(values) = facts.get("values") else { continue };
        if blocked.contains(token)
            || text.text("selected_text") != Some("")
            || node.native_fields.text("selectedText") != Some("")
            || node.text("selectedText") != Some("")
            || values.text("text.selected_text") != Some("")
            || values.value("text.source") != text.value("source")
            || values.value("text.unit") != text.value("unit")
            || SELECTION_PROPERTIES
                .into_iter()
                .any(|property| has_status(facts, property))
            || !source.exact_field(
                &node.reference,
                token,
                "text.selected_text",
                &["text", "selected_text"],
                Some("selectedText"),
                "field",
                Some(""),
            )
            || !source.exact_field(
                &node.reference,
                token,
                "text.source",
                &["text", "source"],
                None,
                "raw",
                None,
            )
            || !source.exact_field(
                &node.reference,
                token,
                "text.unit",
                &["text", "unit"],
                None,
                "raw",
                None,
            )
            || !source.exact_field(
                &node.reference,
                token,
                "text.selections",
                &["text", "selections"],
                None,
                "annotation",
                None,
            )
            || !matches!(text.text("unit"), Some("utf16_code_unit" | "unicode_codepoint"))
        {
            continue;
        }
        let [span] = text.seq("selections") else { continue };
        let Some(start) = span.num("start").filter(|start| *start >= 0) else {
            continue;
        };
        let covers = |node: &dyn Fields| {
            node.value("selectionRange")
                .is_some_and(|range| range.num("location") == Some(start) && range.num("length") == Some(0))
        };
        if !span.flag("collapsed")
            || span.num("end") != Some(start)
            || !span
                .value("text")
                .is_none_or(|value| *value == Value::Null || value.as_str() == Some(""))
            || !covers(node)
            || !covers(&node.native_fields)
        {
            continue;
        }
        source.destination(
            result,
            *id,
            token,
            "text.selected_text",
            "selectedText",
            &["text", "selected_text"],
            "",
        )?;
    }
    Ok(())
}
