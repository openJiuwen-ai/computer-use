// Copyright (c) Huawei Technologies Co., Ltd. 2026. All rights reserved.

use std::collections::{HashMap, HashSet};

use super::{entries, field, literal, map, reference_set, seq, text, AccountingPart, Groups, NodeId, Result, Sources, TextError, Value};

#[derive(Clone, Eq, Hash, PartialEq)]
struct Signature {
    property: String,
    record: String,
}

#[derive(Default)]
pub(super) struct Residual {
    rows: Groups<Signature, String>,
    records: HashMap<Signature, (Value, Vec<String>)>,
}

impl Residual {
    fn add(&mut self, property: &str, reference: &str, metadata: Value, targets: Vec<String>) -> Result<()> {
        let mut original = metadata.as_map().unwrap_or_default().to_vec();
        original.push(("targets".to_owned(), Value::Seq(targets.iter().map(text).collect())));
        let key = Signature { property: property.to_owned(), record: literal(&Value::Map(original))? };
        self.rows.add(key.clone(), reference.to_owned());
        self.records.entry(key).or_insert((metadata, targets));
        Ok(())
    }

    pub(super) fn lines(self) -> Result<Vec<String>> {
        let mut total_shapes: HashMap<(String, String), HashSet<i64>> = HashMap::new();
        let mut rewritten: HashMap<Signature, (String, String)> = HashMap::new();
        for (key, _) in &self.rows.rows {
            let (record, targets) = &self.records[key];
            if let Some(total) = record.get("total").and_then(Value::as_i64).filter(|total| *total == targets.len() as i64) {
                let pattern = Value::Map(entries(Some(record)).iter().filter(|(key, _)| key != "total").cloned().collect());
                let identity = (key.property.clone(), literal(&pattern)?);
                total_shapes.entry(identity.clone()).or_default().insert(total);
                rewritten.insert(key.clone(), identity);
            }
        }
        let mut metadata = Vec::<((String, String), Groups<String, String>)>::new();
        let mut meta_index = HashMap::new();
        for (key, refs) in self.rows.rows {
            let (record, targets) = &self.records[&key];
            let rewrite = rewritten.get(&key).and_then(|identity| total_shapes.get(identity)).is_some_and(|set| set.len() > 1);
            let metadata_record = if rewrite {
                let mut entries: Vec<_> = entries(Some(record)).iter().filter(|(key, _)| key != "total").cloned().collect();
                entries.push(("total".to_owned(), text("target-count")));
                literal(&Value::Map(entries))?
            } else { literal(record)? };
            let index = *meta_index.entry((key.property.clone(), metadata_record.clone())).or_insert_with(|| {
                metadata.push(((key.property, metadata_record), Groups::default()));
                metadata.len() - 1
            });
            let targets = literal(&Value::Seq(targets.iter().map(text).collect()))?;
            for reference in refs { metadata[index].1.add(targets.clone(), reference); }
        }
        let mut shared = Groups::<(String, Vec<(String, Vec<u64>)>), String>::default();
        for ((property, metadata), groups) in metadata {
            let mut mapping = Vec::new();
            for (targets, refs) in groups.rows {
                let mut ordered = refs.iter().map(|reference| reference.parse::<u64>()
                    .map_err(|_| TextError::Invalid("relation display reference"))).collect::<Result<Vec<_>>>()?;
                ordered.sort_unstable();
                ordered.dedup();
                mapping.push((targets, ordered));
            }
            mapping.sort_unstable();
            shared.add((metadata, mapping), property);
        }
        let mut lines = Vec::new();
        for ((metadata, mapping), properties) in shared.rows {
            let name = if properties.len() == 1 { literal(&text(&properties[0]))? }
                else { literal(&Value::Seq(properties.iter().map(text).collect()))? };
            lines.push(format!("Relation {name} {metadata}:"));
            let mut line = String::new();
            for (targets, refs) in mapping {
                let refs: Vec<_> = refs.iter().map(u64::to_string).collect();
                let entry = format!("{} -> {targets}", reference_set(refs.iter().map(String::as_str))?);
                if !line.is_empty() && line.chars().count() + entry.chars().count() > 160 {
                    lines.push(format!("\t{line}"));
                    line.clear();
                }
                if !line.is_empty() { line.push_str("; "); }
                line.push_str(&entry);
            }
            if !line.is_empty() { lines.push(format!("\t{line}")); }
        }
        Ok(lines)
    }
}

fn plain(relation: &Value, targets: usize) -> bool {
    field(relation, "state") == Some("complete") && field(relation, "certainty") == Some("confirmed")
        && relation.get("offset").is_none_or(|value| value.as_i64() == Some(0))
        && relation.get("total").is_none_or(|value| value.as_i64() == Some(targets as i64))
        && relation.get("positions").is_none_or(|positions| positions.as_seq().is_some_and(|positions|
            positions.len() == targets && positions.iter().enumerate().all(|(index, value)| value.as_i64() == Some(index as i64))))
        && entries(Some(relation)).iter().all(|(name, _)| matches!(name.as_str(),
            "source" | "property" | "recordKey" | "kind" | "targets" | "state" | "certainty" | "offset" | "total" | "positions"))
}

pub(super) fn account(
    sources: &Sources<'_>, part: &AccountingPart<'_>, residual: &mut Residual, output: &mut Vec<Value>,
) -> Result<()> {
    let allowed: HashSet<_> = part.members.iter().copied().collect();
    let mut displayed = HashMap::<NodeId, (Option<NodeId>, Option<NodeId>)>::new();
    if let Some(projection) = part.projection.filter(|_| !part.uncompressed) {
        let mut pending: Vec<_> = projection.roots.iter().rev().map(|id| (*id, None, None)).collect();
        while let Some((id, parent, mut window)) = pending.pop() {
            let node = projection.node(id).ok_or(TextError::Invalid("displayed relationship source"))?;
            if !allowed.contains(&id) || displayed.contains_key(&id) {
                return Err(TextError::Invalid("duplicate or out-of-scope displayed relationship"));
            }
            if matches!(node.field("role").and_then(Value::as_str), Some("window" | "sheet" | "popover")) {
                window = Some(id);
            }
            displayed.insert(id, (parent, window));
            pending.extend(node.children.iter().rev().map(|child| (*child, Some(id), window)));
        }
    }
    let mut outgoing: HashMap<&str, Vec<&Value>> = HashMap::new();
    for relation in seq(sources.request.snapshot.facts.get("relations")) {
        let token = field(relation, "source").ok_or(TextError::Invalid("relation source"))?;
        outgoing.entry(token).or_default().push(relation);
    }
    for member in part.members {
        let token = sources.token(*member)?;
        let reference = sources.alias(token)?;
        for relation in outgoing.get(token).into_iter().flatten() {
            let kind = field(relation, "kind").ok_or(TextError::Invalid("relation kind"))?;
            let property = field(relation, "property").ok_or(TextError::Invalid("relation property"))?;
            let targets = seq(relation.get("targets")).iter().map(|token|
                sources.alias(token.as_str().ok_or(TextError::Invalid("relation target"))?)).collect::<Result<Vec<_>>>()?;
            let mut implied = false;
            if let Some((parent, window)) = displayed.get(member) {
                match kind {
                    "children" => {
                        let children = part.projection.and_then(|p| p.node(*member)).ok_or(TextError::Invalid("children source"))?;
                        let children = children.children.iter().map(|child| sources.reference(*child)).collect::<Result<Vec<_>>>()?;
                        implied = children == targets;
                    }
                    "parent" | "window" => {
                        let owner = if kind == "parent" { parent } else { window };
                        implied = targets.len() == 1 && owner.map(|owner| sources.reference(owner)).transpose()? == targets.first().copied();
                    }
                    _ => (),
                }
            }
            let representation = if part.uncompressed { "uncompressed" }
                else if plain(relation, targets.len()) && implied { "containment" }
                else {
                    let metadata = Value::Map(entries(Some(relation)).iter().filter(|(key, _)|
                        !matches!(key.as_str(), "source" | "property" | "targets" | "recordKey")).cloned().collect());
                    residual.add(property, reference, metadata, targets.into_iter().map(str::to_owned).collect())?;
                    "annotation"
                };
            output.push(map([("source", text(token)), ("property", text(property)), ("representation", text(representation))]));
        }
    }
    Ok(())
}
