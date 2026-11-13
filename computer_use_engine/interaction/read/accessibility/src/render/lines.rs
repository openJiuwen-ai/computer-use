// Copyright (c) Huawei Technologies Co., Ltd. 2026. All rights reserved.

//! Projected-node line production with the source-owned layout and detail inputs.

use std::collections::{HashMap, HashSet};
use std::ops::Range;

use unicode_segmentation::UnicodeSegmentation;

use super::super::line_layout::{
    python_alphanumeric, python_space, ActionCatalogue, ChildCoverageHint, EmptyValueLine, LayoutNode, TextPartLine,
    UnexpandedMenuSummary,
};
use super::super::numbering::NodeId;
use super::super::projection::{CompressionEvidence, FactKey, Projection, ProjectionNode};
use super::super::value::Value;
use super::atoms::{Claim, ClaimKind, UrlCaption};
use super::text::{float, json, JsonFormat};
use super::{field, quoted, RenderedNode, RenderedSection, TextError};

const VALUE_LIMIT: usize = 1200;
pub(super) const CONVENTIONAL: [&str; 13] = [
    "button",
    "popUpButton",
    "menuButton",
    "checkBox",
    "radioButton",
    "switch",
    "link",
    "closeButton",
    "zoomButton",
    "minimizeButton",
    "textField",
    "textArea",
    "comboBox",
];
pub(super) const TEXT_ROLES: [&str; 3] = ["textField", "textArea", "comboBox"];
pub(super) const BINARY_ROLES: [&str; 3] = ["checkBox", "switch", "radioButton"];
const CONTAINER_ROLES: [&str; 16] = [
    "application",
    "window",
    "sheet",
    "group",
    "splitGroup",
    "column",
    "scrollArea",
    "toolbar",
    "tabGroup",
    "outline",
    "list",
    "button",
    "image",
    "closeButton",
    "zoomButton",
    "minimizeButton",
];
const MENU_ROLES: [&str; 4] = ["menu", "menuBar", "menuItem", "menuBarItem"];

type Result<T> = std::result::Result<T, TextError>;

#[derive(Default)]
pub(crate) struct LineOptions<'a> {
    pub(crate) compact: bool,
    pub(crate) public_api: bool,
    pub(crate) defer_identifiers: bool,
    pub(crate) record_fields: bool,
    pub(crate) app: Option<&'a str>,
    pub(crate) subtree: Option<&'a str>,
    pub(crate) menu_query: bool,
    pub(crate) focused: Option<&'a str>,
    pub(crate) focus_ancestors: &'a [String],
    pub(crate) readable_text: Option<&'a HashSet<String>>,
    pub(crate) concise_menu_summary: bool,
    pub(crate) summarized_selections: Option<&'a HashSet<String>>,
    pub(crate) suppressed_actions: Option<&'a HashSet<(String, String)>>,
    pub(crate) deferred_fields: Option<&'a HashSet<FactKey>>,
    pub(crate) compression_evidence: Option<&'a CompressionEvidence>,
}

#[derive(Default)]
pub(crate) struct LineSeed {
    pub(crate) lines: Vec<String>,
    pub(crate) section: RenderedSection,
    pub(crate) roots: Vec<LayoutNode>,
    pub(crate) node_ranges: HashMap<String, Range<usize>>,
    pub(crate) child_hints: Vec<ChildCoverageHint>,
    pub(crate) empty_values: Vec<EmptyValueLine>,
    pub(crate) action_sources: HashMap<String, ActionCatalogue>,
    pub(crate) node_prefixes: HashMap<String, String>,
    pub(crate) unexpanded_menus: Vec<UnexpandedMenuSummary>,
    pub(crate) text_parts: HashMap<String, TextPartLine>,
    pub(crate) empty_value_slots: HashMap<String, Vec<String>>,
    pub(crate) inline_actions: HashMap<String, Vec<(String, String)>>,
    pub(crate) claims: Vec<Claim>,
    pub(crate) url_order: Vec<NodeId>,
    pub(crate) url_parents: HashMap<NodeId, Option<NodeId>>,
    pub(crate) url_captions: HashMap<NodeId, Vec<UrlCaption>>,
    pub(crate) url_candidates: Vec<NodeId>,
    url_slots: HashMap<NodeId, (usize, usize, UrlSlot)>,
}

impl LineSeed {
    pub(crate) fn finish(mut self, packed: Vec<String>) -> RenderedSection {
        self.section.text = packed.join("\n");
        self.section
    }

    pub(crate) fn finish_unpacked(mut self) -> RenderedSection {
        let lines = std::mem::take(&mut self.lines);
        self.finish(lines)
    }

    pub(crate) fn defer_urls(&mut self, selected: &[NodeId]) -> Result<()> {
        for id in selected {
            let (line_index, node_index, slot) = self.url_slots.remove(id)
                .ok_or(TextError::Invalid("native URL line slot"))?;
            let mut fields = slot.fields;
            fields.remove(slot.index);
            let suffix = if fields.is_empty() { String::new() }
                else { format!("{}{}", slot.separator, fields.join(", ")) };
            let line = &mut self.lines[line_index];
            let old_length = line.len();
            *line = slot.base + &suffix;
            let node = &mut self.section.nodes[node_index];
            node.text.replace_range(..old_length, line);
            node.content = slot.content_base + &suffix;
        }
        Ok(())
    }
}

pub(super) trait Fields {
    fn value(&self, key: &str) -> Option<&Value>;
    fn text(&self, key: &str) -> Option<&str> {
        self.value(key).and_then(Value::as_str)
    }
    fn flag(&self, key: &str) -> bool {
        self.value(key).and_then(Value::as_bool) == Some(true)
    }
    fn false_flag(&self, key: &str) -> bool {
        self.value(key).and_then(Value::as_bool) == Some(false)
    }
    fn num(&self, key: &str) -> Option<i64> {
        self.value(key).and_then(Value::as_i64)
    }
    fn seq(&self, key: &str) -> &[Value] {
        self.value(key).and_then(Value::as_seq).unwrap_or_default()
    }
}

impl Fields for ProjectionNode {
    fn value(&self, key: &str) -> Option<&Value> {
        self.field(key)
    }
}

impl Fields for Value {
    fn value(&self, key: &str) -> Option<&Value> {
        self.get(key)
    }
}

impl Fields for Vec<(String, Value)> {
    fn value(&self, key: &str) -> Option<&Value> {
        self.iter().find(|(name, _)| name == key).map(|(_, value)| value)
    }
}

pub(super) fn role(node: &impl Fields) -> &str {
    node.text("role").unwrap_or("unknown")
}

fn dialog(node: &impl Fields) -> bool {
    role(node) == "sheet" || matches!(node.text("subrole"), Some("dialog" | "systemDialog"))
}

fn hidden(node: &impl Fields) -> bool {
    node.false_flag("visible") && node.false_flag("showing")
}

pub(super) fn role_selection(node: &ProjectionNode) -> (String, bool, &'static [&'static str]) {
    let role = role(node);
    let description = node.text("roleDescription").filter(|value| !value.is_empty());
    if dialog(node) && role != "sheet" {
        return ("dialog".to_owned(), false, &["role", "subrole"]);
    }
    if node.text("subrole") == Some("popover") {
        return ("popover".to_owned(), false, &["role", "subrole"]);
    }
    if role == "row" && matches!(description, None | Some("row" | "outline row" | "外框行")) {
        return ("row".to_owned(), false, &["role", "roleDescription"]);
    }
    if role == "group" && matches!(description, None | Some("group" | "组")) {
        return ("container".to_owned(), false, &["role", "roleDescription"]);
    }
    if let Some(description) = description {
        return (description.to_owned(), true, &["roleDescription"]);
    }
    let mut label = String::new();
    let mut previous_lower = false;
    for value in role.chars() {
        if previous_lower && value.is_ascii_uppercase() {
            label.push(' ');
            previous_lower = false;
        } else {
            previous_lower = value.is_ascii_lowercase();
        }
        label.extend(value.to_lowercase());
    }
    (label, false, &["role"])
}

fn role_label(node: &ProjectionNode) -> String {
    let (label, source_description, _) = role_selection(node);
    if source_description {
        field(&label)
    } else {
        label
    }
}

pub(super) fn text_items(values: &[Value]) -> Result<Vec<&str>> {
    values
        .iter()
        .map(|value| value.as_str().ok_or(TextError::Invalid("text list")))
        .collect()
}

fn scalar_number(value: &Value) -> Result<String> {
    match value {
        Value::Int(value) => Ok(value.to_string()),
        Value::BigInt(value) => Ok(value.to_owned()),
        Value::Float(value) if value.is_finite() => Ok(float(*value, JsonFormat::Diagnostic)),
        Value::Float(value) if value.is_nan() => Ok("NaN".to_owned()),
        Value::Float(value) if value.is_sign_negative() => Ok("-Infinity".to_owned()),
        Value::Float(_) => Ok("Infinity".to_owned()),
        _ => Err(TextError::Invalid("displayed number")),
    }
}

fn span(node: &impl Fields, key: &str) -> Result<(i64, i64)> {
    let value = node.value(key).ok_or(TextError::Invalid("display span"))?;
    Ok((
        value.num("location").ok_or(TextError::Invalid("span location"))?,
        value.num("length").ok_or(TextError::Invalid("span length"))?,
    ))
}

pub(super) fn carries_value(node: &impl Fields) -> bool {
    node.flag("valueExpected")
        || TEXT_ROLES.contains(&role(node))
        || matches!(
            role(node),
            "slider" | "checkBox" | "switch" | "radioButton" | "progressIndicator" | "incrementor"
        )
        || role(node) == "cell" && node.text("name").is_some_and(|name| !name.is_empty())
}

pub(super) fn has_display_value(node: &impl Fields) -> bool {
    carries_value(node)
        || node.value("value").is_some()
            && (node.text("value") != Some("")
                || !CONTAINER_ROLES.contains(&role(node))
                || capabilities(node).is_some_and(|caps| {
                    caps.text("typeText") == Some("available") || caps.text("replaceText") == Some("available")
                })
                || node.flag("focused")
                || node.value("selectionRange").is_some()
                || node.text("selectedText").is_some_and(|value| !value.is_empty()))
}

pub(super) fn capabilities(node: &impl Fields) -> Option<&Value> {
    node.value("capabilities").filter(|value| **value != Value::Null)
}

pub(super) fn click_deferred(node: &ProjectionNode) -> bool {
    node.false_flag("enabled")
        && capabilities(node).is_some_and(|caps| caps.text("click").unwrap_or("unknown") == "unknown")
        && node.seq("blockedBy").is_empty()
}

pub(super) fn standard_click(node: &ProjectionNode) -> bool {
    CONVENTIONAL.contains(&role(node)) || matches!(role(node), "menuItem" | "menuBarItem")
}

fn disclosed(node: &ProjectionNode) -> Option<bool> {
    if node.flag("expanded") || node.flag("expandable") || matches!(role(node), "row" | "disclosureTriangle") {
        if let Some(expanded) = node.value("expanded") {
            return expanded.as_bool();
        }
        if node.flag("expandable") || node.flag("disclosing") {
            return node.value("disclosing").and_then(Value::as_bool);
        }
    }
    None
}

pub(super) fn has_selection(node: &ProjectionNode) -> bool {
    node.value("selectionRange").map_or_else(
        || node.text("selectedText").is_some_and(|text| !text.is_empty()),
        |range| range.num("length").is_some_and(|length| length > 0),
    )
}

pub(super) fn text_selection_descendants(projection: &Projection, node: &ProjectionNode) -> Result<Vec<NodeId>> {
    let mut result = Vec::new();
    let mut pending: Vec<_> = node.children.iter().rev().copied().collect();
    while let Some(id) = pending.pop() {
        let child = projection.node(id).ok_or(TextError::Invalid("selection child"))?;
        if matches!(role(child), "application" | "window") {
            continue;
        }
        if has_selection(child) {
            result.push(id);
        } else {
            pending.extend(child.children.iter().rev().copied());
        }
    }
    Ok(result)
}

pub(super) fn state_labels(
    node: &ProjectionNode,
    compact: bool,
    focused: bool,
    selected_descendants: bool,
    value: Option<&str>,
    show_value: bool,
) -> Result<Vec<String>> {
    let mut states = Vec::new();
    if node.false_flag("enabled") {
        states.push(
            if click_deferred(node) {
                "reported disabled"
            } else {
                "disabled"
            }
            .to_owned(),
        );
    }
    if node.flag("selected") {
        states.push("selected".to_owned());
    }
    if let Some(toggle) = node
        .text("toggleState")
        .filter(|value| matches!(*value, "on" | "off" | "mixed"))
    {
        if !(compact && BINARY_ROLES.contains(&role(node)) && show_value && value == Some(toggle)) {
            states.push(toggle.to_owned());
        }
    }
    for state in ["busy", "unsaved", "invalid"] {
        if node.flag(state) {
            states.push(state.to_owned());
        }
    }
    let read_only = [
        ("readOnly", "value read-only"),
        ("textReadOnly", "text read-only"),
        ("rangeReadOnly", "range read-only"),
    ];
    if read_only.iter().any(|(key, _)| node.value(key).is_some())
        && read_only
            .iter()
            .all(|(key, _)| node.value(key).is_none() || node.flag(key))
    {
        states.push("read-only".to_owned());
    } else {
        states.extend(
            read_only
                .into_iter()
                .filter(|(key, _)| node.flag(key))
                .map(|(_, label)| label.to_owned()),
        );
    }
    if let Some(level) = node.value("headingLevel").filter(|value| value.as_i64() != Some(0)) {
        states.push(format!("heading level {}", scalar_number(level)?));
    }
    if let Some(landmark) = node.text("landmarkType").filter(|value| !value.is_empty()) {
        states.push(format!("{landmark} landmark"));
    }
    let ordinary = CONVENTIONAL.contains(&role(node));
    let selectable = node.flag("selectable");
    let show_focusable = !ordinary && !selectable && (!compact || !matches!(role(node), "group" | "webArea"));
    if let Some(caps) = capabilities(node) {
        if caps.text("click") == Some("available") && !selectable && (!compact || !standard_click(node)) {
            states.push("clickable".to_owned());
        }
        if selectable
            && !node.false_flag("enabled")
            && !node.flag("selected")
            && caps.text("click") != Some("unavailable")
        {
            states.push("selectable".to_owned());
        }
        if caps.text("typeText") == Some("available") {
            states.push("text-input".to_owned());
        } else if caps.text("replaceText") == Some("available") {
            states.push("replace-only".to_owned());
        }
        if caps.text("replaceText") == Some("unavailable") && caps.text("typeText") != Some("unavailable") {
            states.push("replace unavailable".to_owned());
        }
        if caps.text("focus") == Some("available") && caps.text("typeText") != Some("available") && show_focusable {
            states.push("focusable".to_owned());
        }
    } else {
        if selectable && !node.flag("selected") {
            states.push("selectable".to_owned());
        } else if !selectable && node.flag("pressable") && !ordinary {
            states.push("clickable".to_owned());
        }
        if node.flag("focusable") && show_focusable {
            states.push("focusable".to_owned());
        }
    }
    if node.flag("submenu") {
        states.push("submenu".to_owned());
    }
    if let Some(expanded) = disclosed(node) {
        states.push(if expanded { "expanded" } else { "collapsed" }.to_owned());
    }
    if focused {
        states.push("focused".to_owned());
    } else if node.flag("focused") {
        states.push("reports focus".to_owned());
    }
    if node.flag("modal") {
        states.push("modal".to_owned());
    } else if node.false_flag("modal") && dialog(node) {
        states.push("non-modal".to_owned());
    }
    if hidden(node) {
        states.push("hidden".to_owned());
    }
    if selected_descendants {
        states.push("contains selected text".to_owned());
    }
    Ok(states)
}

struct Shortened {
    head: String,
    tail: String,
    omitted: usize,
}

fn shortened(value: &str, limit: usize) -> Result<Shortened> {
    let unchanged = || Shortened {
        head: value.to_owned(),
        tail: String::new(),
        omitted: 0,
    };
    if value.encode_utf16().count() <= limit {
        return Ok(unchanged());
    }
    let mut clusters: Vec<_> = value.grapheme_indices(true).map(|(index, _)| index).collect();
    let total = clusters.len();
    if total <= limit {
        return Ok(unchanged());
    }
    clusters.push(value.len());
    let head = limit / 4;
    let tail = limit - head;
    Ok(Shortened {
        head: value[..clusters[head]].to_owned(),
        tail: value[clusters[total - tail]..].to_owned(),
        omitted: total - limit,
    })
}

fn preview(value: &str, label: &str, limit: usize) -> Result<String> {
    let value = shortened(value, limit)?;
    if value.omitted == 0 {
        return Ok(format!("{label}: {}", field(&value.head)));
    }
    Ok(format!(
        "{label} preview: {} … [{} characters omitted] … {}",
        field(&value.head),
        value.omitted,
        field(&value.tail)
    ))
}

fn contains_text(projection: &Projection, node: &ProjectionNode) -> Result<bool> {
    let mut pending = node.children.clone();
    while let Some(id) = pending.pop() {
        let child = projection.node(id).ok_or(TextError::Invalid("value label child"))?;
        if !child.flag("valueIsNumber")
            && child.value("value").is_some()
            && (TEXT_ROLES.contains(&role(child)) || role(child) == "staticText")
        {
            return Ok(true);
        }
        pending.extend(&child.children);
    }
    Ok(false)
}

pub(super) fn value_label(projection: &Projection, node: &ProjectionNode) -> Result<&'static str> {
    if node.text("valueKind") == Some("document") {
        return Ok("Document text");
    }
    if node.flag("valueIsNumber")
        && matches!(role(node), "group" | "cell" | "row" | "unknown" | "textField")
        && contains_text(projection, node)?
    {
        return Ok("Native value");
    }
    Ok("Value")
}

fn partial(node: &ProjectionNode) -> Result<Option<String>> {
    let read = node.num("childrenRead").unwrap_or(node.native_children.len() as i64);
    let total = node.num("childCount");
    let reason = node.text("partialReason").or_else(|| {
        (role(node) != "application" && total.is_some_and(|count| read < count)).then_some("children not fully read")
    });
    Ok(reason.map(|reason| {
        if let Some(total) = total {
            format!("showing {read} of {total} items; partial: {}", field(reason))
        } else {
            format!("partial: {}; total unknown", field(reason))
        }
    }))
}

fn compact_url(value: &str) -> String {
    let scheme = value.split_once(':');
    if let Some((scheme, rest)) =
        scheme.filter(|(scheme, _)| scheme.eq_ignore_ascii_case("data") || scheme.eq_ignore_ascii_case("webdoc"))
    {
        let kind: String = if scheme.eq_ignore_ascii_case("data") {
            rest.split(',').next().unwrap_or_default().chars().take(60).collect()
        } else {
            String::new()
        };
        return format!("{} [embedded]", field(&format!("{scheme}:{kind}")));
    }
    let prefix: String = value.chars().take(160).collect();
    if prefix.len() < value.len() {
        return format!("{}… [URL shortened]", field(&prefix));
    }
    field(value)
}

pub(super) fn embedded(value: &str) -> Option<(&str, usize)> {
    // Compact data-carrier elision is applicable only to a complete base64
    // syntax. A literal value outside the syntax retains its original text.
    let (header, payload) = value.split_once(',')?;
    let (syntax, encoding) = header.rsplit_once(';')?;
    let folded = encoding.chars().map(|value| match value {
        'ſ' => 's',
        value => value.to_ascii_lowercase(),
    });
    if !folded.eq("base64".chars()) {
        return None;
    }
    let bare_payload = payload.trim_end_matches('=');
    let padding = payload.len() - bare_payload.len();
    if bare_payload.len() < 128 || padding > 2 {
        return None;
    }
    // Python's Unicode IGNORECASE adds these four code points to [A-Za-z].
    let count = bare_payload.chars().try_fold(0, |count, value| {
        (value.is_ascii_alphanumeric() || matches!(value, '+' | '/' | 'İ' | 'ı' | 'ſ' | 'K'))
            .then_some(count + 1)
    })?;
    let token = |text: &str| !text.is_empty() && text.chars().all(|value| {
        python_alphanumeric(value) || "_!#$&^.+-".contains(value)
    });
    let valid = if let Some(implicit) = syntax.strip_prefix('/') {
        token(implicit)
    } else if syntax
        .get(..5)
        .is_some_and(|prefix| prefix.eq_ignore_ascii_case("data:"))
    {
        let mut fields = syntax[5..].split(';');
        fields
            .next()
            .and_then(|mime| mime.split_once('/'))
            .is_some_and(|(left, right)| token(left) && token(right))
            && fields.all(|parameter| {
                !parameter.is_empty()
                    && parameter.chars().count() <= 128
                    && !parameter.chars().any(python_space)
            })
    } else {
        false
    };
    (valid && count >= 128).then_some((header, count + padding))
}

fn compact_embedded(value: &str) -> Option<String> {
    embedded(value).map(|(header, length)| format!("{} [embedded data, {length} encoded chars]", field(header)))
}

fn display(value: &str, compact: bool) -> String {
    if compact {
        if let Some(embedded) = compact_embedded(value) {
            return embedded;
        }
    }
    field(value)
}

fn ordinary_control(node: &ProjectionNode) -> bool {
    matches!(
        role(node),
        "button"
            | "checkBox"
            | "radioButton"
            | "switch"
            | "link"
            | "menuButton"
            | "popUpButton"
            | "comboBox"
            | "textField"
            | "textArea"
            | "row"
    ) || node.flag("pressable")
        || node.flag("_factBacked") && capabilities(node).is_some_and(|caps| caps.text("click") == Some("available"))
}

pub(super) fn secondary_actions(node: &ProjectionNode) -> Vec<&Value> {
    let actions = node.seq("actions");
    let cap = capabilities(node);
    let selected = node.flag("selectable");
    let role = role(node);
    let menu_item = matches!(role, "menuItem" | "menuBarItem");
    let has_invoke = actions
        .iter()
        .any(|action| action.text("kind").or_else(|| action.text("name")) == Some("invoke"));
    actions
        .iter()
        .filter(|action| {
            let kind = action.text("kind").or_else(|| action.text("name"));
            match kind {
                Some("invoke") if !selected => !cap.map_or(ordinary_control(node) || menu_item, |cap| {
                    cap.text("click") == Some("available")
                }),
                Some("select") if !selected && cap.is_none() && menu_item => false,
                Some("show_menu")
                    if !selected
                        && node.flag("pressable")
                        && matches!(role, "popUpButton" | "menuButton" | "comboBox") =>
                {
                    cap.is_some_and(|cap| cap.text("click") != Some("available") || has_invoke)
                }
                Some("expand" | "collapse") if node.flag("expandable") && cap.is_none() => false,
                _ => true,
            }
        })
        .collect()
}

pub(crate) fn actions(
    node: &ProjectionNode, options: &LineOptions<'_>,
) -> Result<(Vec<String>, Vec<Value>, Vec<(String, String)>)> {
    let mut labels = Vec::new();
    let mut native = Vec::new();
    let mut pairs = Vec::new();
    for action in secondary_actions(node) {
        native.push(action.clone());
        let name = action.text("name").ok_or(TextError::Invalid("action name"))?;
        let kind = action.text("kind").unwrap_or(name);
        if options.compact
            && (matches!(kind, "show_menu" | "scroll_into_view")
                || options
                    .suppressed_actions
                    .is_some_and(|suppressed| suppressed.contains(&(node.reference.clone(), name.to_owned()))))
        {
            continue;
        }
        let label = action
            .text("description")
            .filter(|label| !label.is_empty())
            .unwrap_or(name);
        let ambiguous = node.seq("actions").iter().any(|other| {
            other.text("name") != Some(name)
                && (other.text("name") == Some(label) || other.text("description") == Some(label))
        });
        let label = if ambiguous {
            format!("{} [name: {}]", field(label), quoted(name))
        } else {
            field(label)
        };
        pairs.push((name.to_owned(), label.clone()));
        if !labels.contains(&label) {
            labels.push(label);
        }
    }
    if labels.len() != pairs.len() {
        pairs.clear();
    }
    Ok((labels, native, pairs))
}

pub(crate) fn reference_set<'a>(refs: impl Iterator<Item = &'a str>) -> Result<String> {
    let mut refs = refs
        .map(|reference| {
            reference
                .parse::<u64>()
                .map_err(|_| TextError::Invalid("numeric reference"))
        })
        .collect::<Result<Vec<_>>>()?;
    refs.sort_unstable();
    refs.dedup();
    let mut groups = Vec::new();
    let mut start = 0;
    while start < refs.len() {
        let mut end = start + 1;
        while end < refs.len() && refs[end] - refs[end - 1] == 1 {
            end += 1;
        }
        if end - start > 2 {
            groups.push(format!("{}–{}", refs[start], refs[end - 1]));
        } else {
            groups.extend(refs[start..end].iter().map(u64::to_string));
        }
        start = end;
    }
    Ok(groups.join(", "))
}

fn sort_keys(value: &mut Value) {
    match value {
        Value::Map(fields) => {
            fields.sort_unstable_by(|left, right| left.0.cmp(&right.0));
            for (_, value) in fields {
                sort_keys(value);
            }
        }
        Value::Seq(values) => values.iter_mut().for_each(sort_keys),
        _ => {}
    }
}

fn action_fields(projection: &Projection, node: &ProjectionNode, options: &LineOptions<'_>) -> Result<Vec<String>> {
    let mut sources = projection.folded_into.iter().filter(|(_, owner)| *owner == node.id);
    let Some(first) = sources.next() else {
        let (labels, _, _) = actions(node, options)?;
        return Ok(if labels.is_empty() {
            Vec::new()
        } else {
            vec![format!("Secondary Actions: {}", labels.join("; "))]
        });
    };
    let mut groups: Vec<(Vec<Value>, Vec<String>, Vec<&str>)> = Vec::new();
    for source in std::iter::once(node.id)
        .chain(std::iter::once(first.0))
        .chain(sources.map(|(source, _)| *source))
    {
        let source = projection
            .node(source)
            .ok_or(TextError::Invalid("folded action source"))?;
        let (labels, mut native, _) = actions(source, options)?;
        if labels.is_empty() || native.is_empty() {
            continue;
        }
        native.iter_mut().for_each(sort_keys);
        if let Some((_, _, sources)) = groups
            .iter_mut()
            .find(|(key, displayed, _)| *key == native && *displayed == labels)
        {
            sources.push(&source.reference);
        } else {
            groups.push((native, labels, vec![&source.reference]));
        }
    }
    let mut ordinary = Vec::new();
    let mut fields = Vec::new();
    for (_, labels, sources) in groups {
        if sources.len() > 1 {
            fields.push(format!(
                "Secondary Actions [elements {}]: {}",
                reference_set(sources.into_iter())?,
                labels.join("; ")
            ));
        } else {
            let source = sources[0];
            let suffix = if source == node.reference {
                String::new()
            } else {
                format!(" [element {source}]")
            };
            ordinary.extend(labels.into_iter().map(|label| label + &suffix));
        }
    }
    if !ordinary.is_empty() {
        fields.insert(0, format!("Secondary Actions: {}", ordinary.join("; ")));
    }
    Ok(fields)
}

fn text_block(
    lines: &mut Vec<String>,
    hints: &mut Vec<String>,
    pad: &str,
    label: &str,
    value: Shortened,
    suffix: &str,
) {
    lines.push(format!("{pad}\t{label}:"));
    let paragraphs = |lines: &mut Vec<String>, text: &str| {
        for value in text.replace("\r\n", "\n").replace('\r', "\n").split('\n') {
            lines.push(format!("{pad}\t| {}", field(value)));
        }
    };
    paragraphs(lines, &value.head);
    if value.omitted > 0 {
        let omission = format!("{pad}\tOmitted: {} characters ({label}){suffix}", value.omitted);
        lines.push(omission.clone());
        hints.push(omission);
        paragraphs(lines, &value.tail);
    }
}

fn child_hints(
    node: &ProjectionNode,
    options: &LineOptions<'_>,
    pad: &str,
    partial: Option<&str>,
    deferred_menu: bool,
) -> Result<Vec<String>> {
    let mut hints = Vec::new();
    let start = node.num("childrenStart");
    let end = node.num("childrenEnd");
    let total = node.num("childCount");
    let next = node.num("nextChildOffset");
    if let Some(partial) = partial.filter(|_| !deferred_menu) {
        if let Some(start) = start.filter(|_| options.compact) {
            let end = end.unwrap_or(start);
            let read = node.num("childrenRead").unwrap_or(end - start);
            let total = total.ok_or(TextError::Invalid("child coverage total"))?;
            let offset = if start == 0 {
                String::new()
            } else {
                format!(", offset {start}")
            };
            let mut description = format!("{read}/{total} read{offset}");
            if let Some(next) = next {
                if options.public_api {
                    description.push_str("; partial; cursor below");
                } else {
                    description.push_str(&format!(
                        "; partial; app.element({}).read({{\"offset\": {next}}})",
                        quoted(&node.reference)
                    ));
                }
            } else if end == total {
                if read < end - start {
                    description.push_str("; partial; unavailable native slots");
                }
                description.push_str("; end of direct children");
            } else if options.public_api {
                description.push_str("; partial; read()");
            } else {
                description.push_str("; partial");
            }
            hints.push(format!("{pad}\tChildren: {description}"));
        } else {
            hints.push(format!("{pad}\tChildren: {partial}"));
        }
    }
    if !deferred_menu {
        if let Some((start, end)) = start
            .zip(end)
            .filter(|(_, end)| options.compact && partial.is_none() && Some(*end) == total)
        {
            hints.push(format!(
                "{pad}\tChildren: offsets {start}–{end}; end of direct children."
            ));
        }
        if let Some(start) = start.filter(|_| !options.compact) {
            let inspected = match end {
                Some(end) if end > start => format!("; inspected through offset {}", end - 1),
                Some(_) => "; no direct children inspected".to_owned(),
                None => String::new(),
            };
            let complete = if end.is_some() && end == total {
                "; end of direct children"
            } else {
                ""
            };
            hints.push(format!(
                "{pad}\tChildren: native children offset {start}{inspected}{complete}; order=native."
            ));
        }
    }
    for omission in text_items(node.seq("childOmissions"))? {
        hints.push(format!("{pad}\tOmitted: {omission}"));
    }
    if !deferred_menu {
        if let Some(next) = next.filter(|_| !options.compact) {
            hints.push(if options.public_api {
                format!("{pad}\tChildren coverage: partial")
            } else {
                format!("{pad}\tNext child offset: {next}")
            });
        } else if partial.is_some() && start.is_none() && role(node) != "application" {
            hints.push(format!("{pad}\tSubtree coverage: partial; element {}", node.reference));
        }
    }
    Ok(hints)
}

struct NodeLine {
    rendered: RenderedNode,
    lines: Vec<String>,
    summary: String,
    focus: String,
    child_help: Option<String>,
    deferred_menu: Option<i64>,
    selected_descendants: Vec<NodeId>,
    node_prefix: String,
    empty_value_slots: Option<Vec<String>>,
    url_slot: Option<UrlSlot>,
    captions: Vec<UrlCaption>,
}

struct UrlSlot {
    index: usize,
    fields: Vec<String>,
    base: String,
    content_base: String,
    separator: &'static str,
}

fn capture_caption(
    captions: &mut Vec<UrlCaption>, node: &ProjectionNode, attribute: &str, rendered: &str,
    complete_emission: bool, options: &LineOptions<'_>,
) {
    let Some(evidence) = options.compression_evidence else { return; };
    let facts: Vec<_> = node.provenance.iter().filter(|(field, _)| field == attribute)
        .flat_map(|(_, value)| value.parts.iter().flat_map(|part| part.facts.iter().cloned()))
        .collect();
    let complete = complete_emission && node.text(attribute).is_some_and(|value| !value.is_empty())
        && !facts.is_empty() && facts.iter().all(|fact| evidence.complete_text.contains(fact));
    captions.push(UrlCaption { field: attribute.to_owned(), text: rendered.to_owned(), facts, complete });
}

#[derive(Default)]
struct ClaimLedger {
    entries: Vec<Claim>,
    by_source: HashMap<NodeId, Vec<usize>>,
}

struct LineClaims<'a>(Option<&'a mut ClaimLedger>);

impl LineClaims<'_> {
    fn record(&mut self, node: &ProjectionNode, attribute: &str, target: NodeId, kind: ClaimKind) {
        let (Some(claims), Some(value)) = (self.0.as_deref_mut(), node.field(attribute)) else { return; };
        let first = claims.entries.len();
        if kind != ClaimKind::Continuation && matches!(attribute, "name" | "description" | "value") {
            claims.entries.extend(node.text_sources.iter().filter(|source| source.field == attribute).map(|source| Claim {
                fact: source.fact.clone(), value: source.text.clone(), target, kind: ClaimKind::Equivalent,
            }));
        }
        if first == claims.entries.len() {
            claims.entries.push(Claim {
                fact: FactKey { source: node.id, attribute: attribute.to_owned() },
                value: value.clone(), target, kind,
            });
        }
        for index in first..claims.entries.len() {
            claims.by_source.entry(claims.entries[index].fact.source).or_default().push(index);
        }
    }

    fn display(&mut self, node: &ProjectionNode, attribute: &str) {
        self.record(node, attribute, node.id, ClaimKind::Display);
    }

    fn caption(&mut self, node: &ProjectionNode, attribute: &str, value: &str, compact: bool) -> String {
        if compact {
            if let Some(embedded) = compact_embedded(value) { return embedded; }
        }
        self.display(node, attribute);
        field(value)
    }

    fn equivalent(&mut self, node: &ProjectionNode, attribute: &str) {
        let Some(value) = node.field(attribute).filter(|value| value.as_str().is_some_and(|text| !text.is_empty()))
        else { return; };
        let target = self.0.as_deref().and_then(|claims| claims.by_source.get(&node.id)?.iter().find_map(|index| {
            let claim = &claims.entries[*index];
            (claim.fact.attribute != attribute && &claim.value == value
                && claim.kind != ClaimKind::Continuation).then_some(claim.target)
        }));
        if let Some(target) = target {
            self.record(node, attribute, target, ClaimKind::Equivalent);
        }
    }
}

fn node_line(
    projection: &Projection,
    node: &ProjectionNode,
    options: &LineOptions<'_>,
    depth: usize,
    layout: Vec<(String, String)>,
    parent: Option<&ProjectionNode>,
    shared_help: Option<&str>,
    share_index_basis: bool,
    mut claims: LineClaims<'_>,
) -> Result<NodeLine> {
    let reference = node.reference.as_str();
    let role = role(node);
    let role_label = role_label(node);
    let role_fact = if dialog(node) && role != "sheet" || node.text("subrole") == Some("popover") {
        "subrole"
    } else if node.text("roleDescription").is_some_and(|value| !value.is_empty()) {
        "roleDescription"
    } else { "role" };
    claims.display(node, role_fact);
    let pad = "\t".repeat(depth + layout.len());
    let focused = options.focused == Some(reference);
    let selected_descendants = if focused && !has_selection(node) && !matches!(role, "application" | "window") {
        text_selection_descendants(projection, node)?
    } else {
        Vec::new()
    };
    let native = &node.native_fields;
    let show_value = carries_value(native)
        || focused && node.value("value").is_some()
        || has_display_value(native)
            && node.value("value") != node.value("name")
            && node.value("value") != node.value("description");
    let raw_value = node.text("value");
    let mut value = raw_value;
    if BINARY_ROLES.contains(&role) && (node.flag("valueIsNumber") || matches!(value, Some("true" | "false"))) {
        value = match value {
            Some("0" | "false") => Some("off"),
            Some("1" | "true") => Some("on"),
            value => value,
        };
    }
    let states = state_labels(
        node,
        options.compact,
        focused,
        !selected_descendants.is_empty(),
        value,
        show_value,
    )?;
    for (flag, active) in [
        ("enabled", node.false_flag("enabled")), ("selected", node.flag("selected")),
        ("busy", node.flag("busy")), ("headingLevel", node.value("headingLevel").is_some_and(|value| value.as_i64() != Some(0))),
        ("landmarkType", node.text("landmarkType").is_some_and(|value| !value.is_empty())),
        ("focused", !focused && node.flag("focused")),
        ("modal", node.flag("modal") || node.false_flag("modal") && dialog(node)),
        ("visible", hidden(node)), ("showing", hidden(node)),
    ] {
        if active { claims.display(node, flag); }
    }
    if let Some(toggle) = node.text("toggleState").filter(|value| matches!(*value, "on" | "off" | "mixed")) {
        let kind = if options.compact && BINARY_ROLES.contains(&role) && show_value && value == Some(toggle) {
            ClaimKind::Equivalent
        } else { ClaimKind::Display };
        claims.record(node, "toggleState", node.id, kind);
    }
    if let Some(expanded) = disclosed(node) {
        if node.value("expanded").is_some() {
            claims.display(node, "expanded");
            if node.value("disclosing").and_then(Value::as_bool) == Some(expanded) {
                claims.record(node, "disclosing", node.id, ClaimKind::Equivalent);
            }
        } else {
            claims.display(node, "disclosing");
        }
    }
    let states = if states.is_empty() {
        String::new()
    } else {
        format!(" ({})", states.join(", "))
    };
    let name = node.text("name").filter(|value| !value.is_empty());
    let description = node.text("description").filter(|value| !value.is_empty());
    let mut captions = Vec::new();
    let caption = name.map_or(Ok(String::new()), |name| {
        let rendered = claims.caption(node, "name", name, options.compact);
        let full = !options.compact || compact_embedded(name).is_none();
        capture_caption(&mut captions, node, "name", &rendered, full, options);
        Ok(format!(" {rendered}"))
    })?;
    let mut fields = Vec::new();
    for (attribute, label) in [
        ("spatialPosition", "Position"),
        ("visualLabelRef", "Visual label: element"),
    ] {
        if let Some(value) = node.text(attribute).filter(|value| !value.is_empty()) {
            fields.push(if attribute == "visualLabelRef" {
                format!("{label} {value}")
            } else {
                format!("{label}: {value}")
            });
        }
    }
    for (attribute, label) in [("rowRange", "Row"), ("columnRange", "Column")] {
        if node.value(attribute).is_some() {
            claims.display(node, attribute);
            let (location, length) = span(node, attribute)?;
            fields.push(match length {
                0 => format!("{label} range (0-based): {location}:0"),
                1 => format!("{label}: {}", location + 1),
                _ => format!("{label}s: {}–{}", location + 1, location + length),
            });
        }
    }
    for (attribute, label) in [
        ("rowCount", "Table rows"),
        ("columnCount", "Table columns"),
        ("selectedChildCount", "Selected children"),
    ] {
        if let Some(value) = node.value(attribute) {
            claims.display(node, attribute);
            fields.push(format!("{label}: {}", scalar_number(value)?));
        }
    }
    if let Some(value) = node.value("index") {
        claims.display(node, "index");
        let label = if share_index_basis { "Index" } else { "Index (0-based)" };
        fields.push(format!("{label}: {}", scalar_number(value)?));
    }
    if click_deferred(node) {
        fields.push("Click: deferred".to_owned());
    }
    if let Some(owner) = node.text("editorOwnerRef").filter(|owner| !owner.is_empty()) {
        fields.push(format!("Editor for: {owner}"));
    }
    if let Some(level) = node
        .value("disclosureLevel")
        .filter(|level| Some(*level) != node.value("outlineDepth"))
    {
        claims.display(node, "disclosureLevel");
        fields.push(format!("Outline level: {}", scalar_number(level)?));
    }
    if let Some(owner) = node.text("disclosedBy").filter(|owner| !owner.is_empty()) {
        fields.push(format!("Parent row: {owner}"));
    }
    if node.flag("incompleteDisclosureRelation") {
        fields.push("Parent row: outside this observation or unavailable".to_owned());
    }
    if let Some(value) = node.text("placeholder").filter(|value| !value.is_empty()) {
        fields.push(format!("Placeholder: {}", claims.caption(node, "placeholder", value, options.compact)));
    }
    if let (Some(expanded), Some(disclosing)) = (node.value("expanded"), node.value("disclosing")) {
        if expanded != disclosing {
            claims.display(node, "disclosing");
            let state = if disclosing.as_bool() == Some(true) {
                "expanded"
            } else {
                "collapsed"
            };
            fields.push(format!("Disclosure state: {state} (native reports disagree)"));
        }
    }
    if let Some(caps) = capabilities(node).filter(|_| !node.false_flag("enabled") && node.seq("blockedBy").is_empty()) {
        let standard = if options.compact {
            standard_click(node)
        } else {
            CONVENTIONAL.contains(&role)
        };
        if caps.text("click") == Some("unavailable") && (standard || node.flag("selectable")) {
            fields.push("Click: unavailable".to_owned());
        }
        if TEXT_ROLES.contains(&role)
            && caps.text("typeText") == Some("unavailable")
            && caps.text("replaceText") != Some("available")
        {
            fields.push("Input: unavailable".to_owned());
        }
    }
    if focused && node.false_flag("focused") {
        claims.display(node, "focused");
        fields.push("Focus flag: false".to_owned());
    }
    if let Some(value) = description.filter(|description| Some(*description) != name) {
        let rendered = claims.caption(node, "description", value, options.compact);
        let full = !options.compact || compact_embedded(value).is_none();
        capture_caption(&mut captions, node, "description", &rendered, full, options);
        fields.push(format!("Description: {rendered}"));
    }
    let mut decision_value = None;
    for (attribute, label) in [
        ("rangeMinimum", "Minimum"),
        ("rangeMaximum", "Maximum"),
        ("rangeSmallStep", "Small step"),
        ("rangeLargeStep", "Large step"),
        ("rangeAllowedValues", "Allowed values"),
        ("hasPopup", "Has popup"),
        ("protected", "Protected"),
        ("offscreen", "Offscreen"),
        ("canSelectMultiple", "Multiple selection"),
        ("selectionRequired", "Selection required"),
        ("itemStatus", "Item status"),
    ] {
        if let Some(value) = node.value(attribute) {
            claims.display(node, attribute);
            let value = if attribute == "itemStatus" {
                field(value.as_str().ok_or(TextError::Invalid("item status"))?)
            } else {
                json(value, JsonFormat::Literal)?
            };
            fields.push(format!("{label}: {value}"));
            decision_value = Some(value);
        }
    }
    for (attribute, label, allow_empty) in [
        ("fullDescription", "Full description", false),
        ("valueDescription", "Value description", true),
        ("rangeDescription", "Range description", true),
        ("localizedLandmarkType", "Landmark", false),
    ] {
        if matches!(attribute, "valueDescription" | "rangeDescription")
            && (node
                .shared_description_destinations
                .iter()
                .any(|(name, _)| name == attribute)
                || options.deferred_fields.is_some_and(|fields| {
                    fields.contains(&FactKey {
                        source: node.id,
                        attribute: attribute.to_owned(),
                    })
                }))
        {
            continue;
        }
        if let Some(value) = node.text(attribute).filter(|value| allow_empty || !value.is_empty()) {
            fields.push(format!("{label}: {}", claims.caption(node, attribute, value, options.compact)));
        }
    }
    let value_parts = shortened(raw_value.unwrap_or_default(), VALUE_LIMIT)?;
    // The existing diagnostic formatter reuses the last decision's display
    // value for the following scalar line. Keep that legacy presentation here.
    let value = decision_value.as_deref().or(value);
    let value_label = value_label(projection, node)?;
    let embedded = if options.compact {
        value.and_then(compact_embedded)
    } else {
        None
    };
    let block = show_value
        && raw_value.is_some()
        && embedded.is_none()
        && (raw_value.is_some_and(|value| value.contains(['\n', '\r'])) || value_parts.omitted != 0);
    if show_value && !block {
        if embedded.is_none() { claims.display(node, "value"); }
        let natural =
            options.compact && role == "staticText" && name.is_none() && description.is_none() && value.is_some();
        let value = embedded
            .clone()
            .or_else(|| value.map(field))
            .unwrap_or_else(|| "<unavailable>".to_owned());
        capture_caption(&mut captions, node, "value", &value, embedded.is_none(), options);
        fields.push(if natural {
            value
        } else {
            format!("{value_label}: {value}")
        });
    }
    let targets = text_items(node.seq("labelTargets"))?;
    if !targets.is_empty() {
        fields.push(format!("Labels for: {}", targets.join(", ")));
    }
    if node.flag("incompleteLabelRelation") {
        fields.push("Label relation: partly outside this observation or unavailable".to_owned());
    }
    let selection = node
        .value("selectionRange")
        .map(|_| span(node, "selectionRange"))
        .transpose()?
        .filter(|range| {
            !options.compact
                || *range != (0, 0)
                || TEXT_ROLES.contains(&role)
                || role == "document"
                || capabilities(node).is_some_and(|caps| {
                    caps.text("typeText") == Some("available") || caps.text("replaceText") == Some("available")
                })
        });
    let summarized = options.compact
        && options
            .summarized_selections
            .is_some_and(|refs| refs.contains(reference));
    if let Some((start, length)) = selection.filter(|_| !summarized) {
        fields.push(format!("Selection: {start}:{length}"));
    }
    if selection.is_some() {
        let kind = if summarized { ClaimKind::TextSelectionSummary } else { ClaimKind::Display };
        claims.record(node, "selectionRange", node.id, kind);
    }
    if let Some(value) = node.text("shortcut") {
        fields.push(format!("Shortcut: {}", display(value, options.compact)));
    }
    if let Some(value) = node.text("menuMark") {
        let value = if value.is_empty() {
            "<none>".to_owned()
        } else {
            display(value, options.compact)
        };
        fields.push(format!("Mark: {value}"));
    }
    let selected = node.text("selectedText");
    let selected_parts = selected
        .map(|value| shortened(value, if options.record_fields { usize::MAX } else { 200 }))
        .transpose()?;
    if let Some(value) = selected.filter(|_| selected_parts.as_ref().is_some_and(|parts| parts.omitted == 0)) {
        claims.display(node, "selectedText");
        fields.push(format!("Selected text: {}", field(value)));
    }
    fields.extend(action_fields(projection, node, options)?);
    if node.flag("actionsUnavailable") {
        fields.push("Secondary Actions: <unavailable>".to_owned());
    }
    let help = node.text("help");
    if let Some(value) =
        help.filter(|value| !value.is_empty() && ![shared_help, name, description, raw_value].contains(&Some(value)))
    {
        fields.push(format!("Help: {}", claims.caption(node, "help", value, options.compact)));
    }
    let child_help = node
        .children
        .first()
        .filter(|_| node.children.len() >= 3)
        .and_then(|id| projection.node(*id))
        .and_then(|child| child.text("help"))
        .filter(|value| !value.is_empty())
        .filter(|help| {
            node.children
                .iter()
                .all(|id| projection.node(*id).and_then(|child| child.text("help")) == Some(help))
        });
    if let Some(value) = child_help {
        fields.push(format!("Children Help: {}", display(value, options.compact)));
        if !options.compact || compact_embedded(value).is_none() {
            for child in node.children.iter().filter_map(|id| projection.node(*id)) {
                claims.record(child, "help", node.id, ClaimKind::Equivalent);
            }
        }
    }
    let mut url_index = None;
    if let Some(value) = node
        .text("url")
        .filter(|value| !value.is_empty() && ![name, description, raw_value, help].contains(&Some(value)))
    {
        if options.compression_evidence.is_some() { url_index = Some(fields.len()); }
        let value = if options.compact {
            compact_url(value)
        } else {
            claims.caption(node, "url", value, false)
        };
        fields.push(format!("URL: {value}"));
    }
    if let Some(value) = node.text("documentUrl") {
        claims.display(node, "documentUrl");
        fields.push(format!("Document: {}", field(value)));
    }
    if let Some(value) = node.value("documentReports") {
        claims.display(node, "documentReports");
        fields.push(format!(
            "Document reports (unresolved): {}",
            json(value, JsonFormat::Literal)?
        ));
    }
    if let Some(value) = node
        .text("identifier")
        .filter(|value| !options.defer_identifiers && !value.is_empty())
    {
        claims.display(node, "identifier");
        fields.push(format!("ID: {}", field(value)));
    }
    let blocked = text_items(node.seq("blockedBy"))?;
    if !blocked.is_empty()
        && blocked.join(",") != text_items(parent.map_or(&[], |parent| parent.seq("blockedBy")))?.join(",")
    {
        fields.push(format!("Blocked by: {}", blocked.join(", ")));
    }
    let empty_value_slots = name.filter(|name| {
        show_value && raw_value == Some("") && matches!(role, "cell" | "textField") && caption == format!(" {name}")
    }).map(|_| fields.clone());
    let separator = if name.is_some() { ", " } else { " " };
    let url_slot = url_index.map(|index| UrlSlot {
        index, fields: fields.clone(), separator,
        base: format!("{pad}{reference} {role_label}{states}{caption}"),
        content_base: format!("{pad}{reference} {role_label}{caption}"),
    });
    let fields = if fields.is_empty() {
        String::new()
    } else {
        format!("{separator}{}", fields.join(", "))
    };
    let line = format!("{pad}{reference} {role_label}{states}{caption}{fields}");
    let content = format!("{pad}{reference} {role_label}{caption}{fields}");
    let partial = partial(node)?;
    let deferred_menu = node.num("childCount").filter(|count| {
        options.compact
            && role == "menuBarItem"
            && node.children.is_empty()
            && node.num("childrenStart") == Some(0)
            && node.num("childrenEnd") == Some(0)
            && *count > 0
    });
    let mut hints = Vec::new();
    for group in node.shared_descriptions.iter().chain(&node.source_roles) {
        if options.compact
            && options.subtree.is_none()
            && !group.facts.is_empty()
            && options
                .deferred_fields
                .is_some_and(|fields| group.facts.iter().all(|fact| fields.contains(fact)))
        {
            continue;
        }
        let label = match group.attribute.as_str() {
            "valueDescription" => "Value description",
            "rangeDescription" => "Range description",
            "roleDescription" => "Native role description",
            _ => "Native role",
        };
        let references = group
            .facts
            .iter()
            .map(|fact| {
                projection
                    .node(fact.source)
                    .map(|source| source.reference.as_str())
                    .ok_or(TextError::Invalid("shared field source"))
            })
            .collect::<Result<Vec<_>>>()?;
        for fact in &group.facts {
            let source = projection.node(fact.source).ok_or(TextError::Invalid("shared field source"))?;
            claims.record(source, &fact.attribute, node.id, ClaimKind::Shared);
        }
        hints.push(format!(
            "{pad}\t{label} [elements {}]: {}",
            reference_set(references.into_iter())?,
            quoted(&group.value)
        ));
    }
    hints.extend(child_hints(
        node,
        options,
        &pad,
        partial.as_deref(),
        deferred_menu.is_some(),
    )?);
    let mut lines = vec![line];
    lines.extend(hints.iter().cloned());
    if block {
        let continuation = if options
            .readable_text
            .is_none_or(|readable| readable.contains(reference))
        {
            format!("; text source: element {reference}")
        } else {
            "; full captured value unavailable".to_owned()
        };
        let part = Shortened {
            head: value_parts.head.clone(),
            tail: value_parts.tail.clone(),
            omitted: value_parts.omitted,
        };
        let first = lines.len();
        text_block(&mut lines, &mut hints, &pad, value_label, part, &continuation);
        let kind = if value_parts.omitted == 0 { ClaimKind::Display } else { ClaimKind::Continuation };
        claims.record(node, "value", node.id, kind);
        if options.compression_evidence.is_some() {
            let rendered = lines[first..].iter().map(|line| &line[pad.len() + 1..]).collect::<Vec<_>>().join("\n");
            capture_caption(&mut captions, node, "value", &rendered, value_parts.omitted == 0, options);
        }
    }
    for (attribute, label) in [
        ("documentText", "Document text"),
        ("controlValue", "Control value"),
        ("rangeValue", "Range value"),
    ] {
        if let Some(value) = node.text(attribute) {
            let numeric = text_items(node.seq("_numericValueAliases"))?.contains(&attribute);
            let complete = text_items(node.seq("_completeTextValues"))?;
            if Some(value) == raw_value && numeric && show_value && value_parts.omitted == 0 && embedded.is_none() {
                claims.record(node, attribute, node.id, ClaimKind::Equivalent);
                continue;
            }
            if Some(value) == raw_value && complete.contains(&attribute) && complete.contains(&"value") {
                claims.equivalent(node, attribute);
                continue;
            }
            let part = shortened(value, VALUE_LIMIT)?;
            let kind = if part.omitted == 0 { ClaimKind::Display } else { ClaimKind::Continuation };
            claims.record(node, attribute, node.id, kind);
            if value.contains(['\r', '\n']) || part.omitted != 0 {
                text_block(&mut lines, &mut hints, &pad, label, part, "");
            } else {
                lines.push(format!("{pad}\t{label}: {}", field(value)));
            }
        }
    }
    if let Some(part) = selected_parts.filter(|parts| parts.omitted != 0) {
        text_block(&mut lines, &mut hints, &pad, "Selected text", part, "");
    }
    for attribute in ["description", "help", "url"] {
        claims.equivalent(node, attribute);
    }
    let empty_value_slots = empty_value_slots.filter(|_| lines.len() == 1);
    let label = name.or(description).unwrap_or_default();
    let mut focus = format!("{reference} {role_label}");
    if !label.is_empty() {
        if shortened(label, 120)?.omitted != 0 {
            focus.push_str(&format!(", {}", preview(label, "Name", 120)?));
        } else {
            focus.push_str(&format!(" {}", field(label)));
        }
    }
    if show_value {
        let value = value
            .map(|value| preview(value, value_label, 200))
            .transpose()?
            .unwrap_or_else(|| "Value: <unavailable>".to_owned());
        focus.push_str(&format!(", {value}"));
    }
    if let Some((start, length)) = selection.filter(|_| !summarized) {
        focus.push_str(&format!(", Selection: {start}:{length}"));
    }
    if let Some(value) = selected {
        focus.push_str(&format!(", {}", preview(value, "Selected text", 200)?));
    }
    let summary_name = [Some(label), raw_value, node.text("identifier")]
        .into_iter()
        .flatten()
        .find(|value| !value.is_empty());
    let mut summary = format!("{reference} {role_label}");
    if let Some(value) = summary_name {
        let part = shortened(value, 80)?;
        let omitted = if part.omitted == 0 { "" } else { "…" };
        summary.push_str(&format!(" {}", field(&format!("{}{omitted}{}", part.head, part.tail))));
        if part.omitted != 0 {
            summary.push_str(" (preview; see tree)");
        }
    }
    Ok(NodeLine {
        rendered: RenderedNode {
            reference: reference.to_owned(),
            parent: parent.map(|parent| parent.reference.clone()),
            depth,
            text: lines.join("\n"),
            content,
            hints,
            related: Vec::new(),
            partial: partial.is_some() && deferred_menu.is_none(),
            layout,
        },
        lines,
        summary,
        focus,
        child_help: child_help.map(str::to_owned),
        deferred_menu,
        selected_descendants,
        node_prefix: format!("{role_label}{states}"),
        empty_value_slots,
        url_slot,
        captions,
    })
}

fn visual_group(node: &ProjectionNode, child: &ProjectionNode) -> Result<Option<String>> {
    for group in node.seq("layoutGroups") {
        let members = text_items(group.seq("members"))?;
        if members.contains(&child.reference.as_str()) {
            return Ok(members.first().map(|reference| (*reference).to_owned()));
        }
    }
    Ok(None)
}

fn child_evidence(
    projection: &Projection,
    node: &ProjectionNode,
    index: usize,
    line: &str,
) -> Option<ChildCoverageHint> {
    let original = &node.native_fields;
    let total = node.num("childCount").filter(|value| *value > 0)?;
    let reason = node.text("partialReason").filter(|reason| !reason.is_empty())?;
    if node.num("childrenStart") != Some(0)
        || node.num("childrenEnd") != Some(0)
        || !node.children.is_empty()
        || original.num("childrenStart") != Some(0)
        || original.num("childrenEnd") != Some(0)
        || !node.native_children.is_empty()
        || node.num("childrenRead").unwrap_or(0) != 0
        || original.num("childrenRead").unwrap_or(0) != 0
        || node.value("nextChildOffset").is_some()
        || original.value("nextChildOffset").is_some()
        || !node.seq("childOmissions").is_empty()
        || !original.seq("childOmissions").is_empty()
        || original.num("childCount") != Some(total)
        || original.text("partialReason") != Some(reason)
    {
        return None;
    }
    let mut ancestor = Some(node);
    let mut scope = node.scope;
    while let Some(current) = ancestor {
        if matches!(role(&current.native_fields), "window" | "sheet" | "menuBar" | "menu") {
            scope = current.id;
            break;
        }
        ancestor = current.parent.and_then(|id| projection.node(id));
    }
    Some(ChildCoverageHint {
        reference: node.reference.clone(),
        total: total as u64,
        partial_reason: reason.to_owned(),
        scope: projection.node(scope)?.reference.clone(),
        line_index: index,
        text: line.to_owned(),
    })
}

fn menu_children(node: &ProjectionNode) -> Option<(u64, u64, u64)> {
    let start = u64::try_from(node.num("childrenStart")?).ok()?;
    let end = u64::try_from(node.num("childrenEnd")?).ok()?;
    let count = u64::try_from(node.num("childCount")?).ok()?;
    Some((start, end, count))
}

#[derive(Default)]
struct Summary {
    labels: HashMap<NodeId, String>,
    focus: HashMap<NodeId, String>,
    selected: Vec<NodeId>,
    selected_menus: Vec<NodeId>,
    reported_focus: Vec<NodeId>,
    selected_descendants: Vec<NodeId>,
    collapsed_menus: Vec<(i64, Vec<String>)>,
    context: HashMap<String, Vec<String>>,
}

fn has_summary_label(node: &ProjectionNode) -> bool {
    node.text("name").is_some_and(|value| !value.is_empty())
        || node.text("description").is_some_and(|value| !value.is_empty())
        || node.text("value").is_some_and(|value| !value.is_empty())
            && matches!(
                role(node),
                "staticText" | "heading" | "textField" | "textArea" | "comboBox" | "cell"
            )
}

impl Summary {
    fn label_children<'a>(&self, projection: &'a Projection, node: &ProjectionNode) -> Vec<&'a ProjectionNode> {
        node.children
            .iter()
            .filter_map(|id| projection.node(*id))
            .filter(|child| role(*child) != "row")
            .collect()
    }

    fn selected_label(&self, projection: &Projection, id: NodeId) -> Result<String> {
        let mut label = self
            .labels
            .get(&id)
            .ok_or(TextError::Invalid("selection summary reference"))?
            .clone();
        let mut node = projection.node(id).ok_or(TextError::Invalid("selected node"))?;
        while !has_summary_label(node) {
            let children = self.label_children(projection, node);
            if let [child] = children.as_slice() {
                node = child;
            } else if children.len() > 1 {
                let mut pending: Vec<_> = children.into_iter().rev().collect();
                let mut labelled = Vec::new();
                while let Some(child) = pending.pop() {
                    if has_summary_label(child) {
                        labelled.push(
                            self.labels
                                .get(&child.id)
                                .ok_or(TextError::Invalid("selection summary child"))?
                                .as_str(),
                        );
                    } else {
                        pending.extend(self.label_children(projection, child).into_iter().rev());
                    }
                }
                if !labelled.is_empty() {
                    label.push_str("; contents: ");
                    label.push_str(&labelled[..labelled.len().min(3)].join("; "));
                    if labelled.len() > 3 {
                        label.push_str(&format!("; {} more labelled contents (in tree)", labelled.len() - 3));
                    }
                }
                return Ok(label);
            } else {
                return Ok(label);
            }
        }
        if node.id != id {
            let child = self
                .labels
                .get(&node.id)
                .ok_or(TextError::Invalid("selection summary label"))?;
            label.push_str(&format!("; label: {child}"));
        }
        Ok(label)
    }

    fn focus_summary(&self, projection: &Projection, options: &LineOptions<'_>, lines: &mut Vec<String>) -> Result<()> {
        let focused = options.focused.and_then(|reference| projection.by_reference(reference));
        if let Some(node) = focused.filter(|node| self.focus.contains_key(&node.id)) {
            let mut line = format!("\nReported focus: {}", self.labels[&node.id]);
            if node.native_fields.value("selectionRange").is_some()
                && !(options.compact
                    && options
                        .summarized_selections
                        .is_some_and(|refs| refs.contains(&node.reference)))
            {
                let (start, length) = span(&node.native_fields, "selectionRange")?;
                if length == 0 {
                    line.push_str(&format!("; text selection: empty at UTF-16 offset {start}"));
                } else {
                    line.push_str(&format!("; text selection: {length} UTF-16 units at offset {start}"));
                }
            }
            let descendants: Vec<_> = self
                .selected_descendants
                .iter()
                .filter_map(|id| self.focus.get(id))
                .map(String::as_str)
                .collect();
            if !descendants.is_empty() {
                line.push_str(". Text selection in descendants (not exact focus): ");
                line.push_str(&descendants[..descendants.len().min(3)].join("; "));
                if descendants.len() > 3 {
                    line.push_str(&format!("; {} more selected text controls", descendants.len() - 3));
                }
            }
            lines.push(line);
        } else if let Some(owner) = focused.and_then(|node| {
            projection
                .folded_into
                .iter()
                .find(|(source, owner)| *source == node.id && self.focus.contains_key(owner))
                .and_then(|(_, owner)| projection.node(*owner))
        }) {
            lines.push(format!("\nReported focus: inside {}", owner.reference));
        } else if options.focused.is_some() {
            let ancestor = options.focus_ancestors.iter().find_map(|reference| {
                projection
                    .by_reference(reference)
                    .and_then(|node| self.focus.get(&node.id))
            });
            lines.push(ancestor.map_or_else(
                || "\nReported focus: outside the expanded tree.".to_owned(),
                |ancestor| format!("\nReported focus: inside {ancestor} (native child is not in the expanded tree)."),
            ));
        } else if !self.reported_focus.is_empty() {
            lines.push("\nReported focus: unavailable.".to_owned());
        }
        let mut others = Vec::new();
        for node in self.reported_focus.iter().filter_map(|id| projection.node(*id)) {
            if options.focused != Some(&node.reference) && !others.contains(&node.reference) {
                others.push(node.reference.clone());
            }
        }
        for (source, owner) in &projection.folded_into {
            let Some(source) = projection.node(*source) else {
                continue;
            };
            if options.focused == Some(&source.reference)
                || !source.native_fields.flag("focused")
                || !self.focus.contains_key(owner)
            {
                continue;
            }
            let owner = projection
                .node(*owner)
                .ok_or(TextError::Invalid("reported focus owner"))?;
            let description = format!("inside {}", owner.reference);
            if !others.contains(&description) {
                others.push(description);
            }
        }
        if !others.is_empty() {
            let mut line = format!("Focus flags: {}", others[..others.len().min(8)].join(", "));
            if others.len() > 8 {
                line.push_str(&format!("; {} more", others.len() - 8));
            }
            line.push_str(" (node reports; not confirmed input focus).");
            lines.push(line);
        }
        Ok(())
    }
}

fn app_headings(projection: &Projection, options: &LineOptions<'_>, lines: &mut Vec<String>) -> Result<()> {
    let Some(app) = options.app else { return Ok(()) };
    let mut heading = format!("App: {}.", field(app));
    let windows: Vec<_> = projection
        .roots
        .iter()
        .filter_map(|id| projection.node(*id))
        .filter(|node| matches!(role(*node), "window" | "sheet"))
        .collect();
    let mut omitted = 0;
    if let [window] = windows.as_slice() {
        if let Some(name) = window.text("name").filter(|name| !name.is_empty()) {
            let part = shortened(name, 500)?;
            omitted = part.omitted;
            heading = format!("Window: {}, {heading}", quoted(&(part.head + &part.tail)));
        }
    }
    lines.push(heading);
    lines.push(if let Some(subtree) = options.subtree {
        format!("Tree: full (subtree {subtree} only; ancestors and other window contents not expanded)")
    } else if options.menu_query {
        "Tree: full (native menu query)".to_owned()
    } else {
        "Tree: full (current UI only)".to_owned()
    });
    let mut interaction = Vec::new();
    let mut pending: Vec<_> = projection.roots.iter().rev().map(|id| (*id, None)).collect();
    while let Some((id, owner)) = pending.pop() {
        let node = projection.node(id).ok_or(TextError::Invalid("modal context node"))?;
        if node.flag("modal") && !hidden(node) {
            interaction.push(match owner {
                None => format!("{} modal (owner in window relations)", node.reference),
                Some(owner) => format!("{} modal in window {owner}", node.reference),
            });
        }
        let owner = if matches!(role(node), "window" | "sheet") {
            Some(node.reference.as_str())
        } else {
            owner
        };
        pending.extend(node.children.iter().rev().map(|id| (*id, owner)));
    }
    if !interaction.is_empty() {
        lines.push(format!("Interaction: {}", interaction.join("; ")));
    }
    if omitted != 0 {
        lines.push(format!("Window title omitted: {omitted} characters"));
    }
    Ok(())
}

struct Visitor<'a> {
    projection: &'a Projection,
    options: &'a LineOptions<'a>,
    seed: LineSeed,
    summary: Summary,
    claims: ClaimLedger,
    share_index_basis: bool,
}

struct VisitFrame<'a> {
    source: &'a ProjectionNode,
    output: LayoutNode,
    child_help: Option<String>,
    layout: Vec<(String, String)>,
    depth: usize,
    in_menu: bool,
    next_child: usize,
}

impl<'a> Visitor<'a> {
    fn enter(
        &mut self,
        id: NodeId,
        depth: usize,
        parent: Option<&ProjectionNode>,
        help: Option<&str>,
        in_menu: bool,
        layout: Vec<(String, String)>,
    ) -> Result<VisitFrame<'a>> {
        let projection = self.projection;
        let node = projection.node(id).ok_or(TextError::Invalid("rendered node"))?;
        let in_menu = in_menu || MENU_ROLES.contains(&role(node));
        let line = node_line(
            projection,
            node,
            self.options,
            depth,
            layout.clone(),
            parent,
            help,
            self.share_index_basis,
            LineClaims(self.options.record_fields.then_some(&mut self.claims)),
        )?;
        if let Some(fields) = line.empty_value_slots {
            self.seed.empty_value_slots.insert(node.reference.clone(), fields);
        }
        if self.options.compact {
            let (_, _, pairs) = actions(node, self.options)?;
            if !pairs.is_empty() {
                self.seed.inline_actions.insert(node.reference.clone(), pairs);
            }
        }
        if self.options.compression_evidence.is_some() {
            self.seed.url_order.push(id);
            self.seed.url_parents.insert(id, parent.map(|parent| parent.id));
            if !line.captions.is_empty() { self.seed.url_captions.insert(id, line.captions); }
            if let Some(slot) = line.url_slot {
                self.seed.url_candidates.push(id);
                self.seed.url_slots.insert(id, (self.seed.lines.len(), self.seed.section.nodes.len(), slot));
            }
        }
        if node.flag("selected") {
            if in_menu {
                self.summary.selected_menus.push(id);
            } else {
                self.summary.selected.push(id);
            }
        }
        if node.flag("focused") {
            self.summary.reported_focus.push(id);
        }
        if !line.selected_descendants.is_empty() {
            self.summary.selected_descendants = line.selected_descendants;
        }
        if let Some(count) = line.deferred_menu {
            if let Some((_, refs)) = self
                .summary
                .collapsed_menus
                .iter_mut()
                .find(|(total, _)| *total == count)
            {
                refs.push(node.reference.clone());
            } else {
                self.summary.collapsed_menus.push((count, vec![node.reference.clone()]));
            }
        }
        if matches!(role(node), "group" | "cell")
            && node.text("name").is_none_or(str::is_empty)
            && node.text("description").is_none_or(str::is_empty)
            && node.children.len() > 1
            && node.children.iter().any(|id| {
                projection
                    .node(*id)
                    .is_some_and(|node| matches!(role(node), "staticText" | "heading"))
            })
        {
            let related = self.summary.context.entry(node.reference.clone()).or_default();
            for child in &node.children {
                let reference = &projection
                    .node(*child)
                    .ok_or(TextError::Invalid("related child"))?
                    .reference;
                if !related.contains(reference) {
                    related.push(reference.clone());
                }
            }
        }
        for target in text_items(node.seq("labelTargets"))? {
            self.summary
                .context
                .entry(target.to_owned())
                .or_default()
                .push(node.reference.clone());
        }
        self.summary.labels.insert(id, line.summary);
        self.summary.focus.insert(id, line.focus);
        self.seed.node_prefixes.insert(node.reference.clone(), line.node_prefix);
        let start = self.seed.lines.len();
        if self.options.compact && self.options.public_api && line.rendered.partial {
            if let Some(hint) = line
                .lines
                .iter()
                .enumerate()
                .skip(1)
                .find(|(_, line)| line.trim_start_matches('\t').starts_with("Children: "))
                .and_then(|(index, line)| child_evidence(projection, node, start + index, line))
            {
                self.seed.child_hints.push(hint);
            }
        }
        self.seed.lines.extend(line.lines);
        self.seed
            .node_ranges
            .insert(node.reference.clone(), start..self.seed.lines.len());
        self.seed.section.nodes.push(line.rendered);
        let incomplete = node.value("partialReason").is_some()
            || node.value("nextChildOffset").is_some()
            || node.value("childrenStart").is_some()
            || node.value("childrenEnd").is_some()
            || !node.seq("childOmissions").is_empty()
            || node
                .num("childCount")
                .is_some_and(|count| node.num("childrenRead").unwrap_or(node.children.len() as i64) < count);
        let mut output = LayoutNode::new(
            node.reference.clone(),
            role(node).to_owned(),
            Vec::with_capacity(node.children.len()),
        );
        output.subrole = node.text("subrole").map(str::to_owned);
        output.incomplete_children = incomplete;
        output.menu_children = menu_children(node);
        output.ordinary_control = ordinary_control(node);
        Ok(VisitFrame {
            source: node,
            output,
            child_help: line.child_help,
            layout,
            depth,
            in_menu,
            next_child: 0,
        })
    }

    fn visit(&mut self, root: NodeId) -> Result<LayoutNode> {
        let mut pending = vec![self.enter(root, 0, None, None, false, Vec::new())?];
        loop {
            let parent = pending.last_mut().expect("render root remains on the stack");
            if let Some(&child) = parent.source.children.get(parent.next_child) {
                // Emit the heading at entry, between the previous sibling and this child.
                parent.next_child += 1;
                let child_node = self.projection.node(child).ok_or(TextError::Invalid("rendered child"))?;
                let mut child_layout = parent.layout.clone();
                let depth = parent.depth + 1;
                if let Some(first) = visual_group(parent.source, child_node)? {
                    let heading = format!("{}Visual row:", "\t".repeat(depth + parent.layout.len()));
                    child_layout.push((format!("{}:row:{first}", parent.source.reference), heading.clone()));
                    if child_node.reference == first {
                        self.seed.lines.push(heading);
                    }
                }
                let child = self.enter(
                    child,
                    depth,
                    Some(parent.source),
                    parent.child_help.as_deref(),
                    parent.in_menu,
                    child_layout,
                )?;
                pending.push(child);
                continue;
            }
            let completed = pending.pop().expect("active render frame").output;
            let Some(parent) = pending.last_mut() else { return Ok(completed) };
            parent.output.children.push(completed);
        }
    }
}

pub(crate) fn render_projection(
    projection: &Projection,
    options: &LineOptions<'_>,
) -> Result<LineSeed> {
    let visible = projection
        .visible_order()
        .map_err(|_| TextError::Invalid("render tree order"))?;
    let mut indices = HashSet::new();
    let mut coordinates = false;
    if options.compact && options.subtree.is_none() {
        for node in visible.iter().filter_map(|id| projection.node(*id)) {
            if let Some(index) = node.num("index") {
                indices.insert(index);
            }
            coordinates |= ["rowRange", "columnRange"].iter().any(|key| {
                node.value(key)
                    .and_then(|range| range.num("length"))
                    .is_some_and(|length| length > 0)
            });
        }
    }
    let basis = if coordinates {
        "Index is 0-based. Row/Column are 1-based."
    } else {
        "Index is 0-based."
    };
    let share_index_basis = indices.len() >= 3 && indices.len() * " (0-based)".len() > basis.len() + 1;
    let mut visitor = Visitor {
        projection,
        options,
        seed: LineSeed::default(),
        summary: Summary::default(),
        claims: ClaimLedger::default(),
        share_index_basis,
    };
    app_headings(projection, options, &mut visitor.seed.lines)?;
    if share_index_basis {
        visitor.seed.lines.push(basis.to_owned());
    }
    let tree_start = visitor.seed.lines.len();
    for root in &projection.roots {
        let layout = visitor.visit(*root)?;
        visitor.seed.roots.push(layout);
    }
    let tree_end = visitor.seed.lines.len();
    let Visitor {
        mut seed, mut summary, claims, ..
    } = visitor;
    seed.claims = claims.entries;
    for node in &mut seed.section.nodes {
        node.related = summary.context.remove(&node.reference).unwrap_or_default();
    }
    if !summary.collapsed_menus.is_empty() {
        let group = if options.concise_menu_summary {
            reference_set(
                summary
                    .collapsed_menus
                    .iter()
                    .flat_map(|(_, refs)| refs.iter().map(String::as_str)),
            )?
        } else {
            summary
                .collapsed_menus
                .iter()
                .map(|(count, refs)| {
                    Ok(format!(
                        "{} (0/{count} children)",
                        reference_set(refs.iter().map(String::as_str))?
                    ))
                })
                .collect::<Result<Vec<_>>>()?
                .join("; ")
        };
        let text = format!("Menus unexpanded: {group}.");
        seed.lines.push(text.clone());
        let entries = summary
            .collapsed_menus
            .iter()
            .flat_map(|(count, refs)| refs.iter().map(|reference| (reference.clone(), *count as u64)))
            .collect();
        seed.unexpanded_menus.push(UnexpandedMenuSummary { entries, text });
    }
    if options.app.is_some() {
        if !summary.selected.is_empty() {
            let selected = summary
                .selected
                .iter()
                .take(8)
                .map(|id| summary.selected_label(projection, *id))
                .collect::<Result<Vec<_>>>()?;
            let mut line = format!("Selected: {}", selected.join("; "));
            if summary.selected.len() > 8 {
                line.push_str(&format!(
                    "; {} more selected items (marked in tree)",
                    summary.selected.len() - 8
                ));
            }
            seed.lines.push(line);
        }
        if !summary.selected_menus.is_empty() {
            let selected: Vec<_> = summary
                .selected_menus
                .iter()
                .take(8)
                .map(|id| summary.labels[id].as_str())
                .collect();
            let mut line = format!("Selected menus: {}", selected.join("; "));
            if summary.selected_menus.len() > 8 {
                line.push_str(&format!("; {} more (marked in tree)", summary.selected_menus.len() - 8));
            }
            seed.lines.push(line);
        }
        let root = projection
            .node(projection.root)
            .ok_or(TextError::Invalid("render root"))?;
        if role(root) == "application" {
            if let Some(partial) = partial(root)? {
                seed.lines.push(format!("Tree partial: {partial}"));
            }
        }
        summary.focus_summary(projection, options, &mut seed.lines)?;
    }
    seed.section.prefix = seed.lines[..tree_start].join("\n");
    seed.section.suffix = seed.lines[tree_end..].join("\n");
    seed.section.text = seed.lines.join("\n");
    Ok(seed)
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn encoded_carriers_match_python_unicode_and_character_counts() {
        let header = "data:图像/原始;编码=🧪;Baſe64";
        let data = format!("{header},{}İıſK==", "A".repeat(124));
        assert_eq!(embedded(&data), Some((header, 130)));
        let data = format!("/图像;BASE64,{}", "A".repeat(128));
        assert_eq!(embedded(&data), Some(("/图像;BASE64", 128)));
        let header = format!("data:image/png;{};base64", "🧪".repeat(128));
        let data = format!("{header},{}", "A".repeat(128));
        assert_eq!(embedded(&data), Some((header.as_str(), 128)));

        let invalid = [
            format!("data:image/png;{};base64,{}", "🧪".repeat(129), "A".repeat(128)),
            format!("data:image/png;α\u{1f}β;base64,{}", "A".repeat(128)),
            format!("/x\u{345};base64,{}", "A".repeat(128)),
            format!("/\u{1e4d0};base64,{}", "A".repeat(128)),
            format!("/png;base64,{}", "İ".repeat(127)),
            format!("/png;base64,{}===", "A".repeat(128)),
        ];
        for data in invalid {
            assert_eq!(embedded(&data), None);
            assert_eq!(display(&data, true), field(&data));
        }
    }

    #[test]
    fn shortened_counts_extended_graphemes_after_utf16_gate() {
        let source = "👩‍👩‍👧‍👧云a\u{301}🇨🇳\r\n";
        let clipped = shortened(source, 4).unwrap();
        assert_eq!(clipped.head, "👩‍👩‍👧‍👧");
        assert_eq!(clipped.tail, "a\u{301}🇨🇳\r\n");
        assert_eq!(clipped.omitted, 1);
        assert_eq!(shortened(source, 5).unwrap().head, source);
    }
}
