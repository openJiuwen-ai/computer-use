// Copyright (c) Huawei Technologies Co., Ltd. 2026. All rights reserved.

//! Select reversible captured-content groups over the original fact and layout
//! owners. Group keys are capture-local recovery IDs, never executable targets.

use std::collections::{HashMap, HashSet};

use crate::{
    diff::python_str_repr,
    facts::FactSnapshot,
    geometric::{BlockId, GeometricPlan},
    numbering::NodeId,
    projection::Projection,
    render::atoms::{AtomPlan, Slot},
    value::{decimal, Value},
};
use super::DisclosurePlan;

mod evidence;
mod background;

#[derive(Debug, Eq, PartialEq)]
pub(crate) enum GroupError {
    InvalidPolicy,
    MissingCapture,
    MissingBlock(BlockId),
    MissingSource(NodeId),
    InvalidMembership,
    ConflictingField,
    InvalidRecovery,
}

type Result<T> = std::result::Result<T, GroupError>;

#[derive(Clone, Copy, Debug)]
pub(crate) struct GroupPolicy {
    pub(crate) minimum_objects: usize,
    pub(crate) minimum_characters: usize,
    pub(crate) head_characters: usize,
    pub(crate) tail_characters: usize,
    pub(crate) minimum_saved_units: usize,
    pub(crate) minimum_saved_fraction: f64,
    pub(crate) recovery_weight: f64,
    pub(crate) recovery_page_characters: usize,
    pub(crate) recovery_call_units: usize,
}

impl Default for GroupPolicy {
    fn default() -> Self {
        Self {
            minimum_objects: 3, minimum_characters: 2048, head_characters: 240, tail_characters: 120,
            minimum_saved_units: 256, minimum_saved_fraction: 0.2, recovery_weight: 0.1,
            recovery_page_characters: 4096, recovery_call_units: 32,
        }
    }
}

#[derive(Debug)]
pub(crate) struct WindowContext {
    pub(crate) window_ref: u64,
    pub(crate) current: bool,
    pub(crate) modal: Option<bool>,
    pub(crate) confirmed_blockers: Vec<u64>,
    pub(crate) directory_complete: bool,
    pub(crate) scope_confirmed: bool,
}

pub(crate) struct GroupRequest<'a> {
    pub(crate) interface: &'a GeometricPlan,
    pub(crate) projection: &'a Projection,
    pub(crate) atoms: &'a AtomPlan,
    pub(crate) snapshot: &'a FactSnapshot,
    pub(crate) semantic: &'a Value,
    pub(crate) tokens: &'a [String],
    pub(crate) enabled: bool,
    pub(crate) policy: GroupPolicy,
    pub(crate) window: Option<&'a WindowContext>,
    pub(crate) field_disclosure: Option<&'a DisclosurePlan>,
    pub(crate) deferred_actions: Option<&'a HashSet<(NodeId, usize)>>,
    pub(crate) recovery_prefix_units: usize,
}

#[derive(Clone, Debug, PartialEq)]
pub(crate) struct CapturedGroupField {
    pub(crate) source: NodeId,
    pub(crate) native_field: String,
    pub(crate) value: Value,
    pub(crate) normalized: Value,
    pub(crate) attribute: String,
}

#[derive(Clone, Debug)]
pub(crate) struct GroupField {
    pub(crate) source: NodeId,
    pub(crate) slot: Slot,
    pub(crate) sources: Vec<CapturedGroupField>,
}

#[derive(Clone, Debug)]
pub(crate) struct GroupFragment {
    pub(crate) source: CapturedGroupField,
    pub(crate) start: usize,
    pub(crate) end: usize,
    pub(crate) text: String,
}

#[derive(Debug)]
pub(crate) struct GroupEntry {
    pub(crate) source: NodeId,
    pub(crate) parent: Option<NodeId>,
    pub(crate) role: String,
    pub(crate) fields: Vec<GroupField>,
}

#[derive(Debug)]
pub(crate) struct Background {
    pub(crate) window_ref: u64,
    pub(crate) blockers: Vec<u64>,
    pub(crate) directory_complete: bool,
    pub(crate) important_refs: Vec<NodeId>,
    pub(crate) uncertain_refs: Vec<NodeId>,
    pub(crate) nested_windows: Vec<NodeId>,
    pub(crate) state_facts: Vec<(NodeId, String, Value)>,
}

#[derive(Clone, Copy, Debug, Eq, PartialEq)]
pub(crate) enum GroupKind { Content, Records, Background }

#[derive(Debug)]
pub(crate) struct GroupOverview {
    pub(crate) section_key: String,
    pub(crate) capture: [String; 3],
    pub(crate) parent: BlockId,
    pub(crate) children: Vec<BlockId>,
    pub(crate) projected_refs: Vec<NodeId>,
    pub(crate) recovery_refs: Vec<NodeId>,
    pub(crate) entries: Vec<GroupEntry>,
    pub(crate) outline: Vec<GroupField>,
    pub(crate) head: Vec<GroupFragment>,
    pub(crate) tail: Vec<GroupFragment>,
    pub(crate) kind: GroupKind,
    pub(crate) captured_characters: usize,
    pub(crate) captured_records: Option<usize>,
    pub(crate) full_units: usize,
    pub(crate) overview_units: usize,
    pub(crate) recovery_units: usize,
    pub(crate) estimated_saved_units: usize,
    pub(crate) background: Option<Background>,
}

#[derive(Debug)]
pub(crate) struct Assessment {
    pub(crate) parent: Option<BlockId>,
    pub(crate) children: Vec<BlockId>,
    pub(crate) reason: &'static str,
    pub(crate) accepted: bool,
}

#[derive(Debug, Default)]
pub(crate) struct GroupDisclosurePlan {
    pub(crate) groups: Vec<GroupOverview>,
    pub(crate) omitted_slots: HashMap<NodeId, HashSet<String>>,
    pub(crate) protected_refs: HashMap<NodeId, Vec<&'static str>>,
    pub(crate) assessments: Vec<Assessment>,
}

struct Classified<'r, 'a> {
    request: &'r GroupRequest<'a>,
    protections: HashMap<NodeId, Vec<&'static str>>,
    members: HashMap<BlockId, Vec<NodeId>>,
    native_fields: HashMap<(NodeId, String), Vec<CapturedGroupField>>,
    capture: [String; 3],
    aliases: HashMap<&'a str, NodeId>,
    alias_order: Vec<NodeId>,
}

fn units(value: &str) -> usize { value.len().div_ceil(4) }

fn python_repr(value: &Value, nested: bool) -> String {
    match value {
        Value::Null => "None".to_owned(),
        Value::Bool(value) => (if *value { "True" } else { "False" }).to_owned(),
        Value::Int(value) => value.to_string(),
        Value::BigInt(value) => value.clone(),
        Value::Float(value) if value.is_nan() => "nan".to_owned(),
        Value::Float(value) if value.is_infinite() => (if value.is_sign_negative() { "-inf" } else { "inf" }).to_owned(),
        Value::Float(value) => decimal::float(*value, true),
        Value::Text(value) => if nested { python_str_repr(value) } else { value.clone() },
        Value::Seq(values) => {
            let mut result = values.iter().map(|item| python_repr(item, true)).collect::<Vec<_>>().join(", ");
            if values.len() == 1 { result.push(','); }
            format!("({result})")
        }
        Value::Map(values) => {
            let fields = values.iter().map(|(key, value)| format!("{}: {}", python_str_repr(key), python_repr(value, true)))
                .collect::<Vec<_>>().join(", ");
            if nested { format!("mappingproxy({{{fields}}})") } else { format!("{{{fields}}}") }
        }
        Value::Bytes(value) => format!("b{}", python_str_repr(&String::from_utf8_lossy(value))),
    }
}

fn value_units(value: &Value) -> usize { units(&python_repr(value, false)) }

fn quote_component(value: &str) -> String {
    const HEX: &[u8; 16] = b"0123456789ABCDEF";
    let mut result = String::new();
    for byte in value.bytes() {
        if byte.is_ascii_alphanumeric() || matches!(byte, b'-' | b'_' | b'.' | b'~') {
            result.push(byte as char);
        } else {
            result.push('%');
            result.push(HEX[(byte >> 4) as usize] as char);
            result.push(HEX[(byte & 15) as usize] as char);
        }
    }
    result
}

fn section_key(prefix: &str, capture: &[String; 3], first: &str) -> String {
    let components = capture.iter().map(String::as_str).chain([first]).map(quote_component).collect::<Vec<_>>();
    format!("{prefix}:{}", components.join(":"))
}

fn capture_entries(classified: &Classified<'_, '_>, refs: &[NodeId]) -> Result<Vec<GroupEntry>> {
    refs.iter().map(|id| {
        let atom = classified.request.atoms.atoms.get(id).ok_or(GroupError::MissingSource(*id))?;
        let fields = atom.slots.iter().map(|slot| {
            let sources = slot.facts.iter().flat_map(|fact|
                classified.native_fields.get(&(fact.key.source, fact.key.attribute.clone())).into_iter().flatten()
                    .filter(|original| original.normalized == fact.value).cloned()
            ).collect();
            GroupField { source: *id, slot: slot.clone(), sources }
        }).collect();
        Ok(GroupEntry { source: *id, parent: atom.parent, role: atom.role.clone(), fields })
    }).collect()
}

fn text_fields<'a>(fields: impl Iterator<Item = &'a GroupField>) -> Result<Vec<&'a CapturedGroupField>> {
    let mut seen = HashMap::new();
    let mut result = Vec::new();
    for field in fields {
        for source in &field.sources {
            let Some(value) = source.value.as_str() else { continue };
            let key = (source.source, source.native_field.as_str());
            if let Some(previous) = seen.insert(key, value) {
                if previous != value { return Err(GroupError::ConflictingField); }
            } else {
                result.push(source);
            }
        }
    }
    Ok(result)
}

fn chars_slice(value: &str, start: usize, end: usize) -> &str {
    let begin = value.char_indices().nth(start).map_or(value.len(), |(position, _)| position);
    let end = value.char_indices().nth(end).map_or(value.len(), |(position, _)| position);
    &value[begin..end]
}

fn fragments<'a>(
    fields: impl Iterator<Item = &'a GroupField>, mut budget: usize, tail: bool,
) -> Result<Vec<GroupFragment>> {
    let mut sources = text_fields(fields)?;
    if tail { sources.reverse(); }
    let mut result = Vec::new();
    for source in sources {
        let value = source.value.as_str().unwrap_or_default();
        let len = value.chars().count();
        let take = len.min(budget);
        if take == 0 { continue; }
        let (start, end) = if tail { (len - take, len) } else { (0, take) };
        result.push(GroupFragment { source: source.clone(), start, end, text: chars_slice(value, start, end).to_owned() });
        budget -= take;
    }
    if tail { result.reverse(); }
    Ok(result)
}

fn outline_role(role: Option<&str>) -> bool {
    matches!(role, Some("heading" | "section" | "group" | "list" | "document"))
}

fn candidate(
    classified: &Classified<'_, '_>, parent: BlockId, children: &[BlockId], prefix: usize,
) -> Result<(Option<GroupOverview>, &'static str)> {
    let request = classified.request;
    let policy = request.policy;
    let projected_refs = classified.refs(children)?;
    if projected_refs.len() < policy.minimum_objects { return Ok((None, "too-few-passive-objects")); }
    let recovery_refs = classified.recovery(&projected_refs)?;
    if recovery_refs.iter().any(|id| classified.protections.contains_key(id)) {
        return Ok((None, "protected-source-or-folded-contributor"));
    }
    let scopes: HashSet<_> = recovery_refs.iter().map(|id| request.projection.node(*id).map(|node| node.scope)).collect();
    if scopes.len() != 1 { return Ok((None, "distinct-native-scopes")); }
    let entries = capture_entries(classified, &projected_refs)?;
    let fields = || entries.iter().flat_map(|entry| &entry.fields);
    if fields().any(|field| field.sources.is_empty() || field.slot.facts.iter().any(|fact|
        !field.sources.iter().any(|source| source.source == fact.key.source && source.normalized == fact.value)))
    {
        return Ok((None, "unresolved-native-field"));
    }
    let captured_characters = text_fields(fields())?.iter().map(|source| source.value.as_str().unwrap_or_default().chars().count()).sum();
    if captured_characters < policy.minimum_characters { return Ok((None, "short-captured-content")); }
    let outlined = |field: &&GroupField| field.slot.key == "name"
        && outline_role(classified.native(field.source).and_then(|node| evidence::field(node, "role")));
    let outline: Vec<_> = fields().filter(outlined).cloned().collect();
    let head = fragments(fields().filter(|field| !outlined(field)), policy.head_characters, false)?;
    let tail = fragments(fields().filter(|field| !outlined(field)), policy.tail_characters, true)?;
    if head.is_empty() && tail.is_empty() { return Ok((None, "no-original-body-excerpt")); }
    let section = section_key("content", &classified.capture, classified.reference(projected_refs[0])?);
    let mut full_units = 0;
    let mut own_recovery_units = 80 + 3 * units(&section);
    for entry in &entries {
        let ref_and_role = classified.reference(entry.source)?.len() + entry.role.len();
        full_units += ref_and_role.div_ceil(4) + 3;
        own_recovery_units += ref_and_role.div_ceil(4) + 8;
        for field in &entry.fields {
            let slot = &field.slot;
            full_units += request.field_disclosure.and_then(|plan| plan.get(field.source, &slot.key)).map_or_else(
                || value_units(&slot.value) + units(slot.label.unwrap_or_default()) + 1,
                |excerpt| excerpt.fragments.iter().map(|part| units(&part.text)).sum::<usize>() + 40,
            );
            own_recovery_units += value_units(&slot.value) + units(&slot.key) + 5;
            own_recovery_units += slot.facts.iter().filter(|fact| fact.value != slot.value)
                .map(|fact| value_units(&fact.value) + 8).sum::<usize>();
            own_recovery_units += field.sources.iter().filter(|source| source.value != source.normalized)
                .map(|source| value_units(&source.value) + 8).sum::<usize>();
        }
    }
    let mut overview_units = 100 + 2 * units(&section);
    for field in &outline {
        overview_units += (classified.reference(field.source)?.len() + python_repr(&field.slot.value, false).len()).div_ceil(4) + 5;
    }
    for part in head.iter().chain(&tail) {
        overview_units += units(&part.text)
            + (classified.reference(part.source.source)?.len() + part.source.attribute.len()).div_ceil(4) + 8;
    }
    for id in &recovery_refs { overview_units += units(classified.reference(*id)?) + 1; }
    let recovery_units = prefix + own_recovery_units;
    let calls = (4 * recovery_units).div_ceil(policy.recovery_page_characters);
    let allowance = (recovery_units as f64 * policy.recovery_weight).ceil() as usize + calls * policy.recovery_call_units;
    let saved = full_units.checked_sub(overview_units + allowance);
    let Some(saved) = saved.filter(|saved| *saved >= policy.minimum_saved_units
        && (*saved as f64) >= full_units as f64 * policy.minimum_saved_fraction)
        else { return Ok((None, "insufficient-benefit-after-recovery-cost")); };
    let records = children.iter().all(|id|
        request.interface.block(*id).and_then(|block| block.primary_ref).and_then(|id| classified.native(id))
            .and_then(|node| evidence::field(node, "role")).is_some_and(|role| matches!(role, "row" | "list_item"))
    );
    Ok((Some(GroupOverview {
        section_key: section, capture: classified.capture.clone(), parent, children: children.to_vec(),
        projected_refs, recovery_refs, entries, outline, head, tail,
        kind: if records { GroupKind::Records } else { GroupKind::Content },
        captured_characters, captured_records: records.then_some(children.len()),
        full_units, overview_units, recovery_units, estimated_saved_units: saved, background: None,
    }), "beneficial-complete-passive-run"))
}

fn safe(classified: &Classified<'_, '_>, child: BlockId) -> Result<bool> {
    let members = classified.members.get(&child).ok_or(GroupError::MissingBlock(child))?;
    Ok(!members.is_empty() && !classified.recovery(members)?.iter().any(|id| classified.protections.contains_key(id)))
}

fn freeze(classified: Classified<'_, '_>, mut selected: GroupDisclosurePlan) -> Result<GroupDisclosurePlan> {
    let request = classified.request;
    let mut seen = HashSet::new();
    for group in &selected.groups {
        let parent = request.interface.block(group.parent).ok_or(GroupError::MissingBlock(group.parent))?;
        if group.children.is_empty() || !parent.children.windows(group.children.len()).any(|part| part == group.children)
            || classified.refs(&group.children)? != group.projected_refs
            || group.projected_refs.iter().any(|id| !seen.insert(*id))
        {
            return Err(GroupError::InvalidMembership);
        }
        if group.recovery_refs != classified.recovery(&group.projected_refs)? || group.capture != classified.capture
            || group.estimated_saved_units == 0
        {
            return Err(GroupError::InvalidRecovery);
        }
        if group.kind != GroupKind::Background && group.recovery_refs.iter().any(|id| classified.protections.contains_key(id)) {
            return Err(GroupError::InvalidRecovery);
        }
        if group.kind == GroupKind::Background {
            let window = request.window.filter(|window| window.scope_confirmed && !window.current
                && window.modal != Some(true) && !window.confirmed_blockers.is_empty()).ok_or(GroupError::InvalidRecovery)?;
            let background = group.background.as_ref().ok_or(GroupError::InvalidRecovery)?;
            let primary = parent.primary_ref.ok_or(GroupError::InvalidRecovery)?;
            if background.window_ref != window.window_ref || background.blockers != window.confirmed_blockers
                || background.directory_complete != window.directory_complete || group.children != parent.children
                || request.tokens.get(primary.0).is_none_or(|token| !evidence::seq(request.snapshot.observation.get("roots"))
                    .iter().any(|root| root.as_str() == Some(token)))
            {
                return Err(GroupError::InvalidRecovery);
            }
        }
        for id in &group.recovery_refs {
            let reference = classified.reference(*id)?;
            let bound = request.semantic.get("refs").and_then(|refs| refs.get(reference)).and_then(Value::as_str);
            if classified.native(*id).is_none() || bound != request.tokens.get(id.0).map(String::as_str)
                || !reference.parse::<u64>().is_ok_and(|number| number.to_string() == reference)
            {
                return Err(GroupError::InvalidRecovery);
            }
        }
        if group.entries.iter().map(|entry| entry.source).ne(group.projected_refs.iter().copied()) {
            return Err(GroupError::InvalidRecovery);
        }
        for entry in &group.entries {
            let keys: HashSet<_> = entry.fields.iter().map(|field| field.slot.key.clone()).collect();
            if keys.len() != entry.fields.len() { return Err(GroupError::InvalidRecovery); }
            selected.omitted_slots.insert(entry.source, keys);
        }
        let sources = text_fields(group.entries.iter().flat_map(|entry| &entry.fields))?;
        let length: usize = sources.iter().map(|source| source.value.as_str().unwrap_or_default().chars().count()).sum();
        if length != group.captured_characters { return Err(GroupError::InvalidRecovery); }
        for part in group.head.iter().chain(&group.tail) {
            let value = part.source.value.as_str().ok_or(GroupError::InvalidRecovery)?;
            if !sources.contains(&&part.source) || part.start >= part.end || part.end > value.chars().count()
                || chars_slice(value, part.start, part.end) != part.text
            {
                return Err(GroupError::InvalidRecovery);
            }
        }
    }
    selected.protected_refs = classified.protections;
    Ok(selected)
}

pub(crate) fn plan(request: &GroupRequest<'_>) -> Result<GroupDisclosurePlan> {
    let policy = request.policy;
    if policy.minimum_objects < 2 || policy.head_characters == 0 && policy.tail_characters == 0
        || !(policy.minimum_saved_fraction > 0.0 && policy.minimum_saved_fraction <= 1.0)
        || !(0.0..=1.0).contains(&policy.recovery_weight) || policy.recovery_page_characters == 0
    {
        return Err(GroupError::InvalidPolicy);
    }
    let classified = evidence::classify(request)?;
    let reason = match request.window {
        None => "background-overview-requires-confirmed-window-directory",
        Some(window) if window.current || window.modal == Some(true) => "current-or-modal-window-kept-expanded",
        Some(window) if window.confirmed_blockers.is_empty() => "background-blocking-unconfirmed",
        Some(_) => "background-blocking-confirmed; fixed-scope-overview-assessed-separately",
    };
    let mut output = GroupDisclosurePlan::default();
    output.assessments.push(Assessment { parent: None, children: Vec::new(), reason, accepted: false });
    if !request.enabled || request.window.is_some_and(|window| window.modal == Some(true)) {
        return freeze(classified, output);
    }
    let mut prefix = request.recovery_prefix_units;
    for root in &request.interface.roots {
        if let (Some(background), reason) = background::candidate(&classified, *root, prefix)? {
            prefix = background.recovery_units;
            output.assessments.push(Assessment {
                parent: Some(*root), children: background.children.clone(), reason, accepted: true,
            });
            output.groups.push(background);
            continue;
        }
        let mut pending = vec![(*root, 0)];
        while let Some((parent, start)) = pending.pop() {
            if start == 0 && !classified.permits_descendants(parent)? { continue; }
            let block = request.interface.block(parent).ok_or(GroupError::MissingBlock(parent))?;
            if start == block.children.len() { continue; }
            let mut end = start;
            while end < block.children.len() && safe(&classified, block.children[end])? { end += 1; }
            if end == start {
                pending.push((parent, start + 1));
                pending.push((block.children[start], 0));
                continue;
            }
            let children = &block.children[start..end];
            let (group, reason) = candidate(&classified, parent, children, prefix)?;
            output.assessments.push(Assessment { parent: Some(parent), children: children.to_vec(), reason, accepted: group.is_some() });
            pending.push((parent, end));
            if let Some(group) = group {
                prefix = group.recovery_units;
                output.groups.push(group);
            } else {
                pending.extend(children.iter().rev().map(|child| (*child, 0)));
            }
        }
    }
    freeze(classified, output)
}

#[cfg(test)]
mod tests {
    use super::*;
    use crate::{geometric::GeometricContext, render::atoms::plan_atoms};

    fn map<const N: usize>(fields: [(&str, Value); N]) -> Value {
        Value::Map(fields.into_iter().map(|(key, value)| (key.to_owned(), value)).collect())
    }

    fn text(value: impl Into<String>) -> Value { Value::Text(value.into()) }

    #[test]
    fn exact_passive_groups_keep_capture_aliases_and_drop_uncertain_members() {
        let tokens: Vec<_> = (0..4).map(|index| format!("source{index}")).collect();
        let mut native_nodes = vec![(tokens[0].clone(), map([("role", text("window"))]))];
        let mut fact_rows = Vec::new();
        let mut semantic_nodes = Vec::new();
        let mut source_fields = Vec::new();
        for (index, token) in tokens.iter().enumerate().skip(1) {
            let reference = index.to_string();
            let name = format!("{index}{}", "sample 文本 ".repeat(250));
            native_nodes.push((token.clone(), map([("role", text("static_text")), ("actions", Value::Seq(vec![]))])));
            fact_rows.push(map([
                ("node", text(token)), ("observed", Value::Bool(true)),
                ("values", map([("name", text(&name))])),
                ("requestedFields", Value::Seq(vec![text("name")])),
            ]));
            semantic_nodes.push(map([
                ("ref", text(&reference)), ("role", text("staticText")), ("name", text(&name)),
                ("children", Value::Seq(vec![])),
            ]));
            source_fields.push(map([
                ("ref", text(&reference)), ("key", text("name")),
                ("value", map([("representation", text("field")), ("field", text("name")), ("normalized", text(&name))])),
            ]));
        }
        let mut capture = FactSnapshot {
            observation: map([
                ("backend", text("macos")), ("session", text("session:/你")), ("id", text("capture:?")),
                ("roots", Value::Seq(vec![text(&tokens[0])])), ("nodes", Value::Map(native_nodes)),
            ]),
            facts: map([("nodes", Value::Seq(fact_rows))]),
            metadata: Value::Null,
            query: Value::Null,
        };
        let aliases = (0..4).map(|index| (index.to_string(), NodeId(index))).collect();
        let projection = Projection::prepare(map([
            ("ref", text("0")), ("role", text("window")), ("children", Value::Seq(semantic_nodes)),
        ]), &aliases, Default::default()).unwrap();
        let atoms = plan_atoms(&projection, &Default::default()).unwrap();
        let context = GeometricContext { projection: &projection, geometry: &HashMap::new(), facts: None, atoms: None };
        let interface = GeometricPlan::seed(&context, true).unwrap().freeze().unwrap();
        let semantic = map([
            ("refs", Value::Map(tokens.iter().enumerate().map(|(id, token)| (id.to_string(), text(token))).collect())),
            ("fields", Value::Seq(source_fields)),
        ]);
        let plan_for = |snapshot: &FactSnapshot| plan(&GroupRequest {
            interface: &interface, projection: &projection, atoms: &atoms, snapshot, semantic: &semantic, tokens: &tokens,
            enabled: true, policy: Default::default(), window: None, field_disclosure: None, deferred_actions: None,
            recovery_prefix_units: 0,
        }).unwrap();
        let accepted = plan_for(&capture);
        assert_eq!(accepted.groups.len(), 1);
        let group = &accepted.groups[0];
        assert_eq!(group.section_key, "content:macos:session%3A%2F%E4%BD%A0:capture%3A%3F:1");
        assert_eq!(group.recovery_refs, [NodeId(1), NodeId(2), NodeId(3)]);
        assert_eq!(group.head[0].source.source, NodeId(1));
        assert_eq!(group.head[0].text.chars().count(), 240);
        assert_eq!(group.tail[0].source.source, NodeId(3));
        assert_eq!(group.tail[0].text.chars().count(), 120);
        let Value::Map(observation) = &mut capture.observation else { unreachable!() };
        observation.push(("issues".to_owned(), Value::Seq(vec![map([
            ("path", Value::Seq(vec![text("nodes"), text(&tokens[2]), text("name")])),
            ("state", text("partial")),
        ])])));
        let protected = plan_for(&capture);
        assert!(protected.groups.is_empty());
        assert!(protected.protected_refs[&NodeId(2)].contains(&"native-uncertainty"));
    }
}
