// Copyright (c) Huawei Technologies Co., Ltd. 2026. All rights reserved.

//! Choose exact captured-text excerpts and retain their original detail route.

use std::collections::{HashMap, HashSet};

use super::{facts::FactSnapshot, numbering::NodeId, projection::Projection, value::Value};

pub(crate) mod coverage;

const TEXT_FIELDS: [&str; 5] = ["name", "description", "value", "controlValue", "documentText"];
const EDITING_PATHS: [&str; 8] = [
    "selections", "caret", "caret_active", "selected_text", "unit", "source", "content_field", "character_count",
];

#[derive(Clone, Copy, Debug)]
pub(crate) struct DisclosurePolicy {
    pub(crate) minimum_characters: usize,
    pub(crate) head_characters: usize,
    pub(crate) tail_characters: usize,
    pub(crate) minimum_saved_units: usize,
    pub(crate) minimum_saved_fraction: f64,
    pub(crate) notice_characters: usize,
}

impl Default for DisclosurePolicy {
    fn default() -> Self {
        Self {
            minimum_characters: 2048,
            head_characters: 320,
            tail_characters: 160,
            minimum_saved_units: 256,
            minimum_saved_fraction: 0.2,
            notice_characters: 160,
        }
    }
}

#[derive(Debug, Eq, PartialEq)]
pub(crate) enum DisclosureError {
    InvalidPolicy,
    MissingCapture,
    InvalidProjection,
    CompetingSource,
}

#[derive(Debug, Eq, PartialEq)]
pub(crate) struct CapturedTextField {
    pub(crate) reference: String,
    pub(crate) attribute: String,
    pub(crate) native_field: String,
    pub(crate) value: String,
    pub(crate) native_token: String,
    pub(crate) capture: [String; 3],
    pub(crate) required_ranges: Vec<(usize, usize)>,
}

#[derive(Debug, Eq, PartialEq)]
pub(crate) struct TextFragment {
    pub(crate) start: usize,
    pub(crate) end: usize,
    pub(crate) text: String,
}

#[derive(Debug, Eq, PartialEq)]
pub(crate) struct TextExcerpt {
    pub(crate) source: CapturedTextField,
    pub(crate) fragments: Vec<TextFragment>,
    pub(crate) total: usize,
    pub(crate) shown: usize,
    pub(crate) estimated_saved_units: usize,
}

impl TextExcerpt {
    pub(crate) fn omitted(&self) -> usize { self.total - self.shown }
}

#[derive(Debug, Default)]
pub(crate) struct DisclosurePlan {
    entries: Vec<(NodeId, TextExcerpt)>,
    index: HashMap<NodeId, Vec<usize>>,
}

impl DisclosurePlan {
    pub(crate) fn get(&self, id: NodeId, attribute: &str) -> Option<&TextExcerpt> {
        self.index.get(&id)?.iter().map(|index| &self.entries[*index].1)
            .find(|excerpt| excerpt.source.attribute == attribute)
    }

    pub(crate) fn entries(&self) -> impl Iterator<Item = (NodeId, &TextExcerpt)> {
        self.entries.iter().map(|(id, entry)| (*id, entry))
    }

    fn add(&mut self, id: NodeId, excerpt: TextExcerpt) -> Result<(), DisclosureError> {
        if self.get(id, &excerpt.source.attribute).is_some() { return Err(DisclosureError::CompetingSource); }
        self.index.entry(id).or_default().push(self.entries.len());
        self.entries.push((id, excerpt));
        Ok(())
    }
}

fn flag(value: Option<&Value>) -> bool {
    value.and_then(Value::as_bool) == Some(true)
}

fn has_value(value: Option<&Value>) -> bool {
    match value {
        None | Some(Value::Null | Value::Bool(false) | Value::Int(0)) => false,
        Some(Value::Float(value)) => *value != 0.0,
        Some(Value::Text(value) | Value::BigInt(value)) => !value.is_empty(),
        Some(Value::Seq(value)) => !value.is_empty(),
        Some(Value::Map(value)) => !value.is_empty(),
        Some(Value::Bytes(value)) => !value.is_empty(),
        Some(_) => true,
    }
}

fn active_text(native: &Value) -> bool {
    let Some(text) = native.get("text") else { return false };
    flag(text.get("caret_active")) || has_value(text.get("selected_text"))
        || text.get("selections").and_then(Value::as_seq).is_some_and(|selections| selections.iter().any(|span|
            !flag(span.get("collapsed")) || span.get("start") != span.get("end") || has_value(span.get("text"))))
}

fn native_value<'a>(mut node: &'a Value, path: &[Value]) -> Option<&'a Value> {
    for part in path {
        node = node.get(part.as_str()?)?;
    }
    Some(node)
}

fn content_path(path: &[Value], text: &Value) -> bool {
    text.get("content_field").and_then(Value::as_seq) == Some(path)
        || matches!(path, [Value::Text(group), Value::Text(field)] if group == "text" && field == "content")
}

fn covers_reported_text(native: &Value, path: &[Value], value: &str) -> bool {
    let Some(text) = native.get("text") else { return true };
    let Some(count) = text.get("character_count").filter(|item| !matches!(item, Value::Null)) else { return true };
    if !content_path(path, text) { return true; }
    let count = count.as_i64().and_then(|count| usize::try_from(count).ok());
    match text.get("unit").and_then(Value::as_str) {
        Some("unicode_codepoint") => count == Some(value.chars().count()),
        Some("utf16_code_unit") => count == Some(value.encode_utf16().count()),
        _ => false,
    }
}

fn protected_context(projection: &Projection, reference: NodeId) -> bool {
    let mut visited = HashSet::new();
    let mut current = Some(reference);
    while let Some(id) = current {
        if !visited.insert(id) { return true; }
        let Some(node) = projection.node(id) else { return true };
        let field = |key: &str| node.native_fields.iter().find(|(name, _)| name == key).map(|(_, value)| value);
        if matches!(field("role").and_then(Value::as_str), Some("button" | "link" | "textField" | "textArea" | "comboBox" | "sheet"))
            || matches!(field("subrole").and_then(Value::as_str), Some("dialog" | "systemDialog"))
            || ["selected", "editable", "modal", "busy", "invalid"].into_iter().any(|name| flag(field(name)))
            || ["selectedText", "error"].into_iter().any(|name| has_value(field(name)))
            || field("selectionRange").and_then(|item| item.get("length")).and_then(Value::as_i64).is_some_and(|length| length > 0)
        {
            return true;
        }
        let available = |name: &str| field("capabilities").and_then(|item| item.get(name)).and_then(Value::as_str) == Some("available");
        if ["typeText", "replaceText"].into_iter().any(available)
            || id == reference && (flag(field("focused")) || available("click") || has_value(field("actions")))
        {
            return true;
        }
        current = node.parent;
    }
    false
}

fn offsets(value: &str) -> Vec<usize> {
    value.char_indices().map(|(index, _)| index).chain([value.len()]).collect()
}

fn editing_ranges(
    native: &Value,
    path: &[Value],
    value: &str,
    offsets: &[usize],
    projected: &super::projection::ProjectionNode,
) -> Option<Vec<(usize, usize)>> {
    let semantic = projected.field("selectionRange").and_then(|item| item.get("length"))
        .is_some_and(|item| !matches!(item, Value::Null | Value::Int(0) | Value::Bool(false)));
    let selected = has_value(projected.field("selectedText"));
    let Some(text) = native.get("text") else { return (!semantic && !selected).then(Vec::new) };
    let caret = text.get("caret").filter(|item| !matches!(item, Value::Null));
    let selections = text.get("selections").and_then(Value::as_seq).unwrap_or_default();
    if selections.is_empty() && caret.is_none() {
        return (!has_value(text.get("caret_active")) && !has_value(text.get("selected_text")) && !semantic && !selected)
            .then(Vec::new);
    }
    if !content_path(path, text) { return None; }
    let chars = offsets.len() - 1;
    let units = match text.get("unit").and_then(Value::as_str) {
        Some("unicode_codepoint") => None,
        Some("utf16_code_unit") => {
            let mut position = 0;
            let mut boundaries = Vec::with_capacity(chars + 1);
            boundaries.push(0);
            for character in value.chars() {
                position += character.len_utf16();
                boundaries.push(position);
            }
            Some(boundaries)
        }
        _ => return None,
    };
    let locate = |bound: &Value| {
        let position = usize::try_from(bound.as_i64()?).ok()?;
        if let Some(units) = &units { units.binary_search(&position).ok() }
        else { (position <= chars).then_some(position) }
    };
    let mut ranges = Vec::new();
    for span in selections.iter().chain(caret) {
        let start = locate(span.get("start")?)?;
        let end = locate(span.get("end")?)?;
        if start > end { return None; }
        if let Some(captured) = span.get("text").filter(|value| !matches!(value, Value::Null)) {
            if captured.as_str() != Some(&value[offsets[start]..offsets[end]]) { return None; }
        }
        let left = start.saturating_sub(80);
        let right = chars.min(end.saturating_add(80));
        if left < right { ranges.push((left, right)); }
    }
    Some(ranges)
}

fn overlap(left: &[Value], right: &[Value]) -> bool {
    left.starts_with(right) || right.starts_with(left)
}

fn editing_issues(issues: &[&[Value]]) -> bool {
    issues.iter().any(|path| {
        path.is_empty()
            || path.first().and_then(Value::as_str) == Some("text")
                && (path.len() == 1 || path.get(1).and_then(Value::as_str).is_some_and(|name| EDITING_PATHS.contains(&name)))
    })
}

fn excerpt(source: CapturedTextField, offsets: &[usize], policy: DisclosurePolicy) -> Option<TextExcerpt> {
    let chars = offsets.len() - 1;
    if chars < policy.minimum_characters || chars <= policy.head_characters.saturating_add(policy.tail_characters) {
        return None;
    }
    let mut spans = source.required_ranges.clone();
    if policy.head_characters != 0 { spans.push((0, policy.head_characters)); }
    if policy.tail_characters != 0 { spans.push((chars - policy.tail_characters, chars)); }
    spans.sort_unstable();
    let mut combined: Vec<(usize, usize)> = Vec::new();
    for (start, end) in spans {
        if start >= end || end > chars { return None; }
        if let Some((_, previous)) = combined.last_mut().filter(|(_, previous)| start <= *previous) {
            *previous = (*previous).max(end);
        } else {
            combined.push((start, end));
        }
    }
    let mut shortened_cost = policy.notice_characters.div_ceil(4);
    let mut shown = 0;
    let mut fragments = Vec::new();
    for (start, end) in combined {
        let text = &source.value[offsets[start]..offsets[end]];
        shortened_cost = shortened_cost.saturating_add(text.len().div_ceil(4));
        shown += end - start;
        fragments.push(TextFragment { start, end, text: text.to_owned() });
    }
    let full_cost = source.value.len().div_ceil(4);
    let saved = full_cost.checked_sub(shortened_cost)?;
    if saved == 0 || saved < policy.minimum_saved_units || (saved as f64) < full_cost as f64 * policy.minimum_saved_fraction
        || shown >= chars
    {
        return None;
    }
    Some(TextExcerpt { source, fragments, total: chars, shown, estimated_saved_units: saved })
}

pub(crate) fn plan(
    snapshot: &FactSnapshot,
    projection: &Projection,
    provenance: &Value,
    tokens: &[String],
    enabled: bool,
    policy: DisclosurePolicy,
) -> Result<DisclosurePlan, DisclosureError> {
    if !matches!(policy.minimum_saved_fraction, fraction if fraction > 0.0 && fraction <= 1.0)
        || policy.head_characters == 0 && policy.tail_characters == 0
    {
        return Err(DisclosureError::InvalidPolicy);
    }
    let mut plan = DisclosurePlan::default();
    if !enabled || tokens.len() != tokens.iter().collect::<HashSet<_>>().len() { return Ok(plan); }
    let capture = ["backend", "session", "id"].map(|key| snapshot.observation.get(key).and_then(Value::as_str));
    let [Some(backend), Some(session), Some(capture_id)] = capture else { return Err(DisclosureError::MissingCapture) };
    let native_nodes = snapshot.observation.get("nodes").ok_or(DisclosureError::MissingCapture)?;
    let records: HashMap<_, _> = snapshot.facts.get("nodes").and_then(Value::as_seq).into_iter().flatten()
        .filter_map(|record| record.get("node").and_then(Value::as_str).map(|token| (token, record))).collect();
    let mut fields: HashMap<&str, Vec<(&str, &Value)>> = HashMap::new();
    for row in provenance.get("fields").and_then(Value::as_seq).into_iter().flatten() {
        if let (Some(reference), Some(key), Some(value)) = (row.get("ref").and_then(Value::as_str),
            row.get("key").and_then(Value::as_str), row.get("value"))
        {
            fields.entry(reference).or_default().push((key, value));
        }
    }
    let mut labels = HashSet::new();
    for relation in snapshot.facts.get("relations").and_then(Value::as_seq).into_iter().flatten() {
        if matches!(relation.get("kind").and_then(Value::as_str),
            Some("labelled_by" | "labels" | "described_by" | "describes" | "error"))
            || matches!(relation.get("property").and_then(Value::as_str),
                Some("labelled_by" | "label_for" | "described_by" | "description_for" | "error"))
        {
            labels.extend(relation.get("source").and_then(Value::as_str));
            labels.extend(relation.get("targets").and_then(Value::as_seq).into_iter().flatten().filter_map(Value::as_str));
        }
    }
    let mut issues: HashMap<&str, Vec<&[Value]>> = HashMap::new();
    for issue in snapshot.observation.get("issues").and_then(Value::as_seq).into_iter().flatten() {
        let Some([Value::Text(head), Value::Text(token), tail @ ..]) = issue.get("path").and_then(Value::as_seq)
            else { continue };
        if head == "nodes" { issues.entry(token).or_default().push(tail); }
    }
    let visible = projection.visible_order().map_err(|_| DisclosureError::InvalidProjection)?;
    for id in &visible {
        let Some(projected) = projection.node(*id) else { continue };
        let reference = &projected.reference;
        let published = reference.parse::<u64>().is_ok_and(|number| number.to_string() == *reference);
        if !published { continue; }
        let Some(token) = tokens.get(id.0).map(String::as_str) else { continue };
        let Some(native) = native_nodes.get(token) else { continue };
        let Some(record) = records.get(token) else { continue };
        if record.get("observed") == Some(&Value::Bool(false)) { continue; }
        let role = native.get("role").and_then(Value::as_str);
        let states = native.get("states");
        let state = |key| states.and_then(|state| state.get(key));
        let passive = role == Some("static_text")
            || role == Some("document") && flag(native.get("text").and_then(|text| text.get("read_only")));
        let editor = matches!(role, Some("text_field" | "text_area" | "combo_box" | "document")) || flag(state("editable"));
        if !(passive || editor) || flag(state("protected")) { continue; }
        let label = labels.contains(token);
        let protected = label || protected_context(projection, *id) || active_text(native) || flag(state("selected"))
            || flag(state("focused")) || record.get("actions").and_then(|actions| actions.get("state")).and_then(Value::as_str) != Some("complete");
        let owned_issues = issues.get(token).map_or(&[][..], Vec::as_slice);
        for attribute in TEXT_FIELDS {
            let editing = editor && matches!(attribute, "value" | "controlValue" | "documentText");
            if !passive && !editing || if editing { label } else { protected } { continue; }
            let Some(value) = projected.field(attribute).and_then(Value::as_str) else { continue };
            if value.len() < policy.minimum_characters { continue; }
            let offsets = offsets(value);
            if offsets.len() - 1 < policy.minimum_characters { continue; }
            let matches = fields.get(reference.as_str()).into_iter().flatten().filter_map(|(key, normalized)| {
                let get_text = |name: &str| normalized.get(name).and_then(Value::as_str);
                if get_text("representation") != Some("field") || get_text("field") != Some(attribute)
                    || get_text("node") != Some(token) || get_text("normalized") != Some(value)
                    || !record.get("requestedFields").and_then(Value::as_seq).is_some_and(|keys|
                        keys.iter().any(|item| item.as_str() == Some(*key)))
                    || record.get("values").and_then(|items| items.get(key)).and_then(Value::as_str) != Some(value)
                    || record.get("fieldStatus").and_then(|items| items.get(key)).is_some_and(|status|
                        status.get("state").and_then(Value::as_str) != Some("complete"))
                {
                    return None;
                }
                let path = normalized.get("path").and_then(Value::as_seq)?;
                (!path.is_empty() && native_value(native, path).and_then(Value::as_str) == Some(value)
                    && covers_reported_text(native, path, value) && !owned_issues.iter().any(|issue| overlap(issue, path)))
                    .then_some((*key, path))
            });
            let mut matches = matches.take(2);
            let Some((key, path)) = matches.next() else { continue };
            if matches.next().is_some() { continue; }
            let required_ranges = if editing {
                if editing_issues(owned_issues) { continue; }
                let Some(ranges) = editing_ranges(native, path, value, &offsets, projected) else { continue };
                ranges
            } else {
                Vec::new()
            };
            let source = CapturedTextField {
                reference: reference.clone(), attribute: attribute.to_owned(), native_field: key.to_owned(),
                value: value.to_owned(), native_token: token.to_owned(), capture: [backend, session, capture_id].map(str::to_owned),
                required_ranges,
            };
            if let Some(excerpt) = excerpt(source, &offsets, policy) { plan.add(*id, excerpt)?; }
        }
    }
    Ok(plan)
}

#[cfg(test)]
mod tests {
    use super::*;

    fn value_map(fields: &[(&str, Value)]) -> Value {
        Value::Map(fields.iter().map(|(key, value)| ((*key).to_owned(), value.clone())).collect())
    }

    fn text(value: &str) -> Value { Value::Text(value.to_owned()) }

    #[test]
    fn complete_field_requires_one_captured_detail_proof() {
        let content = "Example character context ".repeat(170);
        let native = value_map(&[("role", text("static_text")), ("name", text(&content))]);
        let mut capture = FactSnapshot {
            observation: value_map(&[
                ("backend", text("macos")), ("session", text("session")), ("id", text("capture")),
                ("nodes", value_map(&[("native", native)])),
            ]),
            facts: value_map(&[("nodes", Value::Seq(vec![value_map(&[
                ("node", text("native")), ("observed", Value::Bool(true)),
                ("values", value_map(&[("name", text(&content))])),
                ("actions", value_map(&[("state", text("complete"))])),
                ("requestedFields", Value::Seq(vec![text("name")])),
            ])]))]),
            metadata: Value::Null,
            query: Value::Null,
        };
        let alias = HashMap::from([("0".to_owned(), NodeId(0))]);
        let projection = Projection::prepare(value_map(&[
            ("ref", text("0")), ("role", text("staticText")), ("name", text(&content)),
            ("children", Value::Seq(Vec::new())),
        ]), &alias, Default::default()).unwrap();
        let provenance = value_map(&[("fields", Value::Seq(vec![value_map(&[
            ("ref", text("0")), ("key", text("name")), ("value", value_map(&[
                ("representation", text("field")), ("field", text("name")), ("node", text("native")),
                ("normalized", text(&content)), ("path", Value::Seq(vec![text("name")])),
            ])),
        ])]))]);
        let tokens = ["native".to_owned()];
        let allowed = plan(&capture, &projection, &provenance, &tokens, true, DisclosurePolicy::default()).unwrap();
        let excerpt = allowed.get(NodeId(0), "name").unwrap();
        assert_eq!(excerpt.source.native_token, "native");
        assert_eq!(excerpt.source.native_field, "name");
        let Value::Map(observation) = &mut capture.observation else { unreachable!() };
        observation.push(("issues".to_owned(), Value::Seq(vec![value_map(&[("path", Value::Seq(vec![
            text("nodes"), text("native"), text("name"),
        ]))])])));
        let protected = plan(&capture, &projection, &provenance, &tokens, true, DisclosurePolicy::default()).unwrap();
        assert!(protected.get(NodeId(0), "name").is_none());
    }

    #[test]
    fn retains_codepoint_boundaries_and_required_utf16_context() {
        let value = "你a😀b".repeat(800);
        let offsets = offsets(&value);
        let source = CapturedTextField {
            reference: "4".into(), attribute: "value".into(), native_field: "control_value.value".into(),
            native_token: "source".into(), capture: ["macos".into(), "session".into(), "capture".into()],
            value, required_ranges: vec![(1281, 1441)],
        };
        let excerpt = excerpt(source, &offsets, DisclosurePolicy::default()).unwrap();
        assert_eq!(excerpt.total, 3200);
        assert_eq!(excerpt.shown, 640);
        assert_eq!(excerpt.fragments[1].start, 1281);
        assert_eq!(excerpt.fragments[1].end, 1441);
        assert!(excerpt.fragments[1].text.starts_with("a😀b"));
        assert_eq!(excerpt.omitted(), 2560);
    }
}

pub(crate) mod groups;
