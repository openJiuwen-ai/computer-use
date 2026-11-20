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

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn relation_annotations_keep_nested_fields_unicode_and_ordered_target_duplicates() {
        let plain = map([
            ("kind", text("related")),
            ("state", text("complete")),
            ("certainty", text("confirmed")),
        ]);
        let mut annotations = Residual::default();
        for (reference, targets) in [("0", vec!["2"]), ("1", vec!["3", "2", "3"])] {
            let mut entries = plain.as_map().unwrap().to_vec();
            entries.push(("total".to_owned(), Value::Int(targets.len() as i64)));
            annotations
                .add("linked", reference, Value::Map(entries), targets.into_iter().map(str::to_owned).collect())
                .unwrap();
        }
        let mut unusual = plain.as_map().unwrap().to_vec();
        unusual.push(("detail".to_owned(), text("😀\u{2028}")));
        unusual.push(("evidence".to_owned(), map([
            ("targets", Value::Seq(vec![text("a nested target")])),
            ("position", Value::Float(-0.0)),
        ])));
        annotations.add("nearby", "4", Value::Map(unusual), vec!["1".to_owned()]).unwrap();
        annotations.add("matching", "5", plain, vec!["2".to_owned()]).unwrap();
        assert_eq!(
            annotations.lines().unwrap(),
            [
                concat!(
                    "Relation \"linked\" ",
                    r#"{"kind":"related","state":"complete","certainty":"confirmed","total":"target-count"}:"#,
                ),
                "\t0 -> [\"2\"]; 1 -> [\"3\",\"2\",\"3\"]",
                concat!(
                    "Relation \"nearby\" ",
                    r#"{"kind":"related","state":"complete","certainty":"confirmed","detail":"😀\u2028","#,
                    r#""evidence":{"targets":["a nested target"],"position":-0.0}}:"#,
                ),
                "\t4 -> [\"1\"]",
                concat!("Relation \"matching\" ", r#"{"kind":"related","state":"complete","certainty":"confirmed"}:"#),
                "\t5 -> [\"2\"]",
            ]
        );
    }

    #[test]
    fn relation_annotation_groups_preserve_separate_totals_with_long_ordered_target_arrays() {
        let long: Vec<String> = (100..132).map(|target| target.to_string()).collect();
        let groups = [vec![], vec!["3".to_owned()], vec!["10".to_owned(), "2".to_owned(), "10".to_owned()], long];
        let mut annotations = Residual::default();
        for name in ["edge\n中\u{2028}", "same\"\\"] {
            for (index, targets) in groups.iter().enumerate() {
                let record = map([
                    ("total", Value::Int(targets.len() as i64)),
                    ("kind", text("related")),
                    ("evidence", map([("position", Value::Float(-0.0))])),
                ]);
                for offset in [2, 3, 4, 8, 9, 8] {
                    annotations.add(name, &(10 * index + offset).to_string(), record.clone(), targets.clone()).unwrap();
                }
            }
        }
        annotations.add(
            "separate",
            "0",
            map([("kind", text("related")), ("total", Value::Int(13))]),
            vec!["7".to_owned()],
        ).unwrap();
        let lines = annotations.lines().unwrap();
        assert_eq!(lines[0], concat!(
            r#"Relation ["edge\n中\u2028","same\"\\"] "#,
            r#"{"kind":"related","evidence":{"position":-0.0},"total":"target-count"}:"#,
        ));
        assert_eq!(lines[1], "\t22–24, 28, 29 -> [\"10\",\"2\",\"10\"]");
        let targets = literal(&Value::Seq(groups[3].iter().map(text).collect())).unwrap();
        assert_eq!(lines[2], format!("\t32–34, 38, 39 -> {targets}"));
        assert_eq!(lines[3], "\t12–14, 18, 19 -> [\"3\"]; 2–4, 8, 9 -> []");
        assert_eq!(lines[4], r#"Relation "separate" {"kind":"related","total":13}:"#);
        assert_eq!(lines[5], "\t0 -> [\"7\"]");
    }

    #[test]
    fn relation_annotation_keeps_the_scalar_from_a_valid_utf16_pair() {
        let metadata = crate::wire::decode_json(br#"{"detail":"\ud83d\ude00"}"#).unwrap();
        let mut annotations = Residual::default();
        annotations.add("edge", "0", metadata, vec!["1".to_owned()]).unwrap();
        assert_eq!(
            annotations.lines().unwrap(),
            [r#"Relation "edge" {"detail":"😀"}:"#, "\t0 -> [\"1\"]"],
        );
    }
}
