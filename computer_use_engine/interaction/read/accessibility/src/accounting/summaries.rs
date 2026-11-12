// Copyright (c) Huawei Technologies Co., Ltd. 2026. All rights reserved.

use std::collections::{HashMap, HashSet};

use super::{entries, exact, field, literal, map, present, reference_set, scalar, seq, text, truthy, Groups, Result, Sources, TextError, Value};

fn roots<'a>(source: &Sources<'a>) -> &'a [Value] { seq(source.request.semantic.get("roots")) }

fn alias_targets<'a>(source: &Sources<'a>, relation: &Value) -> Result<Vec<&'a str>> {
    seq(relation.get("targets")).iter().map(|target|
        source.alias(target.as_str().ok_or(TextError::Invalid("summary relationship target"))?)).collect()
}

pub(super) fn selection_relations(source: &Sources<'_>) -> Result<Vec<String>> {
    let mut result = Vec::new();
    for relation in seq(source.request.snapshot.facts.get("relations")) {
        let name = match field(relation, "kind") {
            Some("selected_children") => "children",
            Some("selected_cells") => "cells",
            Some("selected_rows") => "rows",
            Some("selected_columns") => "columns",
            _ => continue,
        };
        let state = field(relation, "state").unwrap_or_default();
        let certainty = field(relation, "certainty").unwrap_or_default();
        if matches!(state, "absent" | "unsupported") { continue; }
        let token = field(relation, "source").ok_or(TextError::Invalid("selection source"))?;
        let reference = source.alias(token)?;
        let owner = source.facts.get(token).ok_or(TextError::Invalid("selection owner"))?;
        let targets = seq(relation.get("targets"));
        let mut details = Vec::new();
        if certainty != "confirmed" { details.push(format!("membership {certainty}")); }
        if state != "complete" { details.push(state.to_owned()); }
        if let Some(total) = relation.get("total") { details.push(format!("{} listed of {}", targets.len(), scalar(total))); }
        if let Some(offset) = relation.get("offset").filter(|offset| truthy(Some(offset))) {
            details.push(format!("offset {}", scalar(offset)));
        }
        let mut unread = Vec::new();
        let mut conflicts = Vec::new();
        for target in targets {
            let target = target.as_str().ok_or(TextError::Invalid("selected source"))?;
            let row = source.facts.get(target);
            if row.is_none_or(|row| row.get("observed") == Some(&Value::Bool(false))) { unread.push(source.alias(target)?); }
            if row.is_some_and(|row| row.get("values").and_then(|values| values.get("states.selected")) == Some(&Value::Bool(false))
                && row.get("fieldStatus").and_then(|status| status.get("states.selected")).is_none())
            {
                conflicts.push(source.alias(target)?);
            }
        }
        if !unread.is_empty() { details.push(format!("not expanded: {}", unread.join(", "))); }
        if !conflicts.is_empty() { details.push(format!("node reports selected=false: {}", conflicts.join(", "))); }
        let count = owner.get("values").and_then(|values| values.get("structure.selected_count")).and_then(Value::as_i64);
        if let Some(count) = count.filter(|count| (0..=9_007_199_254_740_991).contains(count)
            && owner.get("fieldStatus").and_then(|status| status.get("structure.selected_count")).is_none()
            && relation.get("total").is_some_and(|total| total.as_i64() != Some(*count)))
        {
            details.push(format!("separate count reports {count}"));
        }
        let listed = if targets.is_empty() {
            if state == "complete" && certainty == "confirmed" { "none".to_owned() } else { "no confirmed items collected".to_owned() }
        } else { alias_targets(source, relation)?.join(", ") };
        let suffix = if details.is_empty() { String::new() } else { format!(" ({})", details.join("; ")) };
        result.push(format!("Selected {name} of {reference}: {listed}{suffix}"));
    }
    Ok(result)
}

fn excerpt(value: &str, limit: usize) -> (&str, bool) {
    value.char_indices().nth(limit).map_or((value, false), |(index, _)| (&value[..index], true))
}

fn relation_summaries(source: &Sources<'_>) -> Result<Vec<String>> {
    let mut result = Vec::new();
    let mut flows = Groups::<&str, &str>::default();
    let mut unsafe_order = false;
    for relation in seq(source.request.snapshot.facts.get("relations")) {
        let state = field(relation, "state").unwrap_or_default();
        let certainty = field(relation, "certainty").unwrap_or_default();
        if matches!(state, "absent" | "unsupported") { continue; }
        let token = field(relation, "source").ok_or(TextError::Invalid("relationship summary source"))?;
        let reference = source.alias(token)?;
        let partial = state != "complete" || certainty != "confirmed";
        match field(relation, "kind") {
            Some("described_by") => {
                let mut descriptions = Vec::new();
                for target in seq(relation.get("targets")) {
                    let token = target.as_str().ok_or(TextError::Invalid("described target"))?;
                    let target = source.alias(token)?;
                    let name = source.facts.get(token).and_then(|row| row.get("values")).and_then(|values| field(values, "name"));
                    let mut rendered = target.to_owned();
                    if let Some(name) = name.filter(|name| !name.is_empty()) {
                        let (name, clipped) = excerpt(name, 160);
                        rendered.push(' ');
                        rendered.push_str(&literal(&text(format!("{name}{}", if clipped { "…" } else { "" })))?);
                    }
                    descriptions.push(rendered);
                }
                let suffix = if partial { " (partial/ambiguous)" } else { "" };
                result.push(format!("Description of {reference}: elements {}{suffix}", descriptions.join(", ")));
            }
            Some(kind @ ("flows_to" | "flows_from")) => {
                let targets = seq(relation.get("targets"));
                unsafe_order |= partial || targets.len() > 1 || targets.iter().any(|target| target.as_str()
                    .and_then(|token| source.facts.get(token)).is_none_or(|row| row.get("observed") != Some(&Value::Bool(true))));
                if partial {
                    result.push(format!("Reading order of {reference} ({kind}): {state}, {certainty}; order unconfirmed."));
                }
                for target in alias_targets(source, relation)? {
                    let (left, right) = if kind == "flows_to" { (reference, target) } else { (target, reference) };
                    if flows.index.get(&left).is_none_or(|index| !flows.rows[*index].1.contains(&right)) { flows.add(left, right); }
                }
            }
            _ => (),
        }
    }
    if flows.rows.is_empty() { return Ok(result); }
    let mut counts = HashMap::<&str, usize>::new();
    for (source, targets) in &flows.rows {
        counts.entry(source).or_default();
        for target in targets { *counts.entry(target).or_default() += 1; }
        unsafe_order |= targets.len() > 1;
    }
    unsafe_order |= counts.values().any(|count| *count > 1);
    let mut pending: Vec<_> = counts.iter().filter_map(|(source, count)| (*count == 0).then_some(*source)).collect();
    let mut visited = 0;
    while let Some(source) = pending.pop() {
        visited += 1;
        if let Some(index) = flows.index.get(source) {
            for target in &flows.rows[*index].1 {
                let count = counts.get_mut(target).expect("target has incoming edges");
                *count -= 1;
                if *count == 0 { pending.push(target); }
            }
        }
    }
    let label = if visited != counts.len() { "cyclic/conflicting" } else if unsafe_order { "partial/ambiguous" }
        else { "confirmed provider hints" };
    let mut edges = Vec::new();
    for (source, mut targets) in flows.rows {
        targets.sort_by_key(|target| target.parse::<u64>().unwrap_or_default());
        edges.push(format!("{source} → {}", targets.join(", ")));
    }
    result.push(format!("Reading order ({label}): {}; containment order is unchanged.", edges.join("; ")));
    Ok(result)
}

fn number(value: &Value) -> Option<f64> {
    match value {
        Value::Float(value) => Some(*value),
        Value::Int(value) => Some(*value as f64),
        Value::BigInt(value) => value.parse().ok(),
        _ => None,
    }
}

fn format_general(number: f64) -> String {
    if !number.is_finite() { return number.to_string().to_lowercase(); }
    let scientific = format!("{number:.5e}");
    let (mantissa, exponent) = scientific.split_once('e').expect("scientific format includes an exponent");
    let exponent: i32 = exponent.parse().expect("scientific format exponent is an integer");
    let trim = |value: &str| value.trim_end_matches('0').trim_end_matches('.').to_owned();
    if !(-4..6).contains(&exponent) {
        format!("{}e{exponent:+03}", trim(mantissa))
    } else {
        let precision = (5 - exponent) as usize;
        let fixed = format!("{number:.precision$}");
        if fixed.contains('.') { trim(&fixed) } else { fixed }
    }
}

fn geometry_summaries(source: &Sources<'_>) -> Result<Vec<String>> {
    let facts = &source.request.snapshot.facts;
    let mut children = HashMap::<&str, Vec<&Value>>::new();
    for edge in seq(facts.get("relations")) {
        if field(edge, "kind") == Some("children") {
            children.entry(field(edge, "source").unwrap_or_default()).or_default().push(edge);
        }
    }
    let mut groups = Groups::<&str, String>::default();
    for row in seq(facts.get("nodes")) {
        let token = field(row, "node").unwrap_or_default();
        let values = row.get("values").ok_or(TextError::Invalid("geometry values"))?;
        if field(values, "name") != Some("") || truthy(values.get("description")) || truthy(values.get("control_value.value"))
            || values.get("states.visible") == Some(&Value::Bool(false)) || values.get("states.showing") == Some(&Value::Bool(false))
            || !(values.get("states.showing") == Some(&Value::Bool(true)) || values.get("states.offscreen") == Some(&Value::Bool(false)))
        {
            continue;
        }
        let Some(actions) = row.get("actions") else { continue };
        let Some([child]) = children.get(token).map(Vec::as_slice) else { continue };
        if field(actions, "state") != Some("complete") || seq(actions.get("items")).is_empty()
            || field(child, "state") != Some("complete") || !seq(child.get("targets")).is_empty()
            || entries(row.get("fieldStatus")).iter().any(|(name, _)| name.starts_with("geometry.bounds") || name == "name")
        {
            continue;
        }
        let Some(bounds) = values.get("geometry.bounds") else { continue };
        let rect = ["x", "y", "width", "height"].map(|key| bounds.get(key).and_then(number));
        let [Some(x), Some(y), Some(w), Some(h)] = rect else { continue };
        if w <= 0.0 || h <= 0.0 || bounds.get("valid") == Some(&Value::Bool(false)) { continue; }
        let Some(space) = field(bounds, "space") else { continue };
        if facts.get("coordinateSpaces").and_then(|spaces| spaces.get(space)).is_none() { continue; }
        let coordinates = [x, y, w, h].map(format_general).join(", ");
        groups.add(space, format!("{}=({coordinates})", source.alias(token)?));
    }
    Ok(groups.rows.into_iter().map(|(space, entries)| {
        let metadata = facts.get("coordinateSpaces").and_then(|spaces| spaces.get(space)).expect("known geometry space");
        let unit = match field(metadata, "unit") {
            Some("physical_pixel") => "physical screen pixels", Some("logical_pixel") => "logical pixels",
            Some("point") => "points", _ => "provider units",
        };
        let origin = field(metadata, "origin").unwrap_or_default();
        format!("Unlabeled element bounds ({unit}; {origin}): {}.", entries.join("; "))
    }).collect())
}

fn unit_label(unit: Option<&str>) -> &str {
    match unit {
        Some("utf16_code_unit") => "UTF-16", Some("unicode_codepoint") => "Unicode code points",
        Some("utf8_byte") => "UTF-8 bytes", Some(unit) if !unit.is_empty() => unit, _ => "unspecified unit",
    }
}

fn supplements(values: &Value) -> HashMap<&str, Value> {
    let mut result = HashMap::new();
    let mut conflicts = HashSet::new();
    for (path, item) in entries(Some(values)) {
        if !path.starts_with("supplements[") { continue; }
        let Some(key) = field(item, "key") else { continue };
        let value = item.get("value").cloned().unwrap_or(Value::Null);
        if key.starts_with("text.") && key.ends_with(".coverage") && value.as_map().is_none()
            || result.get(key).is_some_and(|prior| !exact(prior, &value))
        {
            conflicts.insert(key);
        }
        result.insert(key, value);
    }
    for key in conflicts { result.insert(key, map([("conflicting", Value::Bool(true))])); }
    result
}

fn range_excerpt(content: Option<&Value>, quote_ellipsis: bool) -> Result<String> {
    let Some(value) = content.and_then(Value::as_str) else { return Ok("range text not reported".to_owned()); };
    let (value, clipped) = excerpt(value, 160);
    if quote_ellipsis {
        literal(&text(format!("{value}{}", if clipped { "…" } else { "" })))
    } else {
        Ok(format!("{}{}", literal(&text(value))?, if clipped { "…" } else { "" }))
    }
}

fn selection_suffix(spans: &[Value], state: &str, total: Option<&Value>, unit: Option<&str>) -> Result<String> {
    let count = if let Some(total) = total.filter(|total| matches!(total, Value::Int(_) | Value::BigInt(_))) {
        format!("; {}/{} ranges", spans.len(), scalar(total))
    } else if spans.len() != 1 { format!("; {} ranges", spans.len()) } else { String::new() };
    let mut parts = Vec::new();
    for span in spans {
        let position = match (span.get("start"), span.get("end")) {
            (Some(start), Some(end)) => format!("[{}, {}) {}", scalar(start), scalar(end), unit_label(unit)),
            (Some(start), None) => format!("start={}, end=unknown; {}", scalar(start), unit_label(unit)),
            (None, Some(end)) => format!("start=unknown, end={}; {}", scalar(end), unit_label(unit)),
            (None, None) if unit.is_some() => format!("position unknown; {}", unit_label(unit)),
            (None, None) => "position unknown".to_owned(),
        };
        let collapsed = match span.get("collapsed") {
            Some(Value::Bool(true)) => "; collapsed", Some(Value::Bool(false)) => "; not collapsed", _ => "",
        };
        parts.push(format!("{position}{collapsed}; {}", range_excerpt(span.get("text"), false)?));
    }
    Ok(format!(" ({state}{count}): {}", if parts.is_empty() { "none".to_owned() } else { parts.join(" | ") }))
}

#[derive(Debug)]
pub(super) struct Selection {
    pub(super) reference: String,
    pub(super) source: String,
    unit: Option<String>,
    read_state: String,
    spans: Vec<Value>,
    coverage: Value,
    compact: String,
    detailed: String,
    interval: Option<(i64, i64)>,
    group: Option<(String, String)>,
    inactive: bool,
}

impl Selection {
    fn statement(&self, refs: &str, concise: bool) -> String {
        if concise && self.read_state == "complete" && self.interval.is_some() && !truthy(Some(&self.coverage)) {
            if let [span] = self.spans.as_slice() {
                if span.get("collapsed") == Some(&Value::Bool(true)) && !present(span.get("text")) {
                    if let Some((start, length)) = self.interval {
                        let end = i128::from(start) + i128::from(length);
                        return format!("Selection [{refs}]: [{start},{end}) {}", unit_label(self.unit.as_deref()));
                    }
                }
            }
        }
        format!("Text selection [{refs}]{}", self.compact)
    }

    fn detail(&self) -> String { format!("Text selection of {}{}", self.reference, self.detailed) }

    pub(super) fn inactive_interval(&self) -> Option<(i64, i64)> {
        self.inactive.then_some(self.interval).flatten()
    }
}

fn sorted(value: &Value) -> Value {
    match value {
        Value::Map(items) => {
            let mut items: Vec<_> = items.iter().map(|(key, value)| (key.clone(), sorted(value))).collect();
            items.sort_by(|(a, _), (b, _)| a.cmp(b));
            Value::Map(items)
        }
        Value::Seq(items) => Value::Seq(items.iter().map(sorted).collect()),
        value => value.clone(),
    }
}

pub(super) fn selection_plan(source: &Sources<'_>) -> Result<Vec<Selection>> {
    let facts = &source.request.snapshot.facts;
    let mut scopes = HashMap::new();
    let mut ambiguous = HashSet::new();
    let mut inputs = HashSet::new();
    let mut pending: Vec<_> = roots(source).iter().filter_map(|root| Some((root, field(root, "ref")?))).collect();
    while let Some((node, mut scope)) = pending.pop() {
        let reference = field(node, "ref").ok_or(TextError::Invalid("selection scope reference"))?;
        if matches!(field(node, "role"), Some("window" | "sheet" | "menuBar")) { scope = reference; }
        if scopes.insert(reference, scope).is_some_and(|previous| previous != scope) { ambiguous.insert(reference); }
        if matches!(field(node, "role"), Some("textField" | "textArea" | "comboBox" | "document"))
            || node.get("capabilities").is_some_and(|capabilities|
                ["typeText", "replaceText"].iter().any(|key| field(capabilities, key) == Some("available")))
        {
            inputs.insert(reference);
        }
        pending.extend(seq(node.get("children")).iter().map(|child| (child, scope)));
    }
    let mut uncertain_capture = false;
    let mut uncertain_sources = HashSet::new();
    for issue in seq(facts.get("issues")) {
        match seq(issue.get("path")) {
            [] => uncertain_capture = true,
            [Value::Text(scope)] if scope == "nodes" && field(issue, "state") != Some("partial") => uncertain_capture = true,
            [Value::Text(scope), Value::Text(token)] if scope == "nodes" => { uncertain_sources.insert(token.as_str()); }
            _ => (),
        }
    }
    let mut result = Vec::new();
    for row in seq(facts.get("nodes")) {
        let Some(values) = row.get("values") else { continue };
        let Some(spans) = values.get("text.selections").and_then(Value::as_seq) else { continue };
        if values.get("text.source").is_none() { continue; }
        let token = field(row, "node").ok_or(TextError::Invalid("selection node"))?;
        let reference = source.alias(token)?;
        let status = row.get("fieldStatus");
        let selection_status = status.and_then(|status| status.get("text.selections"));
        let has_text = |span: &Value| truthy(span.get("text"));
        if !inputs.contains(reference) && !matches!(field(values, "role"), Some("text_field" | "text_area" | "combo_box" | "document" | "web_area"))
            && !truthy(values.get("states.focused")) && !truthy(values.get("text.selected_text")) && selection_status.is_none()
            && matches!(spans, [span] if span.get("start") == Some(&Value::Int(0)) && span.get("end") == Some(&Value::Int(0)) && !has_text(span))
        {
            continue;
        }
        let unit = field(values, "text.unit");
        let coverage = supplements(values).remove("text.selections.coverage").unwrap_or_else(|| Value::Map(Vec::new()));
        let statuses = entries(status);
        let mut state = selection_status.and_then(|status| field(status, "state")).unwrap_or_else(||
            if statuses.iter().any(|(key, _)| key.starts_with("text.selections")) { "partial" } else { "complete" });
        if truthy(coverage.get("conflicting")) { state = "conflict"; }
        let total = coverage.get("total");
        let count = if let Some(total) = total.filter(|total| matches!(total, Value::Int(_) | Value::BigInt(_))) {
            format!("{}/{}", spans.len(), scalar(total))
        } else { spans.len().to_string() };
        let mut details = Vec::new();
        for (index, span) in spans.iter().enumerate() {
            let label = if span.get("collapsed") == Some(&Value::Bool(true)) { "empty range" } else { "range" };
            let position = match (span.get("start"), span.get("end")) {
                (Some(start), Some(end)) => format!("; offsets [{}, {}) {}", scalar(start), scalar(end), unit.filter(|unit| !unit.is_empty()).unwrap_or("unspecified unit")),
                _ => String::new(),
            };
            details.push(format!("{label} {}: {}{position}", index + 1, range_excerpt(span.get("text"), false)?));
        }
        let detailed = format!(" ({state}; {count} ranges): {}. Keyboard focus and caret activity are separate facts.",
            if details.is_empty() { "none".to_owned() } else { details.join("; ") });
        let coverage_paths: Vec<_> = entries(Some(values)).iter().filter_map(|(key, value)|
            (key.starts_with("supplements[") && field(value, "key") == Some("text.selections.coverage")).then_some(key)).collect();
        let below = |key: &str, base: &str| key == base || key.strip_prefix(base).is_some_and(|suffix| suffix.starts_with('.'));
        let clean = state == "complete" && !statuses.iter().any(|(key, _)|
            key == "supplements" || key == "text" || matches!(key.as_str(), "text.unit" | "text.source")
                || key.starts_with("text.selections") || coverage_paths.iter().any(|base| below(key, base)))
            && !truthy(coverage.get("conflicting")) && entries(Some(&coverage)).iter().all(|(key, _)| key == "total")
            && total.is_none_or(|total| matches!(total, Value::Null) || total.as_i64() == Some(spans.len() as i64));
        let interval = if clean && matches!(unit, Some("unicode_codepoint" | "utf16_code_unit")) && spans.len() == 1 {
            let span = &spans[0];
            span.get("start").and_then(Value::as_i64).zip(span.get("end").and_then(Value::as_i128))
                .filter(|(start, end)| *start >= 0 && i128::from(*start) <= *end)
                .and_then(|(start, end)| i64::try_from(end - i128::from(start)).ok().map(|length| (start, length)))
        } else { None };
        let group = if clean && !ambiguous.contains(reference) && !spans.is_empty()
            && spans.iter().all(|span| span.get("collapsed") == Some(&Value::Bool(true)) && !has_text(span))
        {
            scopes.get(reference).map(|scope| {
                let shape = sorted(&Value::Seq(vec![unit.map_or(Value::Null, text), Value::Seq(spans.to_vec()), coverage.clone()]));
                literal(&shape).map(|key| ((*scope).to_owned(), key))
            }).transpose()?
        } else { None };
        let focus_issues = statuses.iter().any(|(key, _)| key == "states" || ["states.focused", "text.selected_text", "text.caret", "text.caret_active"]
            .iter().any(|base| below(key, base)));
        let inactive = clean && !uncertain_capture && !uncertain_sources.contains(token) && interval.is_some_and(|(_, size)| size == 0)
            && spans.len() == 1 && values.get("states.focused") == Some(&Value::Bool(false)) && !focus_issues
            && values.get("text.caret_active") != Some(&Value::Bool(true))
            && (values.get("text.caret").is_none() || values.get("text.caret_active") == Some(&Value::Bool(false)))
            && !truthy(values.get("text.selected_text")) && spans[0].get("collapsed") == Some(&Value::Bool(true)) && !has_text(&spans[0]);
        let compact = selection_suffix(spans, state, total, unit)?;
        result.push(Selection {
            reference: reference.to_owned(), source: token.to_owned(), unit: unit.map(str::to_owned), read_state: state.to_owned(),
            spans: spans.to_vec(), coverage, compact, detailed, interval, group, inactive,
        });
    }
    Ok(result)
}

fn embedded_content(row: &Value) -> Option<&str> {
    let values = row.get("values")?;
    if let Some(content) = values.get("text.content") { return content.as_str(); }
    let location = values.get("text.content_field")?;
    entries(row.get("fieldPaths")).iter().find_map(|(name, path)| {
        if exact(path, location) { values.get(name).and_then(Value::as_str) } else { None }
    })
}

fn units_length(value: &str, unit: Option<&str>) -> Option<usize> {
    match unit {
        Some("unicode_codepoint") => Some(value.chars().count()), Some("utf16_code_unit") => Some(value.encode_utf16().count()),
        Some("utf8_byte") => Some(value.len()), _ => None,
    }
}

fn text_slice<'a>(value: &'a str, start: usize, end: usize, unit: Option<&str>) -> Option<&'a str> {
    if start > end { return None; }
    let byte = |target| {
        let mut offset = 0usize;
        for (byte, character) in value.char_indices() {
            if offset == target { return Some(byte); }
            offset += match unit {
                Some("unicode_codepoint") => 1, Some("utf16_code_unit") => character.len_utf16(),
                Some("utf8_byte") => character.len_utf8(), _ => return None,
            };
            if offset > target { return None; }
        }
        (offset == target).then_some(value.len())
    };
    value.get(byte(start)?..byte(end)?)
}

fn embedded_summaries(row: &Value, reference: &str, source: &Sources<'_>) -> Result<Vec<String>> {
    let Some(values) = row.get("values") else { return Ok(Vec::new()); };
    let objects = seq(values.get("text.embedded"));
    if objects.is_empty() { return Ok(Vec::new()); }
    let unit = field(values, "text.unit");
    let mut pieces = Vec::new();
    for object in objects {
        let [token, span] = seq(Some(object)) else { return Err(TextError::Invalid("embedded text object")); };
        let alias = source.alias(token.as_str().ok_or(TextError::Invalid("embedded text token"))?)?;
        let position = match (span.get("start"), span.get("end")) {
            (Some(start), Some(end)) => format!("@{}..{}", scalar(start), scalar(end)),
            _ => " (position unconfirmed)".to_owned(),
        };
        pieces.push(format!("{alias}{position}"));
    }
    let mut output = vec![format!("Embedded objects of {reference} ({}): {}; text-range membership is separate from containment.",
        unit_label(unit), pieces.join(", "))];
    let coverage = supplements(values).remove("text.embedded.coverage").unwrap_or(Value::Null);
    let Some(content) = embedded_content(row) else { return Ok(output); };
    if entries(row.get("fieldStatus")).iter().any(|(name, _)| name.starts_with("text.embedded"))
        || truthy(coverage.get("conflicting"))
        || coverage.get("total").and_then(Value::as_i64).is_some_and(|total| total != objects.len() as i64)
    {
        return Ok(output);
    }
    let Some(length) = units_length(content, unit) else { return Ok(output); };
    let mut pieces = Vec::new();
    let mut previous = 0;
    let mut previous_span = None;
    let mut remaining = 640;
    let mut clipped = false;
    let mut preview = |value: &str| -> Result<String> {
        let (shown, clipped_here) = excerpt(value, remaining.min(128));
        remaining -= shown.chars().count();
        clipped |= clipped_here;
        literal(&text(format!("{shown}{}", if clipped_here { "…" } else { "" })))
    };
    for object in objects {
        let [token, span] = seq(Some(object)) else { return Err(TextError::Invalid("embedded text object")); };
        let alias = source.alias(token.as_str().ok_or(TextError::Invalid("embedded text token"))?)?;
        let start = span.get("start").and_then(Value::as_i128).and_then(|value| usize::try_from(value).ok());
        let end = span.get("end").and_then(Value::as_i128).and_then(|value| usize::try_from(value).ok());
        let (Some(start), Some(end)) = (start, end) else { return Ok(output); };
        if start < previous || end < start || previous_span == Some((start, end)) && start == end { return Ok(output); }
        let (Some(before), Some(content_piece)) = (text_slice(content, previous, start, unit), text_slice(content, start, end, unit))
            else { return Ok(output); };
        if !before.is_empty() { pieces.push(preview(before)?); }
        let suffix = if content_piece.is_empty() { String::new() } else { format!(" {}", preview(content_piece)?) };
        pieces.push(format!("element {alias}{suffix}"));
        previous = end;
        previous_span = Some((start, end));
    }
    let Some(trailing) = text_slice(content, previous, length, unit) else { return Ok(output); };
    if !trailing.is_empty() { pieces.push(preview(trailing)?); }
    let suffix = if clipped { " (preview; full text and ranges remain in details)" } else { "" };
    output.push(format!("Reading sequence of {reference}: {}{suffix}", pieces.join(" → ")));
    Ok(output)
}

fn line_summary(row: &Value, reference: &str) -> Result<Option<String>> {
    let Some(values) = row.get("values") else { return Ok(None); };
    let Some(spans) = values.get("text.line_ranges").and_then(Value::as_seq) else { return Ok(None); };
    let metadata = supplements(values).remove("text.line_ranges.coverage").unwrap_or(Value::Null);
    let unit = unit_label(field(values, "text.unit"));
    let mut pieces = Vec::new();
    let mut previous = 0;
    let mut remaining = 1000usize;
    for span in spans {
        let content = field(span, "text");
        let (Some(start), Some(end)) = (span.get("start").and_then(Value::as_i128), span.get("end").and_then(Value::as_i128))
            else {
                let suffix = content.map(|content| literal(&text(content)).map(|value| format!(": {value}"))).transpose()?.unwrap_or_default();
                pieces.push(format!("span unconfirmed{suffix}"));
                continue;
            };
        if previous < start { pieces.push(format!("unsegmented source {previous}..{start}")); }
        let suffix = if let Some(content) = content {
            let (shown, clipped) = excerpt(content, remaining.min(96));
            remaining -= shown.chars().count();
            format!(" {}", literal(&text(format!("{shown}{}", if clipped { "…" } else { "" })))?)
        } else { " text unavailable".to_owned() };
        pieces.push(format!("{start}..{end}{suffix}"));
        previous = previous.max(end);
    }
    if truthy(metadata.get("conflicting")) { pieces.push("provider coverage declarations conflict".to_owned()); }
    if field(&metadata, "unit_support") == Some("unconfirmed") { pieces.push("provider may substitute a larger unit".to_owned()); }
    match metadata.get("document_end_observed") {
        Some(Value::Bool(true)) => pieces.push("document end observed".to_owned()),
        Some(Value::Bool(false)) => pieces.push("remaining layout unconfirmed".to_owned()), _ => (),
    }
    if entries(row.get("fieldStatus")).iter().any(|(name, _)| name.starts_with("text.line_ranges")) {
        pieces.push("range coverage or anchor unconfirmed; see field status".to_owned());
    }
    Ok(Some(format!("Native line-unit spans of {reference} ({unit}): {}", pieces.join("; "))))
}

fn node_context(values: &Value, reference: &str) -> Vec<String> {
    let mut scope = None;
    let mut conflicting = false;
    let mut memberships = Vec::new();
    for (name, item) in entries(Some(values)) {
        if !name.starts_with("supplements[") { continue; }
        let value = item.get("value").unwrap_or(&Value::Null);
        match field(item, "key") {
            Some("observation_scope") => {
                conflicting |= scope.is_some_and(|prior| !exact(prior, value));
                scope = Some(value);
            }
            Some("relation_membership") if value.as_map().is_some() => memberships.push(value),
            _ => (),
        }
    }
    let mut output = Vec::new();
    if let Some(scope) = scope.filter(|scope| !conflicting && field(scope, "input") == Some("observationOnly")) {
        let ownership = field(scope, "ownership").unwrap_or_default();
        let detail = field(scope, "detail").unwrap_or_default();
        output.push(format!("Observation {reference}: read only; window ownership {ownership}. {detail}"));
    }
    for membership in memberships {
        let relation = field(membership, "relation").unwrap_or_default();
        let completed = membership.get("completedAt").map_or_else(|| "None".to_owned(), scalar);
        output.push(format!("Relation {relation} of {reference}: membership captured at {completed}; node values sampled separately."));
    }
    output
}

fn unspecified_body(row: &Value, facts: &Value) -> bool {
    let body = |path: &[Value]| path.is_empty() || path[0].as_str() == Some("role") || path[0].as_str() == Some("text")
        && (path.len() == 1 || matches!(path.get(1).and_then(Value::as_str), Some("content" | "content_field")));
    for (name, _) in entries(row.get("fieldStatus")) {
        if body(seq(row.get("fieldPaths").and_then(|paths| paths.get(name)))) { return false; }
    }
    let token = field(row, "node");
    !seq(facts.get("issues")).iter().any(|issue| {
        let path = seq(issue.get("path"));
        path.len() < 2 || path[0].as_str() != Some("nodes")
            || path.get(1).and_then(Value::as_str) == token && body(&path[2..])
    })
}

pub(super) fn read(source: &Sources<'_>, selections: &[Selection], compact: bool, concise: bool) -> Result<Vec<String>> {
    let facts = &source.request.snapshot.facts;
    let mut output = relation_summaries(source)?;
    if !compact { output.extend(geometry_summaries(source)?); }
    let by_ref: HashMap<_, _> = selections.iter().map(|entry| (entry.reference.as_str(), entry)).collect();
    for relation in seq(facts.get("relations")) {
        if field(relation, "enumeration") != Some("sampled") || matches!(field(relation, "state"), Some("absent" | "unsupported")) { continue; }
        let reference = source.alias(field(relation, "source").ok_or(TextError::Invalid("sampled source"))?)?;
        let kind = field(relation, "kind").unwrap_or_default();
        if kind == "viewport_candidates" {
            output.push(format!("Viewport candidates of {reference}: sampled; full viewport coverage and occlusion are unknown."));
        } else {
            output.push(format!("Relation {kind} of {reference}: sampled targets; complete collection membership is unknown."));
        }
    }
    for row in seq(facts.get("nodes")) {
        let reference = source.alias(field(row, "node").ok_or(TextError::Invalid("read summary source"))?)?;
        let values = row.get("values").ok_or(TextError::Invalid("read summary values"))?;
        output.extend(node_context(values, reference));
        if let Some(mode @ ("virtualized" | "provider_managed")) = field(values, "structure.collection_mode") {
            output.push(format!("Collection {reference}: {mode}; logical total unknown."));
        }
        if values.get("states.virtualized") == Some(&Value::Bool(true)) {
            output.push(format!("Item {reference}: virtualized; some properties may be unavailable in this observation."));
        }
        if matches!(field(values, "role"), Some("document" | "web_area"))
            && !["text.content", "text.content_field"].iter().any(|key| values.get(key).is_some())
            && !(compact && concise && unspecified_body(row, facts))
        {
            let state = row.get("fieldStatus").and_then(|status| status.get("text.content")).and_then(|status| field(status, "state"))
                .unwrap_or("not collected");
            output.push(if compact { format!("Document text [{reference}]: {state}; control values separate.") }
                else { format!("Document text of {reference}: {state}; control values are recorded separately.") });
        }
        if values.get("text.source").is_none() { continue; }
        if let Some(selection) = by_ref.get(reference).filter(|entry| !compact || entry.group.is_none() && !(concise && entry.inactive)) {
            output.push(if compact { selection.statement(reference, concise) } else { selection.detail() });
        }
        let active = values.get("text.caret_active");
        let span = values.get("text.caret");
        if active.is_some() || span.is_some() {
            let position = match span.and_then(|span| span.get("start").zip(span.get("end"))) {
                Some((start, end)) => format!("{} {}..{}", unit_label(field(values, "text.unit")), scalar(start), scalar(end)),
                None => "position unconfirmed".to_owned(),
            };
            let activity = active.map(literal).transpose()?.unwrap_or_else(|| "unknown".to_owned());
            output.push(if compact { format!("Caret [{reference}]: active={activity}; {position}") }
                else { format!("Caret of {reference} (active={activity}): {position}; keyboard focus and selection are independent.") });
        }
        output.extend(embedded_summaries(row, reference, source)?);
        output.extend(line_summary(row, reference)?);
        if !compact {
            if let Some(visible) = values.get("text.visible_ranges") {
                output.push(format!("Visible text ranges of {reference}: {}", literal(visible)?));
            }
        }
    }
    if compact {
        let mut groups = Groups::default();
        for entry in selections {
            if concise && entry.inactive { continue; }
            if let Some(group) = &entry.group { groups.add(group, entry); }
        }
        for (_, group) in groups.rows {
            let refs = reference_set(group.iter().map(|entry| entry.reference.as_str()))?;
            output.push(group[0].statement(&refs, concise));
        }
    }
    Ok(output)
}

pub(super) fn summarized_interval(entry: &Selection, inline: &Value) -> bool {
    inline.as_map().is_some_and(|fields| fields.len() == 2)
        && inline.get("location").and_then(Value::as_i64).zip(inline.get("length").and_then(Value::as_i64))
            .is_some_and(|range| Some(range) == entry.interval)
}

pub(super) fn selection_destinations(
    selections: &[Selection], references: &[&str], concise: bool,
) -> Result<Vec<Value>> {
    let mut grouped = Groups::default();
    let mut declarations = HashMap::new();
    for entry in selections {
        if concise && entry.inactive { continue; }
        if let Some(group) = &entry.group { grouped.add(group, entry); }
        else { declarations.insert(entry.reference.as_str(), entry.statement(&entry.reference, concise)); }
    }
    for (_, group) in grouped.rows {
        let refs = reference_set(group.iter().map(|entry| entry.reference.as_str()))?;
        let line = group[0].statement(&refs, concise);
        for entry in group { declarations.insert(entry.reference.as_str(), line.clone()); }
    }
    let by_ref: HashMap<_, _> = selections.iter().map(|entry| (entry.reference.as_str(), entry)).collect();
    references.iter().map(|reference| {
        let entry = by_ref.get(reference).ok_or(TextError::Invalid("selection summary claim"))?;
        let deferred = concise && entry.inactive;
        let statement = if deferred { entry.detail() } else {
            declarations.get(reference).ok_or(TextError::Invalid("missing selection summary declaration"))?.clone()
        };
        Ok(map([
            ("ref", text(*reference)), ("source", text(&entry.source)), ("property", text("text.selections")),
            ("path", Value::Seq(vec![text("text"), text("selections")])), ("spans", Value::Seq(entry.spans.clone())),
            ("coverage", entry.coverage.clone()),
            ("interval", entry.interval.map_or(Value::Null, |(start, size)| Value::Seq(vec![Value::Int(start), Value::Int(size)]))),
            ("unit", entry.unit.as_deref().map_or(Value::Null, text)), ("state", text(&entry.read_state)),
            ("covered_fields", Value::Seq(vec![text("selectionRange")])),
            ("representation", text(if deferred { "Text selection details" } else { "Text selection summary" })),
            ("statement", text(statement)), ("target", text(*reference)), ("visibility", text(if deferred { "details" } else { "public" })),
        ]))
    }).collect()
}
