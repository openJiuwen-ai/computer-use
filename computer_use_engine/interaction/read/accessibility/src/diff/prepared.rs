// Copyright (c) Huawei Technologies Co., Ltd. 2026. All rights reserved.

//! Comparison rows assembled from the final retained presentation evidence.

use std::collections::{HashMap, HashSet};

use super::{
    signature::{sequence, Digest}, ComparisonNode, ComparisonTree, ContextNode, CurrentTree,
    DisplayOptions, DisplayRoot, InvalidContext, RootContexts, Scope, Signatures, Value,
};
use super::super::projection::Projection;

#[path = "prepared/compact.rs"]
mod compact;

pub(crate) use compact::prepare_compact_comparison;

type Fields = Vec<(String, Value)>;
type Result<T> = std::result::Result<T, InvalidPreparedComparison>;

#[derive(Debug, Eq, PartialEq)]
pub(crate) enum InvalidPreparedComparison {
    Evidence(&'static str),
    Context(InvalidContext),
}

/// All plans are owned Rust records and retain the corresponding interface
/// contract's field names and sequence order. A failed root supplies its exact
/// original member list and no projection.
pub(crate) struct PreparedRoot<'a> {
    pub reference: &'a str,
    pub members: &'a [String],
    pub projection: Option<&'a Projection>,
    pub interface: Option<&'a Value>,
    pub readable: Option<&'a Value>,
    pub groups: Option<&'a Value>,
    pub fields: Option<&'a Value>,
    pub atoms: Option<&'a Value>,
    pub outside_window_fields: &'a HashSet<String>,
}

fn required<'a>(row: &'a Value, key: &'static str) -> Result<&'a Value> {
    row.get(key).ok_or(InvalidPreparedComparison::Evidence(key))
}

fn rows<'a>(row: &'a Value, key: &'static str) -> Result<&'a [Value]> {
    required(row, key)?.as_seq().ok_or(InvalidPreparedComparison::Evidence(key))
}

fn fields<'a>(row: &'a Value, key: &'static str) -> Result<&'a [(String, Value)]> {
    required(row, key)?.as_map().ok_or(InvalidPreparedComparison::Evidence(key))
}

fn text<'a>(row: &'a Value, key: &'static str) -> Result<&'a str> {
    required(row, key)?.as_str().ok_or(InvalidPreparedComparison::Evidence(key))
}

fn tuple<const N: usize>(row: &Value, keys: [&'static str; N]) -> Result<Value> {
    Ok(Value::Seq(keys.into_iter().map(|key| required(row, key).cloned()).collect::<Result<_>>()?))
}

fn put(fields: &mut Fields, key: &str, value: Value) {
    if let Some((_, current)) = fields.iter_mut().find(|(name, _)| name == key) {
        *current = value;
    } else {
        fields.push((key.to_owned(), value));
    }
}

fn pair(key: &str, value: Value) -> Value {
    Value::Seq(vec![Value::Text(key.to_owned()), value])
}

fn block_signature(block: &Value, pattern: Option<&Value>) -> Result<Value> {
    const KEYS: [&str; 19] = [
        "key", "kind", "layout", "primary_ref", "source_refs", "children", "origin", "position", "label_ref",
        "shared_prefix_ref", "columns", "column_prefix_refs", "region_role", "field_target_ref", "member_roles",
        "heading_style", "visual_unit", "column_slots", "column_alignments",
    ];
    let mut result = Vec::with_capacity(KEYS.len() + 4 * usize::from(pattern.is_some()));
    for key in KEYS {
        let value = match key {
            "children" if !matches!(required(block, "primary_ref")?, Value::Null) => Value::Seq(Vec::new()),
            "visual_unit" if super::truth(block.get(key)) => Value::Text("control".to_owned()),
            "visual_unit" => Value::Null,
            _ => required(block, key)?.clone(),
        };
        result.push(pair(key, value));
    }
    if let Some(pattern) = pattern {
        result.push(pair("pattern_kind", required(pattern, "kind")?.clone()));
        result.push(pair("pattern_columns", required(pattern, "column_count")?.clone()));
        let pattern_rows = rows(pattern, "rows")?.iter().map(|row| tuple(row, ["key", "ref", "members", "slots"]));
        result.push(pair("pattern_rows", Value::Seq(pattern_rows.collect::<Result<_>>()?)));
        result.push(pair("pattern_remaining", required(pattern, "remaining_keys")?.clone()));
    }
    Ok(Value::Seq(result))
}

fn layout_signatures(interface: &Value, out: &mut HashMap<String, Digest>) -> Result<()> {
    enum Visit<'a> { Enter(&'a str), Exit }
    let blocks: HashMap<_, _> = fields(interface, "blocks")?.iter().map(|(key, block)| (key.as_str(), block)).collect();
    let mut patterns = HashMap::new();
    for pattern in rows(interface, "geometric_patterns")? {
        patterns.insert(text(pattern, "owner_key")?, pattern);
    }
    let mut pending = Vec::new();
    fn push_layout_keys<'a>(pending: &mut Vec<Visit<'a>>, keys: &'a [Value]) -> Result<()> {
        for key in keys.iter().rev() {
            pending.push(Visit::Enter(key.as_str().ok_or(InvalidPreparedComparison::Evidence("block key"))?));
        }
        Ok(())
    }
    push_layout_keys(&mut pending, rows(interface, "roots")?)?;
    let mut path = Vec::new();
    let mut active = HashSet::new();
    let mut active_keys = Vec::new();
    while let Some(visit) = pending.pop() {
        let key = match visit {
            Visit::Enter(key) => key,
            Visit::Exit => {
                path.pop();
                active.remove(active_keys.pop().expect("entered layout path"));
                continue;
            }
        };
        if !active.insert(key) {
            return Err(InvalidPreparedComparison::Evidence("cyclic layout path"));
        }
        active_keys.push(key);
        let block = *blocks.get(key).ok_or(InvalidPreparedComparison::Evidence("layout block"))?;
        let signature = block_signature(block, patterns.get(key).copied())?;
        path.push(Signatures::default().value(&signature));
        let digest = sequence(&path);
        for reference in rows(block, "source_refs")? {
            let reference = reference.as_str().ok_or(InvalidPreparedComparison::Evidence("layout source reference"))?;
            out.insert(reference.to_owned(), digest);
        }
        pending.push(Visit::Exit);
        push_layout_keys(&mut pending, rows(block, "children")?)?;
    }
    Ok(())
}

fn readable_signatures(plan: &Value, out: &mut HashMap<String, Value>) -> Result<()> {
    let headings = fields(plan, "headings")?;
    for (reference, style) in fields(plan, "nodes")? {
        let style = tuple(style, ["note_slots", "block_slots", "note_actions", "toggle_slot", "glyph", "notes_only", "visual_row"])?;
        let mut related = Vec::new();
        for (key, heading) in headings {
            let owner = text(heading, "ref")?;
            let sources = rows(heading, "sources")?;
            if owner == reference || sources.iter().any(|source| source.as_str() == Some(reference)) {
                let caption = match required(heading, "caption")? {
                    Value::Null => Value::Null,
                    caption => tuple(caption, ["key", "value"] )?,
                };
                related.push(Value::Seq(vec![
                    Value::Text(key.clone()), Value::Text(owner.to_owned()), caption, Value::Seq(sources.to_vec()),
                    required(heading, "shared_index")?.clone(), required(heading, "transparent")?.clone(),
                ]));
            }
        }
        out.insert(reference.clone(), Value::Seq(vec![style, Value::Seq(related)]));
    }
    Ok(())
}

fn group_signatures(plan: &Value, out: &mut HashMap<String, Value>) -> Result<()> {
    for group in rows(plan, "groups")? {
        let references = rows(group, "projected_refs")?;
        let fragments = rows(group, "head")?.iter().chain(rows(group, "tail")?).map(|row| tuple(row, ["ref", "slot_key", "start", "end"]));
        let outline = rows(group, "outline")?.iter().map(|row| tuple(row, ["ref", "slot_key"]));
        let mut signature = vec![
            required(group, "kind")?.clone(), Value::Seq(references.to_vec()), required(group, "captured_records")?.clone(),
            Value::Seq(fragments.collect::<Result<_>>()?), Value::Seq(outline.collect::<Result<_>>()?),
        ];
        if text(group, "kind")? == "background" {
            for key in ["window_ref", "blockers", "directory_complete", "important_refs", "uncertain_refs", "nested_windows", "state_facts"] {
                signature.push(required(group, key)?.clone());
            }
        }
        let signature = Value::Seq(signature);
        for reference in references {
            let reference = reference.as_str().ok_or(InvalidPreparedComparison::Evidence("group source reference"))?;
            out.insert(reference.to_owned(), signature.clone());
        }
    }
    Ok(())
}

fn field_signatures(plan: &Value, out: &mut HashMap<String, Value>) -> Result<()> {
    for (_, field) in fields(plan, "fields")? {
        let target = text(field, "target_ref")?;
        let slots = rows(field, "target_slots")?.iter().map(|slot| tuple(slot, ["key", "label", "value", "format", "placement"]));
        out.insert(target.to_owned(), Value::Seq(slots.collect::<Result<_>>()?));
        for member in rows(field, "members")? {
            out.insert(text(member, "ref")?.to_owned(), Value::Seq(vec![
                Value::Text(target.to_owned()), required(member, "mode")?.clone(), required(member, "consumed_slots")?.clone(),
            ]));
        }
    }
    Ok(())
}

fn covers_selection(interval: &(Value, Value), value: &Value) -> bool {
    let exact_integer = |value: &Value| matches!(value, Value::Int(_) | Value::BigInt(_));
    value.as_map().is_some_and(|fields| fields.len() == 2)
        && value.get("location").is_some_and(|value| exact_integer(value) && *value == interval.0)
        && value.get("length").is_some_and(|value| exact_integer(value) && *value == interval.1)
}

fn selection_claims<'a>(
    atoms: &'a Value,
    inactive_selections: &HashMap<String, (Value, Value)>,
    out: &mut HashSet<String>,
) -> Result<()> {
    for claim in rows(atoms, "field_claims")? {
        let reference = text(claim, "ref")?;
        if text(claim, "kind")? == "text-selection-summary"
            && text(claim, "field")? == "selectionRange"
            && claim.get("target").and_then(Value::as_str) == Some(reference)
            && inactive_selections.get(reference).is_some_and(|interval| {
                claim.get("value").is_some_and(|value| covers_selection(interval, value))
            })
        {
            out.insert(reference.to_owned());
        }
    }
    Ok(())
}

fn root_contexts(semantic: &Value) -> Result<RootContexts> {
    fields(semantic, "root_contexts")?.iter().map(|(reference, entries)| {
        let values = entries.as_seq().ok_or(InvalidPreparedComparison::Evidence("root context"))?;
        let mut context = Vec::with_capacity(values.len());
        for value in values {
            let pair = value.as_seq().ok_or(InvalidPreparedComparison::Evidence("root context owner"))?;
            let [owner, kind] = pair else { return Err(InvalidPreparedComparison::Evidence("root context owner")); };
            let owner = owner.as_str().ok_or(InvalidPreparedComparison::Evidence("root context owner"))?;
            let kind = kind.as_str().ok_or(InvalidPreparedComparison::Evidence("root context kind"))?;
            context.push((owner.to_owned(), kind.to_owned()));
        }
        Ok((reference.clone(), context))
    }).collect()
}

struct PreparedRow {
    reference: String,
    parent: Option<String>,
    depth: i64,
    fields: Fields,
    layout: Option<Digest>,
}

#[derive(Default)]
struct ComparisonEvidence {
    layouts: HashMap<String, Digest>,
    field_layouts: HashMap<String, Value>,
    group_layouts: HashMap<String, Value>,
    readable_layouts: HashMap<String, Value>,
    outside: HashSet<String>,
    selected: HashSet<String>,
    versions: HashMap<String, Option<String>>,
}

struct ComparisonRoot<'a> {
    reference: &'a str,
    members: &'a [String],
    projection: Option<&'a Projection>,
}

/// Materialize the same pre-text node facts used by the existing baseline. A
/// selection is eligible only when the caller owns the exact inactive interval
/// proof from the summary plan and this root publishes the matching atom claim.
pub(crate) fn prepare_comparison_tree(
    scope: Scope,
    options: DisplayOptions,
    indent: i64,
    semantic: &Value,
    facts: &Value,
    roots: &[PreparedRoot<'_>],
    inactive_selections: Option<&HashMap<String, (Value, Value)>>,
) -> Result<CurrentTree> {
    let mut evidence = ComparisonEvidence::default();
    for root in roots {
        let version = if let Some(interface) = root.interface {
            layout_signatures(interface, &mut evidence.layouts)?;
            Some(text(interface, "format_version")?.to_owned())
        } else { None };
        evidence.versions.insert(root.reference.to_owned(), version);
        evidence.outside.extend(root.outside_window_fields.iter().cloned());
        if let (Some(atoms), Some(intervals)) = (root.atoms, inactive_selections) {
            selection_claims(atoms, intervals, &mut evidence.selected)?;
        }
        if let Some(readable) = root.readable { readable_signatures(readable, &mut evidence.readable_layouts)?; }
        if let Some(groups) = root.groups { group_signatures(groups, &mut evidence.group_layouts)?; }
        if let Some(fields) = root.fields { field_signatures(fields, &mut evidence.field_layouts)?; }
    }
    let roots: Vec<_> = roots.iter().map(|root| ComparisonRoot {
        reference: root.reference, members: root.members, projection: root.projection,
    }).collect();
    compare_rows(scope, options, indent, semantic, facts, &roots, evidence, inactive_selections)
}

fn compare_rows(
    scope: Scope,
    options: DisplayOptions,
    indent: i64,
    semantic: &Value,
    facts: &Value,
    roots: &[ComparisonRoot<'_>],
    evidence: ComparisonEvidence,
    inactive_selections: Option<&HashMap<String, (Value, Value)>>,
) -> Result<CurrentTree> {
    let mut fallback = None;
    let mut result = Vec::new();
    for root in roots {
        if let Some(projection) = root.projection {
            let mut pending: Vec<_> = projection.roots.iter().rev().map(|id| (*id, None, indent)).collect();
            let mut visited = HashSet::new();
            while let Some((id, parent, depth)) = pending.pop() {
                if !visited.insert(id) {
                    return Err(InvalidPreparedComparison::Evidence("duplicate projected node"));
                }
                let node = projection.node(id).ok_or(InvalidPreparedComparison::Evidence("projected node"))?;
                let reference = node.reference.as_str();
                let mut fields: Fields = node.fields.iter().filter(|(key, _)| !matches!(key.as_str(), "children" | "native"))
                    .cloned().collect();
                // The Python ViewNode exports these source-accounting fields
                // alongside the display row. The Rust arena retains them as
                // separate typed metadata until this comparison boundary.
                let source_ref = |id| projection.node(id).map(|source| Value::Text(source.reference.clone()))
                    .ok_or(InvalidPreparedComparison::Evidence("comparison source reference"));
                let source_refs = node.source_refs.iter().copied().map(source_ref).collect::<Result<_>>()?;
                let text_sources = node.text_sources.iter().map(|part| Ok(Value::Map(vec![
                    ("sourceRef".to_owned(), source_ref(part.fact.source)?),
                    ("attribute".to_owned(), Value::Text(part.fact.attribute.clone())),
                    ("field".to_owned(), Value::Text(part.field.clone())),
                    ("text".to_owned(), part.text.clone()),
                ]))).collect::<Result<_>>()?;
                put(&mut fields, "sourceRefs", Value::Seq(source_refs));
                put(&mut fields, "textSources", Value::Seq(text_sources));
                if evidence.selected.contains(reference) {
                    let interval = &inactive_selections.expect("matched claim has a selection proof")[reference];
                    fields.retain(|(key, value)| key != "selectionRange" || !covers_selection(interval, value));
                }
                for (name, source) in [
                    ("_interfaceField", &evidence.field_layouts), ("_interfaceDisclosure", &evidence.group_layouts),
                    ("_interfaceReadable", &evidence.readable_layouts),
                ] {
                    if let Some(value) = source.get(reference) { put(&mut fields, name, value.clone()); }
                }
                if evidence.outside.contains(reference) { put(&mut fields, "_boundsOutsideWindow", Value::Bool(true)); }
                result.push(PreparedRow {
                    reference: reference.to_owned(), parent: parent.map(str::to_owned), depth, fields,
                    layout: evidence.layouts.get(reference).copied(),
                });
                let child_depth = depth.checked_add(1).ok_or(InvalidPreparedComparison::Evidence("projected depth"))?;
                pending.extend(node.children.iter().rev().map(|child| (*child, Some(reference), child_depth)));
            }
        } else {
            if fallback.is_none() {
                let native: HashMap<_, _> = rows(facts, "nodes")?.iter().map(|row| Ok((text(row, "node")?, row)))
                    .collect::<Result<_>>()?;
                let refs: HashMap<_, _> = fields(semantic, "refs")?.iter()
                    .map(|(reference, token)| (reference.as_str(), token)).collect();
                fallback = Some((native, refs));
            }
            let (native, refs) = fallback.as_ref().expect("fallback facts initialized");
            for reference in root.members {
                let token = refs.get(reference.as_str()).and_then(|value| value.as_str())
                    .ok_or(InvalidPreparedComparison::Evidence("fallback source reference"))?;
                let mut fields = native.get(token).and_then(|row| row.as_map())
                    .ok_or(InvalidPreparedComparison::Evidence("fallback native row"))?.to_vec();
                let relations = rows(facts, "relations")?.iter().filter(|relation| {
                    relation.get("source").and_then(Value::as_str) == Some(token)
                }).cloned().collect();
                put(&mut fields, "_relations", Value::Seq(relations));
                result.push(PreparedRow { reference: reference.clone(), parent: None, depth: indent, fields, layout: None });
            }
        }
    }
    let mut signatures = Signatures::default();
    let mut nodes = Vec::with_capacity(result.len());
    let mut context = Vec::with_capacity(result.len());
    for row in &result {
        let (signature, content) = signatures.capture_with_layout(&row.fields, row.layout);
        context.push(ContextNode::from_fields(&row.fields).map_err(InvalidPreparedComparison::Context)?);
        nodes.push(ComparisonNode { reference: row.reference.clone(), parent: row.parent.clone(), depth: row.depth, signature, content });
    }
    let comparison = ComparisonTree {
        scope, nodes, options, root_contexts: root_contexts(semantic)?, versions: evidence.versions,
        failed: roots.iter().any(|root| root.projection.is_none()),
    };
    let roots = roots.iter().map(|root| DisplayRoot { reference: root.reference.to_owned(), members: root.members.to_vec() }).collect();
    Ok(CurrentTree { comparison: comparison.into(), context, roots })
}

#[cfg(test)]
mod tests {
    use super::*;
    use crate::wire::decode_json;

    fn json(source: &str) -> Value { decode_json(source.as_bytes()).unwrap() }

    #[test]
    fn prehashed_layout_path_matches_the_exact_tuple_signature() {
        let block = json(r#"{"key":"element:1","kind":"window","layout":"tree","primary_ref":"1","source_refs":["1"],
        "children":["not-part-of-this-native-signature"],"origin":"native","position":null,"label_ref":null,
        "shared_prefix_ref":null,"columns":[],"column_prefix_refs":[],"region_role":null,"field_target_ref":null,
        "member_roles":[],"heading_style":"atom","visual_unit":false,"column_slots":[],"column_alignments":[]}"#);
        let signature = block_signature(&block, None).unwrap();
        assert_eq!(signature.as_seq().unwrap()[5], pair("children", Value::Seq(Vec::new())));
        assert_eq!(signature.as_seq().unwrap()[16], pair("visual_unit", Value::Null));
        let layout = Value::Seq(vec![signature.clone()]);
        let digest = sequence(&[Signatures::default().value(&signature)]);
        let mut expected = vec![("name".to_owned(), Value::Text("Window".to_owned()))];
        let fields = expected.clone();
        expected.push(("_interfaceLayout".to_owned(), layout));
        assert_eq!(Signatures::default().capture(&expected), Signatures::default().capture_with_layout(&fields, Some(digest)));
    }

    #[test]
    fn fallback_rows_use_native_records_and_ordered_local_relations() {
        let semantic = json(r#"{"refs":{"1":"native-a","2":"native-b"},"root_contexts":{}}"#);
        let facts = json(r#"{"nodes":[{"node":"native-b","values":{"name":"B"}},
        {"node":"native-a","values":{"name":"A"}}],"relations":[{"source":"native-a","property":"AXRows"},
        {"source":"native-b","property":"AXChildren"},{"source":"native-a","property":"AXParent"}]}"#);
        let members = ["1".to_owned(), "2".to_owned()];
        let outside = HashSet::new();
        let prepared = prepare_comparison_tree(Vec::new(), DisplayOptions::default(), 2, &semantic, &facts, &[PreparedRoot {
            reference: "1", members: &members, projection: None, interface: None, readable: None,
            groups: None, fields: None, atoms: None, outside_window_fields: &outside,
        }], None).unwrap();
        let expected = json(r#"{"node":"native-a","values":{"name":"A"},"_relations":[
        {"source":"native-a","property":"AXRows"},{"source":"native-a","property":"AXParent"}]}"#);
        assert_eq!(prepared.comparison.nodes[0].signature, Signatures::default().capture(expected.as_map().unwrap()).0);
        assert_eq!(prepared.comparison.nodes[1].reference, "2");
        assert_eq!(prepared.comparison.nodes[0].depth, 2);
        assert_eq!(prepared.comparison.nodes[0].parent, None);
    }
}
