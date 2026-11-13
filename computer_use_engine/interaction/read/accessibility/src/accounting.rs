// Copyright (c) Huawei Technologies Co., Ltd. 2026. All rights reserved.

//! Account for captured source facts and relationship destinations without
//! changing the presentation tree, native aliases, or collection boundaries.

use std::collections::{HashMap, HashSet};

use super::{
    collections::CollectionDocument,
    content::{FieldContentPlan, MemberMode},
    disclosure::{groups::GroupDisclosurePlan, DisclosurePlan},
    facts::FactSnapshot,
    numbering::NodeId,
    projection::Projection,
    render::{
        atoms::{AtomPlan, Claim, ClaimKind},
        details::DetailSource,
        lines::reference_set,
        text::{json, JsonFormat},
        TextError,
    },
    sharing::{SharingPlan, TextSliceDocument},
    value::Value,
};

mod actions;
mod relations;
mod summaries;
mod warnings;

type Result<T> = std::result::Result<T, TextError>;

pub(crate) struct AccountingPart<'a> {
    pub(crate) members: &'a [NodeId],
    pub(crate) projection: Option<&'a Projection>,
    pub(crate) atoms: Option<&'a AtomPlan>,
    pub(crate) claims: &'a [Claim],
    pub(crate) fields: Option<&'a FieldContentPlan>,
    pub(crate) collections: Option<&'a CollectionDocument>,
    pub(crate) disclosure: Option<&'a DisclosurePlan>,
    pub(crate) groups: Option<&'a GroupDisclosurePlan>,
    pub(crate) sharing: Option<&'a SharingPlan>,
    pub(crate) text_slices: Option<&'a TextSliceDocument>,
    pub(crate) deferred_native_fields: &'a [Value],
    pub(crate) published_columns: &'a [Value],
    pub(crate) uncompressed: bool,
}

impl<'a> AccountingPart<'a> {
    pub(crate) fn compact(root: &'a super::compact::CompactRoot, members: &'a [NodeId]) -> Self {
        Self {
            members, projection: Some(&root.projection), atoms: Some(&root.atoms), claims: &[],
            fields: Some(&root.fields), collections: Some(&root.collections), disclosure: Some(&root.disclosure),
            groups: Some(&root.groups), sharing: Some(&root.sharing), text_slices: Some(&root.slices),
            deferred_native_fields: &root.deferred_native_fields, published_columns: &[], uncompressed: false,
        }
    }

    pub(crate) fn uncompressed(members: &'a [NodeId]) -> Self {
        Self {
            members, projection: None, atoms: None, claims: &[], fields: None, collections: None,
            disclosure: None, groups: None, sharing: None, text_slices: None,
            deferred_native_fields: &[], published_columns: &[], uncompressed: true,
        }
    }
}

pub(crate) struct AccountingRequest<'a> {
    pub(crate) snapshot: &'a FactSnapshot,
    pub(crate) semantic: &'a Value,
    pub(crate) tokens: &'a [String],
    pub(crate) parts: &'a [AccountingPart<'a>],
    pub(crate) compact: bool,
    pub(crate) public_api: bool,
    pub(crate) share_descriptions: bool,
}

#[derive(Debug, Default)]
pub(crate) struct AccountingPlan {
    /// Full diagnostic view suffix, in original source-accounting order.
    pub(crate) full_notes: Vec<String>,
    /// Compact body suffix. Empty when the full view was requested.
    pub(crate) compact_notes: Vec<String>,
    /// Full and incremental spellings of the same public read notes.
    pub(crate) table_warning: Option<(String, String)>,
    pub(crate) compact_table_containment: Vec<Value>,
    pub(crate) diagnostic_prefixes: Vec<String>,
    pub(crate) read_issues: Vec<Value>,
    pub(crate) relation_sources: Vec<Value>,
    pub(crate) part_accounting: Vec<Value>,
    /// Producer evidence still needed before this plan can replace Python output.
    pub(crate) uncovered: Vec<&'static str>,
}

fn text(value: impl Into<String>) -> Value { Value::Text(value.into()) }

fn map<K: Into<String>>(items: impl IntoIterator<Item = (K, Value)>) -> Value {
    Value::Map(items.into_iter().map(|(key, value)| (key.into(), value)).collect())
}

fn literal(value: &Value) -> Result<String> { json(value, JsonFormat::Literal) }

fn quoted(value: &str) -> Result<String> { json(&text(value), JsonFormat::Literal) }

fn seq(value: Option<&Value>) -> &[Value] { value.and_then(Value::as_seq).unwrap_or_default() }

fn field<'a>(value: &'a Value, name: &str) -> Option<&'a str> { value.get(name).and_then(Value::as_str) }

fn entries(value: Option<&Value>) -> &[(String, Value)] { value.and_then(Value::as_map).unwrap_or_default() }

fn present(value: Option<&Value>) -> bool { value.is_some_and(|value| !matches!(value, Value::Null)) }

fn truthy(value: Option<&Value>) -> bool { super::geometric::truth(value) }

fn exact(a: &Value, b: &Value) -> bool {
    match (a, b) {
        (Value::Map(a), Value::Map(b)) => a.len() == b.len()
            && a.iter().all(|(key, value)| b.iter().any(|(name, other)| name == key && exact(value, other))),
        (Value::Seq(a), Value::Seq(b)) => a.len() == b.len() && a.iter().zip(b).all(|(a, b)| exact(a, b)),
        _ => a == b,
    }
}

fn scalar(value: &Value) -> String {
    match value {
        Value::Text(value) | Value::BigInt(value) => value.clone(),
        Value::Int(value) => value.to_string(),
        Value::Float(value) => super::value::decimal::float(*value, true),
        Value::Bool(value) => if *value { "True".into() } else { "False".into() },
        Value::Null => "None".into(),
        value => literal(value).unwrap_or_default(),
    }
}

struct Groups<K, V> {
    rows: Vec<(K, Vec<V>)>,
    index: HashMap<K, usize>,
}

impl<K, V> Default for Groups<K, V> {
    fn default() -> Self { Self { rows: Vec::new(), index: HashMap::new() } }
}

impl<K: Clone + Eq + std::hash::Hash, V> Groups<K, V> {
    fn add(&mut self, key: K, value: V) {
        let index = *self.index.entry(key.clone()).or_insert_with(|| {
            self.rows.push((key, Vec::new()));
            self.rows.len() - 1
        });
        self.rows[index].1.push(value);
    }
}

struct Sources<'a> {
    request: &'a AccountingRequest<'a>,
    aliases: HashMap<&'a str, &'a str>,
    refs: HashMap<&'a str, &'a str>,
    facts: HashMap<&'a str, &'a Value>,
    fields: HashMap<(&'a str, &'a str), &'a Value>,
}

impl<'a> Sources<'a> {
    fn new(request: &'a AccountingRequest<'a>) -> Result<Self> {
        let mut aliases = HashMap::new();
        let mut refs = HashMap::new();
        for (reference, token) in entries(request.semantic.get("refs")) {
            let token = token.as_str().ok_or(TextError::Invalid("captured alias"))?;
            if aliases.insert(token, reference.as_str()).is_some() || refs.insert(reference.as_str(), token).is_some() {
                return Err(TextError::Invalid("duplicate captured alias"));
            }
        }
        let mut facts = HashMap::new();
        for row in seq(request.snapshot.facts.get("nodes")) {
            let token = field(row, "node").ok_or(TextError::Invalid("captured fact node"))?;
            if !aliases.contains_key(token) { return Err(TextError::Invalid("captured fact alias")); }
            facts.insert(token, row);
        }
        let fields = seq(request.semantic.get("fields")).iter().filter_map(|row|
            Some(((field(row, "ref")?, field(row, "key")?), row.get("value")?))).collect();
        Ok(Self { request, aliases, refs, facts, fields })
    }

    fn alias(&self, token: &str) -> Result<&'a str> {
        self.aliases.get(token).copied().ok_or(TextError::Invalid("native source alias"))
    }

    fn reference(&self, source: NodeId) -> Result<&'a str> {
        self.alias(self.token(source)?)
    }

    fn token(&self, source: NodeId) -> Result<&'a str> {
        self.request.tokens.get(source.0).map(String::as_str).ok_or(TextError::Invalid("native source index"))
    }

    fn capture(&self, name: &str) -> Result<&'a str> {
        field(&self.request.snapshot.observation, name).ok_or(TextError::Invalid("observation identity"))
    }
}

fn claim_kind(part: &AccountingPart<'_>, claim: &Claim) -> &'static str {
    let source = claim.target;
    let attribute = claim.fact.attribute.as_str();
    if part.collections.is_some_and(|collections| collections.tables.values().any(|table|
        table.detail_fields.iter().any(|item| item.reference == source && item.slot.key == attribute)))
    {
        "details"
    } else if part.groups.is_some_and(|groups| groups.groups.iter().any(|group|
        group.projected_refs.contains(&source) && group.background.as_ref().is_none_or(|info| !info.important_refs.contains(&source))))
        || part.disclosure.is_some_and(|plan| plan.get(source, attribute).is_some())
    {
        "continuation"
    } else if part.text_slices.is_some_and(|slices| slices.plans.contains_key(&(source, attribute.to_owned()))) {
        "text-slice"
    } else if part.sharing.is_some_and(|sharing|
        sharing.omitted_slots.get(&source).is_some_and(|keys| keys.contains(attribute)))
    {
        "shared"
    } else {
        match claim.kind {
            ClaimKind::Display => "display",
            ClaimKind::Equivalent => "equivalent",
            ClaimKind::Shared => "shared",
            ClaimKind::Continuation => "continuation",
            ClaimKind::TextSelectionSummary => "text-selection-summary",
        }
    }
}

fn claims<'a>(part: &'a AccountingPart<'a>) -> Vec<&'a Claim> {
    let composed: HashSet<_> = part.fields.into_iter().flat_map(|fields| &fields.fields)
        .flat_map(|field| &field.members)
        .filter_map(|member| (member.mode != MemberMode::Complete).then_some(member.source)).collect();
    let mut output = Vec::new();
    if let Some(atoms) = part.atoms {
        for owner in &atoms.order {
            if let Some(atom) = atoms.atoms.get(owner) {
                output.extend(atom.claims.iter().filter(|claim| !composed.contains(&claim.fact.source)));
            }
        }
    }
    output.extend(part.claims.iter().filter(|claim| !composed.contains(&claim.fact.source)));
    output.extend(part.fields.into_iter().flat_map(|fields| &fields.fields).flat_map(|field| &field.claims));
    output
}

fn deferred_path(property: &str) -> Option<(&'static str, &'static [&'static str])> {
    match property {
        "identifier" => Some(("identifier", &["identifier"])),
        "url" => Some(("url", &["url"])),
        "description" => Some(("description", &["description"])),
        "text.selected_text" => Some(("selectedText", &["text", "selected_text"])),
        "control_value.description" => Some(("valueDescription", &["control_value", "description"])),
        "numeric_value.description" => Some(("rangeDescription", &["numeric_value", "description"])),
        "role" => Some(("role", &["role"])),
        "role_description" => Some(("roleDescription", &["role_description"])),
        _ => None,
    }
}

fn same_path(value: Option<&Value>, path: &[&str]) -> bool {
    value.and_then(Value::as_seq).is_some_and(|parts|
        parts.len() == path.len() && parts.iter().zip(path).all(|(part, name)| part.as_str() == Some(*name)))
}

fn lookup_path<'a>(mut value: &'a Value, path: &[&str]) -> Option<&'a Value> {
    for name in path { value = value.get(name)?; }
    Some(value)
}

fn deferred_fields<'a>(
    part: &'a AccountingPart<'a>,
) -> Result<HashMap<(&'a str, &'a str), &'a Value>> {
    let mut output = HashMap::new();
    for marker in part.deferred_native_fields {
        let reference = field(marker, "ref").ok_or(TextError::Invalid("deferred native reference"))?;
        let property = field(marker, "property").ok_or(TextError::Invalid("deferred native property"))?;
        output.insert((reference, property), marker);
    }
    if let Some(atoms) = part.atoms {
        for marker in &atoms.deferred_native_urls {
            let reference = field(marker, "ref").ok_or(TextError::Invalid("deferred URL reference"))?;
            output.insert((reference, "url"), marker);
        }
    }
    Ok(output)
}

fn verify_deferred(
    sources: &Sources<'_>, part: &AccountingPart<'_>, member: NodeId, property: &str, raw: &Value, evidence: &Value,
    marker: Option<&Value>,
) -> Result<Value> {
    let token = sources.token(member)?;
    let (attribute, path) = deferred_path(property).ok_or(TextError::Invalid("unsupported deferred property"))?;
    let projection = part.projection.and_then(|projection| projection.node(member));
    let normalized = if property == "role" {
        projection.and_then(|node| node.native_fields.iter().find(|(name, _)| name == "role").map(|(_, value)| value))
    } else { Some(raw) };
    let exact_field = field(evidence, "field") == Some(attribute)
        && evidence.get("normalized").zip(normalized).is_some_and(|(actual, expected)| exact(actual, expected));
    if !exact_field { return Err(TextError::Invalid("native detail source field")); }
    if property != "identifier" {
        let node = sources.request.snapshot.observation.get("nodes").and_then(|nodes| nodes.get(token));
        if field(evidence, "node") != Some(token) || !same_path(evidence.get("path"), path)
            || node.and_then(|node| lookup_path(node, path)).is_none_or(|value| !exact(value, raw))
        {
            return Err(TextError::Invalid("native detail source path"));
        }
    }
    if let Some(marker) = marker {
        let scope = projection.map(|node| sources.reference(node.scope)).transpose()?;
        let captured = [Some(sources.capture("session")?), Some(sources.capture("id")?), scope];
        let scope_matches = marker.get("scope").and_then(Value::as_seq).is_some_and(|scope|
            scope.len() == 3 && scope.iter().zip(captured).all(|(value, expected)| value.as_str() == expected));
        let reported = if property == "url" { marker.get("url") } else { marker.get("value") };
        if field(marker, "native") != Some(token) || !scope_matches || !same_path(marker.get("path"), path)
            || property != "url" && field(marker, "field") != Some(attribute)
            || reported.is_none_or(|value| !exact(value, raw)) || field(marker, "readState") != Some("complete")
            || present(marker.get("status"))
        {
            return Err(TextError::Invalid("native detail occurrence"));
        }
    }
    Ok(Value::Seq(path.iter().copied().map(text).collect()))
}

fn fields(
    sources: &Sources<'_>, part: &AccountingPart<'_>, supplements: &mut Groups<String, String>,
) -> Result<Value> {
    if part.uncompressed { return Ok(Value::Map(Vec::new())); }
    let mut deferred = deferred_fields(part)?;
    let mut claims_by_source = HashMap::<(NodeId, &str), Vec<(&Claim, &str)>>::new();
    for claim in claims(part) {
        claims_by_source.entry((claim.fact.source, &claim.fact.attribute)).or_default().push((claim, claim_kind(part, claim)));
    }
    let mut destinations = Vec::new();
    let mut actions = Vec::new();
    for member in part.members {
        let token = sources.token(*member)?;
        let reference = sources.alias(token)?;
        let native = sources.facts.get(token).ok_or(TextError::Invalid("source fact inventory"))?;
        for (key, raw) in entries(native.get("values")) {
            let source = sources.fields.get(&(reference, key.as_str())).ok_or(TextError::Invalid("missing source field"))?;
            let representation = field(source, "representation").ok_or(TextError::Invalid("source representation"))?;
            if representation == "raw" { continue; }
            let mut row = vec![("ref".to_owned(), text(reference)), ("property".to_owned(), text(key))];
            let matched = if representation == "annotation" {
                row.push(("kind".to_owned(), text("annotation")));
                true
            } else if representation == "projection" {
                let projections = seq(source.get("projections"));
                let mut represented = Vec::new();
                for field_value in projections {
                    let attribute = field(field_value, "field").ok_or(TextError::Invalid("projected source slot"))?;
                    let normalized = field_value.get("normalized").ok_or(TextError::Invalid("projected source value"))?;
                    // The original line renderer's range claims are mutable
                    // dictionaries, while their captured sources are frozen.
                    // Its exact-type accounting keeps the original scalars.
                    if part.atoms.is_none() && matches!(normalized, Value::Map(_)) { break; }
                    let selected = claims_by_source.get(&(*member, attribute)).into_iter().flatten()
                        .find(|(claim, kind)| *kind != "continuation" && exact(&claim.value, normalized));
                    let Some((claim, kind)) = selected else { break };
                    represented.push(map([("field", text(attribute)), ("target", text(sources.reference(claim.target)?)), ("kind", text(*kind))]));
                }
                if represented.is_empty() || represented.len() != projections.len() {
                    false
                } else {
                    row.push(("kind".to_owned(), text("equivalent")));
                    row.push(("projections".to_owned(), Value::Seq(represented)));
                    true
                }
            } else {
                let attribute = field(source, "field").ok_or(TextError::Invalid("source slot"))?;
                let normalized = source.get("normalized").ok_or(TextError::Invalid("normalized source value"))?;
                let status = native.get("fieldStatus").and_then(|status| status.get(key));
                let captured = attribute == "value" && raw.as_str().is_some()
                    && status.is_none_or(|status| status.get("textRef").is_some());
                let selected = claims_by_source.get(&(*member, attribute)).into_iter().flatten()
                    .find(|(claim, kind)| exact(&claim.value, normalized) && (*kind != "continuation" || captured));
                if let Some((claim, kind)) = selected {
                    row.push(("kind".to_owned(), text(*kind)));
                    row.push(("target".to_owned(), text(sources.reference(claim.target)?)));
                    true
                } else { false }
            };
            if !matched {
                supplements.add(format!("{key}: {}", literal(raw)?), reference.to_owned());
                row.push(("kind".to_owned(), text("supplement")));
                let marker = deferred.remove(&(reference, key.as_str()));
                if key == "identifier" || marker.is_some() {
                    let path = verify_deferred(sources, part, *member, key, raw, source, marker)?;
                    row.extend([
                        ("visibility".to_owned(), text("details")), ("target".to_owned(), text(reference)),
                        ("native".to_owned(), text(token)), ("path".to_owned(), path),
                        ("representation".to_owned(), text("Additional native facts")),
                    ]);
                }
            }
            destinations.push(Value::Map(row));
        }
        let catalogue = native.get("actions").ok_or(TextError::Invalid("source actions"))?;
        // Captured catalogues keep an immutable item sequence, including when
        // empty. Their diagnostic spelling is the original complete record.
        supplements.add(format!("Native actions: {}", literal(catalogue)?), reference.to_owned());
        actions.push(map([("ref", text(reference)), ("kind", text("supplement"))]));
    }
    if !deferred.is_empty() { return Err(TextError::Invalid("native detail destination lacks exact supplement")); }
    Ok(map([("values", Value::Seq(destinations)), ("actions", Value::Seq(actions))]))
}

pub(crate) fn selection_summary_refs(
    snapshot: &FactSnapshot, semantic: &Value, projection: &Projection, tokens: &[String],
) -> Result<HashSet<NodeId>> {
    let request = AccountingRequest {
        snapshot, semantic, tokens, parts: &[], compact: true, public_api: true, share_descriptions: true,
    };
    let source = Sources::new(&request)?;
    let selections = summaries::selection_plan(&source)?;
    let mut output = HashSet::new();
    for entry in &selections {
        let Some(node) = projection.by_reference(&entry.reference) else { continue; };
        if node.field("selectionRange").is_some_and(|inline| summaries::summarized_interval(entry, inline)) {
            output.insert(node.id);
        }
    }
    Ok(output)
}

pub(crate) fn inactive_selection_intervals(
    snapshot: &FactSnapshot, semantic: &Value, tokens: &[String],
) -> Result<HashMap<String, (Value, Value)>> {
    let request = AccountingRequest {
        snapshot, semantic, tokens, parts: &[], compact: true, public_api: true, share_descriptions: true,
    };
    let sources = Sources::new(&request)?;
    Ok(summaries::selection_plan(&sources)?.into_iter().filter_map(|entry| {
        let (location, length) = entry.inactive_interval()?;
        Some((entry.reference, (Value::Int(location), Value::Int(length))))
    }).collect())
}

#[derive(Default)]
pub(crate) struct ActionDeferrals {
    pub(crate) native: HashSet<(NodeId, usize)>,
    pub(crate) projected: HashSet<(NodeId, usize)>,
}

pub(crate) fn deferred_actions(
    snapshot: &FactSnapshot, semantic: &Value, projection: &Projection, tokens: &[String],
) -> Result<ActionDeferrals> {
    if !projection.options.compact || !projection.options.public_api || !projection.options.share_descriptions
        || projection.options.subtree.is_some()
    {
        return Ok(ActionDeferrals::default());
    }
    let request = AccountingRequest {
        snapshot, semantic, tokens, parts: &[], compact: true, public_api: true, share_descriptions: true,
    };
    let source = Sources::new(&request)?;
    let native = actions::mechanisms(&source, projection)?;
    let projected = actions::projected_mechanisms(&source, projection, &native)?;
    Ok(ActionDeferrals { native, projected })
}

pub(crate) fn compact_notes(
    snapshot: &FactSnapshot, semantic: &Value, tokens: &[String],
    concise: bool, all_roots_compressed: bool,
) -> Result<Vec<String>> {
    let request = AccountingRequest {
        snapshot, semantic, tokens, parts: &[], compact: true, public_api: concise, share_descriptions: concise,
    };
    compact_notes_for_request(&request, all_roots_compressed)
}

pub(crate) fn compact_notes_for_request(
    request: &AccountingRequest<'_>, all_roots_compressed: bool,
) -> Result<Vec<String>> {
    let concise = request.public_api && request.share_descriptions;
    let sources = Sources::new(request)?;
    let selections = summaries::selection_plan(&sources)?;
    compact_notes_for_sources(&sources, &selections, concise, all_roots_compressed)
}

fn compact_notes_for_sources(
    sources: &Sources<'_>, selections: &[summaries::Selection], concise: bool, all_roots_compressed: bool,
) -> Result<Vec<String>> {
    let mut notes = summaries::selection_relations(sources)?;
    notes.extend(summaries::read(sources, selections, true, concise)?);
    notes.extend(warnings::read_notes(sources, false, concise, concise && all_roots_compressed, &[], &mut Vec::new())?);
    Ok(notes)
}

fn push_uncovered(plan: &mut AccountingPlan, issue: &'static str) {
    if !plan.uncovered.contains(&issue) { plan.uncovered.push(issue); }
}

fn uncovered(source: &Sources<'_>, part: &AccountingPart<'_>, output: &mut AccountingPlan) -> Result<()> {
    if part.uncompressed { return Ok(()); }
    if part.projection.is_none() || part.atoms.is_none() && part.claims.is_empty() {
        push_uncovered(output, "terminal field claim inventory");
    }
    if !source.request.compact { return Ok(()); }
    if source.request.public_api && source.request.share_descriptions {
        if let (Some(projection), Some(atoms)) = (part.projection, part.atoms) {
            let native = actions::mechanisms(source, projection)?;
            let selected = actions::projected_mechanisms(source, projection, &native)?;
            if atoms.atoms.values().flat_map(|atom| &atom.actions)
                .any(|action| selected.contains(&(action.source, action.catalogue_index)))
            {
                push_uncovered(output, "compact action mechanism destination");
            }
        }
    }
    Ok(())
}

pub(crate) fn plan(request: &AccountingRequest<'_>) -> Result<AccountingPlan> {
    let sources = Sources::new(request)?;
    let selections = summaries::selection_plan(&sources)?;
    let mut output = AccountingPlan::default();
    let mut supplements = Groups::default();
    let mut residual = relations::Residual::default();
    let mut action_accounting = actions::ActionAccounting::default();
    let concise = request.public_api && request.share_descriptions;
    for part in request.parts {
        let mut accounted = fields(&sources, part, &mut supplements)?;
        relations::account(&sources, part, &mut residual, &mut output.relation_sources)?;
        if let Some(disclosure) = part.disclosure {
            let mut rendered = Vec::new();
            let mut records = Vec::new();
            for (owner, excerpt) in disclosure.entries() {
                if part.groups.is_some_and(|groups| groups.groups.iter().any(|group|
                    group.projected_refs.contains(&owner)
                    && group.background.as_ref().is_none_or(|background| !background.important_refs.contains(&owner))))
                {
                    continue;
                }
                let reference = sources.reference(owner)?;
                let field_name = &excerpt.source.native_field;
                rendered.push(format!("\t{reference} {field_name}: {}", quoted(&excerpt.source.value)?));
                records.push(map([
                    ("ref", text(reference)), ("field", text(field_name)), ("attribute", text(&excerpt.source.attribute)),
                    ("value", text(&excerpt.source.value)), ("ranges", Value::Seq(excerpt.fragments.iter().map(|item|
                        Value::Seq(vec![Value::Int(item.start as i64), Value::Int(item.end as i64)])).collect())),
                    ("native", text(sources.token(owner)?)),
                    ("visibility", text("details")), ("representation", text("Captured full text")), ("target", text(reference)),
                ]));
            }
            if !rendered.is_empty() {
                output.diagnostic_prefixes.push(format!("Captured full text (excerpted in the interface):\n{}", rendered.join("\n")));
                if let Value::Map(fields) = &mut accounted { fields.push(("capturedTextExcerpts".to_owned(), Value::Seq(records))); }
            }
        }
        if request.compact {
            let (supporting, capabilities) = action_accounting.part(&sources, part)?;
            if let Value::Map(fields) = &mut accounted {
                fields.push(("compactSupportingActions".to_owned(), Value::Seq(supporting)));
                fields.push(("compactNativeCapabilities".to_owned(), Value::Seq(capabilities)));
            }
        }
        if request.compact && !part.uncompressed {
            let references = claims(part).into_iter().filter(|claim| claim_kind(part, claim) == "text-selection-summary")
                .map(|claim| sources.reference(claim.fact.source)).collect::<Result<Vec<_>>>()?;
            if !references.is_empty() {
                let destinations = summaries::selection_destinations(&selections, &references, concise)?;
                if let Value::Map(fields) = &mut accounted { fields.push(("compactTextSelections".to_owned(), Value::Seq(destinations))); }
            }
        }
        uncovered(&sources, part, &mut output)?;
        output.part_accounting.push(accounted);
    }
    if !supplements.rows.is_empty() {
        let mut lines = Vec::new();
        for (label, items) in supplements.rows {
            let refs = reference_set(items.iter().map(String::as_str))?;
            lines.push(format!("\t{refs} {label}"));
        }
        output.full_notes.push(format!("Additional native facts (grouped by element reference):\n{}", lines.join("\n")));
    }
    let relation_lines = residual.lines()?;
    if !relation_lines.is_empty() {
        output.full_notes.push(format!(
            "Native relations (source -> ordered targets; total=\"target-count\" means the length of that target array):\n\t{}",
            relation_lines.join("\n\t"),
        ));
    }
    output.full_notes.extend(summaries::selection_relations(&sources)?);
    output.full_notes.extend(summaries::read(&sources, &selections, false, false)?);
    let annotations: Vec<_> = seq(request.semantic.get("annotations")).iter().filter_map(Value::as_str).collect();
    if !annotations.is_empty() {
        output.full_notes.push(format!("Native facts:\n\t{}", annotations.join("\n\t")));
    }
    if request.compact {
        let all_roots_compressed = request.parts.iter().all(|part| !part.uncompressed);
        output.compact_notes = compact_notes_for_sources(&sources, &selections, concise, all_roots_compressed)?;
        let columns: Vec<_> = request.parts.iter().flat_map(|part| part.published_columns).collect();
        if !columns.is_empty() {
            let full = warnings::read_notes(
                &sources, false, concise, concise && all_roots_compressed, &columns,
                &mut output.compact_table_containment,
            )?;
            if !output.compact_table_containment.is_empty() {
                output.table_warning = Some((
                    full.ok_or(TextError::Invalid("published column warning"))?,
                    output.compact_notes.pop().ok_or(TextError::Invalid("original column warning"))?,
                ));
            }
        }
        output.full_notes.extend(warnings::read_notes(&sources, true, false, false, &[], &mut Vec::new())?);
        output.diagnostic_prefixes.extend(action_accounting.lines()?);
    }
    let issues = seq(request.snapshot.observation.get("issues"));
    if !issues.is_empty() {
        let native = DetailSource::new(
            sources.capture("backend")?, sources.capture("session")?, sources.capture("id")?, &[],
            &request.snapshot.facts, issues, sources.refs.iter().map(|(reference, token)| (*reference, *token)),
        )?.with_typed_alternatives(request.snapshot.metadata.get("issue_alternatives"));
        let (rendered, records) = native.read_issues(None)?;
        if !rendered.is_empty() { output.diagnostic_prefixes.push(rendered); }
        for record in records {
            let Value::Map(mut record) = record else { return Err(TextError::Invalid("native issue record")); };
            record.extend([
                ("visibility".to_owned(), text("details")), ("representation".to_owned(), text("Native read issues")),
                ("session".to_owned(), text(sources.capture("session")?)),
                ("observation".to_owned(), text(sources.capture("id")?)),
            ]);
            output.read_issues.push(Value::Map(record));
        }
    }
    Ok(output)
}

#[cfg(test)]
mod tests {
    use super::*;
    use crate::{projection::ProjectionOptions, wire::decode_json};

    fn json(source: &str) -> Value { decode_json(source.as_bytes()).unwrap() }

    #[test]
    fn full_column_warning_requires_exact_unique_published_order() {
        let snapshot = FactSnapshot {
            observation: json(r#"{"backend":"macos","session":"session","id":"capture","issues":[]}"#),
            facts: json(r#"{"nodes":[],"relations":[]}"#),
            metadata: Value::Null,
            query: Value::Null,
        };
        let refs = [1, 24, 5, 19, 20, 6, 13, 11, 16, 8, 15, 17, 10, 12, 21, 14, 22, 23, 9, 18];
        let tokens = ["table".to_owned(), "column".to_owned()];
        let mut semantic = json(r#"{"refs":{"0":"table","4":"column"},"roots":[],"fields":[]}"#);
        if let Value::Map(fields) = &mut semantic {
            fields.push(("annotations".to_owned(), Value::Seq(refs.iter()
                .map(|target| text(format!("4 contains {target} (shared)"))).collect())));
        }
        let mut column = json(r#"{"ref":"4","source":"column","table":"0","column":2,"scope":["session","capture","0"],"property":"children"}"#);
        if let Value::Map(fields) = &mut column {
            fields.push(("targets".to_owned(), Value::Seq(refs.iter().map(|target| text(target.to_string())).collect())));
        }
        let warnings = |columns: &[Value]| {
            let parts = [AccountingPart { published_columns: columns, ..AccountingPart::uncompressed(&[]) }];
            plan(&AccountingRequest {
                snapshot: &snapshot, semantic: &semantic, tokens: &tokens, parts: &parts,
                compact: true, public_api: true, share_descriptions: false,
            }).unwrap()
        };
        let original = warnings(&[]);
        let full = warnings(&[column.clone()]);
        let (full_text, incremental) = full.table_warning.unwrap();
        assert_eq!(full_text, "Read notes:\n\t4 contains Column 2 cells of table 0 (shared; displayed row order)");
        assert_eq!(incremental, original.compact_notes[0]);
        assert!(full.full_notes.last().unwrap().contains("4 contains 1, 24, 5"));
        assert_eq!(field(&full.compact_table_containment[0], "statement"), Some(full_text.lines().nth(1).unwrap().trim()));
        assert!(warnings(&[column.clone(), column.clone()]).table_warning.is_none());
        if let Some(Value::Seq(targets)) = column.get_mut("targets") { targets.swap(0, 1); }
        assert!(warnings(&[column]).table_warning.is_none());
    }

    #[test]
    fn selection_summary_proves_exact_inline_range_and_preserves_detailed_text() {
        let snapshot = FactSnapshot {
            observation: json(r#"{"backend":"macos","session":"session","id":"capture","issues":[]}"#),
            facts: json(r#"{"nodes":[{"node":"editor","observed":true,"values":{"role":"text_field","text.source":"edit","text.unit":"utf16_code_unit","text.selections":[{"start":2,"end":2,"collapsed":true}]},"fieldStatus":{}}],"relations":[]}"#),
            metadata: Value::Null, query: Value::Null,
        };
        let semantic = json(r#"{"refs":{"0":"editor"},"roots":[{"ref":"0","role":"textField","selectionRange":{"location":2,"length":0},"children":[]}],"fields":[]}"#);
        let tokens = ["editor".to_owned()];
        let aliases = HashMap::from([("0".to_owned(), NodeId(0))]);
        let projection = Projection::prepare(seq(semantic.get("roots"))[0].clone(), &aliases, Default::default()).unwrap();
        assert_eq!(selection_summary_refs(&snapshot, &semantic, &projection, &tokens).unwrap(), HashSet::from([NodeId(0)]));
        let request = AccountingRequest {
            snapshot: &snapshot, semantic: &semantic, tokens: &tokens, parts: &[],
            compact: true, public_api: true, share_descriptions: true,
        };
        let plan = plan(&request).unwrap();
        assert_eq!(plan.compact_notes, ["Selection [0]: [2,2) UTF-16"]);
        assert_eq!(plan.full_notes, [
            "Text selection of 0 (complete; 1 ranges): empty range 1: range text not reported; offsets [2, 2) utf16_code_unit. Keyboard focus and caret activity are separate facts.",
        ]);
    }

    #[test]
    fn action_ledger_keeps_projected_and_native_ordinals_with_original_sources() {
        let mut snapshot = FactSnapshot {
            observation: json(r#"{"backend":"macos","session":"session","id":"capture","nodes":{"root":{"role":"window","actions":[]},"menu":{"role":"menu_item","actions":[{"kind":"focus","available":false,"shortcuts":[]},{"kind":"cancel","key":"AXCancel","label":"AXCancel","description":"Cancel","shortcuts":[]}]},"hidden":{"role":"group","actions":[{"kind":"focus","available":false,"shortcuts":[]}]}},"issues":[]}"#),
            facts: json(r#"{"nodes":[{"node":"root","observed":true,"values":{},"actions":{"state":"complete","items":[]}},{"node":"menu","observed":true,"values":{},"actions":{"state":"complete","items":[]}},{"node":"hidden","observed":true,"values":{},"actions":{"state":"complete","items":[]}}],"relations":[]}"#),
            metadata: Value::Null, query: Value::Null,
        };
        let semantic = json(r#"{"refs":{"0":"root","1":"menu","2":"hidden"},"roots":[{"ref":"0","role":"window","children":[{"ref":"1","role":"menuItem","actions":[{"name":"AXCancel","kind":"cancel","description":"Cancel"}],"children":[]},{"ref":"2","role":"group","children":[]}]}],"fields":[]}"#);
        let tokens = ["root".to_owned(), "menu".to_owned(), "hidden".to_owned()];
        let aliases = HashMap::from([("0".to_owned(), NodeId(0)), ("1".to_owned(), NodeId(1)), ("2".to_owned(), NodeId(2))]);
        let options = ProjectionOptions { compact: true, public_api: true, share_descriptions: true, ..Default::default() };
        let mut projection = Projection::prepare(seq(semantic.get("roots"))[0].clone(), &aliases, options).unwrap();
        projection.roots = vec![NodeId(1)];
        let deferred = deferred_actions(&snapshot, &semantic, &projection, &tokens).unwrap();
        assert_eq!(deferred.native, HashSet::from([(NodeId(1), 1)]));
        assert_eq!(deferred.projected, HashSet::from([(NodeId(1), 0)]));
        let members = [NodeId(0), NodeId(1), NodeId(2)];
        let parts = [AccountingPart {
            members: &members, projection: Some(&projection), atoms: None, claims: &[],
            fields: None, collections: None, disclosure: None, groups: None, sharing: None, text_slices: None,
            deferred_native_fields: &[], published_columns: &[], uncompressed: false,
        }];
        let request = AccountingRequest {
            snapshot: &snapshot, semantic: &semantic, tokens: &tokens, parts: &parts,
            compact: true, public_api: true, share_descriptions: true,
        };
        let plan = plan(&request).unwrap();
        let supporting = seq(plan.part_accounting[0].get("compactSupportingActions"));
        assert_eq!(supporting.len(), 1);
        assert_eq!(field(&supporting[0], "native"), Some("menu"));
        assert_eq!(supporting[0].get("index"), Some(&Value::Int(1)));
        let capabilities = seq(plan.part_accounting[0].get("compactNativeCapabilities"));
        assert_eq!(capabilities.len(), 1);
        assert_eq!(field(&capabilities[0], "native"), Some("hidden"));
        assert_eq!(field(&capabilities[0], "target"), Some("2"));
        assert_eq!(plan.diagnostic_prefixes, [
            "Supporting actions [elements 1]: Cancel [name: \"AXCancel\"]; availability unknown",
            "Native capabilities (reported; no performAction name):\n  [elements 2]: focus unavailable",
        ]);
        let Value::Map(values) = &mut snapshot.observation else { unreachable!() };
        values.iter_mut().find(|(key, _)| key == "issues").unwrap().1 = json(r#"[{"path":["nodes","menu","role"],"state":"partial"}]"#);
        assert!(deferred_actions(&snapshot, &semantic, &projection, &tokens).unwrap().native.is_empty());
    }
}
