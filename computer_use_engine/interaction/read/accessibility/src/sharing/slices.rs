// Copyright (c) Huawei Technologies Co., Ltd. 2026. All rights reserved.

//! Select exact, profitable native ancestor text ranges before serialization.

use super::*;
use crate::facts::FactSnapshot;

pub(crate) type SlotKey = (NodeId, String);

#[derive(Clone, Debug)]
pub(crate) struct TextSliceField {
    pub id: NodeId,
    pub reference: String,
    pub slot: String,
    pub native_token: String,
    pub native_path: Vec<&'static str>,
    pub scope: NodeId,
    pub capture: [String; 3],
    pub value: String,
    pub atom_slot: Slot,
}

#[derive(Clone, Copy, Debug, Eq, PartialEq)]
pub(crate) enum TextSliceMode {
    Same,
    Excerpt,
}

#[derive(Clone, Debug, Eq, PartialEq)]
pub(crate) struct SliceFragment {
    pub start: usize,
    pub end: usize,
    pub text: String,
}

#[derive(Clone, Debug)]
pub(crate) struct TextSlicePlan {
    pub source: TextSliceField,
    pub anchor: TextSliceField,
    pub start_utf16: usize,
    pub end_utf16: usize,
    pub estimated_saved_bytes: usize,
    pub mode: TextSliceMode,
    pub fragments: Vec<SliceFragment>,
}

impl TextSlicePlan {
    pub(crate) fn statement(&self) -> String {
        use crate::render::{atoms::capitalize_key, quoted};
        let anchor = format!("element {}.{}", self.anchor.reference, capitalize_key(&self.anchor.slot));
        if self.mode == TextSliceMode::Same {
            return format!("same text as {anchor}");
        }
        let fragments = self.fragments.iter().map(|fragment| quoted(&fragment.text)).collect::<Vec<_>>();
        format!("excerpt {} [part of {anchor}; captured details: element {}]", fragments.join(" … "), self.source.reference)
    }

    pub(crate) fn destination(&self) -> Value {
        let text = |value: &str| Value::Text(value.to_owned());
        let path = |field: &TextSliceField| Value::Seq(field.native_path.iter().map(|name| text(name)).collect());
        let fragments = self.fragments.iter().map(|fragment| Value::Map(vec![
            ("start".to_owned(), Value::Int(fragment.start as i64)),
            ("end".to_owned(), Value::Int(fragment.end as i64)),
            ("text".to_owned(), text(&fragment.text)),
        ])).collect();
        Value::Map([
            ("ref", text(&self.source.reference)), ("native", text(&self.source.native_token)),
            ("field", text(&self.source.slot)), ("path", path(&self.source)), ("value", text(&self.source.value)),
            ("anchor", text(&self.anchor.reference)), ("anchorNative", text(&self.anchor.native_token)),
            ("anchorField", text(&self.anchor.slot)), ("anchorPath", path(&self.anchor)),
            ("start", Value::Int(self.start_utf16 as i64)), ("end", Value::Int(self.end_utf16 as i64)),
            ("unit", text("utf16_code_unit")),
            ("scope", Value::Seq(self.source.capture.iter().map(|part| text(part)).collect())),
            ("bare", Value::Bool(self.source.atom_slot.label.is_none())),
            ("mode", text(if self.mode == TextSliceMode::Same { "same" } else { "excerpt" })),
            ("fragments", Value::Seq(fragments)), ("statement", Value::Text(self.statement())),
        ].into_iter().map(|(key, value)| (key.to_owned(), value)).collect())
    }
}

#[derive(Default)]
pub(crate) struct TextSliceDocument {
    pub plans: HashMap<SlotKey, TextSlicePlan>,
    pub anchors: HashSet<SlotKey>,
    pub order: Vec<SlotKey>,
}

impl TextSliceDocument {
    pub(crate) fn get(&self, id: NodeId, slot: &str) -> Option<&TextSlicePlan> {
        self.plans.get(&(id, slot.to_owned()))
    }

    pub(crate) fn destinations(&self) -> impl Iterator<Item = Value> + '_ {
        self.order.iter().map(|key| self.plans[key].destination())
    }
}

pub(crate) struct TextSliceRequest<'a> {
    pub projection: &'a Projection,
    pub atoms: &'a AtomPlan,
    pub snapshot: &'a FactSnapshot,
    pub value_fields: Option<&'a Value>,
    pub disclosure: Option<&'a DisclosurePlan>,
    pub excluded_sources: &'a HashSet<NodeId>,
    pub excluded_slots: &'a HashSet<SlotKey>,
}

struct Sources<'a> {
    nodes: &'a [(String, Value)],
    session: &'a str,
    capture: &'a str,
    issues: HashMap<&'a str, Vec<&'a [Value]>>,
    parents: HashMap<NodeId, NodeId>,
}

fn complete(issues: Option<&Vec<&[Value]>>, path: &[&str]) -> bool {
    !issues.is_some_and(|issues| issues.iter().any(|issue| {
        path.iter().zip(issue.iter()).all(|(name, component)| component.as_str() == Some(name))
    }))
}

fn allowed_relation(relation: &Value) -> bool {
    matches!(relation.get("evidence").and_then(Value::as_str).unwrap_or("reported"), "reported" | "verified")
}

fn missing(value: Option<&Value>) -> bool {
    matches!(value, None | Some(Value::Null))
}

fn valid_children(relation: &Value, targets: &[Value], proved: bool) -> bool {
    proved && allowed_relation(relation)
        && relation.get("enumeration").and_then(Value::as_str).unwrap_or("enumerated") == "enumerated"
        && (missing(relation.get("offset")) || relation.get("offset").and_then(Value::as_i64) == Some(0))
        && (missing(relation.get("total")) || relation.get("total").and_then(Value::as_i64) == Some(targets.len() as i64))
        && (missing(relation.get("positions")) || relation.get("positions").and_then(Value::as_seq).is_some_and(|positions| {
            positions.len() == targets.len() && positions.iter().enumerate().all(|(index, item)| item.as_i64() == Some(index as i64))
        }))
        && targets.iter().filter_map(Value::as_str).collect::<HashSet<_>>().len() == targets.len()
}

fn relations(node: &Value) -> &[(String, Value)] {
    node.get("relations").and_then(Value::as_map).unwrap_or_default()
}

fn capture_sources<'a>(request: &TextSliceRequest<'a>) -> Result<Option<Sources<'a>>> {
    let observation = &request.snapshot.observation;
    let nodes = observation.get("nodes").and_then(Value::as_map).ok_or(SharingError("missing text slice sources"))?;
    let session = observation.get("session").and_then(Value::as_str).ok_or(SharingError("missing text slice session"))?;
    let capture = observation.get("id").and_then(Value::as_str).ok_or(SharingError("missing text slice capture"))?;
    let identifiers: HashMap<_, _> = nodes.iter().enumerate().map(|(index, (token, _))| (token.as_str(), NodeId(index))).collect();
    let mut issues: HashMap<_, Vec<_>> = HashMap::new();
    for issue in observation.get("issues").and_then(Value::as_seq).unwrap_or_default() {
        let Some(path) = issue.get("path").and_then(Value::as_seq) else { continue };
        if path.is_empty() {
            return Ok(None);
        }
        if let [Value::Text(root), Value::Text(token), fields @ ..] = path {
            if root == "nodes" {
                issues.entry(token.as_str()).or_default().push(fields);
            }
        }
    }
    let mut candidates: HashMap<NodeId, HashSet<NodeId>> = HashMap::new();
    let mut unreliable = HashSet::new();
    for (index, (token, node)) in nodes.iter().enumerate() {
        for (key, relation) in relations(node) {
            if relation.get("kind").and_then(Value::as_str) != Some("children") {
                continue;
            }
            let targets = relation.get("targets").and_then(Value::as_seq).unwrap_or_default();
            let valid = valid_children(relation, targets, complete(issues.get(token.as_str()), &["relations", key]));
            for child in targets {
                if let Some(&id) = child.as_str().and_then(|token| identifiers.get(token)) {
                    candidates.entry(id).or_default().insert(NodeId(index));
                    if !valid {
                        unreliable.insert(id);
                    }
                }
            }
        }
    }
    let mut parents = HashMap::new();
    for (&id, candidates) in &candidates {
        let Some(source) = request.projection.node(id) else { continue };
        if candidates.len() != 1 || unreliable.contains(&id) || id == source.scope {
            continue;
        }
        let parent = *candidates.iter().next().unwrap();
        let Some(parent_source) = request.projection.node(parent) else { continue };
        if parent_source.scope != source.scope {
            continue;
        }
        let (token, node) = &nodes[id.0];
        let native_parent = &nodes[parent.0].0;
        let matches_parent = relations(node).iter().all(|(key, relation)| {
            if relation.get("kind").and_then(Value::as_str) != Some("parent") {
                return true;
            }
            complete(issues.get(token.as_str()), &["relations", key])
                && allowed_relation(relation)
                && relation.get("targets").and_then(Value::as_seq).is_some_and(|targets| {
                    matches!(targets, [target] if target.as_str() == Some(native_parent.as_str()))
                })
        });
        if matches_parent {
            parents.insert(id, parent);
        }
    }
    let mut checked = HashSet::new();
    let mut cyclic = HashSet::new();
    for &id in parents.keys() {
        let mut current = id;
        let mut path = Vec::new();
        let mut positions = HashMap::new();
        while !checked.contains(&current) {
            if let Some(position) = positions.insert(current, path.len()) {
                cyclic.extend(path[position..].iter().copied());
                break;
            }
            path.push(current);
            let Some(parent) = parents.get(&current) else { break };
            current = *parent;
        }
        checked.extend(path);
    }
    parents.retain(|id, _| !cyclic.contains(id));
    Ok(Some(Sources { nodes, session, capture, issues, parents }))
}

fn primary_control(request: &TextSliceRequest<'_>, atom: &Atom, native: &Value) -> bool {
    if let Some(fields) = request.value_fields {
        return fields.get(&atom.reference).and_then(Value::as_str) == Some("control_value.value");
    }
    let text = native.get("text").filter(|value| !matches!(value, Value::Null));
    text.is_none_or(|text| text.get("content_field").and_then(Value::as_seq).is_some_and(|path| {
        matches!(path, [group, key] if group.as_str() == Some("control_value") && key.as_str() == Some("value"))
    }))
}

fn capture_fields(request: &TextSliceRequest<'_>, sources: &Sources<'_>) -> Vec<TextSliceField> {
    let mut fields = Vec::new();
    for &id in &request.atoms.order {
        if request.excluded_sources.contains(&id) {
            continue;
        }
        let (Some(atom), Some(source), Some((token, native))) = (
            request.atoms.atoms.get(&id), request.projection.node(id), sources.nodes.get(id.0),
        ) else { continue };
        let Some(scope) = request.projection.node(source.scope) else { continue };
        for key in ["name", "value"] {
            let (value, path): (_, &[&str]) = if key == "name" {
                (native.get("name"), &["name"])
            } else if primary_control(request, atom, native) {
                (native.get("control_value").and_then(|control| control.get("value")), &["control_value", "value"])
            } else {
                continue;
            };
            let Some(value) = value.and_then(Value::as_str).filter(|value| !value.is_empty()) else { continue };
            let Some(slot) = atom.slots.iter().find(|slot| slot.key == key) else { continue };
            if !complete(sources.issues.get(token.as_str()), path)
                || slot.value.as_str() != Some(value) || slot.has_preview() || slot.format != Format::Text
                || slot.protection.is_some() || value.contains(['\r', '\n', '\u{2028}', '\u{2029}'])
                || request.excluded_slots.contains(&(id, key.to_owned()))
                || request.disclosure.is_some_and(|plan| plan.get(id, key).is_some()) || slot.facts.is_empty()
                || slot.facts.iter().any(|fact| {
                    fact.key.source != id || fact.key.attribute != key || fact.value.as_str() != Some(value)
                })
            {
                continue;
            }
            fields.push(TextSliceField {
                id, reference: atom.reference.clone(), slot: key.to_owned(), native_token: token.clone(),
                native_path: path.to_vec(), scope: source.scope,
                capture: [sources.session.to_owned(), sources.capture.to_owned(), scope.reference.clone()],
                value: value.to_owned(), atom_slot: slot.clone(),
            });
        }
    }
    fields
}

fn quoted_bytes(value: &str) -> usize {
    2 + value.chars().map(|character| match character {
        '"' | '\\' | '\n' | '\r' | '\t' | '\u{8}' | '\u{c}' => 2,
        '\0'..='\u{1f}' | '\u{7f}'..='\u{9f}' => 6,
        _ => character.len_utf8(),
    }).sum::<usize>()
}

pub(super) fn scalar_bytes(value: &str) -> usize {
    let quote = value.is_empty() || value != value.trim_matches(python_space)
        || value.contains([',', '"', '>', '\\', '|', ';', '{', '}'])
        || value.starts_with(['<', '+', '~', '|'])
        || value.contains("(partial:") || value.contains("(showing ")
        || value.chars().any(|character| matches!(character, '\0'..='\u{1f}' | '\u{7f}'..='\u{9f}'));
    if quote { quoted_bytes(value) } else { value.len() }
}

fn replacement_saving(plan: &TextSlicePlan, new_anchor: bool) -> usize {
    let source = &plan.source;
    let anchor = &plan.anchor;
    let reference = if plan.mode == TextSliceMode::Same {
        "same text as element .".len() + anchor.reference.len() + anchor.slot.len()
    } else {
        "excerpt  [part of element .; full: state.read_details(element=)]".len()
            + anchor.reference.len() + anchor.slot.len() + source.reference.len()
            + plan.fragments.iter().map(|fragment| quoted_bytes(&fragment.text)).sum::<usize>()
            + " … ".len() * plan.fragments.len().saturating_sub(1)
    };
    let label = if source.atom_slot.label.is_none() { "Text: ".len() } else { 0 };
    let anchor_label = if new_anchor && anchor.slot == "name" { "Name: ".len() } else { 0 };
    scalar_bytes(&source.value).saturating_sub(reference + label + anchor_label)
}

fn fragments(value: &str, mut ranges: Vec<(usize, usize)>) -> Vec<SliceFragment> {
    ranges.sort_unstable();
    let mut merged: Vec<(usize, usize)> = Vec::new();
    for (start, end) in ranges {
        if start == end {
            continue;
        }
        if let Some(last) = merged.last_mut().filter(|last| start <= last.1) {
            last.1 = last.1.max(end);
        } else {
            merged.push((start, end));
        }
    }
    let bytes = value.char_indices().map(|(index, _)| index).chain([value.len()]).collect::<Vec<_>>();
    merged.into_iter().map(|(start, end)| {
        SliceFragment { start, end, text: value[bytes[start]..bytes[end]].to_owned() }
    }).collect()
}

fn edge_ranges(value: &str) -> Vec<(usize, usize)> {
    let mut used = 0;
    let head = value.chars().take_while(|character| {
        used += character.len_utf8();
        used <= 36
    }).count();
    let mut used = 0;
    let tail = value.chars().rev().take_while(|character| {
        used += character.len_utf8();
        used <= 24
    }).count();
    let length = value.chars().count();
    vec![(0, head), (length - tail, length)]
}

fn fragment_signature(plan: &TextSlicePlan) -> Vec<String> {
    plan.fragments.iter().map(|fragment| fragment.text.clone()).collect()
}

fn distinguish(plans: &mut [TextSlicePlan]) -> HashSet<usize> {
    let mut collisions = HashMap::new();
    let mut groups = Vec::new();
    for (index, plan) in plans.iter().enumerate() {
        if plan.mode == TextSliceMode::Excerpt {
            let key = (plan.anchor.id, plan.anchor.slot.clone(), fragment_signature(plan));
            grouped(&mut collisions, &mut groups, key, index);
        }
    }
    let mut rejected = HashSet::new();
    for indexes in groups {
        if indexes.iter().map(|index| &plans[*index].source.value).collect::<HashSet<_>>().len() < 2 {
            continue;
        }
        let mut ranges: HashMap<_, Vec<_>> = indexes.iter().map(|&index| {
            (index, plans[index].fragments.iter().map(|fragment| (fragment.start, fragment.end)).collect())
        }).collect();
        for (offset, &left) in indexes.iter().enumerate() {
            for &right in &indexes[offset + 1..] {
                let first = &plans[left].source.value;
                let second = &plans[right].source.value;
                if first == second {
                    continue;
                }
                let difference = first.chars().zip(second.chars()).take_while(|(left, right)| left == right).count();
                for index in [left, right] {
                    let length = plans[index].source.value.chars().count();
                    ranges.get_mut(&index).unwrap().push((difference.saturating_sub(12), length.min(difference + 24)));
                }
            }
        }
        let mut signatures = HashMap::new();
        for &index in &indexes {
            let plan = &mut plans[index];
            plan.fragments = fragments(&plan.source.value, ranges.remove(&index).unwrap());
            signatures.entry(fragment_signature(plan)).or_insert_with(HashSet::new).insert(plan.source.value.clone());
        }
        for index in indexes {
            if signatures[&fragment_signature(&plans[index])].len() > 1 {
                rejected.insert(index);
            }
        }
    }
    rejected
}

fn key(field: &TextSliceField) -> SlotKey { (field.id, field.slot.clone()) }

fn unique_start(anchor: &str, child: &str) -> Option<usize> {
    let start = anchor.find(child)?;
    let next = start + anchor[start..].chars().next()?.len_utf8();
    (!anchor[next..].contains(child)).then_some(start)
}

fn choose(fields: Vec<TextSliceField>, sources: &Sources<'_>, projection: &Projection) -> TextSliceDocument {
    let lookup: HashMap<_, _> = fields.iter().map(|field| (key(field), field)).collect();
    let mut selected = Vec::new();
    let mut used = HashSet::new();
    let mut anchors = HashSet::new();
    for source in &fields {
        let source_key = key(source);
        if source.slot != "value" || anchors.contains(&source_key) {
            continue;
        }
        let mut current = sources.parents.get(&source.id).copied();
        let mut seen = HashSet::from([source.id]);
        while let Some(parent) = current.filter(|id| seen.insert(*id)) {
            if !projection.node(parent).is_some_and(|node| node.scope == source.scope) {
                break;
            }
            let mut found = false;
            for slot in ["value", "name"] {
                let anchor_key = (parent, slot.to_owned());
                let Some(anchor) = lookup.get(&anchor_key) else { continue };
                if used.contains(&anchor_key) || slot == "value" && anchor.atom_slot.label.is_none() {
                    continue;
                }
                let Some(position) = unique_start(&anchor.value, &source.value) else { continue };
                let start_utf16 = anchor.value[..position].encode_utf16().count();
                let end_utf16 = start_utf16 + source.value.encode_utf16().count();
                let mode = if source.value == anchor.value { TextSliceMode::Same } else { TextSliceMode::Excerpt };
                let fragments = if mode == TextSliceMode::Same { Vec::new() }
                    else { fragments(&source.value, edge_ranges(&source.value)) };
                let mut plan = TextSlicePlan {
                    source: source.clone(), anchor: (**anchor).clone(), start_utf16, end_utf16,
                    estimated_saved_bytes: 0, mode, fragments,
                };
                let saving = replacement_saving(&plan, !anchors.contains(&anchor_key));
                if saving < 64 {
                    continue;
                }
                plan.estimated_saved_bytes = saving;
                selected.push(plan);
                used.insert(source_key.clone());
                anchors.insert(anchor_key);
                found = true;
                break;
            }
            if found { break; }
            current = sources.parents.get(&parent).copied();
        }
    }
    let rejected = distinguish(&mut selected);
    let mut result = TextSliceDocument::default();
    for (index, mut plan) in selected.into_iter().enumerate() {
        if rejected.contains(&index) {
            continue;
        }
        let anchor = key(&plan.anchor);
        let saving = replacement_saving(&plan, !result.anchors.contains(&anchor));
        if saving >= 64 {
            plan.estimated_saved_bytes = saving;
            let source = key(&plan.source);
            result.anchors.insert(anchor);
            result.order.push(source.clone());
            result.plans.insert(source, plan);
        }
    }
    result
}

fn validate(document: &TextSliceDocument, sources: &Sources<'_>) -> Result<()> {
    let mut anchors = HashSet::new();
    let mut visible: HashMap<_, HashSet<_>> = HashMap::new();
    for source in &document.order {
        let plan = &document.plans[source];
        let anchor = key(&plan.anchor);
        if *source != key(&plan.source) || document.plans.contains_key(&anchor) || plan.source.scope != plan.anchor.scope {
            return Err(SharingError("text slice must preserve a literal same-scope anchor"));
        }
        let mut current = sources.parents.get(&plan.source.id).copied();
        let mut seen = HashSet::from([plan.source.id]);
        while let Some(parent) = current.filter(|id| *id != plan.anchor.id && seen.insert(*id)) {
            current = sources.parents.get(&parent).copied();
        }
        if current != Some(plan.anchor.id) {
            return Err(SharingError("text slice anchor is outside the original source path"));
        }
        let raw: Vec<_> = plan.anchor.value.encode_utf16().collect();
        if plan.start_utf16 >= plan.end_utf16
            || raw.get(plan.start_utf16..plan.end_utf16).and_then(|range| String::from_utf16(range).ok()).as_deref()
                != Some(plan.source.value.as_str())
        {
            return Err(SharingError("text slice range does not restore the original scalar"));
        }
        if plan.mode == TextSliceMode::Same {
            if plan.source.value != plan.anchor.value || !plan.fragments.is_empty() {
                return Err(SharingError("same text reuse requires equal complete values"));
            }
        } else {
            let mut previous = None;
            let bytes = plan.source.value.char_indices().map(|(index, _)| index)
                .chain([plan.source.value.len()]).collect::<Vec<_>>();
            for fragment in &plan.fragments {
                if fragment.start >= fragment.end || previous.is_some_and(|end| fragment.start <= end)
                    || bytes.get(fragment.start).zip(bytes.get(fragment.end))
                        .is_none_or(|(&start, &end)| plan.source.value[start..end] != fragment.text)
                {
                    return Err(SharingError("text excerpts must preserve ordered original source spans"));
                }
                previous = Some(fragment.end);
            }
            if plan.fragments.len() < 2 || plan.fragments[0].start != 0 || previous != Some(bytes.len() - 1) {
                return Err(SharingError("partial text reuse requires distinct first and last excerpts"));
            }
            visible.entry((anchor.clone(), fragment_signature(plan))).or_default().insert(plan.source.value.as_str());
        }
        let saving = replacement_saving(plan, !anchors.contains(&anchor));
        if saving < 64 || saving != plan.estimated_saved_bytes {
            return Err(SharingError("text reuse must include actual recovery and excerpt costs"));
        }
        anchors.insert(anchor);
    }
    if anchors != document.anchors || visible.values().any(|values| values.len() > 1) {
        return Err(SharingError("text reuse requires exact anchors and distinguishable excerpts"));
    }
    Ok(())
}

pub(crate) fn plan_text_slices(request: TextSliceRequest<'_>) -> Result<TextSliceDocument> {
    let Some(sources) = capture_sources(&request)? else { return Ok(TextSliceDocument::default()) };
    let fields = capture_fields(&request, &sources);
    let document = choose(fields, &sources, request.projection);
    validate(&document, &sources)?;
    Ok(document)
}
