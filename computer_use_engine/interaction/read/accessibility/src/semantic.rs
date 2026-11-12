// Copyright (c) Huawei Technologies Co., Ltd. 2026. All rights reserved.

//! Alias-dependent display roots and source accounting over acquired facts.

use std::borrow::Cow;
use std::collections::{BTreeSet, HashMap, HashSet};
use std::fmt::{self, Write};

use super::{
    facts::FactSnapshot,
    numbering::PartAliases,
    render::text::{json, JsonFormat, TextError},
    value::Value,
};

type Record = Vec<(String, Value)>;
type Result<T> = std::result::Result<T, ReadError>;

#[derive(Clone, Debug, PartialEq, Eq)]
pub(crate) struct ReadError(String);

impl fmt::Display for ReadError {
    fn fmt(&self, formatter: &mut fmt::Formatter<'_>) -> fmt::Result {
        formatter.write_str(&self.0)
    }
}

impl std::error::Error for ReadError {}

fn missing(name: &str) -> ReadError {
    ReadError(format!("semantic projection requires {name}"))
}

fn text(value: impl Into<String>) -> Value {
    Value::Text(value.into())
}

fn map(items: impl IntoIterator<Item = (impl Into<String>, Value)>) -> Value {
    Value::Map(items.into_iter().map(|(name, value)| (name.into(), value)).collect())
}

fn get<'a>(row: &'a Record, key: &str) -> Option<&'a Value> {
    row.iter().find(|(name, _)| name == key).map(|(_, value)| value)
}

fn put(row: &mut Record, key: &str, value: Value) {
    if let Some((_, current)) = row.iter_mut().find(|(name, _)| name == key) {
        *current = value;
    } else {
        row.push((key.to_owned(), value));
    }
}

fn append(row: &mut Record, key: &str, value: Value) {
    if let Some((_, Value::Seq(items))) = row.iter_mut().find(|(name, _)| name == key) {
        items.push(value);
    } else {
        put(row, key, Value::Seq(vec![value]));
    }
}

fn required<'a>(row: &'a Value, key: &str) -> Result<&'a Value> {
    row.get(key).ok_or_else(|| missing(key))
}

fn string<'a>(row: &'a Value, key: &str) -> Result<&'a str> {
    required(row, key)?.as_str().ok_or_else(|| missing(key))
}

fn seq(row: Option<&Value>) -> &[Value] {
    row.and_then(Value::as_seq).unwrap_or_default()
}

fn entries(row: Option<&Value>) -> &[(String, Value)] {
    row.and_then(Value::as_map).unwrap_or_default()
}

fn flag(value: Option<&Value>) -> bool {
    value.and_then(Value::as_bool) == Some(true)
}

fn truth(value: Option<&Value>) -> bool {
    match value {
        None | Some(Value::Null | Value::Bool(false)) => false,
        Some(Value::Int(value)) => *value != 0,
        Some(Value::BigInt(value)) => value != "0",
        Some(Value::Float(value)) => *value != 0.0,
        Some(Value::Text(value)) => !value.is_empty(),
        Some(Value::Bytes(value)) => !value.is_empty(),
        Some(Value::Seq(value)) => !value.is_empty(),
        Some(Value::Map(value)) => !value.is_empty(),
        Some(Value::Bool(true)) => true,
    }
}

fn finite(value: &Value) -> bool {
    match value {
        Value::Int(_) => true,
        Value::Float(value) => value.is_finite(),
        Value::BigInt(value) => value.parse::<f64>().is_ok_and(f64::is_finite),
        _ => false,
    }
}

fn safe_integer(value: Option<&Value>) -> Option<i64> {
    const MAX: i64 = (1 << 53) - 1;
    match value? {
        Value::Int(value) if (-MAX..=MAX).contains(value) => Some(*value),
        Value::Float(value) if value.fract() == 0.0 && (-(MAX as f64)..=MAX as f64).contains(value) => {
            Some(*value as i64)
        }
        _ => None,
    }
}

fn same_number(a: &Value, b: &Value) -> bool {
    match (a, b) {
        (Value::Int(a), Value::Int(b)) => a == b,
        (Value::BigInt(a), Value::BigInt(b)) => a == b,
        (Value::Float(a), Value::Float(b)) => a.to_bits() == b.to_bits() && a.is_finite(),
        _ => false,
    }
}

fn value_kind(name: &str) -> Option<&'static str> {
    Some(match name {
        "control_value.value" => "controlValue",
        "text.content" => "documentText",
        "numeric_value.value" => "rangeValue",
        _ => return None,
    })
}

fn known_field(name: &str) -> Option<(&'static str, bool)> {
    Some(match name {
        "role_description" => ("roleDescription", false),
        "control_value.description" => ("valueDescription", false),
        "numeric_value.description" => ("rangeDescription", false),
        "text.selected_text" => ("selectedText", false),
        "states.checked" => ("toggleState", false),
        "structure.heading_level" => ("headingLevel", true),
        "structure.landmark" => ("landmarkType", false),
        "structure.landmark_description" => ("localizedLandmarkType", false),
        "structure.level" => ("disclosureLevel", true),
        "structure.row_count" => ("rowCount", true),
        "structure.column_count" => ("columnCount", true),
        "structure.index" => ("index", true),
        "structure.selected_count" => ("selectedChildCount", true),
        _ => return None,
    })
}

fn read_only(name: &str) -> Option<&'static str> {
    Some(match name {
        "control_value.read_only" => "readOnly",
        "text.read_only" => "textReadOnly",
        "numeric_value.read_only" => "rangeReadOnly",
        _ => return None,
    })
}

pub(crate) fn presentation_role(role: Option<&str>) -> String {
    let Some(role) = role else { return "unknown".to_owned() };
    match role {
        "checkbox" => "checkBox",
        "popup_button" => "popUpButton",
        "scrollbar" => "scrollBar",
        "static_text" => "staticText",
        "tree" => "outline",
        "spinner" => "incrementor",
        "list_item" | "tree_item" => "row",
        "dialog" | "alert" => "window",
        _ => {
            // Role is an enumeration of ASCII words in the normalized model.
            let mut parts = role.split('_');
            let mut name = parts.next().unwrap_or_default().to_owned();
            for part in parts {
                let mut chars = part.chars();
                if let Some(first) = chars.next() {
                    name.extend(first.to_uppercase());
                    name.push_str(chars.as_str());
                }
            }
            return name;
        }
    }
    .to_owned()
}

pub(crate) fn literal(value: &Value) -> Result<String> {
    json(value, JsonFormat::Literal).map_err(format_error)
}

fn format_error(error: TextError) -> ReadError {
    let detail = match error {
        TextError::Invalid("nonfinite literal") => "Out of range float values are not JSON compliant",
        TextError::Unsupported("binary JSON value") => "Cannot serialize bytes",
        TextError::Invalid(detail) | TextError::Unsupported(detail) => detail,
    };
    ReadError(detail.to_owned())
}

fn one_field<'a>(value: &'a Value, key: &str) -> Option<&'a Value> {
    let [(field, value)] = value.as_map()? else { return None };
    (field == key).then_some(value)
}

fn value_text(value: &Value, aliases: &Aliases) -> Result<Value> {
    if value.as_str().is_some() {
        return Ok(value.clone());
    }
    if let Some(element) = one_field(value, "element").and_then(Value::as_str) {
        return Ok(text(format!("element {}", alias(aliases, element)?)));
    }
    if let Some(value) = one_field(value, "datetime").or_else(|| one_field(value, "number")) {
        return Ok(value.clone());
    }
    Ok(text(literal(value)?))
}

fn path_key(path: &[Value], paths: &Value) -> Result<String> {
    if let Some((key, _)) = entries(Some(paths))
        .iter()
        .find(|(_, value)| value.as_seq() == Some(path))
    {
        return Ok(key.clone());
    }
    // A selected content field can be unread and absent from the index. In
    // that case the caller merely tests membership in the indexed values.
    Ok(String::new())
}

fn overlapping(left: &[Value], right: &[Value]) -> bool {
    left.starts_with(right) || right.starts_with(left)
}

struct Source<'a> {
    token: &'a str,
    row: &'a Value,
    original: &'a Value,
    values: &'a Value,
    paths: &'a Value,
    statuses: &'a Value,
    status_paths: Vec<&'a [Value]>,
}

impl<'a> Source<'a> {
    fn new(row: &'a Value, original: &'a Value) -> Result<Self> {
        let paths = required(row, "fieldPaths")?;
        let statuses = required(row, "fieldStatus")?;
        let status_paths = entries(Some(statuses))
            .iter()
            .map(|(key, _)| required(paths, key)?.as_seq().ok_or_else(|| missing("status path")))
            .collect::<Result<_>>()?;
        Ok(Self {
            token: string(row, "node")?,
            row,
            original,
            values: required(row, "values")?,
            paths,
            statuses,
            status_paths,
        })
    }

    fn complete(&self, key: &str) -> bool {
        self.paths
            .get(key)
            .and_then(Value::as_seq)
            .is_some_and(|path| self.unrestricted(path))
    }

    fn unrestricted(&self, path: &[Value]) -> bool {
        self.status_paths.iter().all(|status| !overlapping(path, status))
    }

    fn reports<'b>(&'b self, kind: &'b str) -> impl Iterator<Item = (&'a str, &'a Value)> + 'b {
        entries(Some(self.values)).iter().filter_map(move |(key, item)| {
            (key.starts_with("supplements[") && item.get("key").and_then(Value::as_str) == Some(kind))
                .then_some((key.as_str(), item))
        })
    }

    fn agreeing_booleans(&self, kind: &str, only_plain_record: bool) -> Vec<(&'a str, bool)> {
        let mut selected = Vec::new();
        let mut expected = None;
        for (key, item) in self.reports(kind) {
            let Some(value) = item.get("value").and_then(Value::as_bool) else {
                return Vec::new();
            };
            if (only_plain_record && entries(Some(item)).len() != 2)
                || expected.is_some_and(|expected| expected != value)
                || !self.complete(key)
            {
                return Vec::new();
            }
            expected = Some(value);
            selected.push((key, value));
        }
        selected
    }

    fn primary(&self) -> Result<Option<String>> {
        let text_source = match self
            .original
            .get("text")
            .and_then(|item| item.get("content_field"))
            .and_then(Value::as_seq)
            .filter(|path| !path.is_empty())
        {
            Some(path) => path_key(path, self.paths)?,
            None => "text.content".to_owned(),
        };
        let role = self.original.get("role").and_then(Value::as_str);
        let document = matches!(role, Some("document" | "web_area"));
        let has_text = self.original.get("text").is_some_and(|item| *item != Value::Null);
        if document && (has_text || self.statuses.get("text.content").is_some()) {
            return Ok(self.values.get(&text_source).map(|_| text_source));
        }
        let primary = ["control_value.value", &text_source, "numeric_value.value"]
            .into_iter()
            .find(|key| self.values.get(key).is_some())
            .map(str::to_owned);
        Ok(primary)
    }

    fn numeric_aliases(&self, primary: Option<&str>, selectors: Option<&Value>) -> Vec<Value> {
        let Some(primary) = primary else { return Vec::new() };
        let Some(value) = self.values.get(primary).filter(|value| same_number(value, value)) else {
            return Vec::new();
        };
        if self.statuses.get(primary).is_some() {
            return Vec::new();
        }
        let Some(selectors) = selectors.and_then(|values| values.get(self.token)) else {
            return Vec::new();
        };
        let Some(selector) = selectors.get(primary).filter(|value| truth(Some(value))) else {
            return Vec::new();
        };
        ["control_value.value", "numeric_value.value"]
            .into_iter()
            .filter_map(|name| {
                let other = self.values.get(name)?;
                (name != primary
                    && self.statuses.get(name).is_none()
                    && same_number(value, other)
                    && selectors.get(name) == Some(selector))
                .then(|| text(value_kind(name).unwrap()))
            })
            .collect()
    }

    fn document(&self) -> Result<Option<Document>> {
        let mut reports = Vec::new();
        let mut complete = true;
        for (key, record) in self.reports("document_uri") {
            let path = seq(self.paths.get(key));
            let index = path
                .get(1)
                .and_then(Value::as_i64)
                .and_then(|index| usize::try_from(index).ok())
                .ok_or_else(|| missing("supplement index"))?;
            let item = seq(self.original.get("supplements"))
                .get(index)
                .ok_or_else(|| missing("supplement"))?;
            // The fact index already holds data()'s public scalar spelling.
            // Only an explicit null can be omitted by dataclass indexing.
            let address = match item.get("value") {
                None | Some(Value::Null) => Value::Null,
                Some(_) => required(record, "value")?.clone(),
            };
            let mut record = entries(Some(record)).to_vec();
            put(&mut record, "value", address);
            complete &= record.len() == 2 && self.complete(key);
            reports.push((key.to_owned(), Value::Map(record)));
        }
        let Some((_, first)) = reports.first() else {
            return Ok(None);
        };
        let value = required(first, "value")?;
        complete &= value.as_str().is_some() && reports.iter().all(|(_, item)| item.get("value") == Some(value));
        let keys = reports.iter().map(|(key, _)| key.clone()).collect();
        Ok(Some(if complete {
            Document {
                field: "documentUrl",
                value: value.clone(),
                keys,
            }
        } else {
            Document {
                field: "documentReports",
                value: Value::Map(reports),
                keys,
            }
        }))
    }

    fn decisions(&self) -> Result<HashMap<&'a str, (&'static str, &'a Value)>> {
        const DECISIONS: &[(&str, &str)] = &[
            ("numeric_value.minimum", "rangeMinimum"),
            ("numeric_value.maximum", "rangeMaximum"),
            ("numeric_value.small_step", "rangeSmallStep"),
            ("numeric_value.large_step", "rangeLargeStep"),
            ("numeric_value.allowed_values", "rangeAllowedValues"),
            ("states.has_popup", "hasPopup"),
            ("states.protected", "protected"),
            ("states.offscreen", "offscreen"),
            ("structure.can_select_multiple", "canSelectMultiple"),
            ("structure.selection_required", "selectionRequired"),
        ];
        let mut selected = HashMap::new();
        for &(key, field) in DECISIONS {
            let Some(value) = self.values.get(key).filter(|_| self.complete(key)) else {
                continue;
            };
            let valid = if key == "numeric_value.allowed_values" {
                value.as_seq().is_some_and(|items| {
                    items.iter().all(|item| {
                        matches!(item, Value::Text(_) | Value::Bool(_))
                            || finite(item)
                            || one_field(item, "element").is_some()
                    })
                })
            } else if key.starts_with("numeric_value.") {
                finite(value)
            } else {
                value.as_bool().is_some()
            };
            if valid {
                selected.insert(key, (field, value));
            }
        }
        if !selected.is_empty() {
            let role = self
                .values
                .get("role")
                .and_then(Value::as_str)
                .filter(|_| self.complete("role"));
            let actions = required(self.row, "actions")?;
            let actions = if string(actions, "state")? == "complete" {
                seq(actions.get("items"))
                    .iter()
                    .enumerate()
                    .filter(|(index, _)| self.unrestricted(&[text("actions"), Value::Int(*index as i64)]))
                    .map(|(_, item)| item)
                    .collect::<Vec<_>>()
            } else {
                Vec::new()
            };
            let range = matches!(
                role,
                Some("slider" | "spinner" | "scrollbar" | "progress_indicator" | "level_indicator")
            );
            let numeric = range || (self.values.get("numeric_value.value").is_some_and(finite)
                && self.complete("numeric_value.value"))
                || ["numeric_value.small_step", "numeric_value.large_step", "numeric_value.allowed_values"]
                    .iter().any(|key| selected.contains_key(key))
                || actions.iter().any(|item| {
                    let target = seq(item.get("target"));
                    matches!(target, [first, second] if first.as_str() == Some("numeric_value") && second.as_str() == Some("value"))
                });
            let actionable = range
                || matches!(
                    role,
                    Some(
                        "button"
                            | "popup_button"
                            | "menu_button"
                            | "checkbox"
                            | "radio_button"
                            | "switch"
                            | "link"
                            | "text_field"
                            | "text_area"
                            | "combo_box"
                            | "menu_item"
                    )
                )
                || actions.iter().any(|item| {
                    item.get("key").is_some_and(|key| *key != Value::Null)
                        && !matches!(
                            item.get("kind").and_then(Value::as_str),
                            Some("focus" | "set_value" | "replace_selection" | "show_menu" | "scroll_into_view")
                        )
                });
            selected.retain(|key, (_, value)| {
                (numeric || !key.starts_with("numeric_value."))
                    && (actionable
                        || **value != Value::Bool(false)
                        || !matches!(*key, "states.has_popup" | "states.protected" | "states.offscreen"))
            });
        }
        let statuses = self.reports("item_status").collect::<Vec<_>>();
        if let Some(value) = statuses
            .first()
            .and_then(|(_, item)| item.get("value"))
            .filter(|value| value.as_str().is_some())
        {
            if statuses
                .iter()
                .all(|(key, report)| report.get("value") == Some(value) && self.complete(key))
            {
                selected.extend(statuses.into_iter().map(|(key, _)| (key, ("itemStatus", value))));
            }
        }
        Ok(selected)
    }
}

struct Document {
    field: &'static str,
    value: Value,
    keys: HashSet<String>,
}

#[derive(Default)]
struct Ledger {
    slots: HashMap<(String, String), usize>,
    rows: Vec<Value>,
}

impl Ledger {
    fn put(&mut self, reference: &str, key: &str, value: Value) {
        let row = map([("ref", text(reference)), ("key", text(key)), ("value", value)]);
        match self.slots.entry((reference.to_owned(), key.to_owned())) {
            std::collections::hash_map::Entry::Occupied(slot) => self.rows[*slot.get()] = row,
            std::collections::hash_map::Entry::Vacant(slot) => {
                slot.insert(self.rows.len());
                self.rows.push(row);
            }
        }
    }
}

#[derive(Default)]
struct Groups {
    active: HashMap<(String, String), usize>,
    groups: Vec<(String, String, Vec<String>)>,
}

impl Groups {
    fn append(&mut self, key: &str, label: &str, reference: &str) {
        let index = *self
            .active
            .entry((key.to_owned(), label.to_owned()))
            .or_insert_with(|| {
                let index = self.groups.len();
                self.groups.push((key.to_owned(), label.to_owned(), Vec::new()));
                index
            });
        self.groups[index].2.push(reference.to_owned());
    }

    fn remove(&mut self, key: &str, label: &str, reference: &str) -> Result<()> {
        let identity = (key.to_owned(), label.to_owned());
        let index = *self
            .active
            .get(&identity)
            .ok_or_else(|| missing("grouped annotation"))?;
        let references = &mut self.groups[index].2;
        let at = references
            .iter()
            .position(|value| value == reference)
            .ok_or_else(|| missing("annotation reference"))?;
        references.remove(at);
        if references.is_empty() {
            self.active.remove(&identity);
        }
        Ok(())
    }

    fn render(&self, output: &mut Vec<Value>) -> Result<()> {
        let mut indices = HashMap::new();
        let mut shared = Vec::<(BTreeSet<u128>, &str, Vec<Value>)>::new();
        for (name, label, refs) in self.groups.iter().filter(|(_, _, refs)| !refs.is_empty()) {
            let refs = refs
                .iter()
                .map(|value| value.parse::<u128>().map_err(|_| missing("numeric display alias")))
                .collect::<Result<BTreeSet<_>>>()?;
            let index = *indices.entry((refs.clone(), label)).or_insert_with(|| {
                let index = shared.len();
                shared.push((refs, label, Vec::new()));
                index
            });
            shared[index].2.push(text(name));
        }
        for (refs, label, mut fields) in shared {
            let name = if fields.len() == 1 {
                let Value::Text(name) = fields.pop().unwrap() else {
                    unreachable!()
                };
                let mut bytes = name.bytes();
                if bytes
                    .next()
                    .is_some_and(|byte| byte.is_ascii_alphabetic() || byte == b'_')
                    && bytes.all(|byte| byte.is_ascii_alphanumeric() || matches!(byte, b'_' | b'.'))
                {
                    name
                } else {
                    json(&text(name), JsonFormat::Plain).map_err(format_error)?
                }
            } else {
                "Properties ".to_owned() + &json(&Value::Seq(fields), JsonFormat::Plain).map_err(format_error)?
            };
            output.push(text(format!("{} {name}: {label}", reference_set(refs))));
        }
        Ok(())
    }
}

fn reference_set(refs: BTreeSet<u128>) -> String {
    let refs: Vec<_> = refs.into_iter().collect();
    let mut parts = Vec::new();
    let mut start = 0;
    while start < refs.len() {
        let mut end = start;
        while end + 1 < refs.len() && refs[end].checked_add(1) == Some(refs[end + 1]) {
            end += 1;
        }
        if end - start >= 2 {
            parts.push(format!("{}–{}", refs[start], refs[end]));
        } else {
            parts.extend(refs[start..=end].iter().map(u128::to_string));
        }
        start = end + 1;
    }
    parts.join(", ")
}

fn field_evidence(
    source: &Source,
    key: &str,
    field: Option<&str>,
    representation: &str,
    extra: Record,
) -> Result<Value> {
    let mut result = vec![
        ("node".into(), text(source.token)),
        ("path".into(), required(source.paths, key)?.clone()),
        ("field".into(), field.map(text).unwrap_or(Value::Null)),
        ("representation".into(), text(representation)),
    ];
    result.extend(extra);
    Ok(Value::Map(result))
}

fn status_label(status: &Value) -> Result<String> {
    let state = string(status, "state")?;
    Ok(
        match status
            .get("detail")
            .and_then(Value::as_str)
            .filter(|detail| !detail.is_empty())
        {
            Some(detail) => format!("{state}: {detail}"),
            None => state.to_owned(),
        },
    )
}

type Aliases<'a> = HashMap<&'a str, String>;

fn alias<'a>(aliases: &'a Aliases<'_>, token: &str) -> Result<&'a str> {
    aliases
        .get(token)
        .map(String::as_str)
        .ok_or_else(|| missing("display alias"))
}

fn make_node(
    source: &Source,
    reference: &str,
    aliases: &Aliases,
    metadata: &Value,
    projection: &mut Projection,
) -> Result<Record> {
    let role = source.original.get("role").and_then(Value::as_str);
    let capabilities = metadata
        .get("operation_capabilities")
        .and_then(|items| items.get(source.token))
        .cloned()
        .unwrap_or_else(|| map(["click", "typeText", "replaceText", "focus"].map(|key| (key, text("unknown")))));
    let mut node = vec![
        ("ref".into(), text(reference)),
        ("role".into(), text(presentation_role(role))),
        ("children".into(), Value::Seq(Vec::new())),
        ("capabilities".into(), capabilities),
    ];
    let distinct_subrole = matches!(role, Some("dialog" | "alert" | "list_item" | "tree_item"));
    if distinct_subrole {
        put(&mut node, "subrole", text(role.unwrap()));
    }
    let primary = source.primary()?;
    if let Some(primary) = &primary {
        projection.value_fields.push((reference.into(), text(primary)));
    }
    let numeric_aliases = source.numeric_aliases(primary.as_deref(), metadata.get("field_sources"));
    if !numeric_aliases.is_empty() {
        put(&mut node, "_numericValueAliases", Value::Seq(numeric_aliases));
    }
    let disclosure = if matches!(role, Some("row" | "tree_item")) {
        source
            .agreeing_booleans("disclosing", false)
            .into_iter()
            .collect::<HashMap<_, _>>()
    } else {
        HashMap::new()
    };
    let document = source.document()?;
    let states = ["unsaved", "invalid"]
        .into_iter()
        .flat_map(|name| {
            source
                .agreeing_booleans(name, true)
                .into_iter()
                .map(move |(key, value)| (key, (name, value)))
        })
        .collect::<HashMap<_, _>>();
    let decisions = source.decisions()?;
    let diagnostics = seq(metadata
        .get("diagnostic_fields")
        .and_then(|items| items.get(source.token)));

    for (key, original) in entries(Some(source.values)) {
        let key = key.as_str();
        let mut value = Cow::Borrowed(original);
        let field = if matches!(
            key,
            "role" | "name" | "description" | "help" | "identifier" | "placeholder" | "url"
        ) {
            Some(key)
        } else if key == "role_detail" && !distinct_subrole {
            Some("subrole")
        } else if let Some((name, integer)) = known_field(key) {
            (!integer || safe_integer(Some(original)).is_some()).then_some(name)
        } else if let Some(field) = read_only(key).filter(|_| source.statuses.get(key).is_none()) {
            Some(field)
        } else if let Some((field, item)) = decisions.get(key) {
            value = if *field == "rangeAllowedValues" {
                Cow::Owned(Value::Seq(
                    seq(Some(item))
                        .iter()
                        .map(|item| {
                            if let Some(token) = one_field(item, "element").and_then(Value::as_str) {
                                Ok(map([("element", text(alias(aliases, token)?))]))
                            } else {
                                Ok(item.clone())
                            }
                        })
                        .collect::<Result<_>>()?,
                ))
            } else {
                Cow::Borrowed(*item)
            };
            put(&mut node, "preserveGroup", Value::Bool(true));
            Some(*field)
        } else if let Some((field, state)) = states.get(key) {
            value = Cow::Owned(Value::Bool(*state));
            Some(*field)
        } else if let Some(
            state @ ("enabled" | "focused" | "focusable" | "selected" | "selectable" | "modal" | "busy" | "visible"
            | "showing"),
        ) = key.strip_prefix("states.")
        {
            Some(state)
        } else if key == "states.expanded" && matches!(original.as_str(), Some("expanded" | "collapsed")) {
            value = Cow::Owned(Value::Bool(original.as_str() == Some("expanded")));
            Some("expanded")
        } else if let Some(disclosing) = disclosure.get(key) {
            value = Cow::Owned(Value::Bool(*disclosing));
            Some("disclosing")
        } else if let Some(document) = document.as_ref().filter(|report| report.keys.contains(key)) {
            value = Cow::Borrowed(&document.value);
            Some(document.field)
        } else if let Some(kind) = value_kind(key) {
            let field = if primary.as_deref() == Some(key) { "value" } else { kind };
            value = Cow::Owned(value_text(original, aliases)?);
            if field == "value" {
                if matches!(original, Value::Int(_) | Value::BigInt(_) | Value::Float(_)) {
                    put(&mut node, "valueIsNumber", Value::Bool(true));
                }
                if key == "text.content" && matches!(role, Some("document" | "web_area")) {
                    put(&mut node, "valueKind", text("document"));
                }
            }
            if original.as_str().is_some() && source.statuses.get(key).is_none() {
                append(&mut node, "_completeTextValues", text(field));
            }
            Some(field)
        } else {
            None
        };
        if let Some(field) = field {
            let preserve = matches!(
                field,
                "readOnly" | "textReadOnly" | "rangeReadOnly" | "unsaved" | "invalid"
            ) && *value != Value::Bool(false);
            if key != "role" {
                put(&mut node, field, value.into_owned());
            }
            let normalized = get(&node, field).ok_or_else(|| missing(field))?.clone();
            projection.fields.put(
                reference,
                key,
                field_evidence(
                    source,
                    key,
                    Some(field),
                    "field",
                    vec![("normalized".into(), normalized)],
                )?,
            );
            if preserve {
                put(&mut node, "preserveGroup", Value::Bool(true));
            }
        } else {
            let structural = matches!(key, "text.source" | "text.unit" | "text.content_field");
            projection.fields.put(
                reference,
                key,
                field_evidence(
                    source,
                    key,
                    None,
                    if structural { "raw" } else { "annotation" },
                    Vec::new(),
                )?,
            );
            if !structural {
                projection.grouped.append(key, &literal(original)?, reference);
            }
            let supplement = key
                .starts_with("supplements[")
                .then(|| original.get("key").and_then(Value::as_str))
                .flatten();
            let provider_fact = supplement.is_some_and(|name| name.starts_with("provider."))
                && !diagnostics.contains(required(source.paths, key)?);
            let meaningful = matches!(supplement, Some("item_status" | "item_type")) && truth(original.get("value"));
            let invalid_geometry = key.starts_with("geometry.") && original.get("valid") == Some(&Value::Bool(false));
            if provider_fact
                || meaningful
                || invalid_geometry
                || matches!(key, "states.editable" | "text.read_only") && original == &Value::Bool(true)
            {
                put(&mut node, "preserveGroup", Value::Bool(true));
            }
        }
    }

    for (field, keys) in [
        ("rowRange", ["structure.row_index", "structure.row_span"]),
        ("columnRange", ["structure.column_index", "structure.column_span"]),
    ] {
        let originals = keys.map(|key| source.values.get(key));
        if let [Some(index), Some(span)] = originals.map(safe_integer) {
            if index >= 0
                && span >= 0
                && safe_integer(Some(&Value::Int(index + span))).is_some()
                && keys.iter().all(|key| source.statuses.get(key).is_none())
            {
                let range = map([
                    ("location", originals[0].unwrap().clone()),
                    ("length", originals[1].unwrap().clone()),
                ]);
                put(&mut node, field, range.clone());
                for key in keys {
                    let shown = map([("field", text(field)), ("normalized", range.clone())]);
                    projection.fields.put(
                        reference,
                        key,
                        field_evidence(
                            source,
                            key,
                            None,
                            "projection",
                            vec![("projections".into(), Value::Seq(vec![shown]))],
                        )?,
                    );
                    projection
                        .grouped
                        .remove(key, &literal(required(source.values, key)?)?, reference)?;
                }
            }
        }
    }

    let text_info = source.original.get("text");
    if matches!(
        text_info.and_then(|item| item.get("unit")).and_then(Value::as_str),
        Some("unicode_codepoint" | "utf16_code_unit")
    ) {
        if let [span] = seq(text_info.and_then(|item| item.get("selections"))) {
            let range = span.get("start").and_then(Value::as_i128)
                .zip(span.get("end").and_then(Value::as_i128))
                .and_then(|(start, end)| end.checked_sub(start).map(|length| (start, length)));
            if let Some((start, length)) = range {
                put(
                    &mut node,
                    "selectionRange",
                    map([("location", Value::from_i128(start)), ("length", Value::from_i128(length))]),
                );
            }
        }
    }

    let shortcuts = seq(source.original.get("shortcuts"));
    if !shortcuts.is_empty() {
        let strings = shortcuts
            .iter()
            .map(|item| string(item, "text"))
            .collect::<Result<Vec<_>>>()?;
        put(&mut node, "shortcut", text(strings.join("; ")));
    }
    let mut actions = Vec::new();
    for action in seq(source.original.get("actions")) {
        let Some(key) = action.get("key").filter(|item| *item != &Value::Null) else {
            continue;
        };
        let mut record = vec![
            ("name".into(), key.clone()),
            ("kind".into(), required(action, "kind")?.clone()),
        ];
        let description = action
            .get("description")
            .filter(|value| truth(Some(value)))
            .or_else(|| action.get("label").filter(|value| truth(Some(value))));
        if let Some(description) = description {
            put(&mut record, "description", description.clone());
        }
        actions.push(Value::Map(record));
    }
    put(&mut node, "actions", Value::Seq(actions));
    if string(required(source.row, "actions")?, "state")? != "complete" {
        put(&mut node, "actionsUnavailable", Value::Bool(true));
    }
    for (key, status) in entries(Some(source.statuses)) {
        let label = status_label(status)?;
        projection.grouped.append(key, &label, reference);
        if status.get("textRef").is_some() {
            projection.text_refs.put(reference, key, status.clone());
            let mut caption = String::from("; captured text available");
            if primary.as_deref() == Some(key) {
                caption.push_str(" via this element's read_text()");
            } else {
                caption.push_str("; no public continuation for this field");
            }
            if let Some(total) = status.get("totalUnits").filter(|value| *value != &Value::Null) {
                let unit = match status.get("unit").and_then(Value::as_str) {
                    Some("utf16_code_unit") => "UTF-16 units",
                    Some("unicode_codepoint") => "Unicode code points",
                    Some(unit) if !unit.is_empty() => unit,
                    _ => "units",
                };
                write!(caption, "; {} {unit}", literal(total)?).unwrap();
            }
            projection.grouped.remove(key, &label, reference)?;
            projection.grouped.append(key, &(label + &caption), reference);
        }
        let incidental = key.starts_with("text.selections")
            && !matches!(
                role,
                Some("text_field" | "text_area" | "combo_box" | "document" | "web_area")
            )
            && !truth(source.original.get("states").and_then(|states| states.get("focused")))
            && !truth(text_info.and_then(|item| item.get("selected_text")));
        if !incidental
            && matches!(
                string(status, "state")?,
                "partial" | "error" | "timeout" | "unavailable" | "redacted" | "stale" | "conflict"
            )
        {
            put(&mut node, "preserveGroup", Value::Bool(true));
        }
    }
    Ok(node)
}

#[derive(Default)]
struct Projection {
    fields: Ledger,
    text_refs: Ledger,
    value_fields: Record,
    grouped: Groups,
    annotations: Vec<Value>,
}

struct Node {
    record: Option<Record>,
    children: Vec<usize>,
    token: String,
}

fn integer_sum(left: i64, right: i64) -> Value {
    left.checked_add(right)
        .map(Value::Int)
        .unwrap_or_else(|| Value::BigInt((i128::from(left) + i128::from(right)).to_string()))
}

fn is_child_view(edge: &Value) -> Result<bool> {
    Ok(string(edge, "kind")? == "children"
        && string(edge, "certainty")? == "confirmed"
        && !matches!(string(edge, "state")?, "absent" | "unsupported"))
}

fn project_edges<'a>(
    nodes: &mut [Node],
    indices: &HashMap<&str, usize>,
    relations: &'a [Value],
    native: &HashMap<&str, &Value>,
    unread: &HashSet<&str>,
    aliases: &Aliases,
) -> Result<Vec<&'a Value>> {
    let mut child_views = HashMap::<&str, usize>::new();
    for edge in relations {
        if is_child_view(edge)? {
            *child_views.entry(string(edge, "source")?).or_default() += 1;
        }
    }

    let mut extra = Vec::new();
    for edge in relations {
        let token = string(edge, "source")?;
        let kind = string(edge, "kind")?;
        let state = string(edge, "state")?;
        let complete = state == "complete";
        let confirmed = string(edge, "certainty")? == "confirmed";
        let targets = seq(edge.get("targets"))
            .iter()
            .map(|item| item.as_str().ok_or_else(|| missing("relation target")))
            .collect::<Result<Vec<_>>>()?;
        let node = &mut nodes[*indices.get(token).ok_or_else(|| missing("relation source"))?];
        let source = node.record.as_mut().ok_or_else(|| missing("relation record"))?;
        let has_unread = targets.iter().any(|target| unread.contains(target));
        if matches!(state, "absent" | "unsupported") {
            let role = native
                .get(token)
                .and_then(|node| node.get("role"))
                .and_then(Value::as_str);
            let region = matches!(
                role,
                Some(
                    "window"
                        | "dialog"
                        | "alert"
                        | "sheet"
                        | "popover"
                        | "browser"
                        | "scroll_area"
                        | "web_area"
                        | "document"
                        | "list"
                        | "table"
                        | "tree"
                )
            );
            if kind == "children" && state == "unsupported" && region && !child_views.contains_key(token) {
                put(
                    source,
                    "partialReason",
                    text("child enumeration unsupported; contents unknown"),
                );
                put(source, "childEnumerationUnsupported", Value::Bool(true));
                put(source, "preserveGroup", Value::Bool(true));
            }
            extra.push(edge);
            continue;
        }
        if kind == "children" && confirmed {
            if child_views.get(token).copied().ok_or_else(|| missing("child view"))? > 1 {
                put(source, "preserveGroup", Value::Bool(true));
                put(
                    source,
                    "partialReason",
                    text("multiple observed child views; inspect relationship details"),
                );
                extra.push(edge);
                continue;
            }
            // Preserve unobserved children as the same ordered links, leaving
            // their display disposition to the forest walk.
            node.children.reserve(targets.len());
            for target in &targets {
                let index = indices.get(target).ok_or_else(|| missing("child node"))?;
                node.children.push(*index);
            }
            if !complete {
                if let Some(total) = edge.get("total") {
                    put(source, "childCount", total.clone());
                }
                if let Some(offset) = edge.get("offset") {
                    let start = offset.as_i64().ok_or_else(|| missing("child offset"))?;
                    let checked = edge
                        .get("inspectedCount")
                        .map(|count| count.as_i64().ok_or_else(|| missing("child count")))
                        .transpose()?
                        .unwrap_or(targets.len() as i64);
                    let end = integer_sum(start, checked);
                    put(source, "childrenStart", offset.clone());
                    put(source, "childrenEnd", end.clone());
                    if edge.get("cursor").is_some() {
                        put(source, "nextChildOffset", end);
                    }
                }
                if edge.get("unavailablePositions").is_some() {
                    put(source, "childrenRead", Value::Int(targets.len() as i64));
                }
                put(
                    source,
                    "partialReason",
                    text("observed children partly collected; see Relation details"),
                );
            }
            if has_unread {
                put(source, "partialReason", text("child facts have not been collected"));
            }
        } else {
            if kind == "error" {
                let mut error = Vec::new();
                for (key, value) in entries(Some(edge)) {
                    if matches!(key.as_str(), "source" | "recordKey") {
                        continue;
                    }
                    let value = if key == "targets" {
                        Value::Seq(target_aliases(&targets, aliases)?)
                    } else {
                        value.clone()
                    };
                    error.push((key.clone(), value));
                }
                append(source, "errorRelations", Value::Map(error));
                put(source, "preserveGroup", Value::Bool(true));
            } else if kind == "labelled_by" && confirmed && complete && targets.len() == 1 && !has_unread {
                put(source, "titleRef", text(alias(aliases, targets[0])?));
            } else if kind == "labels" && confirmed {
                put(source, "labelTargets", Value::Seq(target_aliases(&targets, aliases)?));
                put(source, "incompleteLabelRelation", Value::Bool(!complete || has_unread));
            } else if kind == "disclosed_by" && confirmed && complete && targets.len() == 1 {
                put(source, "disclosedBy", text(alias(aliases, targets[0])?));
            }
            extra.push(edge);
        }
        if !complete || !confirmed {
            put(source, "preserveGroup", Value::Bool(true));
        }
    }
    Ok(extra)
}

fn target_aliases(targets: &[&str], aliases: &Aliases) -> Result<Vec<Value>> {
    targets.iter().map(|token| Ok(text(alias(aliases, token)?))).collect()
}

fn related_contexts<'a>(edges: &[&'a Value], aliases: &Aliases) -> Result<HashMap<&'a str, Vec<Value>>> {
    let mut contexts = HashMap::<&str, Vec<Value>>::new();
    for edge in edges {
        let kind = string(edge, "kind")?;
        if string(edge, "certainty")? != "confirmed" || matches!(kind, "parent" | "window") {
            continue;
        }
        let source = string(edge, "source")?;
        let item = Value::Seq(vec![text(alias(aliases, source)?), text(kind)]);
        for target in seq(edge.get("targets")) {
            let target = target.as_str().ok_or_else(|| missing("relation target"))?;
            if target != source {
                let context = contexts.entry(target).or_default();
                if !context.contains(&item) {
                    context.push(item.clone());
                }
            }
        }
    }
    Ok(contexts)
}

#[derive(Clone, Copy, PartialEq, Eq)]
enum Visit {
    Unread,
    Unplaced,
    Active,
    Placed,
}

fn forest_root(
    root: usize,
    nodes: &mut [Node],
    visited: &mut [Visit],
    aliases: &Aliases,
    annotations: &mut Vec<Value>,
) -> Result<Value> {
    visited[root] = Visit::Active;
    let mut stack = vec![(root, 0)];
    while let Some((index, next)) = stack.last_mut() {
        if let Some(&child) = nodes[*index].children.get(*next) {
            *next += 1;
            let reason = match visited[child] {
                Visit::Unplaced => {
                    visited[child] = Visit::Active;
                    stack.push((child, 0));
                    continue;
                }
                Visit::Unread => "unread",
                Visit::Active => "cycle",
                Visit::Placed => "shared",
            };
            let node = &mut nodes[*index];
            annotations.push(text(format!(
                "{} contains {} ({reason})",
                alias(aliases, &node.token)?,
                alias(aliases, &nodes[child].token)?
            )));
            if reason != "unread" {
                put(
                    nodes[*index].record.as_mut().ok_or_else(|| missing("active node"))?,
                    "preserveGroup",
                    Value::Bool(true),
                );
            }
            continue;
        }
        let (index, _) = stack.pop().unwrap();
        visited[index] = Visit::Placed;
        let record = Value::Map(nodes[index].record.take().ok_or_else(|| missing("display node"))?);
        if let Some((parent, _)) = stack.last() {
            append(
                nodes[*parent].record.as_mut().ok_or_else(|| missing("active parent"))?,
                "children",
                record,
            );
        } else {
            return Ok(record);
        }
    }
    Err(missing("projected root"))
}

pub(crate) fn source_order(snapshot: &FactSnapshot) -> Result<Vec<&str>> {
    let observed = seq(snapshot.facts.get("nodes"))
        .iter()
        .filter(|row| flag(row.get("observed")))
        .map(|row| string(row, "node"))
        .collect::<Result<Vec<_>>>()?;
    let observed_set: HashSet<_> = observed.iter().copied().collect();
    let mut order = observed;
    for (token, _) in entries(snapshot.observation.get("nodes")) {
        if !observed_set.contains(token.as_str()) {
            order.push(token.as_str());
        }
    }
    Ok(order)
}

pub(crate) fn project_numbered(
    snapshot: &FactSnapshot,
    tokens: &[String],
    numbers: &PartAliases,
    scope_kind: &Value,
    driver_features: &Value,
) -> Result<Value> {
    if *scope_kind != Value::Null
        && !matches!(
            scope_kind.as_str(),
            Some("window" | "applicationMenu" | "applicationContent")
        )
    {
        return Err(ReadError("Unknown observation scope kind".to_owned()));
    }
    let aliases = numbers
        .ordered()
        .iter()
        .map(|(node, number)| {
            let token = tokens.get(node.0).ok_or_else(|| missing("numbered node"))?;
            Ok((token.as_str(), number.to_string()))
        })
        .collect::<Result<Aliases>>()?;
    let refs = source_order(snapshot)?
        .into_iter()
        .map(|token| Ok((alias(&aliases, token)?.to_owned(), text(token))))
        .collect::<Result<Record>>()?;
    let native: HashMap<_, _> = entries(snapshot.observation.get("nodes"))
        .iter()
        .map(|(token, node)| (token.as_str(), node))
        .collect();
    let requested_roots = seq(snapshot.observation.get("roots"))
        .iter()
        .map(|item| item.as_str().ok_or_else(|| missing("requested root")))
        .collect::<Result<Vec<_>>>()?;
    let requested_set: HashSet<_> = requested_roots.iter().copied().collect();
    let unread = seq(snapshot.facts.get("unread"))
        .iter()
        .map(|item| item.as_str().ok_or_else(|| missing("unread token")))
        .collect::<Result<Vec<_>>>()?;
    let unread_set: HashSet<_> = unread.iter().copied().collect();
    let rows = seq(snapshot.facts.get("nodes"));
    let mut projection = Projection::default();
    let mut nodes = Vec::with_capacity(native.len());
    let mut indices = HashMap::<&str, usize>::with_capacity(native.len());
    let mut display = Vec::new();
    for row in rows {
        let token = string(row, "node")?;
        let reference = alias(&aliases, token)?;
        let original = *native.get(token).ok_or_else(|| missing("original node"))?;
        let index = nodes.len();
        indices.insert(token, index);
        let record = if flag(row.get("observed")) || requested_set.contains(token) {
            let source = Source::new(row, original)?;
            display.push(index);
            Some(make_node(
                &source,
                reference,
                &aliases,
                &snapshot.metadata,
                &mut projection,
            )?)
        } else {
            for (key, status) in entries(row.get("fieldStatus")) {
                projection.grouped.append(key, &status_label(status)?, reference);
            }
            None
        };
        nodes.push(Node {
            record,
            children: Vec::new(),
            token: token.to_owned(),
        });
    }
    // Native targets can be known and retained without a fact row.
    for token in native.keys() {
        if !indices.contains_key(token) {
            indices.insert(token, nodes.len());
            nodes.push(Node {
                record: None,
                children: Vec::new(),
                token: (*token).to_owned(),
            });
        }
    }

    let extra = project_edges(
        &mut nodes,
        &indices,
        seq(snapshot.facts.get("relations")),
        &native,
        &unread_set,
        &aliases,
    )?;
    let contexts = related_contexts(&extra, &aliases)?;
    let mut visited = nodes
        .iter()
        .map(|node| {
            if node.record.is_some() {
                Visit::Unplaced
            } else {
                Visit::Unread
            }
        })
        .collect::<Vec<_>>();
    let mut root_contexts = Vec::new();
    let mut roots = Vec::new();
    let root_order = requested_roots
        .iter()
        .map(|token| {
            indices
                .get(token)
                .copied()
                .ok_or_else(|| missing("requested root node"))
        })
        .collect::<Result<Vec<_>>>()?;
    for index in root_order.into_iter().chain(display) {
        if visited[index] == Visit::Placed {
            continue;
        }
        let token = nodes[index].token.as_str();
        let reference = alias(&aliases, token)?;
        if !requested_set.contains(token) {
            if let Some(context) = contexts.get(token) {
                root_contexts.push((reference.to_owned(), Value::Seq(context.clone())));
            } else if !unread_set.contains(token) {
                projection.annotations.push(text(format!(
                    "{reference} has no expanded containment path from the requested roots"
                )));
            }
        }
        roots.push(forest_root(
            index,
            &mut nodes,
            &mut visited,
            &aliases,
            &mut projection.annotations,
        )?);
    }
    projection.grouped.render(&mut projection.annotations)?;
    if !unread.is_empty() {
        let values = unread
            .iter()
            .map(|token| alias(&aliases, token))
            .collect::<Result<Vec<_>>>()?;
        projection
            .annotations
            .push(text(format!("Uncollected: {}", values.join(", "))));
    }
    for reason in seq(snapshot.facts.get("stopReasons")) {
        projection
            .annotations
            .push(text(format!("Collection: {}", literal(reason)?)));
    }
    if let Some(spaces) = snapshot
        .observation
        .get("coordinate_spaces")
        .filter(|value| truth(Some(value)))
    {
        projection
            .annotations
            .push(text(format!("Coordinate spaces: {}", literal(spaces)?)));
    }
    let mut seen_features = HashSet::new();
    let features = seq(Some(driver_features))
        .iter()
        .filter(|item| item.as_str().is_some_and(|feature| seen_features.insert(feature)))
        .cloned()
        .collect();

    Ok(map([
        ("roots", Value::Seq(roots)),
        ("refs", Value::Map(refs)),
        ("annotations", Value::Seq(projection.annotations)),
        ("fields", Value::Seq(projection.fields.rows)),
        ("text_refs", Value::Seq(projection.text_refs.rows)),
        ("scope_kind", scope_kind.clone()),
        ("root_contexts", Value::Map(root_contexts)),
        ("value_fields", Value::Map(projection.value_fields)),
        ("driver_features", Value::Seq(features)),
    ]))
}

#[cfg(test)]
mod tests {
    use super::*;
    use crate::numbering::{acquisition_aliases, NodeId, Part, SessionAliases};

    fn words(items: &[&str]) -> Value {
        Value::Seq(items.iter().map(|item| text(*item)).collect())
    }

    fn row(token: &str, observed: bool, values: Value, paths: Value, statuses: Value) -> Value {
        map([
            ("node", text(token)),
            ("observed", Value::Bool(observed)),
            ("values", values),
            ("fieldPaths", paths),
            ("fieldStatus", statuses),
            (
                "actions",
                map([("state", text("complete")), ("items", Value::Seq(Vec::new()))]),
            ),
        ])
    }

    fn empty() -> Value {
        Value::Map(Vec::new())
    }

    fn edge(source: &str, kind: &str, targets: &[&str]) -> Value {
        map([
            ("source", text(source)),
            ("kind", text(kind)),
            ("targets", words(targets)),
            ("state", text("complete")),
            ("certainty", text("confirmed")),
        ])
    }

    fn snapshot(nodes: Value, rows: Vec<Value>, relations: Vec<Value>, unread: Value, metadata: Value) -> FactSnapshot {
        FactSnapshot {
            observation: map([("nodes", nodes), ("roots", words(&["root"]))]),
            facts: map([
                ("nodes", Value::Seq(rows)),
                ("relations", Value::Seq(relations)),
                ("unread", unread),
                ("stopReasons", words(&[])),
            ]),
            metadata,
            query: empty(),
        }
    }

    fn run(snapshot: &FactSnapshot) -> Value {
        let tokens = entries(snapshot.observation.get("nodes"))
            .iter()
            .map(|(token, _)| token.clone())
            .collect::<Vec<_>>();
        let source_order = source_order(snapshot)
            .unwrap()
            .into_iter()
            .map(|token| NodeId(tokens.iter().position(|other| other == token).unwrap()))
            .collect::<Vec<_>>();
        let mut session = SessionAliases::new("driver".into());
        let aliases = acquisition_aliases(
            &mut session,
            &Part {
                session: "driver",
                surface: None,
                scope_kind: Some("window"),
                tokens: &tokens,
                source_order: &source_order,
            },
        )
        .unwrap();
        project_numbered(snapshot, &tokens, &aliases, &text("window"), &words(&[])).unwrap()
    }

    #[test]
    fn topology_preserves_unread_cycles_shared_targets_and_related_roots() {
        let node = |role| map([("role", text(role))]);
        let input = snapshot(
            map([
                ("u", empty()),
                ("root", node("window")),
                ("a", node("group")),
                ("b", node("button")),
                ("x", node("static_text")),
            ]),
            ["root", "a", "b", "u", "x"]
                .into_iter()
                .map(|token| row(token, token != "u", empty(), empty(), empty()))
                .collect(),
            vec![
                edge("root", "children", &["a", "b", "u"]),
                edge("a", "children", &["root", "b"]),
                edge("root", "labels", &["x"]),
            ],
            words(&["u"]),
            empty(),
        );
        let projected = run(&input);
        assert_eq!(
            projected.get("refs"),
            Some(&map([
                ("0", text("root")),
                ("1", text("a")),
                ("2", text("b")),
                ("3", text("x")),
                ("4", text("u")),
            ]))
        );
        assert_eq!(
            projected.get("annotations"),
            Some(&words(&[
                "1 contains 0 (cycle)",
                "0 contains 2 (shared)",
                "0 contains 4 (unread)",
                "Uncollected: 4",
            ]))
        );
        assert_eq!(
            projected.get("root_contexts"),
            Some(&map([("3", Value::Seq(vec![words(&["0", "labels"])]))]))
        );
        let roots = seq(projected.get("roots"));
        assert_eq!(roots.len(), 2);
        let first = &roots[0];
        assert_eq!(
            first.get("partialReason"),
            Some(&text("child facts have not been collected"))
        );
        let group = &seq(first.get("children"))[0];
        assert_eq!(group.get("preserveGroup"), Some(&Value::Bool(true)));
        assert_eq!(seq(group.get("children"))[0].get("ref"), Some(&text("2")));
    }

    #[test]
    fn provenance_preserves_original_paths_numeric_sources_ranges_and_text_continuations() {
        let paths = map([
            ("role", words(&["role"])),
            ("control_value.value", words(&["control_value", "value"])),
            ("numeric_value.value", words(&["numeric_value", "value"])),
            ("structure.row_index", words(&["structure", "row_index"])),
            ("structure.row_span", words(&["structure", "row_span"])),
            ("text.selected_text", words(&["text", "selected_text"])),
        ]);
        let statuses = map([(
            "text.selected_text",
            map([
                ("state", text("partial")),
                ("textRef", text("selector")),
                ("unit", text("utf16_code_unit")),
                ("totalUnits", Value::Int(9)),
            ]),
        )]);
        let values = map([
            ("role", text("row")),
            ("control_value.value", Value::Float(-0.0)),
            ("numeric_value.value", Value::Float(-0.0)),
            ("structure.row_index", Value::Int(4)),
            ("structure.row_span", Value::Int(2)),
            ("text.selected_text", text("preview")),
        ]);
        let input = snapshot(
            map([("root", map([("role", text("row"))]))]),
            vec![row("root", true, values, paths, statuses)],
            Vec::new(),
            words(&[]),
            map([(
                "field_sources",
                map([(
                    "root",
                    map([
                        ("control_value.value", text("AXValue")),
                        ("numeric_value.value", text("AXValue")),
                    ]),
                )]),
            )]),
        );
        let result = run(&input);
        let node = &seq(result.get("roots"))[0];
        assert_eq!(node.get("value"), Some(&text("-0.0")));
        assert_eq!(node.get("_numericValueAliases"), Some(&words(&["rangeValue"])));
        let range = map([("location", Value::Int(4)), ("length", Value::Int(2))]);
        assert_eq!(node.get("rowRange"), Some(&range));
        let fields = seq(result.get("fields"));
        assert_eq!(fields[3].get("key"), Some(&text("structure.row_index")));
        let evidence = fields[3].get("value").unwrap();
        assert_eq!(evidence.get("path"), Some(&words(&["structure", "row_index"])));
        assert_eq!(seq(evidence.get("projections"))[0].get("normalized"), Some(&range));
        assert_eq!(result.get("annotations"), Some(&words(&[
            "0 text.selected_text: partial; captured text available; no public continuation for this field; 9 UTF-16 units",
        ])));
        let continuation = &seq(result.get("text_refs"))[0];
        assert_eq!(continuation.get("key"), Some(&text("text.selected_text")));
    }

    #[test]
    fn document_null_and_conflicting_state_supplements_remain_explicit() {
        let supplements = Value::Seq(vec![
            map([("key", text("document_uri")), ("value", Value::Null)]),
            map([("key", text("document_uri")), ("value", text("file:///report"))]),
            map([("key", text("invalid")), ("value", Value::Bool(true))]),
            map([("key", text("invalid")), ("value", Value::Bool(false))]),
            map([("key", text("document_uri")), ("value", Value::Float(f64::INFINITY))]),
        ]);
        let values = map([
            ("supplements[0]", map([("key", text("document_uri"))])),
            ("supplements[1]", seq(Some(&supplements))[1].clone()),
            ("supplements[2]", seq(Some(&supplements))[2].clone()),
            ("supplements[3]", seq(Some(&supplements))[3].clone()),
            (
                "supplements[4]",
                map([
                    ("key", text("document_uri")),
                    ("value", map([("number", text("Infinity"))])),
                ]),
            ),
        ]);
        let paths = map((0..5).map(|index| {
            (
                format!("supplements[{index}]"),
                Value::Seq(vec![text("supplements"), Value::Int(index)]),
            )
        }));
        let input = snapshot(
            map([("root", map([("role", text("document")), ("supplements", supplements)]))]),
            vec![row("root", true, values, paths, empty())],
            Vec::new(),
            words(&[]),
            empty(),
        );
        let projected = run(&input);
        let root = &seq(projected.get("roots"))[0];
        assert!(root.get("invalid").is_none());
        assert!(root.get("documentUrl").is_none());
        assert_eq!(
            root.get("documentReports")
                .unwrap()
                .get("supplements[0]")
                .unwrap()
                .get("value"),
            Some(&Value::Null)
        );
        assert_eq!(
            root.get("documentReports")
                .unwrap()
                .get("supplements[4]")
                .unwrap()
                .get("value"),
            Some(&map([("number", text("Infinity"))]))
        );
        assert_eq!(
            projected.get("annotations"),
            Some(&words(&[
                "0 \"supplements[2]\": {\"key\":\"invalid\",\"value\":true}",
                "0 \"supplements[3]\": {\"key\":\"invalid\",\"value\":false}",
            ]))
        );
    }

    #[test]
    fn invalid_scope_kind_is_rejected_before_reference_projection() {
        let input = snapshot(empty(), Vec::new(), Vec::new(), words(&[]), empty());
        let invalid = project_numbered(&input, &[], &PartAliases::default(), &text("desktop"), &words(&[]));
        assert_eq!(invalid.unwrap_err().to_string(), "Unknown observation scope kind");
    }

    #[test]
    fn json_literals_keep_python_number_and_line_spelling() {
        let cases = [
            (Value::Float(1.0e-5), "1e-05"),
            (Value::Float(1.0e16), "1e+16"),
            (Value::Float(-0.0), "-0.0"),
            (Value::Float(0.0001), "0.0001"),
            (Value::Float(f64::from_bits(0x428cce430a7bc840)), "3959026700153.0312"),
            (Value::Float(f64::from_bits(0xc2dce54a634bb4a8)), "-127084484439762.62"),
            (text("a\n\u{7f}\u{2028}😀/"), "\"a\\n\\u007f\\u2028😀/\""),
        ];
        for (input, expected) in cases {
            assert_eq!(literal(&input).unwrap(), expected);
        }
    }
}
