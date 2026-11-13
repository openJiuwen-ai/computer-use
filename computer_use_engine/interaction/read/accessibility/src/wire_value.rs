// Copyright (c) Huawei Technologies Co., Ltd. 2026. All rights reserved.

//! Driver observation admission and NativeValue decoding.

use std::collections::{HashMap, HashSet};

use super::value::Value;
use super::wire::{WireError, WireResult};

mod url;
use url::resolve_url;

pub(super) const VALUE_DEPTH_LIMIT: usize = 96;
const VALUE_BUDGET: i64 = 2_000_000;
const NODE_BUDGET: usize = 65_536;
const METADATA_EXPANSION_LIMIT: usize = 64 * 1024 * 1024;
const VALUE_FIELDS: &[&str] = &["nativeType", "data"];
const RECORD_ERROR: WireError = WireError::Invalid("driver observation: unexpected or missing record fields");
const BUDGET_ERROR: WireError = WireError::Invalid("driver observation: value budget exceeded");

pub(super) fn record<'a>(
    value: &'a Value,
    required: &[&str],
    optional: &[&str],
) -> WireResult<&'a Value> {
    let fields = value.as_map().ok_or(RECORD_ERROR)?;
    if required.iter().any(|field| value.get(field).is_none())
        || fields.iter().any(|(field, _)| !required.contains(&field.as_str()) && !optional.contains(&field.as_str()))
    {
        return Err(RECORD_ERROR);
    }
    Ok(value)
}

pub(super) fn text(value: &Value) -> WireResult<&str> {
    value.as_str().filter(|value| !value.is_empty()).ok_or(WireError::Invalid(
        "driver observation: expected a nonempty string",
    ))
}

pub(super) fn array(value: &Value) -> WireResult<&[Value]> {
    value.as_seq().ok_or(WireError::Invalid("driver observation: expected an array"))
}

fn map<const N: usize>(fields: [(&str, Value); N]) -> Value {
    Value::Map(fields.into_iter().map(|(name, value)| (name.to_owned(), value)).collect())
}

fn put(fields: &mut Vec<(String, Value)>, name: String, value: Value) {
    if let Some((_, prior)) = fields.iter_mut().find(|(key, _)| *key == name) {
        *prior = value;
    } else {
        fields.push((name, value));
    }
}

fn present(value: Option<&Value>) -> Option<&Value> {
    value.filter(|value| !matches!(value, Value::Null))
}

pub(super) struct WireProperty<'a> {
    pub key_namespace: std::borrow::Cow<'a, str>,
    pub key: Value,
    pub value: Value,
    pub state: &'static str,
    pub arguments: Vec<Value>,
    pub read: Value,
}

impl WireProperty<'_> {
    pub(super) fn into_owned(self) -> WireProperty<'static> {
        WireProperty { key_namespace: self.key_namespace.into_owned().into(), ..self }
    }
}

struct MetadataValue {
    value: Value,
    depth: usize,
    owned_size: usize,
}

pub(super) struct WireValues<'a> {
    pub raw: &'a Value,
    pub platform: &'a str,
    pub session: &'a str,
    pub nodes: HashMap<&'a str, &'a Value>,
    pub roots: Vec<&'a str>,
    pub read: Value,
    pub has_semantic_urls: bool,
    pub remaining: i64,
    semantic_values: bool,
    metadata_depth: Option<usize>,
    metadata_values: Vec<MetadataValue>,
    metadata_expansion_remaining: usize,
    attribute_catalogues: HashSet<usize>,
}

impl<'a> WireValues<'a> {
    pub(super) fn new(raw: &'a Value, semantic_values: bool) -> WireResult<Self> {
        record(raw, &["kind", "session", "platform", "roots", "nodes", "read"], &["metadataValues"])?;
        let platform = raw.get("platform").and_then(Value::as_str).unwrap_or("");
        if raw.get("kind").and_then(Value::as_str) != Some("observation")
            || !matches!(platform, "macos" | "windows" | "linux")
        {
            return Err(WireError::Invalid("driver observation: unsupported transport format"));
        }
        let session = text(raw.get("session").unwrap())?;
        let rows = array(raw.get("nodes").unwrap())?;
        if rows.len() > NODE_BUDGET {
            return Err(WireError::Invalid("driver observation: object budget exceeded"));
        }
        let mut nodes = HashMap::new();
        nodes.try_reserve(rows.len())?;
        for row in rows {
            record(row, &["id", "source", "kind", "nativeType", "properties"], &["identity", "read"])?;
            let identity = text(row.get("id").unwrap())?;
            let kind = row.get("kind").and_then(Value::as_str);
            if nodes.contains_key(identity)
                || !matches!(kind, Some("element" | "text_range" | "text_marker" | "text_marker_range" | "native_value" | "unknown"))
            {
                return Err(WireError::Invalid("driver observation: duplicate identity or invalid object kind"));
            }
            text(row.get("source").unwrap())?;
            text(row.get("nativeType").unwrap())?;
            nodes.insert(identity, row);
        }
        let roots = array(raw.get("roots").unwrap())?.iter().map(text).collect::<WireResult<Vec<_>>>()?;
        let mut seen_roots = HashSet::new();
        seen_roots.try_reserve(roots.len())?;
        if roots.iter().any(|root| !seen_roots.insert(*root) || !nodes.contains_key(root)) {
            return Err(WireError::Invalid("driver observation: missing or duplicate root"));
        }
        if roots.iter().any(|root| nodes[root].get("kind").and_then(Value::as_str) != Some("element")) {
            return Err(WireError::Invalid("driver observation: an observation root must be an element"));
        }

        let mut wire = Self {
            raw,
            platform,
            session,
            nodes,
            roots,
            read: Value::Null,
            has_semantic_urls: false,
            remaining: VALUE_BUDGET,
            semantic_values,
            metadata_depth: None,
            metadata_values: Vec::new(),
            metadata_expansion_remaining: METADATA_EXPANSION_LIMIT,
            attribute_catalogues: HashSet::new(),
        };
        if let Some(entries) = raw.get("metadataValues") {
            let entries = array(entries)?;
            wire.metadata_values.try_reserve_exact(entries.len())?;
            for entry in entries {
                wire.metadata_depth = Some(0);
                let value = wire.value(entry, 0)?;
                let depth = wire.metadata_depth.take().unwrap();
                wire.metadata_values.push(MetadataValue { owned_size: owned_size(&value), value, depth });
            }
        }
        wire.read = wire.read_info(raw.get("read"), false)?;
        let details = wire.read.get("details").unwrap();
        let observation = details.get("observation_id").and_then(Value::as_str);
        if present(wire.read.get("intervalNs")).is_none() || observation.is_none() {
            return Err(WireError::Invalid("driver observation: capture identity and interval are required"));
        }
        text(details.get("observation_id").unwrap())?;
        text(details.get("surface").unwrap_or(&Value::Null))?;
        Ok(wire)
    }

    fn debit(&mut self, depth: usize) -> WireResult<()> {
        self.remaining -= 1;
        if self.remaining < 0 || depth > VALUE_DEPTH_LIMIT {
            return Err(BUDGET_ERROR);
        }
        Ok(())
    }

    fn pairs(&mut self, raw: &Value, depth: usize, pooled: bool) -> WireResult<Vec<(String, Value)>> {
        let mut pairs = Vec::new();
        for pair in array(raw)? {
            let [key, value] = array(pair)? else {
                return Err(WireError::Invalid("driver observation: invalid metadata pair"));
            };
            let key = text(key)?;
            let value = if pooled { self.metadata_value(value, depth + 1)? } else { self.value(value, depth + 1)? };
            put(&mut pairs, key.to_owned(), value);
        }
        Ok(pairs)
    }

    pub(super) fn metadata_value(&mut self, raw: &Value, depth: usize) -> WireResult<Value> {
        let Some(index) = self.metadata_reference(raw, depth)? else { return self.value(raw, depth); };
        let entry = &self.metadata_values[index];
        // Python shares immutable pool entries. Bound copies of retained
        // metadata before allocating them.
        self.metadata_expansion_remaining = self.metadata_expansion_remaining.checked_sub(entry.owned_size)
            .ok_or(WireError::Unsupported("metadata expansion limit"))?;
        Ok(entry.value.clone())
    }

    fn metadata_reference(&mut self, raw: &Value, depth: usize) -> WireResult<Option<usize>> {
        let Some(index) = raw.get("metadataValue") else { return Ok(None); };
        self.debit(depth)?;
        record(raw, &["metadataValue"], &[])?;
        let index = index.as_i64().and_then(|index| usize::try_from(index).ok())
            .filter(|index| *index < self.metadata_values.len())
            .ok_or(WireError::Invalid("driver observation: invalid metadata value reference"))?;
        if depth + self.metadata_values[index].depth > VALUE_DEPTH_LIMIT {
            return Err(BUDGET_ERROR);
        }
        Ok(Some(index))
    }

    fn attribute_catalogue(&mut self, raw: &Value) -> WireResult<Value> {
        let value = self.metadata_value(raw, 0)?;
        let index = raw.get("metadataValue").unwrap().as_i64().unwrap() as usize;
        if !self.attribute_catalogues.contains(&index) {
            let catalogue = &array(self.raw.get("metadataValues").unwrap())?[index];
            record(catalogue, VALUE_FIELDS, &[])?;
            if catalogue.get("nativeType").and_then(Value::as_str) != Some("CFArray") {
                return Err(WireError::Invalid("driver observation: invalid shared attribute catalogue"));
            }
            let data = record(catalogue.get("data").unwrap(), &["items"], &[])?;
            for item in array(data.get("items").unwrap())? {
                record(item, VALUE_FIELDS, &[])?;
                if item.get("nativeType").and_then(Value::as_str) != Some("CFString")
                    || !item.get("data").and_then(Value::as_str).is_some_and(str::is_ascii)
                {
                    return Err(WireError::Invalid("driver observation: invalid shared attribute name"));
                }
            }
            self.attribute_catalogues.insert(index);
        }
        Ok(value)
    }

    pub(super) fn value(&mut self, raw: &Value, depth: usize) -> WireResult<Value> {
        self.debit(depth)?;
        if let Some(maximum) = &mut self.metadata_depth {
            *maximum = (*maximum).max(depth);
        }
        record(raw, VALUE_FIELDS, &["metadata", "raw"])?;
        let native_type = text(raw.get("nativeType").unwrap())?;
        let data = raw.get("data").unwrap();
        if raw.get("metadata").is_none() && raw.get("raw").is_none() && native_type != "CFDate" {
            match data {
                Value::Text(_) | Value::Bool(_) | Value::Int(_) | Value::BigInt(_) | Value::Null => {
                    return Ok(data.clone());
                }
                Value::Float(number) if number.is_finite() => return Ok(data.clone()),
                Value::Map(fields) if matches!(fields.as_slice(), [(tag, _)]
                    if matches!(tag.as_str(), "ref" | "items" | "fields" | "entries" | "integer")) => {
                    return self.tagged_value(fields, native_type, depth);
                }
                _ => (),
            }
        }
        self.value_with_metadata(raw, data, native_type, depth)
    }

    fn value_with_metadata(&mut self, raw: &Value, data: &Value, native_type: &str, depth: usize) -> WireResult<Value> {
        let metadata = raw.get("metadata").map(|metadata| self.pairs(metadata, depth + 1, false))
            .transpose()?.unwrap_or_default();
        let raw_bytes = raw.get("raw").map(decode_base64).transpose()?;
        let data = match data {
            Value::Map(fields) => self.tagged_value(fields, native_type, depth)?,
            Value::Text(_) | Value::Bool(_) | Value::Int(_) | Value::BigInt(_) | Value::Null => data.clone(),
            Value::Float(number) if number.is_finite() => data.clone(),
            Value::Float(_) => return Err(WireError::Invalid("driver observation: special float needs a tag")),
            _ => return Err(WireError::Invalid("driver observation: invalid scalar")),
        };
        // The finalizer does not recurse. Keeping it outside the recursive
        // decoder leaves room for nested Core Foundation dictionary keys.
        self.finish_value(data, native_type, metadata, raw_bytes)
    }

    fn finish_value(
        &mut self, data: Value, native_type: &str, mut metadata: Vec<(String, Value)>, raw_bytes: Option<Vec<u8>>,
    ) -> WireResult<Value> {
        for key in ["toolkit_value", "enum_name"] {
            if let Some(index) = metadata.iter().position(|(name, _)| name == key) {
                return Ok(metadata.swap_remove(index).1);
            }
        }
        if native_type == "CFDate" && matches!(data, Value::Int(_) | Value::BigInt(_) | Value::Float(_)) {
            return Ok(map([("nativeType", Value::Text("CFDate".to_owned())), ("absoluteTime", data)]));
        }
        let metadata = Value::Map(metadata);
        if native_type == "CFURL" {
            if let (Some(data_text), Some(base)) = (data.as_str(), metadata.get("base").and_then(Value::as_str)) {
                self.has_semantic_urls = true;
                if self.semantic_values {
                    return resolve_url(base, data_text).map(Value::Text);
                }
                return Ok(data);
            }
        }
        if let Value::Bytes(data) = data {
            if matches!(metadata.get("string_encoding").and_then(Value::as_str), Some("utf-16le" | "utf-16-le")) {
                return decode_utf16(&data).map(Value::Text);
            }
            return Ok(map([
                ("nativeType", Value::Text(native_type.to_owned())),
                ("base64", Value::Text(encode_base64(&data))),
            ]));
        }
        if let Some(raw_bytes) = raw_bytes {
            if matches!(metadata.get("raw_encoding").and_then(Value::as_str), Some("utf-16le" | "utf-16-le")) {
                return decode_utf16(&raw_bytes).map(Value::Text);
            }
        }
        if let Value::Float(number) = data {
            if !number.is_finite() {
                let name = if number.is_nan() { "NaN" } else if number.is_sign_positive() { "+Infinity" } else { "-Infinity" };
                return Ok(map([
                    ("nativeType", Value::Text(native_type.to_owned())),
                    ("value", Value::Text(name.to_owned())),
                ]));
            }
        }
        Ok(data)
    }

    fn tagged_value(&mut self, fields: &[(String, Value)], native_type: &str, depth: usize) -> WireResult<Value> {
        let [(tag, payload)] = fields else {
            return Err(WireError::Invalid("driver observation: ambiguous value tag"));
        };
        match tag.as_str() {
            "ref" => {
                let identity = text(payload)?;
                let node = self.nodes.get(identity).ok_or(WireError::Invalid("driver observation: undeclared reference"))?;
                Ok(map([("nativeType", node.get("nativeType").unwrap().clone()), ("node", payload.clone())]))
            }
            "items" => array(payload)?.iter().map(|item| self.item(item, depth + 1)).collect::<WireResult<_>>().map(Value::Seq),
            "fields" | "entries" => self.tagged_record(payload, tag, native_type, depth),
            "bytes" => decode_base64(payload).map(Value::Bytes),
            "integer" => decode_integer(payload),
            "float" => {
                let number = match payload.as_str() {
                    Some("-0") => -0.0,
                    Some("nan") => f64::NAN,
                    Some("inf") => f64::INFINITY,
                    Some("-inf") => f64::NEG_INFINITY,
                    _ => return Err(WireError::Invalid("driver observation: unknown value tag")),
                };
                Ok(Value::Float(number))
            }
            _ => Err(WireError::Invalid("driver observation: unknown value tag")),
        }
    }

    fn tagged_record(&mut self, raw: &Value, tag: &str, native_type: &str, depth: usize) -> WireResult<Value> {
        let mut pairs = Vec::new();
        for pair in array(raw)? {
            let [key, item] = array(pair)? else {
                return Err(WireError::Invalid("driver observation: invalid record pair"));
            };
            let key = if tag == "entries" {
                self.value(key, depth + 1)?
            } else {
                if !matches!(key, Value::Null | Value::Text(_) | Value::Int(_) | Value::BigInt(_)) {
                    return Err(WireError::Invalid("driver observation: invalid record key"));
                }
                key.clone()
            };
            pairs.push((key, self.item(item, depth + 1)?));
        }
        let mut keys = HashSet::new();
        if pairs.iter().all(|(key, _)| key.as_str().is_some_and(|key| keys.insert(key))) {
            return Ok(Value::Map(pairs.into_iter().map(|(key, value)| {
                let Value::Text(key) = key else { unreachable!() };
                (key, value)
            }).collect()));
        }
        let pairs = Value::Seq(pairs.into_iter().map(|(key, value)| Value::Seq(vec![key, value])).collect());
        Ok(map([("nativeType", Value::Text(native_type.to_owned())), (tag, pairs)]))
    }

    fn item(&mut self, raw: &Value, depth: usize) -> WireResult<Value> {
        if let Some(fields) = raw.as_map() {
            if let [(name, issue)] = fields {
                if name == "issue" {
                    return Ok(map([("issue", self.issue(issue, depth + 1, false)?)]));
                }
            }
        }
        self.value(raw, depth + 1)
    }

    pub(super) fn issue(&mut self, raw: &Value, depth: usize, pooled: bool) -> WireResult<Value> {
        record(raw, &["reason"], &["path", "detail", "nativeStatus", "continuation"])?;
        let reason = raw.get("reason").unwrap();
        text(reason)?;
        let mut fields = vec![("reason".to_owned(), reason.clone())];
        if let Some(path) = raw.get("path") {
            if array(path)?.iter().any(|part| !matches!(part, Value::Text(_) | Value::Int(_) | Value::BigInt(_))) {
                return Err(WireError::Invalid("driver observation: invalid issue path"));
            }
            fields.push(("path".to_owned(), path.clone()));
        }
        if let Some(detail) = present(raw.get("detail")) {
            if detail.as_str().is_none() {
                return Err(WireError::Invalid("driver observation: invalid issue detail"));
            }
            fields.push(("detail".to_owned(), detail.clone()));
        }
        for key in ["nativeStatus", "continuation"] {
            if let Some(raw) = present(raw.get(key)) {
                let value = if pooled { self.metadata_value(raw, depth + 1)? } else { self.value(raw, depth + 1)? };
                fields.push((key.to_owned(), value));
            }
        }
        Ok(Value::Map(fields))
    }

    pub(super) fn read_info(&mut self, raw: Option<&Value>, query_only: bool) -> WireResult<Value> {
        let Some(raw) = present(raw) else {
            return Ok(map([("details", Value::Map(Vec::new())), ("issues", Value::Seq(Vec::new()))]));
        };
        record(raw, &[], &["api", "producer", "cached", "intervalNs", "complete", "issues", "details"])?;
        for key in ["api", "producer"] {
            if let Some(value) = present(raw.get(key)) {
                text(value)?;
            }
        }
        for key in ["cached", "complete"] {
            if present(raw.get(key)).is_some_and(|value| value.as_bool().is_none()) {
                return Err(WireError::Invalid("driver observation: invalid read state"));
            }
        }
        if let Some(interval) = present(raw.get("intervalNs")) {
            let [start, end] = array(interval)? else {
                return Err(WireError::Invalid("driver observation: invalid capture interval"));
            };
            if matches!(start, Value::BigInt(_)) || matches!(end, Value::BigInt(_)) {
                return Err(WireError::Unsupported("wide capture interval"));
            }
            if !matches!((start.as_i64(), end.as_i64()), (Some(start), Some(end)) if start >= 0 && end >= start) {
                return Err(WireError::Invalid("driver observation: invalid capture interval"));
            }
        }
        let details = raw.get("details").map(|raw| self.read_details(raw, query_only)).transpose()?.unwrap_or_default();
        let mut issues = Vec::new();
        if let Some(raw) = raw.get("issues") {
            for item in array(raw)? {
                issues.push(self.issue(item, 0, true)?);
            }
        }
        let mut fields = raw.as_map().unwrap().to_vec();
        put(&mut fields, "details".to_owned(), Value::Map(details));
        put(&mut fields, "issues".to_owned(), Value::Seq(issues));
        Ok(Value::Map(fields))
    }

    fn read_details(&mut self, raw: &Value, query_only: bool) -> WireResult<Vec<(String, Value)>> {
        let details = array(raw)?;
        if query_only {
            if let [owner, index] = details {
                if let (Some([owner_name, owner]), Some([index_name, index])) = (owner.as_seq(), index.as_seq()) {
                    if owner_name.as_str() == Some("batch_owner") && index_name.as_str() == Some("batch_index") {
                        for slot in [owner, index] {
                            if self.metadata_reference(slot, 1)?.is_none() {
                                self.value(slot, 1)?;
                            }
                        }
                        return Ok(Vec::new());
                    }
                }
            }
        }
        self.pairs(raw, 0, true)
    }

    pub(super) fn property(&mut self, raw: &'a Value, query_only: bool) -> WireResult<WireProperty<'a>> {
        record(raw, &["key", "value"], &["state", "arguments", "read"])?;
        let [namespace, key] = array(raw.get("key").unwrap())? else {
            return Err(WireError::Invalid("driver observation: invalid property key"));
        };
        if !matches!(key, Value::Text(_) | Value::Int(_) | Value::BigInt(_)) {
            return Err(WireError::Invalid("driver observation: invalid property key"));
        }
        let state = match raw.get("state") {
            None => "value",
            Some(Value::Text(state)) => match state.as_str() {
                "value" => "value",
                "absent" => "absent",
                "unsupported" => "unsupported",
                "not_read" => "not_read",
                "error" => "error",
                "timeout" => "timeout",
                "unavailable" => "unavailable",
                "redacted" => "redacted",
                _ => return Err(WireError::Invalid("driver observation: invalid property state or payload")),
            },
            _ => return Err(WireError::Invalid("driver observation: invalid property state or payload")),
        };
        let value = raw.get("value").unwrap();
        if (state == "value") == matches!(value, Value::Null) {
            return Err(WireError::Invalid("driver observation: invalid property state or payload"));
        }
        let value = if matches!(value, Value::Null) {
            Value::Null
        } else if self.platform == "macos" && raw.as_map().unwrap().len() == 2
            && namespace.as_str() == Some("toolkit") && key.as_str() == Some("attribute_names")
            && value.get("metadataValue").is_some()
        {
            self.attribute_catalogue(value)?
        } else {
            self.value(value, 0)?
        };
        let key_namespace = text(namespace)?;
        let mut arguments = Vec::new();
        if let Some(raw) = raw.get("arguments") {
            for argument in array(raw)? {
                arguments.push(self.value(argument, 0)?);
            }
        }
        let read = self.read_info(raw.get("read"), query_only)?;
        Ok(WireProperty { key_namespace: key_namespace.into(), key: key.clone(), value, state, arguments, read })
    }
}

fn owned_size(value: &Value) -> usize {
    let children = match value {
        Value::Text(text) | Value::BigInt(text) => text.len(),
        Value::Bytes(data) => data.len(),
        Value::Seq(items) => items.iter().map(owned_size).sum(),
        Value::Map(fields) => fields.iter().map(|(key, value)| {
            std::mem::size_of::<String>() + key.len() + owned_size(value)
        }).sum(),
        _ => 0,
    };
    std::mem::size_of::<Value>() + children
}

fn decode_integer(payload: &Value) -> WireResult<Value> {
    let value = payload.as_str().ok_or(WireError::Invalid("driver observation: invalid integer"))?;
    let digits = value.strip_prefix('-').unwrap_or(value);
    if !value.is_ascii() || digits.len() > 4300 {
        return Err(WireError::Unsupported("noncanonical tagged integer"));
    }
    if digits.is_empty() || !digits.bytes().all(|byte| byte.is_ascii_digit()) {
        return Err(WireError::Invalid("driver observation: invalid integer"));
    }
    let digits = digits.trim_start_matches('0');
    if digits.is_empty() {
        return Ok(Value::Int(0));
    }
    let number = if value.starts_with('-') { format!("-{digits}") } else { digits.to_owned() };
    Ok(number.parse().map(Value::Int).unwrap_or(Value::BigInt(number)))
}

const BASE64: &[u8; 64] = b"ABCDEFGHIJKLMNOPQRSTUVWXYZabcdefghijklmnopqrstuvwxyz0123456789+/";

fn decode_base64(value: &Value) -> WireResult<Vec<u8>> {
    const INVALID: WireError = WireError::Invalid("driver observation: invalid base64 data");
    let text = value.as_str().ok_or(WireError::Invalid("driver observation: expected base64 text"))?;
    let data = text.trim_end_matches('=');
    let padding = text.len() - data.len();
    let remaining = data.len() % 4;
    if padding > 2 || (remaining == 0 && padding != 0) {
        return Err(WireError::Unsupported("noncanonical base64 padding"));
    }
    if !matches!((remaining, padding), (0, 0) | (2, 2) | (3, 1)) {
        return Err(INVALID);
    }
    let mut result = Vec::new();
    result.try_reserve_exact(data.len() * 3 / 4)?;
    let mut bits = 0_u16;
    let mut count = 0;
    for byte in data.bytes() {
        let number = match byte {
            b'A'..=b'Z' => byte - b'A',
            b'a'..=b'z' => byte - b'a' + 26,
            b'0'..=b'9' => byte - b'0' + 52,
            b'+' => 62,
            b'/' => 63,
            _ => return Err(INVALID),
        };
        bits = (bits << 6) | u16::from(number);
        count += 6;
        if count >= 8 {
            count -= 8;
            result.push((bits >> count) as u8);
        }
    }
    Ok(result)
}

fn encode_base64(value: &[u8]) -> String {
    let mut text = String::with_capacity(value.len().div_ceil(3) * 4);
    for bytes in value.chunks(3) {
        let first = bytes[0];
        let second = bytes.get(1).copied().unwrap_or(0);
        let third = bytes.get(2).copied().unwrap_or(0);
        text.push(char::from(BASE64[usize::from(first >> 2)]));
        text.push(char::from(BASE64[usize::from((first & 3) << 4 | second >> 4)]));
        text.push(if bytes.len() > 1 { char::from(BASE64[usize::from((second & 15) << 2 | third >> 6)]) } else { '=' });
        text.push(if bytes.len() > 2 { char::from(BASE64[usize::from(third & 63)]) } else { '=' });
    }
    text
}

fn decode_utf16(value: &[u8]) -> WireResult<String> {
    if value.len() % 2 != 0 {
        return Err(WireError::Invalid("driver observation: invalid UTF-16 bytes"));
    }
    let words = value.chunks_exact(2).map(|pair| u16::from_le_bytes([pair[0], pair[1]]));
    std::char::decode_utf16(words).collect::<Result<String, _>>()
        .map_err(|_| WireError::Unsupported("unpaired UTF-16 surrogate"))
}

#[cfg(test)]
mod tests {
    use super::*;
    use crate::wire::decode_json;

    fn observation() -> Value {
        decode_json(br#"{
            "kind": "observation", "session": "session", "platform": "macos", "roots": ["root"],
            "nodes": [{"id":"root","source":"macos.ax","kind":"element","nativeType":"AXUIElement","properties":[]}],
            "read": {
                "intervalNs": [1, 2],
                "details": [
                    ["surface", {"data": "window", "nativeType": "CFString"}],
                    ["observation_id", {"data": "capture", "nativeType": "CFString"}]
                ]
            },
            "metadataValues": [
                {"data":{"items":[{"data":"AXTitle","nativeType":"CFString"}]},"nativeType":"CFArray"},
                {"data":2,"nativeType":"CFNumber"}
            ]
        }"#).unwrap()
    }

    #[test]
    fn metadata_depth_and_budget_are_debited_at_each_occurrence() {
        let raw = observation();
        let mut wire = WireValues::new(&raw, false).unwrap();
        let reference = decode_json(br#"{"metadataValue":0}"#).unwrap();
        let bad_reference = decode_json(br#"{"metadataValue":2}"#).unwrap();
        let before = wire.remaining;
        let names = Value::Seq(vec![Value::Text("AXTitle".to_owned())]);
        assert_eq!(wire.metadata_value(&reference, 94), Ok(names.clone()));
        assert_eq!(wire.metadata_value(&reference, 94), Ok(names));
        assert_eq!(wire.metadata_value(&reference, 95), Err(BUDGET_ERROR));
        assert_eq!(wire.remaining, before - 3);
        assert_eq!(
            wire.metadata_value(&bad_reference, 0),
            Err(WireError::Invalid("driver observation: invalid metadata value reference"))
        );
        assert_eq!(wire.remaining, before - 4);
    }

    #[test]
    fn read_decodes_queries_and_native_values_in_original_order() {
        let raw = observation();
        let mut wire = WireValues::new(&raw, false).unwrap();
        let read = decode_json(br#"{
            "complete":false,
            "details":[["batch_owner",{"metadataValue":1}],["batch_index",{"metadataValue":1}]],
            "issues":[{"reason":"absent","path":["field",0],"nativeStatus":{"metadataValue":1}}]
        }"#).unwrap();
        let value = decode_json(br#"{
            "nativeType":"CFArray",
            "data":{"items":[{"data":{"ref":"root"},"nativeType":"AXUIElement"},
                            {"data":{"float":"nan"},"nativeType":"CFNumber"},
                            {"data":{"bytes":"6Zs="},"metadata":[["string_encoding",{"data":"utf-16le","nativeType":"CFString"}]],"nativeType":"CFData"}]}
        }"#).unwrap();
        let before = wire.remaining;
        let decoded_read = wire.read_info(Some(&read), true).unwrap();
        assert_eq!(wire.remaining, before - 3);
        assert_eq!(decoded_read.get("details"), Some(&Value::Map(Vec::new())));
        assert_eq!(
            decoded_read.get("issues"),
            Some(&decode_json(br#"[{"reason":"absent","path":["field",0],"nativeStatus":2}]"#).unwrap())
        );
        assert_eq!(
            wire.value(&value, 0),
            Ok(decode_json(br#"[{"nativeType":"AXUIElement","node":"root"},{"nativeType":"CFNumber","value":"NaN"},"\u9be9"]"#).unwrap())
        );
    }

    #[test]
    fn omitted_batch_provenance_does_not_materialize_pooled_values() {
        let raw = observation();
        let read = decode_json(br#"{
            "details":[["batch_owner",{"metadataValue":0}],["batch_index",{"metadataValue":1}]]
        }"#).unwrap();
        let mut wire = WireValues::new(&raw, false).unwrap();
        wire.metadata_expansion_remaining = 0;
        let before = wire.remaining;
        let query = wire.read_info(Some(&read), true).unwrap();
        assert_eq!(query.get("details"), Some(&Value::Map(Vec::new())));
        assert_eq!(wire.remaining, before - 2);
        assert_eq!(
            wire.read_info(Some(&read), false),
            Err(WireError::Unsupported("metadata expansion limit"))
        );
        let invalid = decode_json(br#"{
            "details":[["batch_owner",{"metadataValue":2}],["batch_index",{"metadataValue":1}]]
        }"#).unwrap();
        assert_eq!(
            wire.read_info(Some(&invalid), true),
            Err(WireError::Invalid("driver observation: invalid metadata value reference"))
        );
    }

    #[test]
    fn isolated_surrogate_in_original_cfstring_is_reported_without_replacing_it() {
        let raw = observation();
        let mut wire = WireValues::new(&raw, false).unwrap();
        // Matches AXNativeValueEncoding.string: a repaired preview plus the
        // original little-endian units, including an isolated high surrogate.
        let input = decode_json(br#"{
            "nativeType":"CFString","data":"A\ufffdB","raw":"QQAA2EIA",
            "metadata":[["raw_encoding",{"nativeType":"CFString","data":"utf-16-le"}]]
        }"#).unwrap();
        assert_eq!(
            wire.value(&input, 0),
            Err(WireError::Unsupported("unpaired UTF-16 surrogate"))
        );
        let paired = decode_json(br#"{
            "nativeType":"CFString","data":"preview","raw":"PdgA3g==",
            "metadata":[["raw_encoding",{"nativeType":"CFString","data":"utf-16-le"}]]
        }"#).unwrap();
        assert_eq!(wire.value(&paired, 0), Ok(Value::Text("😀".to_owned())));
    }

    #[test]
    fn file_url_semantics_are_explicit() {
        let raw = observation();
        let input = decode_json(br#"{"nativeType":"CFURL","data":"../item%20one","metadata":[["base",{"nativeType":"CFURL","data":"file:///tmp/parent/"}]]}"#).unwrap();
        let mut query = WireValues::new(&raw, false).unwrap();
        let mut semantic = WireValues::new(&raw, true).unwrap();
        assert_eq!(query.value(&input, 0), Ok(Value::Text("../item%20one".to_owned())));
        assert!(query.has_semantic_urls);
        assert_eq!(semantic.value(&input, 0), Ok(Value::Text("file:///tmp/item%20one".to_owned())));
        assert_eq!(
            resolve_url("https://example.test/base/", "item"),
            Ok("https://example.test/base/item".to_owned())
        );
    }
}
