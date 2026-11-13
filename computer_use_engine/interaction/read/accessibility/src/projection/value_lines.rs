// Copyright (c) Huawei Technologies Co., Ltd. 2026. All rights reserved.

//! Exact source-field occurrences covered by a control's own value.

use std::collections::HashSet;

use super::{field, rules, CompressionEvidence, FactKey, NodeId, Projection, ProjectionNode, Result, TextSource, Value};

const VALUE_PREVIEW_LIMIT: usize = 1200;

fn exact_field(node: &ProjectionNode, key: &FactKey, value: &str) -> bool {
    node.provenance.iter().any(|(name, provenance)| {
        if name != &key.attribute || node.field(name).and_then(Value::as_str) != Some(value) {
            return false;
        }
        let [part] = provenance.parts.as_slice() else { return false };
        part.facts.as_slice() == std::slice::from_ref(key)
    })
}

fn owned(
    projection: &Projection, owner: &ProjectionNode, id: NodeId, evidence: &CompressionEvidence,
) -> Result<bool> {
    let mut current = id;
    let mut seen = HashSet::new();
    while seen.insert(current) {
        let Some(source) = projection.node(current) else { return Ok(false) };
        if source.scope != owner.scope || evidence.contested_sources.contains(&current)
            || evidence.incomplete_children.contains(&current) || evidence.incomplete_content.contains(&current)
            || rules::incomplete_fields(&source.native_fields, source.native_children.len())?
        {
            return Ok(false);
        }
        if current == owner.id {
            return Ok(current != id);
        }
        let Some(parent) = source.parent else { return Ok(false) };
        if !evidence.complete_children.get(&parent).is_some_and(|children| children.contains(&current)) {
            return Ok(false);
        }
        current = parent;
    }
    Ok(false)
}

pub(super) fn covered_value_lines(
    projection: &Projection, target: &ProjectionNode, parts: &[TextSource], evidence: &CompressionEvidence,
) -> Result<Option<Vec<Value>>> {
    let anchor = FactKey { source: target.id, attribute: "value".to_owned() };
    if !matches!(target.field("role").and_then(Value::as_str), Some("textField" | "textArea" | "comboBox"))
        || parts.is_empty()
        || !evidence.control_value_lines.contains(&target.id) || !evidence.complete_text.contains(&anchor)
    {
        return Ok(None);
    }
    let Some(original) = field(&target.native_fields, "value").and_then(Value::as_str) else { return Ok(None) };
    if original.is_empty() || original.encode_utf16().count() > VALUE_PREVIEW_LIMIT
        || original.to_ascii_lowercase().contains(";base64,") || !exact_field(target, &anchor, original)
    {
        return Ok(None);
    }
    let mut offset = 0;
    let mut lines = original.split(['\r', '\n', '\u{85}', '\u{2028}', '\u{2029}']).map(|line| {
        let start = offset;
        let end = start + line.len();
        offset = end + original[end..].chars().next().map_or(0, char::len_utf8);
        (start, end, line)
    }).filter(|(_, _, line)| !line.is_empty());
    let mut seen = HashSet::new();
    let mut result = Vec::new();
    for part in parts {
        let key = &part.fact;
        let Some(text) = part.text.as_str().filter(|text| !text.is_empty()) else { return Ok(None) };
        if !seen.insert(key) || !evidence.complete_text.contains(key)
            || !matches!(key.attribute.as_str(), "name" | "description" | "value")
        {
            return Ok(None);
        }
        let Some(source) = projection.node(key.source) else { return Ok(None) };
        if field(&source.native_fields, &key.attribute).and_then(Value::as_str) != Some(text)
            || !owned(projection, target, source.id, evidence)? || !exact_field(source, key, text)
        {
            return Ok(None);
        }
        let Some((start, end, _)) = lines.find(|(_, _, line)| *line == text) else { return Ok(None) };
        result.push(Value::Map(vec![
            ("sourceRef".to_owned(), Value::Text(source.reference.clone())),
            ("attribute".to_owned(), Value::Text(key.attribute.clone())),
            ("targetRef".to_owned(), Value::Text(target.reference.clone())),
            ("field".to_owned(), Value::Text("value".to_owned())),
            ("start".to_owned(), Value::Int(original[..start].encode_utf16().count() as i64)),
            ("end".to_owned(), Value::Int(original[..end].encode_utf16().count() as i64)),
            ("unit".to_owned(), Value::Text("utf16_code_unit".to_owned())),
        ]));
    }
    Ok(Some(result))
}

#[cfg(test)]
mod tests {
    use super::*;
    use crate::{projection::ProjectionOptions, wire::decode_json};
    use std::collections::HashMap;

    #[test]
    fn exact_caption_ranges_count_utf16_and_keep_original_line_endings() {
        let raw = decode_json(br#"{
            "ref":"1","role":"textField","value":"\ud83e\uddea\r\nlabel\u2028other",
            "children":[{"ref":"2","role":"staticText","name":"label","children":[]}]
        }"#).unwrap();
        let ids = HashMap::from([("1".to_owned(), NodeId(0)), ("2".to_owned(), NodeId(1))]);
        let options = ProjectionOptions { native_facts: false, ..ProjectionOptions::default() };
        let projection = Projection::prepare(raw, &ids, options).unwrap();
        let mut evidence = CompressionEvidence::default();
        evidence.control_value_lines.insert(NodeId(0));
        evidence.complete_children.insert(NodeId(0), vec![NodeId(1)]);
        evidence.complete_text.extend([
            FactKey { source: NodeId(0), attribute: "value".to_owned() },
            FactKey { source: NodeId(1), attribute: "name".to_owned() },
        ]);
        let owner = projection.node(NodeId(0)).unwrap();
        let parts = rules::text_sources(projection.node(NodeId(1)).unwrap());
        let records = covered_value_lines(&projection, owner, &parts, &evidence).unwrap().unwrap();
        assert_eq!(records, [decode_json(br#"{
            "sourceRef":"2","attribute":"name","targetRef":"1","field":"value","start":4,"end":9,"unit":"utf16_code_unit"
        }"#).unwrap()]);
        evidence.contested_sources.insert(NodeId(1));
        assert!(covered_value_lines(&projection, owner, &parts, &evidence).unwrap().is_none());
    }
}
