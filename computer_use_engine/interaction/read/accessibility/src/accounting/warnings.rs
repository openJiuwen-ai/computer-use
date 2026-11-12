// Copyright (c) Huawei Technologies Co., Ltd. 2026. All rights reserved.

use std::collections::{HashMap, HashSet};

use super::{entries, field, present, reference_set, scalar, seq, text, truthy, Groups, JsonFormat, Result, Sources, TextError, Value};

const WARNING_STATES: [&str; 7] = ["error", "timeout", "partial", "stale", "conflict", "unavailable", "redacted"];

fn json_string(value: &str) -> Result<String> { super::json(&text(value), JsonFormat::Plain) }

fn property_token(value: &str) -> bool {
    !value.is_empty() && value.chars().all(|item| item.is_alphanumeric() || matches!(item, '_' | '.' | ':' | '-'))
}

fn label(value: &str) -> Result<String> {
    if property_token(value) { Ok(value.to_owned()) } else { json_string(value) }
}

fn detail(value: &Value, diagnostic: bool) -> Result<String> {
    let detail = field(value, "detail");
    if diagnostic {
        if let Some(detail) = detail.filter(|detail| !detail.is_empty()) { return Ok(format!(": {}", json_string(detail)?)); }
        return Ok(String::new());
    }
    let coordinate = "Live coordinate page; values and native objects may change between reads.";
    if detail.is_some_and(|detail| detail == coordinate
        || detail.strip_suffix(coordinate).is_some_and(|prefix| prefix.ends_with(' ')))
    {
        return Ok(format!(": {}", json_string(coordinate)?));
    }
    if field(value, "consistency") == Some("live") {
        return Ok(": \"Live continuation; contents may change between reads.\"".to_owned());
    }
    Ok(String::new())
}

fn incidental_menu_error(source: &Sources<'_>, relation: &Value) -> bool {
    let snapshot = source.request.snapshot;
    if field(&snapshot.observation, "backend") != Some("macos") || field(relation, "kind") != Some("labels")
        || field(relation, "state") != Some("error") || field(relation, "certainty") != Some("unknown")
        || !seq(relation.get("targets")).is_empty() || present(relation.get("cursor"))
    {
        return false;
    }
    let (Some(token), Some(key)) = (field(relation, "source"), field(relation, "recordKey")) else { return false; };
    if snapshot.metadata.get("relation_fields").and_then(|fields| fields.get(token)).and_then(|fields| field(fields, key))
        != Some("AXServesAsTitleForUIElements")
    {
        return false;
    }
    let Some(node) = snapshot.observation.get("nodes").and_then(|nodes| nodes.get(token)) else { return false; };
    if !matches!(field(node, "role"), Some("menu_item" | "menu_bar_item"))
        || field(node, "name").is_none_or(str::is_empty)
    {
        return false;
    }
    let Some(original) = node.get("relations").and_then(|relations| relations.get(key)) else { return false; };
    if field(original, "kind") != Some("labels") || !seq(original.get("targets")).is_empty()
        || field(original, "evidence") != Some("candidate") || present(original.get("total"))
        || present(original.get("offset")) || present(original.get("positions"))
        || field(original, "enumeration") != Some("enumerated")
    {
        return false;
    }
    let mut found = false;
    for issue in seq(snapshot.observation.get("issues")) {
        let path = seq(issue.get("path"));
        let at_node = path.first().and_then(Value::as_str) == Some("nodes");
        if path.len() < 2 || !at_node {
            if path.len() != 1 || !at_node || field(issue, "state") != Some("partial") { return false; }
        } else if path[1].as_str() == Some(token) {
            let Some(group) = path.get(2).and_then(Value::as_str) else { return false; };
            if matches!(group, "role" | "name") || path.len() == 3 && group == "relations" { return false; }
            if group == "relations" && path.get(3).and_then(Value::as_str) == Some(key) {
                if path.len() != 4 || field(issue, "state") != Some("error") || present(issue.get("continuation"))
                    || !seq(issue.get("alternatives")).is_empty()
                {
                    return false;
                }
                found = true;
            }
        }
    }
    found
}

fn continued(source: &Sources<'_>) -> Result<HashSet<String>> {
    let mut result = HashSet::new();
    let mut pending: Vec<_> = seq(source.request.semantic.get("roots")).iter().collect();
    while let Some(node) = pending.pop() {
        let children = seq(node.get("children"));
        let menu = field(node, "role") == Some("menuBarItem") && children.is_empty()
            && node.get("childrenStart").and_then(Value::as_i64) == Some(0)
            && node.get("childrenEnd").and_then(Value::as_i64) == Some(0)
            && node.get("childCount").and_then(Value::as_i64).is_some_and(|count| count > 0);
        if menu || node.get("nextChildOffset").is_some() {
            if let Some(reference) = field(node, "ref") { result.insert(reference.to_owned()); }
        }
        pending.extend(children);
    }
    for part in source.request.parts {
        if part.uncompressed { continue; }
        if let Some(atoms) = part.atoms {
            for id in &atoms.order {
                if let Some(atom) = atoms.atoms.get(id) {
                    if atom.coverage.partial() && !atom.coverage.unexpanded_menu {
                        result.insert(source.reference(*id)?.to_owned());
                    }
                }
            }
        }
    }
    Ok(result)
}

fn property_list(properties: &[String]) -> String {
    let mut parts = Vec::new();
    let mut index = 0;
    while index < properties.len() {
        let Some((prefix, leaf)) = properties[index].rsplit_once('.') else {
            parts.push(properties[index].clone());
            index += 1;
            continue;
        };
        let mut leaves = vec![leaf];
        let mut end = index + 1;
        while let Some((following, leaf)) = properties.get(end).and_then(|item| item.rsplit_once('.')) {
            if following != prefix { break; }
            leaves.push(leaf);
            end += 1;
        }
        if leaves.len() > 1 {
            let packed = format!("{prefix}.{{{}}}", leaves.join(", "));
            let original = properties[index..end].join("; ");
            parts.push(if packed.chars().count() < original.chars().count() { packed } else { original });
        } else {
            parts.push(properties[index].clone());
        }
        index = end;
    }
    parts.join("; ")
}

fn warning_lines(groups: Groups<String, String>) -> Result<Vec<String>> {
    let mut shared = Groups::<(String, String), Option<String>>::default();
    for (message, refs) in groups.rows {
        let members = reference_set(refs.iter().map(String::as_str))?;
        let split = message.find(" (").and_then(|position| {
            let (property, suffix) = message.split_at(position);
            let detail = suffix.find(')').map(|end| &suffix[end + 1..]);
            (property_token(property) && detail.is_some_and(|detail| detail.is_empty() || detail.starts_with(": ")))
                .then_some((property, suffix))
        });
        if let Some((property, suffix)) = split {
            shared.add((members, suffix.to_owned()), Some(property.to_owned()));
        } else {
            shared.add((members, message), None);
        }
    }
    enum Item { Line(String), Group(usize) }
    let mut order = Vec::new();
    let mut bundled = Groups::<(String, String), (String, String, String)>::default();
    for ((members, suffix), properties) in shared.rows {
        let names = if properties.first().is_some_and(Option::is_some) {
            Some(property_list(&properties.into_iter().flatten().collect::<Vec<_>>()))
        } else {
            None
        };
        let original = format!("{members}: {}{suffix}", names.as_deref().unwrap_or_default());
        if let Some((state, message)) = suffix.strip_prefix(" (").and_then(|suffix| suffix.split_once("): "))
            .filter(|(_, message)| message.starts_with('"') && message.ends_with('"'))
        {
            if let Some(names) = names {
                let key = (members, message.to_owned());
                if !bundled.index.contains_key(&key) { order.push(Item::Group(bundled.rows.len())); }
                bundled.add(key, (state.to_owned(), names, original));
                continue;
            }
        }
        order.push(Item::Line(original));
    }
    let mut output = Vec::new();
    for item in order {
        match item {
            Item::Line(line) => output.push(line),
            Item::Group(index) => {
                let ((members, message), records) = &bundled.rows[index];
                let statuses: Vec<_> = records.iter().map(|(status, names, _)| format!("{status} [{names}]")).collect();
                let combined = format!("{members}: {message}; {}", statuses.join("; "));
                let original: Vec<_> = records.iter().map(|(_, _, original)| original.as_str()).collect();
                if records.len() > 1 && combined.chars().count() < original.join("\n").chars().count() {
                    output.push(combined);
                } else {
                    output.extend(original.into_iter().map(str::to_owned));
                }
            }
        }
    }
    Ok(output)
}

fn ordered_refs(values: &[String]) -> Result<String> {
    let mut previous = None;
    let increasing = values.iter().all(|value| value.parse::<u64>().is_ok_and(|number| {
        let ordered = previous.is_none_or(|previous| number > previous);
        previous = Some(number);
        ordered
    }));
    if increasing { reference_set(values.iter().map(String::as_str)) } else { Ok(values.join(", ")) }
}

fn containment(
    notes: Vec<&str>, columns: &HashMap<&str, &Value>, covered: &mut Vec<Value>,
) -> Result<Vec<String>> {
    fn flush(
        output: &mut Vec<String>, block: &mut Vec<(String, String, String, String)>,
        columns: &HashMap<&str, &Value>, covered: &mut Vec<Value>,
    ) -> Result<()> {
        let mut sources = Groups::<(String, String), (String, String)>::default();
        for (parent, child, kind, original) in block.drain(..) { sources.add((parent, kind), (child, original)); }
        let mut grouped = Groups::<(String, Vec<String>), (String, Vec<String>)>::default();
        for ((parent, kind), edges) in sources.rows {
            let (targets, originals): (Vec<_>, Vec<_>) = edges.into_iter().unzip();
            if targets.iter().collect::<HashSet<_>>().len() != targets.len() {
                output.extend(originals);
            } else {
                grouped.add((kind, targets), (parent, originals));
            }
        }
        for ((kind, targets), parents_and_lines) in grouped.rows {
            let (parents, originals): (Vec<_>, Vec<_>) = parents_and_lines.into_iter().unzip();
            let originals: Vec<_> = originals.into_iter().flatten().collect();
            let compact = format!("{} {} {} ({kind})", ordered_refs(&parents)?,
                if parents.len() == 1 { "contains" } else { "contain" }, ordered_refs(&targets)?);
            let previous = if compact.chars().count() < originals.join("\n").chars().count() {
                vec![compact]
            } else {
                originals
            };
            if let [parent] = parents.as_slice() {
                if let Some(column) = columns.get(parent.as_str()).filter(|column| {
                    kind == "shared" && seq(column.get("targets")).iter().map(Value::as_str)
                        .eq(targets.iter().map(|target| Some(target.as_str())))
                }) {
                    let index = column.get("column").ok_or(TextError::Invalid("published column index"))?;
                    let table = field(column, "table").ok_or(TextError::Invalid("published column table"))?;
                    let statement = format!("{parent} contains Column {} cells of table {table} (shared; displayed row order)", scalar(index));
                    if statement.len() < previous.join("\n").len() {
                        let mut record = entries(Some(column)).to_vec();
                        record.push(("representation".to_owned(), text("table-column")));
                        record.push(("statement".to_owned(), text(&statement)));
                        covered.push(Value::Map(record));
                        output.push(statement);
                        continue;
                    }
                }
            }
            output.extend(previous);
        }
        Ok(())
    }
    let mut output = Vec::new();
    let mut block = Vec::new();
    for note in notes {
        let edge = note.split_once(" contains ").and_then(|(parent, rest)| {
            let (child, kind) = rest.split_once(" (")?;
            let kind = kind.strip_suffix(')')?;
            let number = |value: &str| !value.is_empty() && value.bytes().all(|byte| byte.is_ascii_digit());
            (number(parent) && number(child) && matches!(kind, "shared" | "cycle" | "unread")).then_some((parent, child, kind))
        });
        if let Some((parent, child, kind)) = edge {
            block.push((parent.to_owned(), child.to_owned(), kind.to_owned(), note.to_owned()));
        } else {
            flush(&mut output, &mut block, columns, covered)?;
            output.push(note.to_owned());
        }
    }
    flush(&mut output, &mut block, columns, covered)?;
    Ok(output)
}

fn covered_notes(source: &Sources<'_>, covered: &HashSet<(&str, &str)>) -> Result<HashSet<String>> {
    let snapshot = source.request.snapshot;
    let mut by_selector = HashMap::<&str, bool>::new();
    for relation in seq(snapshot.facts.get("relations")) {
        if matches!(field(relation, "state"), Some("complete" | "absent" | "unsupported")) { continue; }
        let (Some(token), Some(key)) = (field(relation, "source"), field(relation, "recordKey")) else { continue; };
        let Some(selector) = snapshot.metadata.get("relation_fields").and_then(|fields| fields.get(token))
            .and_then(|fields| field(fields, key)) else { return Ok(HashSet::new()); };
        *by_selector.entry(selector).or_insert(true) &= covered.contains(&(token, key));
    }
    let selectors: HashSet<_> = by_selector.into_iter().filter_map(|(key, covered)| covered.then_some(key)).collect();
    let mut output = HashSet::new();
    for issue in seq(snapshot.observation.get("issues")) {
        if !matches!(seq(issue.get("path")), [Value::Text(scope)] if scope == "nodes")
            || field(issue, "state") != Some("partial") || present(issue.get("continuation"))
            || !seq(issue.get("alternatives")).is_empty()
        {
            continue;
        }
        if let Some(detail) = field(issue, "detail").filter(|detail| detail.strip_prefix("partial native relation: ")
            .is_some_and(|selector| selectors.contains(selector)))
        {
            output.insert(format!("Collection: {}", json_string(&format!("nodes: {detail}"))?));
        }
    }
    Ok(output)
}

pub(super) fn read_notes(
    source: &Sources<'_>, diagnostic: bool, concise: bool, defer_menu: bool,
    table_columns: &[&Value], covered_columns: &mut Vec<Value>,
) -> Result<Option<String>> {
    let snapshot = source.request.snapshot;
    let continued = continued(source)?;
    let mut groups = Groups::default();
    let mut missing_targets = Groups::<String, (String, Vec<String>)>::default();
    let mut covered = HashSet::new();
    for node in seq(snapshot.facts.get("nodes")) {
        let reference = source.alias(field(node, "node").ok_or(TextError::Invalid("warning source"))?)?;
        for (key, value) in entries(node.get("fieldStatus")) {
            let primary = field(value, "state").unwrap_or_default();
            // Priority of each warning state exceeds all non-warning states.
            if !WARNING_STATES.contains(&primary) { continue; }
            let mut states = vec![primary];
            if !diagnostic {
                for issue in seq(value.get("issues")) {
                    if let Some(state) = field(issue, "state").filter(|state| !states.contains(state)) { states.push(state); }
                }
            }
            for state in states.into_iter().filter(|state| WARNING_STATES.contains(state)) {
                groups.add(format!("{} ({state}){}", label(key)?, detail(value, diagnostic)?), reference.to_owned());
            }
        }
    }
    for relation in seq(snapshot.facts.get("relations")) {
        if !diagnostic && defer_menu && incidental_menu_error(source, relation) { continue; }
        let token = field(relation, "source").ok_or(TextError::Invalid("warning relation source"))?;
        let reference = source.alias(token)?;
        let key = field(relation, "recordKey").unwrap_or_default();
        let property = label(field(relation, "property").unwrap_or_default())?;
        let kind = field(relation, "kind").unwrap_or_default();
        let state = field(relation, "state").unwrap_or_default();
        let certainty = field(relation, "certainty").unwrap_or_default();
        let message = field(relation, "detail");
        let covered_here = kind == "children" && continued.contains(reference) && state == "partial" && certainty == "confirmed"
            && matches!(message, None | Some("native relation has an unread continuation" | "native relation not expanded; read its source to continue"));
        if covered_here { covered.insert((token, key)); }
        let visible = matches!(state, "error" | "timeout" | "truncated")
            || !diagnostic && matches!(state, "partial" | "not_read" | "stale" | "redacted" | "unavailable")
            || certainty == "conflicting" || !matches!(state, "absent" | "unsupported")
                && (message.is_some_and(|message| !message.is_empty()) || certainty == "unknown");
        if visible && !covered_here {
            let mut coverage = String::new();
            if !diagnostic && !matches!(state, "complete" | "absent" | "unsupported") {
                if let Some(raw) = snapshot.observation.get("nodes").and_then(|nodes| nodes.get(token))
                    .and_then(|node| node.get("relations")).and_then(|relations| relations.get(key))
                {
                    if let Some(total) = raw.get("total").filter(|total| !matches!(total, Value::Null)) {
                        coverage = format!("; {}/{} targets read", seq(raw.get("targets")).len(), scalar(total));
                        if let Some(offset) = raw.get("offset").filter(|offset| !matches!(offset, Value::Null)) {
                            coverage.push_str(&format!(", offset {}", scalar(offset)));
                        }
                    }
                }
                if present(relation.get("cursor")) { coverage.push_str("; continuation available"); }
            }
            groups.add(format!("{property} ({state}, {certainty}){}{coverage}", detail(relation, diagnostic)?), reference.to_owned());
            if state == "partial" && certainty == "confirmed"
                && message == Some("native relation not expanded; read its source to continue")
            {
                covered.insert((token, key));
            }
        }
        if matches!(kind, "children" | "parent" | "window") { continue; }
        let mut missing = Vec::new();
        for target in seq(relation.get("targets")) {
            let target = target.as_str().ok_or(TextError::Invalid("warning relation target"))?;
            if source.facts.get(target).is_none_or(|row| row.get("observed") == Some(&Value::Bool(false))) {
                missing.push(source.alias(target)?.to_owned());
            }
        }
        if !missing.is_empty() {
            if concise && !diagnostic {
                missing_targets.add(property, (reference.to_owned(), missing));
            } else {
                groups.add(format!("{property} -> {} (not collected; inspect with app.element(ref).read())", missing.join(", ")),
                    reference.to_owned());
            }
        }
    }
    let mut warnings = warning_lines(groups)?;
    for (property, targets) in missing_targets.rows {
        let edges: Vec<_> = targets.into_iter().map(|(source, targets)| format!("{source}→{}", targets.join(", "))).collect();
        warnings.push(format!("{property} targets not collected: {}.", edges.join("; ")));
    }
    let explained = covered_notes(source, &covered)?;
    let notes: Vec<_> = seq(source.request.semantic.get("annotations")).iter().filter_map(Value::as_str)
        .filter(|line| (line.contains(" contains ") || line.contains(" has no expanded containment path ")
            || diagnostic && line.starts_with("Collection:")) && !explained.contains(*line)).collect();
    let mut published = HashMap::<&str, Option<&Value>>::new();
    for &column in table_columns {
        let (Some(reference), Some(token)) = (field(column, "ref"), field(column, "source")) else { continue; };
        let scope = seq(column.get("scope"));
        if scope.first().and_then(Value::as_str) != Some(source.capture("session")?)
            || scope.get(1).and_then(Value::as_str) != Some(source.capture("id")?)
            || source.refs.get(reference).copied() != Some(token)
        {
            continue;
        }
        published.entry(reference).and_modify(|entry| *entry = None).or_insert(Some(column));
    }
    let published = published.into_iter().filter_map(|(reference, column)|
        column.map(|column| (reference, column))).collect();
    warnings.extend(containment(notes, &published, covered_columns)?);
    if !diagnostic {
        let mut reported = Vec::new();
        for issue in seq(snapshot.observation.get("issues")) {
            let path = seq(issue.get("path"));
            let state = field(issue, "state").unwrap_or_default();
            if path.len() >= 2 && path[0].as_str() == Some("nodes") || matches!(state, "absent" | "unsupported") { continue; }
            if let Some(detail) = field(issue, "detail").filter(|detail| !detail.is_empty()) {
                if explained.contains(&format!("Collection: {}", json_string(&format!("nodes: {detail}"))?)) { continue; }
            }
            if reported.contains(&(path, state)) { continue; }
            reported.push((path, state));
            let location = if path.is_empty() { "observation".to_owned() }
                else { path.iter().map(scalar).collect::<Vec<_>>().join(".") };
            warnings.push(format!("Collection: {} ({state})", label(&location)?));
        }
    }
    if warnings.is_empty() { return Ok(None); }
    Ok(Some(format!("Read notes:\n\t{}", warnings.join("\n\t"))))
}
