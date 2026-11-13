// Copyright (c) Huawei Technologies Co., Ltd. 2026. All rights reserved.

// Own the normalized accessibility graph, its acquisition metadata, and its
// presentation fact index without crossing the Python object boundary.

mod index;
mod macos;
pub(super) mod provider;
pub(super) mod query;
mod text;

use std::collections::{HashMap, HashSet};

use super::value::Value;
use super::wire::WireError;
use super::wire_value::WireProperty;

#[derive(Debug)]
pub(crate) enum FactsError {
    Invalid(&'static str),
    Unsupported(&'static str),
    DriverQuery(&'static str),
    NormalizationOverflow(&'static str),
    NormalizationAttribute(&'static str),
    NormalizationUnhashable(&'static str),
    NormalizationKey(Value),
    NormalizedType { path: String },
    TypedValueDepth(TypedValueDepth),
    Wire(WireError),
}

#[derive(Debug, PartialEq)]
pub(crate) enum FactsFailureKind {
    Value,
    Overflow,
    Memory,
    Attribute,
    Type,
    Key(Value),
}

pub(crate) struct FactsFailure {
    pub(crate) kind: FactsFailureKind,
    pub(crate) message: String,
}

impl FactsError {
    pub(crate) fn failure(&self) -> FactsFailure {
        use FactsFailureKind::{Attribute, Key, Memory, Overflow, Type, Value};
        let (kind, message) = match self {
            Self::Invalid(detail) => (Value, format!("Native read facts: {detail}")),
            Self::Unsupported(detail) => (Value, format!("Native read facts do not support {detail}")),
            Self::DriverQuery(detail) => (Value, format!("driver observation: {detail}")),
            Self::NormalizationOverflow(detail) => (Overflow, (*detail).to_owned()),
            Self::NormalizationAttribute(kind) => (Attribute, format!("'{kind}' object has no attribute 'get'")),
            Self::NormalizationUnhashable(kind) => (Type, format!("unhashable type: '{kind}'")),
            Self::NormalizationKey(key) => (Key(key.clone()), String::new()),
            Self::NormalizedType { path } => (Value, typed_error_message(path)),
            Self::TypedValueDepth(depth) => (Value, depth.message()),
            Self::Wire(WireError::Invalid(detail) | WireError::Frame(crate::dense::Error::Invalid(detail))) => {
                (Value, (*detail).to_owned())
            }
            Self::Wire(WireError::Unsupported(detail)) => (Value, format!("Native read wire does not support {detail}")),
            Self::Wire(WireError::Syntax) => (Value, "Native read wire has invalid observation JSON".to_owned()),
            Self::Wire(WireError::Allocation | WireError::Frame(crate::dense::Error::Allocation)) => {
                (Memory, "cannot allocate native read facts".to_owned())
            }
        };
        FactsFailure { kind, message }
    }
}

fn typed_error_message(path: &str) -> String {
    format!("observation: {path}: value does not match its declared type")
}

fn source_type(value: &Value) -> &'static str {
    match value {
        Value::Null => "NoneType",
        Value::Bool(_) => "bool",
        Value::Int(_) | Value::BigInt(_) => "int",
        Value::Float(_) => "float",
        Value::Text(_) => "str",
        Value::Bytes(_) => "bytes",
        Value::Seq(_) => "list",
        Value::Map(_) => "dict",
    }
}

#[derive(Debug, PartialEq, Eq)]
pub(crate) enum TypedValueDepth {
    Supplement { node: String, index: usize },
    Alternative { issue: usize, index: usize },
}

impl TypedValueDepth {
    pub(crate) fn message(&self) -> String {
        let path = match self {
            Self::Supplement { node, index } => format!("nodes.{node}.supplements.{index}.value"),
            Self::Alternative { issue, index } => format!("issues.{issue}.alternatives.{index}"),
        };
        typed_error_message(&path)
    }
}

impl From<WireError> for FactsError {
    fn from(error: WireError) -> Self { Self::Wire(error) }
}

pub(super) type Result<T> = std::result::Result<T, FactsError>;

pub(crate) struct FactSnapshot {
    pub(crate) observation: Value,
    pub(crate) facts: Value,
    pub(crate) metadata: Value,
    pub(crate) query: Value,
}

fn text(value: impl Into<String>) -> Value { Value::Text(value.into()) }

fn map<K: Into<String>>(items: impl IntoIterator<Item = (K, Value)>) -> Value {
    Value::Map(items.into_iter().map(|(key, value)| (key.into(), value)).collect())
}

fn array(value: &Value) -> Result<&[Value]> {
    value.as_seq().ok_or(FactsError::Invalid("expected an array"))
}

fn required<'a>(record: &'a Value, key: &str) -> Result<&'a Value> {
    record.get(key).ok_or(FactsError::Invalid("missing normalized fact field"))
}

fn set(record: &mut Value, key: &str, value: Value) {
    let Value::Map(fields) = record else { unreachable!("fact builder owns an ordered map") };
    if let Some((_, current)) = fields.iter_mut().find(|(name, _)| name == key) {
        *current = value;
    } else {
        fields.push((key.to_owned(), value));
    }
}

fn take(record: &mut Value, key: &str) -> Option<Value> {
    let Value::Map(fields) = record else { return None };
    let position = fields.iter().position(|(name, _)| name == key)?;
    Some(fields.remove(position).1)
}

fn integer(value: &Value) -> Option<i64> {
    if let Value::Int(value) = value { Some(*value) } else { None }
}

fn number(value: &Value) -> Option<f64> {
    match value {
        Value::Int(value) => Some(*value as f64),
        Value::BigInt(value) => value.parse().ok(),
        Value::Float(value) => Some(*value),
        _ => None,
    }
}

fn truthy(value: &Value) -> bool {
    match value {
        Value::Null => false,
        Value::Bool(value) => *value,
        Value::Int(value) => *value != 0,
        Value::BigInt(value) | Value::Text(value) => !value.is_empty(),
        Value::Float(value) => *value != 0.0,
        Value::Bytes(value) => !value.is_empty(),
        Value::Seq(value) => !value.is_empty(),
        Value::Map(value) => !value.is_empty(),
    }
}

fn equal(a: &Value, b: &Value) -> bool {
    match (a, b) {
        (Value::Float(a), Value::Float(b)) => a == b || a.is_nan() && b.is_nan(),
        (Value::Map(a), Value::Map(b)) => a.len() == b.len()
            && a.iter().all(|(key, value)| b.iter().any(|(other, item)| key == other && equal(value, item))),
        (Value::Seq(a), Value::Seq(b)) => a.len() == b.len() && a.iter().zip(b).all(|(a, b)| equal(a, b)),
        _ => a == b,
    }
}

type Path = Vec<Value>;

fn path(names: &[&str]) -> Path { names.iter().map(|name| text(*name)).collect() }

fn append(mut path: Path, field: Value) -> Path {
    path.push(field);
    path
}

fn identifiers(value: &str) -> bool {
    let mut chars = value.chars();
    chars.next().is_some_and(|item| item == '_' || item.is_alphabetic())
        && chars.all(|item| item == '_' || item.is_alphanumeric())
}

fn json_string(value: &str, output: &mut String) {
    output.push('"');
    for item in value.chars() {
        match item {
            '"' => output.push_str("\\\""),
            '\\' => output.push_str("\\\\"),
            '\u{8}' => output.push_str("\\b"),
            '\u{c}' => output.push_str("\\f"),
            '\n' => output.push_str("\\n"),
            '\r' => output.push_str("\\r"),
            '\t' => output.push_str("\\t"),
            item if item < '\u{20}' => output.push_str(&format!("\\u{:04x}", item as u32)),
            item => output.push(item),
        }
    }
    output.push('"');
}

pub(super) fn field_name(path: &[Value]) -> String {
    let mut name = String::new();
    for part in path {
        match part {
            Value::Text(part) if identifiers(part) => {
                if !name.is_empty() { name.push('.'); }
                name.push_str(part);
            }
            Value::Text(part) => {
                name.push('[');
                json_string(part, &mut name);
                name.push(']');
            }
            Value::Int(part) => name.push_str(&format!("[{part}]")),
            Value::BigInt(part) => name.push_str(&format!("[{part}]")),
            _ => unreachable!("fact paths contain names and indices"),
        }
    }
    name
}

const NODE_FIELDS: &[&str] = &[
    "role", "role_detail", "role_description", "name", "description", "help", "identifier", "placeholder", "url",
    "control_value", "numeric_value", "text", "states", "geometry", "actions", "relations", "structure",
    "shortcuts", "supplements",
];

fn ordered(record: &Value, fields: &[&str]) -> Value {
    map(fields.iter().filter_map(|key| record.get(key).cloned().map(|value| (*key, value))))
}

fn group_order(group: &str) -> &'static [&'static str] {
    match group {
        "control_value" => &["value", "description", "read_only"],
        "numeric_value" => &["value", "minimum", "maximum", "small_step", "large_step", "description", "read_only", "allowed_values"],
        "text" => &["source", "unit", "content", "content_field", "character_count", "selected_text", "read_only",
            "runs", "selections", "caret", "caret_active", "visible_ranges", "line_ranges", "embedded", "supported_selection"],
        "states" => &["enabled", "focusable", "focused", "selectable", "selected", "editable", "protected", "busy",
            "modal", "has_popup", "active", "main_window", "visible", "showing", "offscreen", "virtualized",
            "checked", "expanded", "window_state"],
        "structure" => &["heading_level", "landmark", "landmark_description", "level", "index", "set_size",
            "selected_count", "row_count", "column_count", "row_index", "column_index", "row_span", "column_span",
            "can_select_multiple", "selection_required", "orientation", "collection_mode", "scroll_x", "scroll_y"],
        _ => &[],
    }
}

enum Extra {
    Scalar(Value),
    Element(String),
    Date(String),
    Sequence(Vec<Extra>),
    Record(Vec<(String, Extra)>),
}

enum ExtraInput<'a> {
    Ready(Option<Extra>),
    Children(ExtraFrame<'a>),
}

enum ExtraFrame<'a> {
    Sequence { items: &'a [Value], next: usize, output: Vec<Extra> },
    Record {
        items: std::slice::Iter<'a, (String, Value)>, key: &'a str, output: Vec<(String, Extra)>,
    },
}

impl<'a> ExtraFrame<'a> {
    fn next(&mut self) -> Option<(Value, &'a Value)> {
        match self {
            Self::Sequence { items, next, .. } => {
                let item = items.get(*next)?;
                let index = *next;
                *next += 1;
                Some((Value::Int(index as i64), item))
            }
            Self::Record { items, key, .. } => {
                let (name, item) = items.find(|(name, _)| name != "nativeType")?;
                *key = name;
                Some((text(name), item))
            }
        }
    }

    fn push(&mut self, item: Option<Extra>) {
        match self {
            Self::Sequence { output, .. } => output.push(item.unwrap_or_else(|| {
                Extra::Record(vec![("unavailable".into(), Extra::Scalar(Value::Bool(true)))])
            })),
            Self::Record { key, output, .. } => {
                if let Some(item) = item { output.push(((*key).to_owned(), item)); }
            }
        }
    }

    fn finish(self) -> Extra {
        match self {
            Self::Sequence { output, .. } => Extra::Sequence(output),
            Self::Record { output, .. } => Extra::Record(output),
        }
    }
}

const TYPED_OBSERVATION_DEPTH: usize = 96;
const SUPPLEMENT_VALUE_DEPTH: usize = 5;
const ISSUE_ALTERNATIVE_DEPTH: usize = 4;

struct TypedIssueValue {
    diagnostic: Value,
    exceeds_depth: bool,
}

impl Extra {
    fn scalar(&self) -> bool {
        matches!(self, Self::Scalar(_) | Self::Element(_) | Self::Date(_))
    }

    fn allowed_values(&self) -> bool {
        matches!(self, Self::Sequence(items) if items.iter().all(Self::scalar))
    }

    fn has_typed_origin(&self) -> bool {
        match self {
            Self::Element(_) | Self::Date(_) => true,
            Self::Sequence(items) => items.iter().any(Self::has_typed_origin),
            Self::Record(items) => items.iter().any(|(_, item)| item.has_typed_origin()),
            Self::Scalar(_) => false,
        }
    }

    fn diagnostic_value(&self) -> Value {
        use super::value::diagnostic;
        match self {
            Self::Scalar(value) => diagnostic::encode(value),
            Self::Element(value) => diagnostic::tagged("element", "node", value.clone()),
            Self::Date(value) => diagnostic::tagged("datetime", "value", value.clone()),
            Self::Sequence(items) => Value::Seq(items.iter().map(Self::diagnostic_value).collect()),
            Self::Record(items) => diagnostic::record(items.iter()
                .map(|(key, item)| (key.clone(), item.diagnostic_value())).collect()),
        }
    }

    fn typed_diagnostic_value(&self) -> Option<TypedIssueValue> {
        if !self.within_type_depth(ISSUE_ALTERNATIVE_DEPTH) {
            // A rejected alternative has no public diagnostic payload.
            return Some(TypedIssueValue { diagnostic: Value::Null, exceeds_depth: true });
        }
        self.has_typed_origin().then(|| TypedIssueValue {
            diagnostic: self.diagnostic_value(),
            exceeds_depth: false,
        })
    }

    /// ExtraValue is a union in the public type validator. A container uses
    /// one level for its selected branch and another for each item.
    fn within_type_depth(&self, depth: usize) -> bool {
        if depth >= TYPED_OBSERVATION_DEPTH { return false; }
        match self {
            Self::Scalar(_) | Self::Date(_) => true,
            Self::Element(_) => depth + 2 <= TYPED_OBSERVATION_DEPTH,
            Self::Sequence(items) => items.iter().all(|item| item.within_type_depth(depth + 2)),
            Self::Record(items) => items.iter().all(|(_, item)| item.within_type_depth(depth + 2)),
        }
    }

    fn into_value(self) -> Value {
        match self {
            Self::Scalar(value) => value,
            Self::Element(value) => map([("element", text(value))]),
            Self::Date(value) => map([("datetime", text(value))]),
            tree => Self::materialize(tree),
        }
    }

    fn materialize(root: Self) -> Value {
        enum Step {
            Visit(Extra),
            FinishSequence(usize),
            FinishRecord(Vec<String>),
        }

        let mut pending = vec![Step::Visit(root)];
        let mut values = Vec::new();
        while let Some(step) = pending.pop() {
            match step {
                Step::Visit(Self::Sequence(items)) => {
                    pending.push(Step::FinishSequence(items.len()));
                    pending.extend(items.into_iter().rev().map(Step::Visit));
                }
                Step::Visit(Self::Record(items)) => {
                    let finish = pending.len();
                    pending.push(Step::FinishRecord(Vec::new()));
                    let mut keys = Vec::with_capacity(items.len());
                    for (key, item) in items.into_iter().rev() {
                        keys.push(key);
                        pending.push(Step::Visit(item));
                    }
                    pending[finish] = Step::FinishRecord(keys);
                }
                Step::Visit(leaf) => values.push(leaf.into_value()),
                Step::FinishSequence(count) => {
                    let items = values.split_off(values.len() - count);
                    values.push(Value::Seq(items));
                }
                Step::FinishRecord(keys) => {
                    let items = values.split_off(values.len() - keys.len());
                    values.push(Value::Map(keys.into_iter().rev().zip(items).collect()));
                }
            }
        }
        values.pop().expect("materialized extra value")
    }
}

struct Builder<'a> {
    identity: &'a str,
    row: &'a Value,
    values: &'a Value,
    kinds: &'a HashMap<String, String>,
    capture: &'a str,
    backend: &'a str,
    properties: &'a [(usize, WireProperty<'static>)],
    catalogues: HashSet<String>,
    data: Value,
    groups: Value,
    supplements: Vec<Value>,
    actions: Vec<Value>,
    relations: Value,
    geometry: Value,
    paths: HashMap<String, Path>,
    conflicts: Vec<Path>,
    issues: Vec<Value>,
    typed_issue_values: Vec<(Path, TypedIssueValue)>,
    deep_supplement: Option<usize>,
    normalization_error: Option<FactsError>,
    field_sources: Value,
    relation_fields: Value,
    diagnostic_fields: Vec<Value>,
    text_read: bool,
    role: Option<String>,
    screen_space: bool,
}

impl<'a> Builder<'a> {
    fn new(identity: &'a str, row: &'a Value, query: &'a query::Query, capture: &'a str) -> Result<Self> {
        let values = required(row, "values")?;
        let properties = query.properties.get(identity).map_or(&[][..], Vec::as_slice);
        let mut catalogues = HashSet::new();
        for (ordinal, prop) in properties {
            let Some(key @ ("attribute_names" | "action_names" | "parameterized_attribute_names")) = prop.key.as_str()
                else { continue };
            let name = format!("toolkit::{key}#{ordinal}");
            let items = values.get(&name).and_then(Value::as_seq);
            if prop.key_namespace == "toolkit" && prop.arguments.is_empty()
                && row.get("fieldStatus").and_then(|status| status.get(&name)).is_none()
                && prop.read.get("issues").and_then(Value::as_seq).is_some_and(<[Value]>::is_empty)
                && items.is_some_and(|items| items.iter().all(|value| value.as_str().is_some_and(|text| !text.is_empty())))
            {
                catalogues.insert(name);
            }
        }
        let backend = required(&query.facts, "platform")?.as_str()
            .ok_or(FactsError::Invalid("expected platform"))?;
        Ok(Self {
            identity, row, values, capture, backend, properties, kinds: &query.kinds, catalogues,
            data: Value::Map(Vec::new()), groups: Value::Map(Vec::new()), supplements: Vec::new(),
            actions: Vec::new(), relations: Value::Map(Vec::new()), geometry: Value::Map(Vec::new()),
            paths: HashMap::new(), conflicts: Vec::new(), issues: Vec::new(), typed_issue_values: Vec::new(),
            deep_supplement: None,
            normalization_error: None,
            field_sources: Value::Map(Vec::new()),
            relation_fields: Value::Map(Vec::new()), diagnostic_fields: Vec::new(),
            text_read: false, role: None, screen_space: false,
        })
    }

    fn issue(&mut self, relative: &[Value], state: &str, detail: Option<Value>, continuation: Option<Value>,
             alternatives: Vec<Value>) {
        let state = match state { "truncated" | "complete" => "partial", "notProbed" => "not_read", state => state };
        let (state, detail) = if matches!(state,
            "not_read" | "absent" | "unsupported" | "partial" | "error" | "timeout" | "unavailable" |
            "redacted" | "stale" | "conflict")
        {
            (state, detail)
        } else {
            ("error", detail.filter(truthy).or_else(|| Some(text(state))))
        };
        let mut absolute = path(&["nodes", self.identity]);
        absolute.extend_from_slice(relative);
        let mut issue = map([("path", Value::Seq(absolute)), ("state", text(state))]);
        if let Some(detail) = detail.filter(|value| !matches!(value, Value::Null)) { set(&mut issue, "detail", detail); }
        if let Some(continuation) = continuation { set(&mut issue, "continuation", continuation); }
        set(&mut issue, "alternatives", Value::Seq(alternatives));
        self.issues.push(issue);
    }

    fn gap(&mut self, relative: &[Value], state: &str, detail: Option<&str>) {
        self.issue(relative, state, detail.map(text), None, Vec::new());
    }

    fn extra(&mut self, value: &Value, at: &[Value]) -> Option<Extra> {
        // Native dictionaries can retain a deep key as several semantic maps
        // per wire level. Walk them in provider order without using the worker
        // stack so conversion and issue order precede typed-depth validation.
        let mut pending = match self.extra_input(value, at) {
            ExtraInput::Ready(item) => return item,
            children => children,
        };
        let mut frames = Vec::new();
        let mut at = at.to_vec();
        'visit: loop {
            let mut converted = match pending {
                ExtraInput::Ready(item) => item,
                ExtraInput::Children(mut frame) => {
                    if let Some((key, child)) = frame.next() {
                        at.push(key);
                        frames.push(frame);
                        pending = self.extra_input(child, &at);
                        continue 'visit;
                    }
                    Some(frame.finish())
                }
            };
            while let Some(frame) = frames.last_mut() {
                at.pop();
                frame.push(converted);
                if let Some((key, child)) = frame.next() {
                    at.push(key);
                    pending = self.extra_input(child, &at);
                    continue 'visit;
                }
                converted = frames.pop().map(ExtraFrame::finish);
            }
            return converted;
        }
    }

    fn extra_input<'v>(&mut self, value: &'v Value, at: &[Value]) -> ExtraInput<'v> {
        use ExtraInput::{Children, Ready};
        let converted = match value {
            Value::Null => {
                self.gap(at, "absent", None);
                None
            }
            Value::Text(_) | Value::Bool(_) | Value::Int(_) | Value::BigInt(_) | Value::Float(_) => Some(Extra::Scalar(value.clone())),
            Value::Seq(items) => return Children(ExtraFrame::Sequence {
                items, next: 0, output: Vec::with_capacity(items.len()),
            }),
            Value::Map(fields) => {
                let native = value.get("nativeType");
                if let Some(reference) = value.get("node").filter(|_| fields.len() == 2 && native.is_some()) {
                    return Ready(self.extra_reference(reference, at));
                }
                if native.and_then(Value::as_str) == Some("CFDate") {
                    if let Some((time, seconds)) = value.get("absoluteTime").and_then(|time| number(time).map(|seconds| (time, seconds))) {
                        if let Some(date) = macos::cf_date(seconds) {
                            return Ready(Some(Extra::Date(date)));
                        }
                        self.gap(at, "error", Some("Date cannot be represented by a timezone-aware datetime."));
                        return Ready(Some(Extra::Record(vec![(
                            "date_seconds_since_2001".into(), Extra::Scalar(time.clone())
                        )])));
                    }
                }
                if native.is_some() {
                    let special = match value.get("value").and_then(Value::as_str) {
                        Some("NaN") => Some(f64::NAN),
                        Some("+Infinity") => Some(f64::INFINITY),
                        Some("-Infinity") => Some(f64::NEG_INFINITY),
                        _ => None,
                    };
                    if let Some(special) = special { return Ready(Some(Extra::Scalar(Value::Float(special)))); }
                    if value.get("base64").is_some() {
                        self.gap(at, "unavailable", Some("Binary provider content has no registered semantic decoder."));
                        return Ready(None);
                    }
                }
                if fields.len() == 1 {
                    if let Some(issue) = value.get("issue") {
                        self.extra_issue(issue, at);
                        return Ready(None);
                    }
                }
                return Children(ExtraFrame::Record { items: fields.iter(), key: "", output: Vec::new() });
            }
            _ => {
                self.gap(at, "error", Some("Unrecognized provider value."));
                None
            }
        };
        Ready(converted)
    }

    fn extra_reference(&mut self, reference: &Value, at: &[Value]) -> Option<Extra> {
        match reference.as_str().and_then(|token| self.kinds.get(token).map(|kind| (token, kind.as_str()))) {
            Some((token, "element")) => return Some(Extra::Element(token.to_owned())),
            Some(_) => self.gap(at, "unavailable", Some("Native resource has no supported public observation query.")),
            None => {
                let error = if matches!(reference, Value::Seq(_) | Value::Map(_)) {
                    FactsError::NormalizationUnhashable(source_type(reference))
                } else {
                    FactsError::NormalizationKey(reference.clone())
                };
                self.normalization_error.get_or_insert(error);
            }
        }
        None
    }

    fn extra_issue(&mut self, issue: &Value, at: &[Value]) {
        if issue.as_map().is_none() {
            self.normalization_error.get_or_insert(FactsError::NormalizationAttribute(source_type(issue)));
            return;
        }
        let detail = issue.get("detail").cloned();
        match issue.get("reason") {
            None => self.issue(at, "unavailable", detail, None, Vec::new()),
            Some(Value::Text(state)) => self.issue(at, state, detail, None, Vec::new()),
            Some(state @ (Value::Seq(_) | Value::Map(_))) => {
                self.normalization_error.get_or_insert(FactsError::NormalizationUnhashable(source_type(state)));
            }
            Some(state) => self.issue(at, "error", detail.filter(truthy).or_else(|| Some(state.clone())), None, Vec::new()),
        }
    }

    fn supplement(&mut self, provider: &str, value: &Value, semantic: Option<&str>, diagnostic: bool) {
        let key = semantic.map_or_else(|| format!("provider.{}.{provider}", self.backend), str::to_owned);
        let location = vec![text("supplements"), Value::Int(self.supplements.len() as i64)];
        let at = append(location.clone(), text("value"));
        let converted = self.extra(value, &at);
        self.paths.insert(provider.to_owned(), at);
        if let Some(converted) = converted {
            if self.deep_supplement.is_none() && !converted.within_type_depth(SUPPLEMENT_VALUE_DEPTH) {
                self.deep_supplement = Some(self.supplements.len());
            }
            if diagnostic || self.catalogues.contains(provider) { self.diagnostic_fields.push(Value::Seq(location)); }
            self.supplements.push(map([("key", text(key)), ("value", converted.into_value())]));
        }
    }

    fn source(&mut self, provider: &str, at: &[Value]) {
        self.paths.insert(provider.to_owned(), at.to_vec());
        set(&mut self.field_sources, &field_name(at), text(provider));
    }

    fn group(&mut self, name: &str) -> &mut Value {
        if self.groups.get(name).is_none() { set(&mut self.groups, name, Value::Map(Vec::new())); }
        let Value::Map(groups) = &mut self.groups else { unreachable!() };
        &mut groups.iter_mut().find(|(key, _)| key == name).expect("new group was stored").1
    }

    fn put(&mut self, provider: &str, keys: &[&str], value: Value, expected: fn(&Value) -> bool) {
        self.put_with_typed_origin(provider, keys, value, expected, None);
    }

    fn put_with_typed_origin(
        &mut self, provider: &str, keys: &[&str], value: Value, expected: fn(&Value) -> bool,
        typed: Option<TypedIssueValue>,
    ) {
        let at = path(keys);
        self.source(provider, &at);
        if matches!(value, Value::Null) {
            self.gap(&at, "absent", None);
            return;
        }
        if !expected(&value) {
            self.gap(&at, "error", Some("Provider value does not satisfy the public field type."));
            self.supplement(provider, &value, None, false);
            return;
        }
        let Some(key) = keys.last() else { return };
        let conflicted = self.conflicts.contains(&at);
        let target = if keys.len() == 1 { &mut self.data } else { self.group(keys[0]) };
        if target.get(key).is_some_and(|old| !equal(old, &value)) {
            let old = take(target, key).expect("conflicting field exists");
            self.conflicts.push(at.clone());
            let old = self.extra(&old, &at);
            let new = self.extra(&value, &at);
            let old_typed = self.typed_issue_values.iter().position(|(path, _)| path == &at)
                .map(|index| self.typed_issue_values.swap_remove(index).1);
            let deep_alternative = [(old.as_ref(), old_typed.as_ref()), (new.as_ref(), typed.as_ref())]
                .into_iter().position(|(extra, typed)| typed.map_or_else(
                    || extra.is_some_and(|extra| !extra.within_type_depth(ISSUE_ALTERNATIVE_DEPTH)),
                    |typed| typed.exceeds_depth,
                ));
            let typed_alternatives = (deep_alternative.is_none() && (old_typed.is_some() || typed.is_some())).then(|| Value::Seq(vec![
                old_typed.map(|typed| typed.diagnostic)
                    .unwrap_or_else(|| old.as_ref().map_or(Value::Null, Extra::diagnostic_value)),
                typed.map(|typed| typed.diagnostic)
                    .unwrap_or_else(|| new.as_ref().map_or(Value::Null, Extra::diagnostic_value)),
            ]));
            self.issue(&at, "conflict", Some(text("Provider declarations disagree.")), None, vec![
                old.map_or(Value::Null, Extra::into_value), new.map_or(Value::Null, Extra::into_value),
            ]);
            if let Some(typed_alternatives) = typed_alternatives {
                set(self.issues.last_mut().expect("recorded conflict"), "diagnostic_alternatives", typed_alternatives);
            }
            if let Some(index) = deep_alternative {
                set(self.issues.last_mut().expect("recorded conflict"), "deep_alternative", Value::Int(index as i64));
            }
            return;
        }
        if !conflicted {
            set(target, key, value);
            if let Some(typed) = typed {
                if let Some((_, existing)) = self.typed_issue_values.iter_mut().find(|(path, _)| path == &at) {
                    *existing = typed;
                } else {
                    self.typed_issue_values.push((at, typed));
                }
            }
        }
    }

    fn drain_issues(
        &mut self, issues: &mut Vec<Value>, alternatives: &mut Vec<(String, Value)>,
        first_deep_alternative: &mut Option<(usize, usize)>,
    ) {
        for mut issue in self.issues.drain(..) {
            if let Some(tagged) = take(&mut issue, "diagnostic_alternatives") {
                alternatives.push((issues.len().to_string(), tagged));
            }
            if let Some(Value::Int(index)) = take(&mut issue, "deep_alternative") {
                first_deep_alternative.get_or_insert((issues.len(), index as usize));
            }
            issues.push(issue);
        }
    }

    fn has_method(&self, name: &str) -> bool {
        self.properties.iter().any(|(ordinal, property)| !property.arguments.is_empty()
            && property.key.as_str().is_some_and(|key| format!("{}::{key}#{ordinal}", property.key_namespace) == name))
    }

    fn text_info(&mut self) -> &mut Value {
        if self.groups.get("text").is_none() {
            set(&mut self.groups, "text", map([
                ("source", text(format!("{}:{}:text", self.capture, self.identity))), ("unit", text(if self.backend == "linux" { "provider" } else { "utf16_code_unit" })),
            ]));
        }
        self.group("text")
    }

    fn bounds(&mut self, raw: &Value, at: &[Value]) -> Value {
        self.screen_space = true;
        let mut result = map([("space", text(format!("{}:screen", self.backend)))]);
        for key in ["x", "y", "width", "height"] {
            if let Some(value) = raw.get(key).filter(|value| !matches!(value, Value::Null)) {
                let numeric = number(value);
                if matches!(value, Value::BigInt(_)) && numeric.is_some_and(f64::is_infinite) {
                    self.normalization_error.get_or_insert(FactsError::NormalizationOverflow(
                        "int too large to convert to float",
                    ));
                }
                if numeric.is_some_and(f64::is_finite) { set(&mut result, key, value.clone()); }
                else { self.gap(&append(at.to_vec(), text(key)), "error", Some("Geometry component is not finite.")); }
            }
        }
        if let Some(value @ Value::Bool(_)) = raw.get("valid") {
            set(&mut result, "valid", value.clone());
        } else if ["x", "y", "width", "height"].iter().all(|key| result.get(key).is_some()) {
            let valid = ["width", "height"].iter().all(|key| result.get(key).and_then(number).is_some_and(|num| num >= 0.0));
            set(&mut result, "valid", Value::Bool(valid));
        }
        let residual: Vec<_> = raw.as_map().into_iter().flatten()
            .filter(|(key, _)| key == "space" || result.get(key).is_none()).cloned().collect();
        if !residual.is_empty() {
            let name = at.iter().map(|item| item.as_str().map(str::to_owned)
                .or_else(|| integer(item).map(|value| value.to_string())).unwrap_or_default()).collect::<Vec<_>>().join(".");
            self.supplement(&format!("{name}.components"), &Value::Map(residual), Some("geometry.provider_components"), false);
        }
        result
    }

    fn build(&mut self) -> Value {
        for group in ["control_value", "numeric_value", "text", "states", "structure"] {
            if let Some(value) = self.groups.get(group) {
                set(&mut self.data, group, ordered(value, group_order(group)));
            }
        }
        if self.data.get("states").is_none() { set(&mut self.data, "states", Value::Map(Vec::new())); }
        if !self.actions.is_empty() || self.row.get("actions").and_then(|actions| actions.get("state"))
            .and_then(Value::as_str) == Some("complete")
        {
            set(&mut self.data, "actions", Value::Seq(std::mem::take(&mut self.actions)));
        }
        set(&mut self.data, "geometry", std::mem::replace(&mut self.geometry, Value::Map(Vec::new())));
        set(&mut self.data, "relations", std::mem::replace(&mut self.relations, Value::Map(Vec::new())));
        set(&mut self.data, "supplements", Value::Seq(std::mem::take(&mut self.supplements)));
        ordered(&self.data, NODE_FIELDS)
    }
}

fn any(_: &Value) -> bool { true }
fn is_text(value: &Value) -> bool { value.as_str().is_some() }
fn is_bool(value: &Value) -> bool { matches!(value, Value::Bool(_)) }
fn nonnegative(value: &Value) -> bool {
    match value {
        Value::Int(value) => *value >= 0,
        Value::BigInt(value) => !value.starts_with('-'),
        _ => false,
    }
}

fn validate_issue_types(issues: &[Value], first_deep_alternative: Option<(usize, usize)>) -> Result<()> {
    // NativeObservation validates its completed nodes first, then each
    // ReadIssue's detail and ordered alternatives. Keep that precedence.
    for (issue, row) in issues.iter().enumerate() {
        if row.get("detail").is_some_and(|value| !matches!(value, Value::Null | Value::Text(_))) {
            return Err(FactsError::NormalizedType { path: format!("issues.{issue}.detail") });
        }
        for (index, value) in row.get("alternatives").and_then(Value::as_seq).unwrap_or_default().iter().enumerate() {
            if *value == Value::Null {
                return Err(FactsError::NormalizedType { path: format!("issues.{issue}.alternatives.{index}") });
            }
            if first_deep_alternative == Some((issue, index)) {
                return Err(FactsError::TypedValueDepth(TypedValueDepth::Alternative { issue, index }));
            }
        }
    }
    Ok(())
}

pub(crate) fn normalize(raw: &Value) -> Result<FactSnapshot> {
    normalize_admitted(raw, |_| Ok(()), |error| error)
}

/// Run query admission before semantic normalization, while the query facts
/// still carry the original scope and continuation rows.
pub(crate) fn normalize_admitted<E>(
    raw: &Value, admit: impl FnOnce(&Value) -> std::result::Result<(), E>, map_error: impl Fn(FactsError) -> E,
) -> std::result::Result<FactSnapshot, E> {
    let query = query::collect(raw).map_err(&map_error)?;
    admit(&query.facts)?;
    normalize_query(query).map_err(map_error)
}

pub(super) fn normalize_query(query: query::Query) -> Result<FactSnapshot> {
    let rows = array(required(&query.facts, "nodes")?)?;
    let backend = required(&query.facts, "platform")?.as_str()
        .ok_or(FactsError::Invalid("expected platform"))?;
    let mut builders = Vec::with_capacity(rows.len());
    let mut ordinals = HashMap::new();
    let observed = required(&query.facts, "observed")?;
    let capture = required(observed, "id")?.as_str().ok_or(FactsError::Invalid("expected capture identity"))?;
    let mut issues = Vec::new();
    let mut issue_alternatives = Vec::new();
    let mut first_deep_alternative = None;
    for row in rows {
        let token = required(row, "node")?.as_str().ok_or(FactsError::Invalid("expected node identity"))?;
        let mut builder = Builder::new(token, row, &query, capture)?;
        if backend == "macos" {
            macos::fields(&mut builder);
            macos::actions(&mut builder);
        } else {
            provider::fields(&mut builder);
            provider::actions(&mut builder);
        }
        text::methods(&mut builder);
        for (name, status) in required(row, "fieldStatus")?.as_map().into_iter().flatten() {
            if !provider::discarded(backend, name) { macos::status(&mut builder, name, status); }
        }
        if let Some(error) = builder.normalization_error.take() { return Err(error); }
        builder.drain_issues(&mut issues, &mut issue_alternatives, &mut first_deep_alternative);
        ordinals.insert(token, builders.len());
        builders.push(builder);
    }
    for edge in array(required(&query.facts, "relations")?)? {
        let token = required(edge, "source")?.as_str().ok_or(FactsError::Invalid("expected relation source"))?;
        let builder = ordinals.get(token).and_then(|index| builders.get_mut(*index))
            .ok_or(FactsError::Invalid("undeclared relation source"))?;
        macos::relation(builder, edge);
        if let Some(error) = builder.normalization_error.take() { return Err(error); }
        builder.drain_issues(&mut issues, &mut issue_alternatives, &mut first_deep_alternative);
    }
    let mut nodes = Value::Map(Vec::new());
    let mut spaces = Value::Map(Vec::new());
    let mut relation_fields = Value::Map(Vec::new());
    let mut requested_fields = Value::Map(Vec::new());
    let mut field_sources = Value::Map(Vec::new());
    let mut diagnostic_fields = Value::Map(Vec::new());
    let mut text_read_fields = Value::Map(Vec::new());
    for mut builder in builders {
        let token = builder.identity;
        // Type validation visits completed nodes in order before any issue.
        // A relation can append an earlier node's first invalid supplement.
        if let Some(index) = builder.deep_supplement {
            return Err(FactsError::TypedValueDepth(TypedValueDepth::Supplement { node: token.to_owned(), index }));
        }
        let node = builder.build();
        set(&mut nodes, token, node);
        if builder.screen_space && spaces.as_map().is_some_and(|entries| entries.is_empty()) {
            let (unit, origin) = match backend {
                "windows" => ("physical_pixel", "primary_screen_top_left"),
                "linux" => ("provider", "desktop_top_left"),
                _ => ("point", "primary_screen_top_left"),
            };
            set(&mut spaces, &format!("{backend}:screen"), map([
                ("unit", text(unit)), ("origin", text(origin)), ("y_direction", text("down")),
            ]));
        }
        set(&mut requested_fields, token, required(builder.row, "requestedFields")?.clone());
        if builder.relation_fields.as_map().is_some_and(|value| !value.is_empty()) {
            set(&mut relation_fields, token, builder.relation_fields);
        }
        if builder.field_sources.as_map().is_some_and(|value| !value.is_empty()) {
            set(&mut field_sources, token, builder.field_sources);
        }
        if !builder.diagnostic_fields.is_empty() {
            set(&mut diagnostic_fields, token, Value::Seq(builder.diagnostic_fields));
        }
        if builder.text_read { set(&mut text_read_fields, token, Value::Seq(path(&["text", "content"]))); }
    }
    for token in array(required(&query.facts, "unread")?)? {
        let token = token.as_str().ok_or(FactsError::Invalid("expected unread node identity"))?;
        let node = map([
            ("states", Value::Map(Vec::new())), ("geometry", Value::Map(Vec::new())),
            ("relations", Value::Map(Vec::new())), ("supplements", Value::Seq(Vec::new())),
        ]);
        set(&mut nodes, token, node);
        issues.push(map([
            ("path", Value::Seq(path(&["nodes", token]))), ("state", text("not_read")),
            ("detail", text(if query.facts.get("outsideWindowNodes").and_then(Value::as_seq)
                .is_some_and(|items| items.iter().any(|item| item.as_str() == Some(token))) {
                "Referenced element belongs to another window."
            } else {
                "Referenced element was not expanded."
            })), ("alternatives", Value::Seq(Vec::new())),
        ]));
    }
    for reason in array(required(&query.facts, "stopReasons")?)? {
        issues.push(map([
            ("path", Value::Seq(path(&["nodes"]))), ("state", text("partial")),
            ("detail", reason.clone()), ("alternatives", Value::Seq(Vec::new())),
        ]));
    }
    validate_issue_types(&issues, first_deep_alternative)?;
    let observation = map([
        ("backend", text(backend)), ("session", required(&query.facts, "session")?.clone()),
        ("id", text(capture)), ("interval_ns", query.interval.clone()),
        ("roots", required(&query.facts, "roots")?.clone()), ("nodes", nodes), ("coordinate_spaces", spaces),
        ("issues", Value::Seq(issues)),
    ]);
    let mut metadata = map([
        ("surface", required(&query.facts, "surface")?.clone()),
        ("change_token", query.facts.get("changeToken").cloned().unwrap_or(Value::Null)),
        ("relation_fields", relation_fields), ("requested_fields", requested_fields),
        ("requested_relations", required(&query.facts, "requestedRelations")?.clone()),
        ("field_sources", field_sources), ("diagnostic_fields", diagnostic_fields), ("text_read_fields", text_read_fields),
        ("focused_element", query.facts.get("focusedElement").cloned().unwrap_or(Value::Null)),
    ]);
    if !issue_alternatives.is_empty() {
        set(&mut metadata, "issue_alternatives", Value::Map(issue_alternatives));
    }
    let facts = index::build(&observation, observed)?;
    Ok(FactSnapshot { observation, facts, metadata, query: query.facts })
}

#[cfg(test)]
mod tests {
    use super::*;
    use crate::wire::decode_json;

    fn fixture() -> Value {
        decode_json(br#"{
            "kind":"observation","platform":"macos","session":"session","roots":["window"],
            "read":{"intervalNs":[1000000,2000000],"details":[
                ["observation_id",{"nativeType":"CFString","data":"capture"}],
                ["surface",{"nativeType":"CFString","data":"window"}]
            ]},
            "nodes":[
                {"id":"window","source":"macos.ax","kind":"element","nativeType":"AXUIElement","properties":[
                    {"key":["macos.ax","AXRole"],"value":{"nativeType":"CFString","data":"AXCheckBox"}},
                    {"key":["macos.ax","AXTitle"],"value":{"nativeType":"CFString","data":"Allow"}},
                    {"key":["macos.ax","AXValue"],"value":{"nativeType":"CFNumber","data":1}},
                    {"key":["macos.ax","AXDescription"],"state":"unsupported","value":null},
                    {"key":["macos.ax","AXChildren"],"value":{"nativeType":"CFArray","data":{"items":[
                        {"nativeType":"AXUIElement","data":{"ref":"child"}}
                    ]}},"read":{"complete":false,"details":[
                        ["relation",{"nativeType":"CFBoolean","data":true}],
                        ["certainty",{"nativeType":"CFString","data":"confirmed"}],
                        ["state",{"nativeType":"CFString","data":"partial"}]
                    ]}}
                ]},
                {"id":"child","source":"macos.ax","kind":"element","nativeType":"AXUIElement","properties":null}
            ]
        }"#).unwrap()
    }

    #[test]
    fn preserves_source_order_and_coverage_from_raw_macos_facts() {
        let snapshot = normalize(&fixture()).unwrap();
        let observation = &snapshot.observation;
        let window = observation.get("nodes").unwrap().get("window").unwrap();
        assert_eq!(window.get("role").and_then(Value::as_str), Some("checkbox"));
        assert_eq!(window.get("states").unwrap().get("checked").and_then(Value::as_str), Some("on"));
        let rows = snapshot.facts.get("nodes").and_then(Value::as_seq).unwrap();
        assert_eq!(rows[0].get("node").and_then(Value::as_str), Some("window"));
        assert_eq!(rows[1].get("observed"), Some(&Value::Bool(false)));
        let statuses = rows[0].get("fieldStatus").unwrap();
        assert_eq!(statuses.get("description").unwrap().get("state").and_then(Value::as_str), Some("unsupported"));
        let relations = snapshot.facts.get("relations").and_then(Value::as_seq).unwrap();
        assert_eq!(relations[0].get("targets"), Some(&Value::Seq(vec![text("child")])));
        assert_eq!(relations[0].get("state").and_then(Value::as_str), Some("partial"));
        let raw = snapshot.query.get("nodes").and_then(Value::as_seq).unwrap();
        assert_eq!(raw[0].get("values").unwrap().get("AXTitle").and_then(Value::as_str), Some("Allow"));
        let fields = snapshot.metadata.get("field_sources").unwrap().get("window").unwrap();
        assert_eq!(fields.get("control_value.value").and_then(Value::as_str), Some("AXValue"));
        assert_eq!(fields.get("states.checked").and_then(Value::as_str), Some("AXValue"));
    }

    #[test]
    fn menu_shortcuts_keep_invisible_provider_values_as_diagnostic_evidence() {
        let observation = br#"{
            "kind":"observation","platform":"macos","session":"session","roots":["menu"],
            "read":{"intervalNs":[1000000,2000000],"details":[
                ["observation_id",{"nativeType":"CFString","data":"capture"}],
                ["surface",{"nativeType":"CFString","data":"window"}]
            ]},
            "nodes":[{"id":"menu","source":"macos.ax","kind":"element","nativeType":"AXUIElement","properties":[
                {"key":["macos.ax","AXRole"],"value":{"nativeType":"CFString","data":"AXMenuItem"}},
                {"key":["macos.ax","AXMenuItemCmdChar"],"value":{"nativeType":"CFString","data":"\u200d"}},
                {"key":["macos.ax","AXMenuItemCmdModifiers"],"value":{"nativeType":"CFNumber","data":0}},
                {"key":["macos.ax","AXMenuItemCmdVirtualKey"],"value":{"nativeType":"CFNumber","data":0.0}}
            ]}]
        }"#;
        let snapshot = normalize(&decode_json(observation).unwrap()).unwrap();
        let menu = snapshot.observation.get("nodes").unwrap().get("menu").unwrap();
        assert!(menu.get("shortcuts").is_none());
        let supplements = array(menu.get("supplements").unwrap()).unwrap();
        assert_eq!(supplements.len(), 1);
        assert_eq!(supplements[0].get("key").and_then(Value::as_str),
            Some("provider.macos.shortcut.AXMenuItemCmdChar"));
        assert_eq!(supplements[0].get("value").and_then(Value::as_str), Some("\u{200d}"));
    }

    fn wire_value(native_type: &str, data: Value) -> Value {
        map([("nativeType", text(native_type)), ("data", data)])
    }

    fn nested_keys(count: usize) -> Value {
        (0..count).fold(wire_value("CFString", text("leaf")), |key, _| {
            wire_value("CFDictionary", map([("entries", Value::Seq(vec![Value::Seq(vec![
                key, wire_value("CFNull", Value::Null),
            ])]))]))
        })
    }

    fn wire_property(name: &str, value: Value) -> Value {
        map([("key", Value::Seq(path(&["macos.ax", name]))), ("value", value)])
    }

    fn append_window_properties(raw: &mut Value, more: impl IntoIterator<Item = Value>) {
        let Some(Value::Seq(nodes)) = raw.get_mut("nodes") else { unreachable!() };
        let Some(Value::Seq(properties)) = nodes[0].get_mut("properties") else { unreachable!() };
        properties.extend(more);
    }

    #[test]
    fn inline_native_dictionary_keys_normalize_on_small_sdk_worker() {
        std::thread::Builder::new().stack_size(512 * 1024).spawn(|| {
            for (depth, field) in [(66, "AXHelp"), (95, "AXHelp"), (95, "AXValue")] {
                let mut raw = fixture();
                if field == "AXValue" {
                    let Some(Value::Seq(nodes)) = raw.get_mut("nodes") else { unreachable!() };
                    let Some(Value::Seq(properties)) = nodes[0].get_mut("properties") else { unreachable!() };
                    properties.retain(|property| property.get("key") != Some(&Value::Seq(path(&["macos.ax", "AXValue"]))));
                }
                append_window_properties(&mut raw, [wire_property(field, nested_keys(depth))]);
                let Err(FactsError::TypedValueDepth(error)) = normalize(&raw) else {
                    panic!("expected typed normalization to reject the retained value")
                };
                assert_eq!(error.message(), "observation: nodes.window.supplements.0.value: value does not match its declared type");
            }
        }).unwrap().join().unwrap();
    }

    #[test]
    fn typed_value_depth_is_checked_only_where_the_graph_retains_the_value() {
        fn property(name: &str, value: Value) -> Value {
            let mut raw = fixture();
            append_window_properties(&mut raw, [wire_property(name, value)]);
            raw
        }

        let within = property("AXHelp", nested_keys(15));
        let within = normalize(&within).unwrap();
        let window = within.observation.get("nodes").unwrap().get("window").unwrap();
        assert!(array(window.get("supplements").unwrap()).unwrap().iter()
            .any(|supplement| supplement.get("key").and_then(Value::as_str) == Some("provider.macos.AXHelp")));

        let beyond = property("AXHelp", nested_keys(16));
        let mut admitted = false;
        let result = normalize_admitted(&beyond, |_| { admitted = true; Ok(()) }, |error| error);
        let Err(FactsError::TypedValueDepth(error)) = result else { panic!("expected a typed depth error") };
        assert_eq!(error.message(), "observation: nodes.window.supplements.0.value: value does not match its declared type");
        assert!(admitted);

        let mut discarded = property("AXPID", nested_keys(16));
        set(&mut discarded, "metadataValues", Value::Seq(vec![nested_keys(16)]));
        let discarded = normalize(&discarded).unwrap();
        let window = discarded.observation.get("nodes").unwrap().get("window").unwrap();
        assert!(array(window.get("supplements").unwrap()).unwrap().is_empty());
    }

    #[test]
    fn native_failure_matches_geometry_conversion_before_typed_validation() {
        let mut raw = fixture();
        let huge = format!("1{}", "0".repeat(400));
        let point = wire_value("AXValue.CGPoint", map([("fields", Value::Seq(vec![
            Value::Seq(vec![text("x"), wire_value("CFNumber", map([("integer", text(huge))]))]),
            Value::Seq(vec![text("y"), wire_value("CFNumber", Value::Int(0))]),
        ]))]));
        append_window_properties(&mut raw, [
            wire_property("AXHelp", nested_keys(16)),
            wire_property("AXPosition", point),
        ]);
        let Err(error) = normalize(&raw) else { panic!("expected geometry conversion to fail") };
        let failure = error.failure();
        assert_eq!(failure.kind, FactsFailureKind::Overflow);
        assert_eq!(failure.message, "int too large to convert to float");
    }

    #[test]
    fn invalid_issue_detail_precedes_that_issues_alternative_depth() {
        let issue = map([
            ("detail", Value::Bool(false)),
            ("alternatives", Value::Seq(vec![text("deep")])),
        ]);
        let error = validate_issue_types(&[issue], Some((0, 0))).unwrap_err();
        let failure = error.failure();
        assert_eq!(failure.kind, FactsFailureKind::Value);
        assert_eq!(failure.message, "observation: issues.0.detail: value does not match its declared type");
    }

    #[test]
    fn native_selection_range_preserves_the_sum_of_two_cfindex_integers() {
        let mut raw = fixture();
        let start = i64::MAX - 9;
        let length = 20;
        let fields = [("location", start), ("length", length)].into_iter().map(|(name, value)| {
            Value::Seq(vec![text(name), wire_value("CFIndex", map([("integer", text(value.to_string()))]))])
        });
        let range = wire_value("AXValue.CFRange", map([("fields", Value::Seq(fields.collect()))]));
        append_window_properties(&mut raw, [wire_property("AXSelectedTextRange", range)]);
        let snapshot = normalize(&raw).unwrap();
        let node = snapshot.observation.get("nodes").unwrap().get("window").unwrap();
        let spans = array(node.get("text").unwrap().get("selections").unwrap()).unwrap();
        assert_eq!(spans[0].get("start"), Some(&Value::Int(start)));
        assert_eq!(spans[0].get("end"), Some(&Value::BigInt("9223372036854775818".to_owned())));
        assert_eq!(spans[0].get("collapsed"), Some(&Value::Bool(false)));
    }

    #[test]
    fn typed_depth_error_visits_earlier_nodes_after_relation_supplements_are_complete() {
        let mut raw = fixture();
        let Some(Value::Seq(nodes)) = raw.get_mut("nodes") else { unreachable!() };
        let Some(Value::Seq(properties)) = nodes[0].get_mut("properties") else { unreachable!() };
        properties.push(wire_property("AXPlain", wire_value("CFString", text("prior"))));
        let mut relation = wire_property("AXVisibleRows", wire_value("CFArray", map([
            ("items", Value::Seq(Vec::new())),
        ])));
        let details = [
            ("relation", wire_value("CFBoolean", Value::Bool(true))),
            ("state", wire_value("CFString", text("complete"))),
            ("certainty", wire_value("CFString", text("confirmed"))),
            ("detail", nested_keys(16)),
        ];
        set(&mut relation, "read", map([
            ("complete", Value::Bool(true)),
            ("details", Value::Seq(details.into_iter()
                .map(|(key, value)| Value::Seq(vec![text(key), value])).collect())),
        ]));
        properties.push(relation);
        set(&mut nodes[1], "properties", Value::Seq(vec![
            wire_property("AXRole", wire_value("CFString", text("AXButton"))),
            wire_property("AXHelp", nested_keys(16)),
        ]));
        let Err(FactsError::TypedValueDepth(error)) = normalize(&raw) else { panic!("expected a typed depth error") };
        assert_eq!(error.message(), "observation: nodes.window.supplements.1.value: value does not match its declared type");
    }

    #[test]
    fn typed_depth_alternative_uses_the_normalized_issue_and_value_ordinals() {
        let raw = fixture();
        let query = query::collect(&raw).unwrap();
        let row = &array(query.facts.get("nodes").unwrap()).unwrap()[0];
        let mut builder = Builder::new("window", row, &query, "capture").unwrap();
        let deep = (0..46).fold(text("leaf"), |item, _| Value::Seq(vec![item]));
        builder.put("AXBefore", &["control_value", "value"], text("safe"), any);
        builder.put("AXAfter", &["control_value", "value"], deep, any);
        let mut issues = vec![map([("path", Value::Seq(Vec::new())), ("state", text("partial"))])];
        let mut first = None;
        builder.drain_issues(&mut issues, &mut Vec::new(), &mut first);
        let (issue, index) = first.unwrap();
        let error = TypedValueDepth::Alternative { issue, index };
        assert_eq!(error.message(), "observation: issues.1.alternatives.1: value does not match its declared type");
    }

    #[test]
    fn issue_diagnostics_preserve_typed_origin_and_original_issue_ordinal() {
        use crate::render::details::DetailSource;

        let raw = fixture();
        let query = query::collect(&raw).unwrap();
        let row = &array(query.facts.get("nodes").unwrap()).unwrap()[0];
        let mut builder = Builder::new("window", row, &query, "capture").unwrap();
        let value = Extra::Sequence(vec![
            Extra::Element("child".to_owned()),
            Extra::Date("2001-01-01T00:00:00+00:00".to_owned()),
            Extra::Scalar(Value::Float(-0.0)),
            Extra::Scalar(Value::Float(f64::INFINITY)),
            Extra::Record(vec![("$type".to_owned(), Extra::Scalar(text("ordinary provider key")))]),
        ]);
        let typed = value.typed_diagnostic_value();
        builder.issue(&path(&["text", "content"]), "partial", None, Some(map([
            ("token", text("text-reference")), ("kind", text("text")), ("consistency", text("captured")),
            ("unit", text("utf16_code_unit")), ("total", Value::BigInt("18446744073709551616".to_owned())),
            ("delivered", Value::Seq(vec![Value::Int(0), Value::Int(2)])),
        ])), Vec::new());
        builder.put_with_typed_origin("AXAllowedValues", &["numeric_value", "allowed_values"], value.into_value(), any, typed);
        builder.put("AXOther", &["numeric_value", "allowed_values"], Value::Int(9_007_199_254_740_992), any);
        let mut issues = vec![map([("path", Value::Seq(Vec::new())), ("state", text("partial"))])];
        let mut alternatives = Vec::new();
        builder.drain_issues(&mut issues, &mut alternatives, &mut None);
        let alternatives = Value::Map(alternatives);
        assert!(alternatives.get("1").is_none());
        assert_eq!(issues[2].get("diagnostic_alternatives"), None);
        let captured = normalize(&raw).unwrap();
        let detail = DetailSource::new("macos", "session", "capture", &[], &captured.facts, &issues,
            [("0", "window"), ("1", "child")]).unwrap().with_typed_alternatives(Some(&alternatives));
        let (all, records) = detail.read_issues(None).unwrap();
        let (_, scoped) = detail.read_issues(Some("window")).unwrap();
        assert_eq!(scoped, records);
        assert!(all.contains(r#""total":{"$type":"integer","value":"18446744073709551616"}"#));
        assert!(all.contains(concat!(
            r#""alternatives":[[{"$type":"element","node":"child"},{"$type":"datetime","value":"2001-01-01T00:00:00+00:00"},"#,
            r#"{"$type":"float","value":"-0"},{"$type":"float","value":"inf"},{"$type":"map","entries":[["$type","ordinary provider key"]]}],"#,
            r#"{"$type":"integer","value":"9007199254740992"}]"#,
        )));
    }
}
