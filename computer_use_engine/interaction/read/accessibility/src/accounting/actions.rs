// Copyright (c) Huawei Technologies Co., Ltd. 2026. All rights reserved.

use std::collections::{HashMap, HashSet};

use super::{
    entries, field, json, map, present, reference_set, seq, text, truthy,
    AccountingPart, Groups, JsonFormat, NodeId, Projection, Result, Sources, TextError, Value,
};
use crate::render::field as format_field;

fn plain(value: &Value) -> Result<String> { json(value, JsonFormat::Plain) }

fn get<'a>(fields: &'a [(String, Value)], key: &str) -> Option<&'a Value> {
    fields.iter().find(|(name, _)| name == key).map(|(_, item)| item)
}

fn secondaries(fields: &[(String, Value)]) -> HashSet<(&str, &str)> {
    let actions = seq(get(fields, "actions"));
    let role = get(fields, "role").and_then(Value::as_str).unwrap_or_default();
    let selectable = truthy(get(fields, "selectable"));
    let pressable = truthy(get(fields, "pressable"));
    let capabilities = get(fields, "capabilities");
    let click = capabilities.and_then(|caps| field(caps, "click")) == Some("available");
    let ordinary = matches!(role, "button" | "checkBox" | "radioButton" | "switch" | "link" | "menuButton" |
        "popUpButton" | "comboBox" | "textField" | "textArea" | "row") || pressable
        || truthy(get(fields, "_factBacked")) && click;
    let menu = matches!(role, "menuItem" | "menuBarItem");
    let invoke = actions.iter().any(|item| field(item, "kind").or_else(|| field(item, "name")) == Some("invoke"));
    actions.iter().filter_map(|action| {
        let name = field(action, "name")?;
        let kind = field(action, "kind").unwrap_or(name);
        let primary = match kind {
            "invoke" if !selectable => if capabilities.is_some() { click } else { ordinary || menu },
            "select" if !selectable => capabilities.is_none() && menu,
            "show_menu" if !selectable && pressable && matches!(role, "popUpButton" | "menuButton" | "comboBox") =>
                capabilities.is_none() || click && !invoke,
            "expand" | "collapse" => truthy(get(fields, "expandable")) && capabilities.is_none(),
            _ => false,
        };
        (!primary).then_some((name, kind))
    }).collect()
}

fn at_actions(issue: &Value, token: &str) -> bool {
    let path = seq(issue.get("path"));
    path.first().and_then(Value::as_str) == Some("nodes") && path.get(1).and_then(Value::as_str) == Some(token)
        && path.get(2).and_then(Value::as_str) == Some("actions")
}

fn native_rows<'a>(source: &Sources<'a>, id: NodeId) -> Result<(&'a str, &'a Value, &'a Value)> {
    let token = source.token(id)?;
    let native = source.request.snapshot.observation.get("nodes").and_then(|nodes| nodes.get(token))
        .ok_or(TextError::Invalid("native action owner"))?;
    let facts = source.facts.get(token).copied().ok_or(TextError::Invalid("native action facts"))?;
    Ok((token, native, facts))
}

fn ordinary_control(native: &Value) -> bool {
    if !matches!(field(native, "role"), Some("button" | "checkbox" | "radio_button" | "switch" | "popup_button" |
        "menu_button" | "static_text" | "image" | "link" | "text_field" | "combo_box"))
        || native.get("states").and_then(|states| states.get("modal")) == Some(&Value::Bool(true))
        || entries(native.get("structure")).iter().any(|(key, value)| !matches!(key.as_str(), "orientation" | "index")
            && !matches!(value, Value::Null) && !(key == "level" && value == &Value::Int(0)))
    {
        return false;
    }
    let mut focus = seq(native.get("actions")).iter().filter(|action| !present(action.get("key"))
        && field(action, "kind") == Some("focus")).peekable();
    focus.peek().is_some() && focus.all(|action| action.get("available") == Some(&Value::Bool(true))
        && ["target", "label", "description"].iter().all(|key| !present(action.get(key)))
        && seq(action.get("shortcuts")).is_empty())
}

pub(super) fn mechanisms(source: &Sources<'_>, projection: &Projection) -> Result<HashSet<(NodeId, usize)>> {
    let mut output = HashSet::new();
    if field(&source.request.snapshot.observation, "backend") != Some("macos") { return Ok(output); }
    let issues = seq(source.request.snapshot.observation.get("issues"));
    let mut blocked = HashSet::new();
    for issue in issues {
        let path = seq(issue.get("path"));
        if path.first().and_then(Value::as_str) != Some("nodes") { continue; }
        if let Some(token) = path.get(1).and_then(Value::as_str).filter(|_| path.len() == 2
            || matches!(path.get(2).and_then(Value::as_str), Some("role" | "actions")))
        {
            blocked.insert(token);
        }
    }
    for id in &projection.source_order {
        let (token, native, facts) = native_rows(source, *id)?;
        let Some(node) = projection.node(*id) else { continue; };
        let fields = &node.native_fields;
        let Some(actions) = native.get("actions").and_then(Value::as_seq) else { continue; };
        if blocked.contains(token) || truthy(get(fields, "actionsUnavailable"))
            || facts.get("actions").and_then(|catalogue| field(catalogue, "state")) != Some("complete")
        {
            continue;
        }
        let secondary = secondaries(fields);
        let role = field(native, "role");
        if matches!(role, Some("menu" | "menu_bar" | "menu_item" | "menu_bar_item")) {
            let primary = get(fields, "capabilities").and_then(|caps| field(caps, "click")) == Some("available")
                || actions.iter().any(|action| field(action, "key") == Some("AXPress") && field(action, "kind") == Some("invoke")
                    && action.get("available") != Some(&Value::Bool(false)) && !present(action.get("target")));
            for (index, action) in actions.iter().enumerate() {
                let (Some(key), Some(kind)) = (field(action, "key"), field(action, "kind")) else { continue; };
                let known = key == "AXCancel" && kind == "cancel" || key == "AXPick" && primary
                    && matches!(role, Some("menu_item" | "menu_bar_item")) && matches!(kind, "custom" | "pick" | "select");
                if known && secondary.contains(&(key, kind)) && !present(action.get("target"))
                    && seq(action.get("shortcuts")).is_empty()
                    && !actions.iter().any(|other| field(other, "key") == Some(key)
                        && (field(other, "kind") != Some(kind) || present(other.get("target")) || !seq(other.get("shortcuts")).is_empty()))
                {
                    output.insert((*id, index));
                }
            }
        }
        if ordinary_control(native) && secondary.contains(&("AXRaise", "raise")) {
            let raises: Vec<_> = actions.iter().enumerate().filter(|(_, action)| field(action, "key") == Some("AXRaise")).collect();
            if !raises.is_empty() && raises.iter().all(|(_, action)| field(action, "kind") == Some("raise")
                && !present(action.get("target")) && seq(action.get("shortcuts")).is_empty()
                && action.get("available") != Some(&Value::Bool(false)))
            {
                output.extend(raises.into_iter().map(|(index, _)| (*id, index)));
            }
        }
    }
    Ok(output)
}

pub(super) fn projected_mechanisms(
    source: &Sources<'_>, projection: &Projection, native: &HashSet<(NodeId, usize)>,
) -> Result<HashSet<(NodeId, usize)>> {
    let mut output = HashSet::new();
    for (owner, ordinal) in native {
        let (_, node, _) = native_rows(source, *owner)?;
        let action = seq(node.get("actions")).get(*ordinal).ok_or(TextError::Invalid("native mechanism ordinal"))?;
        let (Some(key), Some(kind)) = (field(action, "key"), field(action, "kind")) else {
            return Err(TextError::Invalid("native action mechanism"));
        };
        let projected = projection.node(*owner).ok_or(TextError::Invalid("projected mechanism owner"))?;
        for (index, action) in seq(projected.field("actions")).iter().enumerate() {
            let name = field(action, "name");
            if name == Some(key) && field(action, "kind").or(name) == Some(kind) {
                output.insert((*owner, index));
            }
        }
    }
    Ok(output)
}

fn action_record(action: &Value) -> Value {
    map(["kind", "key", "label", "description", "target", "available", "shortcuts"].map(|key| {
        let value = action.get(key).cloned().unwrap_or_else(|| if key == "shortcuts" { Value::Seq(Vec::new()) } else { Value::Null });
        (key, value)
    }))
}

fn related_issues(issues: &[Value], token: &str, index: usize) -> Value {
    let mut rows = Vec::new();
    for issue in issues.iter().filter(|issue| at_actions(issue, token)) {
        let relative = &seq(issue.get("path"))[3..];
        if relative.first().is_some_and(|part| part.as_i64() != Some(index as i64)) { continue; }
        let mut path = vec![text(if relative.is_empty() { "catalogue" } else { "action" })];
        path.extend(relative.iter().skip(1).cloned());
        let continuation = issue.get("continuation").filter(|continuation| !matches!(continuation, Value::Null)).map_or(Value::Null, |record|
            map(["token", "kind", "consistency", "unit", "total", "delivered"].map(|key|
                (key, record.get(key).cloned().unwrap_or(Value::Null)))));
        rows.push(map([
            ("path", Value::Seq(path)), ("state", issue.get("state").cloned().unwrap_or(Value::Null)),
            ("detail", issue.get("detail").cloned().unwrap_or(Value::Null)), ("continuation", continuation),
            ("alternatives", issue.get("alternatives").cloned().unwrap_or_else(|| Value::Seq(Vec::new()))),
        ]));
    }
    Value::Seq(rows)
}

struct Declaration {
    action: Value,
    state: String,
    issues: Value,
    refs: Vec<String>,
    occurrences: Vec<(String, usize)>,
    scope: Option<String>,
}

#[derive(Default)]
struct Declarations {
    rows: Vec<Declaration>,
    keys: HashMap<String, usize>,
}

fn canonical(value: &Value) -> Value {
    match value {
        Value::Map(entries) => {
            let mut entries: Vec<_> = entries.iter().map(|(key, value)| (key.clone(), canonical(value))).collect();
            entries.sort_by(|(a, _), (b, _)| a.cmp(b));
            Value::Map(entries)
        }
        Value::Seq(items) => Value::Seq(items.iter().map(canonical).collect()),
        value => value.clone(),
    }
}

fn record(
    action: &Value, state: &str, issues: &Value, reference: &str, token: &str, index: usize, scope: Option<&str>,
) -> Value {
    let mut fields = vec![
        ("ref".to_owned(), text(reference)), ("native".to_owned(), text(token)),
        ("index".to_owned(), Value::Int(index as i64)), ("action".to_owned(), action.clone()),
        ("state".to_owned(), text(state)), ("issues".to_owned(), issues.clone()),
    ];
    if let Some(scope) = scope { fields.push(("scope".to_owned(), text(scope))); }
    fields.extend([
        ("kind".to_owned(), text("display")), ("target".to_owned(), text(reference)),
        ("representation".to_owned(), text(if scope.is_some() { "native capability declaration" } else { "supporting action declaration" })),
        ("visibility".to_owned(), text("details")),
    ]);
    Value::Map(fields)
}

impl Declarations {
    fn add(
        &mut self, action: &Value, state: &str, issues: &Value, scope: Option<&str>, reference: &str, index: usize,
    ) -> Result<()> {
        let key = plain(&canonical(&Value::Seq(vec![
            scope.map_or(Value::Null, text), action.clone(), text(state), issues.clone(),
        ])))?;
        let found = *self.keys.entry(key).or_insert_with(|| {
            self.rows.push(Declaration {
                action: action.clone(), state: state.to_owned(), issues: issues.clone(), refs: Vec::new(),
                occurrences: Vec::new(), scope: scope.map(str::to_owned),
            });
            self.rows.len() - 1
        });
        let row = &mut self.rows[found];
        if !row.refs.iter().any(|ref_| ref_ == reference) { row.refs.push(reference.to_owned()); }
        row.occurrences.push((reference.to_owned(), index));
        Ok(())
    }
}

fn availability(action: &Value) -> &'static str {
    match action.get("available") {
        Some(Value::Bool(true)) => "available", Some(Value::Bool(false)) => "unavailable", _ => "availability unknown",
    }
}

fn details(output: &mut Vec<String>, row: &Declaration) -> Result<()> {
    if let Some(target) = row.action.get("target").filter(|target| !matches!(target, Value::Null)) {
        output.push(format!("Target: {}", plain(target)?));
    }
    if let Some(shortcuts) = row.action.get("shortcuts").filter(|shortcuts| !seq(Some(shortcuts)).is_empty()) {
        output.push(format!("Shortcuts: {}", plain(shortcuts)?));
    }
    if row.state != "complete" { output.push(format!("catalogue {}", row.state)); }
    if !seq(Some(&row.issues)).is_empty() { output.push(format!("Issues: {}", plain(&row.issues)?)); }
    Ok(())
}

fn supporting(row: &Declaration) -> Result<String> {
    let action = &row.action;
    let key = field(action, "key");
    let description = field(action, "description");
    let label = field(action, "label");
    let kind = field(action, "kind").unwrap_or_default();
    let friendly = match kind { "show_menu" => "Show menu", "scroll_into_view" => "Scroll into view", _ => kind };
    let title = [description, label, key].into_iter().flatten().find(|item| !item.is_empty()).unwrap_or(friendly);
    let mut fields = vec![format_field(title)];
    let mut represented = HashSet::from([title]);
    if let Some(key) = key {
        if key != title { fields[0].push_str(&format!(" [name: {}]", plain(&text(key))?)); }
        represented.insert(key);
    } else { fields[0].push_str(" (no callable name)"); }
    for (name, value) in [("Description", description), ("Label", label)] {
        if let Some(value) = value.filter(|value| represented.insert(value)) {
            fields.push(format!("{name}: {}", format_field(value)));
        }
    }
    fields.push(availability(action).to_owned());
    details(&mut fields, row)?;
    let references = reference_set(row.refs.iter().map(String::as_str))?;
    Ok(format!("Supporting actions [elements {references}]: {}", fields.join("; ")))
}

fn capability(row: &Declaration) -> Result<String> {
    let action = &row.action;
    let mut fields = vec![format!("{} {}", format_field(field(action, "kind").unwrap_or_default()), availability(action))];
    for (label, field_name) in [("Label", "label"), ("Description", "description")] {
        if let Some(value) = field(action, field_name) { fields.push(format!("{label}: {}", format_field(value))); }
    }
    details(&mut fields, row)?;
    Ok(fields.join("; "))
}

fn capability_lines(groups: Declarations) -> Result<Vec<String>> {
    if groups.rows.is_empty() { return Ok(Vec::new()); }
    let mut scopes = Groups::default();
    for row in groups.rows { scopes.add(row.scope.clone().unwrap_or_default(), row); }
    let mut lines = vec!["Native capabilities (reported; no performAction name):".to_owned()];
    for (_, rows) in scopes.rows {
        let formatted = rows.iter().map(capability).collect::<Result<Vec<_>>>()?;
        let baseline = rows.iter().zip(&formatted).map(|(row, text)|
            Ok(format!("  [elements {}]: {text}", reference_set(row.refs.iter().map(String::as_str))?))).collect::<Result<Vec<_>>>()?;
        if rows.len() < 2 { lines.extend(baseline); continue; }
        let mut by_ref = Groups::<&str, (usize, usize)>::default();
        for (group_index, row) in rows.iter().enumerate() {
            for (reference, index) in &row.occurrences { by_ref.add(reference.as_str(), (*index, group_index)); }
        }
        let mut bundles = Groups::<Vec<usize>, &str>::default();
        for (reference, mut occurrences) in by_ref.rows {
            occurrences.sort_unstable();
            if occurrences.windows(2).any(|pair| pair[0].0 == pair[1].0) { continue; }
            bundles.add(occurrences.into_iter().map(|(_, group)| group).collect(), reference);
        }
        let mut candidate = Vec::new();
        let mut bundled = HashSet::new();
        for (mut order, references) in bundles.rows {
            order.sort_unstable();
            order.dedup();
            if references.len() < 2 || order.len() < 2 { continue; }
            candidate.push(format!("  [elements {}]:", reference_set(references.iter().copied())?));
            candidate.extend(order.into_iter().map(|index| format!("    {}", formatted[index])));
            bundled.extend(references);
        }
        if bundled.is_empty() { lines.extend(baseline); continue; }
        for (group, text) in rows.iter().zip(&formatted) {
            let remaining: Vec<_> = group.refs.iter().map(String::as_str).filter(|reference| !bundled.contains(reference)).collect();
            if !remaining.is_empty() {
                candidate.push(format!("  [elements {}]: {text}", reference_set(remaining.into_iter())?));
            }
        }
        if candidate.join("\n").chars().count() < baseline.join("\n").chars().count() { lines.extend(candidate); }
        else { lines.extend(baseline); }
    }
    Ok(lines)
}

#[derive(Default)]
pub(super) struct ActionAccounting {
    supporting: Declarations,
    capabilities: Declarations,
}

impl ActionAccounting {
    pub(super) fn part(&mut self, source: &Sources<'_>, part: &AccountingPart<'_>) -> Result<(Vec<Value>, Vec<Value>)> {
        let Some(projection) = part.projection.filter(|_| !part.uncompressed) else { return Ok((Vec::new(), Vec::new())); };
        let visible: HashSet<_> = projection.visible_order().map_err(|_| TextError::Invalid("action presentation order"))?.into_iter().collect();
        let whole = source.request.public_api && source.request.share_descriptions && projection.options.subtree.is_none();
        let deferred = if whole { mechanisms(source, projection)? } else { HashSet::new() };
        let scope = source.reference(projection.root)?;
        let issues = seq(source.request.snapshot.observation.get("issues"));
        let mut supporting = Vec::new();
        let mut capabilities = Vec::new();
        for id in &projection.source_order {
            let (token, native, facts) = native_rows(source, *id)?;
            let reference = source.alias(token)?;
            let node = projection.node(*id).ok_or(TextError::Invalid("supporting action projection"))?;
            let secondary = secondaries(&node.native_fields);
            let state = facts.get("actions").and_then(|actions| field(actions, "state")).unwrap_or("not_read");
            for (index, action) in seq(native.get("actions")).iter().enumerate() {
                let key = field(action, "key");
                let kind = field(action, "kind").unwrap_or_default();
                let supports = (matches!(kind, "show_menu" | "scroll_into_view") || deferred.contains(&(*id, index)))
                    && key.is_none_or(|key| secondary.contains(&(key, kind)));
                let capability = !supports && key.is_none() && !visible.contains(id);
                if !(supports || capability) { continue; }
                let data = action_record(action);
                let related = related_issues(issues, token, index);
                if supports {
                    self.supporting.add(&data, state, &related, None, reference, index)?;
                    supporting.push(record(&data, state, &related, reference, token, index, None));
                } else {
                    self.capabilities.add(&data, state, &related, Some(scope), reference, index)?;
                    capabilities.push(record(&data, state, &related, reference, token, index, Some(scope)));
                }
            }
        }
        Ok((supporting, capabilities))
    }

    pub(super) fn lines(self) -> Result<Vec<String>> {
        let supporting = self.supporting.rows.iter().map(supporting).collect::<Result<Vec<_>>>()?;
        let capabilities = capability_lines(self.capabilities)?;
        Ok([supporting, capabilities].into_iter().filter(|lines| !lines.is_empty()).map(|lines| lines.join("\n")).collect())
    }
}
