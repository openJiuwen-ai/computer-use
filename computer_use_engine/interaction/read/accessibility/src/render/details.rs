// Copyright (c) Huawei Technologies Co., Ltd. 2026. All rights reserved.

//! Captured diagnostics and uncompressed records, independent of the UI provider.

use std::collections::HashMap;
use std::fmt::Write;

use super::super::value::{diagnostic, Value};
use super::text::{json, JsonFormat};
use super::{quoted, RenderedNode, RenderedSection, TextError};

const FIRST_FIELDS: [&str; 9] = [
    "role",
    "identifier",
    "url",
    "role_description",
    "name",
    "description",
    "control_value.value",
    "text.content",
    "numeric_value.value",
];

type Result<T> = std::result::Result<T, TextError>;

fn text(value: &str) -> Value {
    Value::Text(value.to_owned())
}

fn map<const N: usize>(items: [(&str, Value); N]) -> Value {
    Value::Map(items.into_iter().map(|(key, value)| (key.to_owned(), value)).collect())
}

fn member_text<'a>(item: &'a Value, key: &'static str) -> Result<&'a str> {
    item.get(key).and_then(Value::as_str).ok_or(TextError::Invalid(key))
}

fn member_seq<'a>(item: &'a Value, key: &'static str) -> Result<&'a [Value]> {
    item.get(key).and_then(Value::as_seq).ok_or(TextError::Invalid(key))
}

fn encode_issue(issue: &Value, typed_alternatives: Option<&Value>) -> Result<Value> {
    let continuation = issue.get("continuation").filter(|value| **value != Value::Null);
    let continuation = continuation.map_or(Value::Null, |value| Value::Map(
        ["token", "kind", "consistency", "unit", "total", "delivered"].into_iter()
            .filter_map(|key| value.get(key).filter(|value| **value != Value::Null)
                .map(|value| (key.to_owned(), diagnostic::encode(value))))
            .collect(),
    ));
    let value = map([
        ("path", Value::Seq(member_seq(issue, "path")?.to_vec())),
        ("state", text(member_text(issue, "state")?)),
        ("detail", issue.get("detail").cloned().unwrap_or(Value::Null)),
        ("continuation", continuation),
        (
            "alternatives",
            typed_alternatives.cloned().unwrap_or_else(||
                issue.get("alternatives").map_or_else(|| Value::Seq(Vec::new()), diagnostic::encode)),
        ),
    ]);
    Ok(value)
}

fn issue_node(issue: &Value) -> Option<&Value> {
    let path = issue.get("path")?.as_seq()?;
    (path.first()?.as_str()? == "nodes").then_some(())?;
    path.get(1)
}

fn belongs_to(value: &Value, token: &str) -> bool {
    match value {
        Value::Text(value) | Value::BigInt(value) => value == token,
        Value::Int(value) => value.to_string() == token,
        _ => false,
    }
}

pub(crate) struct DetailSource<'a> {
    pub(crate) backend: &'a str,
    pub(crate) session: &'a str,
    pub(crate) observation: &'a str,
    pub(crate) scope: &'a [Value],
    facts: &'a Value,
    issues: &'a [Value],
    typed_alternatives: Option<&'a Value>,
    references: HashMap<&'a str, &'a str>,
    aliases: HashMap<&'a str, &'a str>,
    nodes: HashMap<&'a str, &'a Value>,
    relations: HashMap<&'a str, Vec<&'a Value>>,
}

impl<'a> DetailSource<'a> {
    pub(crate) fn new(
        backend: &'a str,
        session: &'a str,
        observation: &'a str,
        scope: &'a [Value],
        facts: &'a Value,
        issues: &'a [Value],
        refs: impl IntoIterator<Item = (&'a str, &'a str)>,
    ) -> Result<Self> {
        let mut references = HashMap::new();
        let mut aliases = HashMap::new();
        for (reference, token) in refs {
            if references.insert(reference, token).is_some() || aliases.insert(token, reference).is_some() {
                return Err(TextError::Invalid("duplicate detail alias"));
            }
        }
        let mut nodes = HashMap::new();
        for node in member_seq(facts, "nodes")? {
            nodes.insert(member_text(node, "node")?, node);
        }
        let mut relations = HashMap::<_, Vec<_>>::new();
        for relation in member_seq(facts, "relations")? {
            relations
                .entry(member_text(relation, "source")?)
                .or_default()
                .push(relation);
        }
        Ok(Self {
            backend,
            session,
            observation,
            scope,
            facts,
            issues,
            typed_alternatives: None,
            references,
            aliases,
            nodes,
            relations,
        })
    }

    pub(crate) fn with_typed_alternatives(mut self, alternatives: Option<&'a Value>) -> Self {
        self.typed_alternatives = alternatives;
        self
    }

    pub(crate) fn read_issues(&self, element: Option<&str>) -> Result<(String, Vec<Value>)> {
        let mut records = Vec::new();
        for (index, issue) in self.issues.iter().enumerate() {
            let owner = issue_node(issue);
            if owner.zip(element).is_some_and(|(owner, element)| !belongs_to(owner, element)) {
                continue;
            }
            let reference = owner.and_then(Value::as_str)
                .and_then(|value| self.aliases.get(value))
                .map_or(Value::Null, |value| text(value));
            records.push(map([
                ("index", Value::Int(index as i64)),
                ("ref", reference),
                ("issue", encode_issue(issue, self.typed_alternatives.and_then(|items| items.get(&index.to_string())))?),
            ]));
        }
        if records.is_empty() {
            return Ok((String::new(), records));
        }
        let identity = map([("session", text(self.session)), ("observation", text(self.observation))]);
        let mut body = format!("Native read issues {}:", json(&identity, JsonFormat::Diagnostic)?);
        for record in &records {
            body.push_str("\n\t");
            body.push_str(&json(record, JsonFormat::Diagnostic)?);
        }
        Ok((body, records))
    }

    fn record(&self, reference: &str, node: &Value, first_fields: &[&str]) -> Result<String> {
        let mut output = format!("{reference} native element");
        let token = member_text(node, "node")?;
        let requested = member_seq(node, "requestedFields")?;
        let mut fields = Vec::new();
        for name in requested {
            fields.push(name.as_str().ok_or(TextError::Invalid("requested field"))?);
        }
        let ordered = first_fields
            .iter()
            .copied()
            .filter(|name| fields.contains(name))
            .chain(fields.iter().copied().filter(|name| !first_fields.contains(name)));
        let values = node.get("values").ok_or(TextError::Invalid("values"))?;
        let status = node.get("fieldStatus").ok_or(TextError::Invalid("fieldStatus"))?;
        for name in ordered {
            let name_text = quoted(name);
            if let Some(value) = values.get(name) {
                let value = json(value, JsonFormat::Literal)?;
                let _ = write!(output, "\n\t{name_text}: {value}");
            }
            if let Some(value) = status.get(name) {
                let value = json(value, JsonFormat::Literal)?;
                let _ = write!(output, "\n\t{name_text} status: {value}");
            }
        }
        let actions = node.get("actions").ok_or(TextError::Invalid("actions"))?;
        let _ = write!(output, "\n\tNative actions: {}", json(actions, JsonFormat::Literal)?);
        if let Some(relations) = self.relations.get(token) {
            for relation in relations {
                let property = quoted(member_text(relation, "property")?);
                let mut fields = relation
                    .as_map()
                    .ok_or(TextError::Invalid("relation"))?
                    .iter()
                    .filter(|(key, _)| !matches!(key.as_str(), "source" | "property" | "targets"))
                    .cloned()
                    .collect::<Vec<_>>();
                let mut targets = Vec::new();
                for item in member_seq(relation, "targets")? {
                    let token = item.as_str().ok_or(TextError::Invalid("relation target"))?;
                    let target = self.aliases.get(token).map_or_else(
                        || map([("native", text(token)), ("ref", Value::Null)]),
                        |alias| text(alias),
                    );
                    targets.push(target);
                }
                fields.push(("targets".to_owned(), Value::Seq(targets)));
                let _ = write!(
                    output,
                    "\n\tRelation {property}: {}",
                    json(&Value::Map(fields), JsonFormat::Literal)?
                );
            }
        }
        Ok(output)
    }

    pub(crate) fn element_source(&self, reference: &str) -> Result<String> {
        let token = self.references.get(reference).ok_or(TextError::Invalid("element was not recorded"))?;
        self.render_element(reference, token)
    }

    fn render_element(&self, reference: &str, token: &str) -> Result<String> {
        let identity = map([
            ("backend", text(self.backend)),
            ("session", text(self.session)),
            ("observation", text(self.observation)),
            ("node", text(token)),
            ("scope", Value::Seq(self.scope.to_vec())),
            (
                "observed",
                self.facts
                    .get("observed")
                    .cloned()
                    .unwrap_or_else(|| Value::Map(Vec::new())),
            ),
        ]);
        let mut output = format!("Source: {}\n", json(&identity, JsonFormat::Literal)?);
        if let Some(node) = self.nodes.get(token) {
            if node.get("observed").and_then(Value::as_bool) == Some(false) {
                output.push_str("Collection: referenced element was not expanded; retained boundary facts follow.\n");
            }
            output.push_str(&self.record(reference, node, &FIRST_FIELDS)?);
        } else {
            let _ = write!(
                output,
                "{reference} native element: referenced but not collected in this capture."
            );
        }
        let (issues, _) = self.read_issues(Some(token))?;
        if !issues.is_empty() {
            output.push('\n');
            output.push_str(&issues);
        }
        Ok(output)
    }

    pub(crate) fn fallback(
        &self,
        members: &[String],
        error: &str,
        detail: &str,
        refresh: bool,
    ) -> Result<RenderedSection> {
        let root = members.first().ok_or(TextError::Invalid("fallback root"))?;
        let prefix = format!(
            "Presentation fallback for {root}: uncompressed collected facts ({error}: {}). Relations: element references.",
            quoted(detail),
        );
        let mut text = prefix.clone();
        let mut nodes = Vec::with_capacity(members.len());
        for reference in members {
            let token = self
                .references
                .get(reference.as_str())
                .ok_or(TextError::Invalid("fallback reference"))?;
            let node = self.nodes.get(token).ok_or(TextError::Invalid("fallback node"))?;
            let record = self.record(reference, node, &[])?;
            text.push('\n');
            text.push_str(&record);
            nodes.push(RenderedNode {
                reference: reference.clone(),
                content: format!("{reference} native element"),
                text: record,
                ..RenderedNode::default()
            });
        }
        Ok(RenderedSection {
            text,
            nodes,
            prefix,
            suffix: String::new(),
            refresh,
        })
    }
}

pub(crate) fn element_details(reference: &str, sources: &[DetailSource<'_>]) -> Result<String> {
    let mut selected = Vec::new();
    let mut identity = None;
    let mut instance = None;
    for source in sources {
        let Some(&token) = source.references.get(reference) else {
            continue;
        };
        let key = (source.backend, source.session, token);
        if identity.replace(key).is_some_and(|previous| previous != key) {
            return Err(TextError::Invalid("conflicting captured identities"));
        }
        if let Some(current) = source.scope.get(1).filter(|value| **value != Value::Null) {
            if instance.replace(current).is_some_and(|previous| previous != current) {
                return Err(TextError::Invalid("conflicting captured identities"));
            }
        }
        if !selected.iter().any(|(previous, _): &(&DetailSource<'_>, _)| {
            std::ptr::eq(previous.facts, source.facts) && previous.scope == source.scope
        }) {
            selected.push((source, token));
        }
    }
    if selected.is_empty() {
        return Err(TextError::Invalid("element was not recorded"));
    }
    let mut output = format!("Captured element {reference}:");
    for (source, token) in selected {
        output.push_str("\n\n");
        output.push_str(&source.render_element(reference, token)?);
    }
    Ok(output)
}

pub(crate) fn detail_slice(text: &str, start: usize, length: usize) -> Option<(&str, usize, usize)> {
    let end = start.checked_add(length)?;
    let mut start_byte = None;
    let mut end_byte = None;
    let mut total = 0;
    for (index, (offset, _)) in text.char_indices().enumerate() {
        if index == start {
            start_byte = Some(offset);
        }
        if index == end {
            end_byte = Some(offset);
        }
        total = index + 1;
    }
    let start_byte = if total == start { text.len() } else { start_byte? };
    let end_byte = end_byte.unwrap_or(text.len());
    Some((&text[start_byte..end_byte], end.min(total), total))
}
