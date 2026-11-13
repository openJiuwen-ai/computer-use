// Copyright (c) Huawei Technologies Co., Ltd. 2026. All rights reserved.

//! Structured observation comparison before terminal rendering.

use std::{collections::{HashMap, HashSet}, sync::Arc};

use super::value::Value;

#[path = "diff/signature.rs"]
mod signature;

#[path = "diff/prepared.rs"]
mod prepared;

pub(crate) use prepared::{prepare_compact_comparison, prepare_comparison_tree, InvalidPreparedComparison, PreparedRoot};
pub(crate) use signature::{python_is_printable, python_str_repr, sha256, Signatures};

pub(crate) type Scope = Vec<Option<String>>;
pub(crate) type RootContexts = HashMap<String, Vec<(String, String)>>;
pub(crate) type Bindings = HashMap<String, String>;

#[derive(Clone, Debug, Eq, PartialEq)]
pub(crate) struct ComparisonNode {
    pub reference: String,
    pub parent: Option<String>,
    pub depth: i64,
    pub signature: String,
    pub content: String,
}

#[derive(Clone, Copy, Debug, Default, Eq, PartialEq)]
pub(crate) struct DisplayOptions {
    pub compact: bool,
    pub public_api: bool,
    pub details: bool,
}

#[derive(Clone, Debug, Default, Eq, PartialEq)]
pub(crate) struct ComparisonTree {
    pub scope: Scope,
    pub nodes: Vec<ComparisonNode>,
    pub options: DisplayOptions,
    pub root_contexts: RootContexts,
    pub versions: HashMap<String, Option<String>>,
    pub failed: bool,
}

#[derive(Clone, Debug, Default, Eq, PartialEq)]
pub(crate) struct ComparisonDocument {
    pub trees: Vec<Arc<ComparisonTree>>,
}

#[derive(Clone, Debug, Default, Eq, PartialEq)]
pub(crate) struct ContextNode {
    pub partial: bool,
    pub anonymous_group_or_cell: bool,
    pub static_text_or_heading: bool,
    pub label_targets: Vec<String>,
    pub context_refs: Vec<String>,
    pub related: Vec<String>,
}

#[derive(Clone, Copy, Debug, Eq, PartialEq)]
pub(crate) struct InvalidContext(pub &'static str);

impl ContextNode {
    /// Extract only the evidence consumed by diff planning. The caller passes
    /// the same final field rows that supply the structural signatures.
    pub(crate) fn from_fields(fields: &[(String, Value)]) -> Result<Self, InvalidContext> {
        let get = |name: &str| fields.iter().find(|(key, _)| key == name).map(|(_, value)| value);
        let references = |name: &'static str| -> Result<Vec<String>, InvalidContext> {
            let Some(items) = get(name) else { return Ok(Vec::new()); };
            let items = items.as_seq().ok_or(InvalidContext(name))?;
            items.iter().map(|value| value.as_str().map(str::to_owned).ok_or(InvalidContext(name))).collect()
        };
        let role = get("role").and_then(Value::as_str);
        let partial = get("partialReason").is_some_and(|value| !matches!(value, Value::Null))
            || truth(get("childOmissions")) || get("nextChildOffset").is_some()
            || child_count_less(get("childrenRead"), get("childCount"))?;
        Ok(Self {
            partial,
            anonymous_group_or_cell: matches!(role, Some("group" | "cell"))
                && !truth(get("name")) && !truth(get("description")),
            static_text_or_heading: matches!(role, Some("staticText" | "heading")),
            label_targets: references("labelTargets")?,
            context_refs: references("_contextRefs")?,
            related: references("related")?,
        })
    }
}

fn truth(value: Option<&Value>) -> bool {
    match value {
        None | Some(Value::Null) => false,
        Some(Value::Bool(value)) => *value,
        Some(Value::Int(value)) => *value != 0,
        Some(Value::BigInt(value)) => value != "0",
        Some(Value::Float(value)) => *value != 0.0,
        Some(Value::Text(value)) => !value.is_empty(),
        Some(Value::Bytes(value)) => !value.is_empty(),
        Some(Value::Seq(value)) => !value.is_empty(),
        Some(Value::Map(value)) => !value.is_empty(),
    }
}

fn child_count_less(left: Option<&Value>, right: Option<&Value>) -> Result<bool, InvalidContext> {
    fn integer<'a>(value: Option<&'a Value>, name: &'static str) -> Result<std::borrow::Cow<'a, str>, InvalidContext> {
        use std::borrow::Cow::{Borrowed, Owned};
        match value {
            None | Some(Value::Bool(false)) => Ok(Borrowed("0")),
            Some(Value::Bool(true)) => Ok(Borrowed("1")),
            Some(Value::Int(value)) => Ok(Owned(value.to_string())),
            Some(Value::BigInt(value)) => Ok(Borrowed(value)),
            _ => Err(InvalidContext(name)),
        }
    }
    if let (Some(Value::Int(left)), Some(Value::Int(right))) = (left, right) {
        return Ok(left < right);
    }
    let left = integer(left, "childrenRead")?;
    let right = integer(right, "childCount")?;
    let left_abs = left.trim_start_matches('-');
    let right_abs = right.trim_start_matches('-');
    let left_negative = left.len() != left_abs.len();
    let right_negative = right.len() != right_abs.len();
    let magnitude = left_abs.len().cmp(&right_abs.len()).then_with(|| left_abs.cmp(right_abs));
    Ok(match (left_negative, right_negative) {
        (true, false) => true,
        (false, true) => false,
        (true, true) => magnitude.is_gt(),
        (false, false) => magnitude.is_lt(),
    })
}

#[derive(Clone, Debug, Default, Eq, PartialEq)]
pub(crate) struct DisplayRoot {
    pub reference: String,
    pub members: Vec<String>,
}

#[derive(Clone, Debug, Default, Eq, PartialEq)]
pub(crate) struct CurrentTree {
    pub comparison: Arc<ComparisonTree>,
    /// One context row per comparison node, in the same display order.
    pub context: Vec<ContextNode>,
    pub roots: Vec<DisplayRoot>,
}

#[derive(Clone, Debug, Default, Eq, PartialEq)]
pub(crate) struct Coverage {
    pub parents: HashMap<String, Option<String>>,
    pub complete: HashSet<String>,
}

#[derive(Clone, Copy, Debug, Eq, PartialEq)]
pub(crate) struct InvalidCoverage(pub &'static str);

fn coverage_text<'a>(row: &'a Value, key: &'static str) -> Result<&'a str, InvalidCoverage> {
    row.get(key).and_then(Value::as_str).ok_or(InvalidCoverage(key))
}

fn coverage_sequence<'a>(row: &'a Value, key: &'static str) -> Result<&'a [Value], InvalidCoverage> {
    row.get(key).and_then(Value::as_seq).ok_or(InvalidCoverage(key))
}

fn equals_count(value: &Value, count: usize) -> bool {
    match value {
        Value::Bool(value) => usize::from(*value) == count,
        Value::Int(value) => usize::try_from(*value) == Ok(count),
        Value::BigInt(value) => value.parse::<usize>() == Ok(count),
        Value::Float(value) => *value == count as f64,
        _ => false,
    }
}

/// Collect containment and confirmed child coverage from each original
/// semantic forest and its capture-wide fact index, in document tree order.
pub(crate) fn document_coverage<'a>(
    trees: impl IntoIterator<Item = (&'a Value, &'a Value)>,
) -> Result<Coverage, InvalidCoverage> {
    let mut coverage = Coverage::default();
    for (semantic, facts) in trees {
        let roots = coverage_sequence(semantic, "roots")?;
        let mut pending: Vec<(&Value, Option<&str>)> = roots.iter().map(|root| (root, None)).collect();
        while let Some((node, parent)) = pending.pop() {
            let reference = coverage_text(node, "ref")?;
            coverage.parents.insert(reference.to_owned(), parent.map(str::to_owned));
            pending.extend(coverage_sequence(node, "children")?.iter().map(|child| (child, Some(reference))));
        }
        let refs = semantic.get("refs").and_then(Value::as_map).ok_or(InvalidCoverage("refs"))?;
        let mut aliases = HashMap::with_capacity(refs.len());
        for (reference, native) in refs {
            let native = native.as_str().ok_or(InvalidCoverage("native reference"))?;
            aliases.insert(native, reference);
        }
        let mut children = HashMap::<&str, Option<&Value>>::new();
        for relation in coverage_sequence(facts, "relations")? {
            if coverage_text(relation, "kind")? == "children" && coverage_text(relation, "certainty")? == "confirmed" {
                let source = coverage_text(relation, "source")?;
                children.entry(source).and_modify(|prior| *prior = None).or_insert(Some(relation));
            }
        }
        let unread = facts.get("unread").and_then(Value::as_seq).unwrap_or_default().iter()
            .map(|value| value.as_str().ok_or(InvalidCoverage("unread")))
            .collect::<Result<HashSet<_>, _>>()?;
        for (source, relation) in children {
            let (Some(relation), Some(reference)) = (relation, aliases.get(source)) else { continue; };
            let targets = coverage_sequence(relation, "targets")?;
            if coverage_text(relation, "state")? == "complete"
                && relation.get("offset").is_none_or(|offset| equals_count(offset, 0))
                && relation.get("total").is_none_or(|total| equals_count(total, targets.len()))
                && targets.iter().all(|target| target.as_str().is_some_and(|target| !unread.contains(target)))
            {
                coverage.complete.insert((*reference).clone());
            }
        }
    }
    Ok(coverage)
}

#[derive(Clone, Copy, Debug, Eq, PartialEq)]
pub(crate) enum Change {
    Added,
    Modified,
}

impl Change {
    pub(crate) fn marker(self) -> char {
        match self {
            Self::Added => '+',
            Self::Modified => '~',
        }
    }
}

#[derive(Clone, Debug, Default, Eq, PartialEq)]
pub(crate) struct TreeDelta {
    changes: Vec<(String, Change)>,
    by_ref: HashMap<String, Change>,
    pub missing: Vec<String>,
    pub context: HashSet<String>,
    pub expanded: HashSet<String>,
}

impl TreeDelta {
    pub(crate) fn new(
        changes: Vec<(String, Change)>,
        missing: Vec<String>,
        context: HashSet<String>,
        expanded: HashSet<String>,
    ) -> Self {
        let by_ref = changes.iter().cloned().collect();
        Self { changes, by_ref, missing, context, expanded }
    }

    pub(crate) fn changes(&self) -> &[(String, Change)] {
        &self.changes
    }

    pub(crate) fn change(&self, reference: &str) -> Option<Change> {
        self.by_ref.get(reference).copied()
    }
}

#[derive(Clone, Copy, Debug, Eq, PartialEq)]
pub(crate) enum Mode {
    Full,
    Diff,
    Unchanged,
}

#[derive(Clone, Copy, Debug, Eq, PartialEq)]
pub(crate) enum Reason {
    InitialObservation,
    NoStructuredBaseline,
    NewOrReplacedNativeScope,
    ComparedNativeScopes,
}

impl Reason {
    pub(crate) fn as_str(self) -> &'static str {
        match self {
            Self::InitialObservation => "initial-observation",
            Self::NoStructuredBaseline => "no-structured-baseline",
            Self::NewOrReplacedNativeScope => "new-or-replaced-native-scope",
            Self::ComparedNativeScopes => "compared-native-scopes",
        }
    }
}

#[derive(Clone, Debug, Eq, PartialEq)]
pub(crate) struct DeltaPlan {
    pub mode: Mode,
    pub trees: Vec<Option<TreeDelta>>,
    pub not_observed: HashSet<String>,
    pub retained: HashSet<String>,
    pub reason: Reason,
}

impl DeltaPlan {
    pub(crate) fn full(reason: Reason) -> Self {
        Self {
            mode: Mode::Full,
            trees: Vec::new(),
            not_observed: HashSet::new(),
            retained: HashSet::new(),
            reason,
        }
    }
}

#[derive(Clone, Copy, Debug, Eq, PartialEq)]
pub(crate) struct ContextRowMismatch {
    pub tree: usize,
    pub nodes: usize,
    pub rows: usize,
}

type NodeIndex<'a> = HashMap<&'a str, usize>;

fn index_nodes<'a>(tree: &'a ComparisonTree, bindings: &Bindings) -> Option<NodeIndex<'a>> {
    let mut seen = NodeIndex::with_capacity(tree.nodes.len());
    for (index, node) in tree.nodes.iter().enumerate() {
        if !bindings.contains_key(node.reference.as_str()) {
            return None;
        }
        if let Some(parent) = &node.parent {
            let previous = *seen.get(parent.as_str())?;
            if tree.nodes[previous].depth.checked_add(1) != Some(node.depth) {
                return None;
            }
        }
        if seen.insert(node.reference.as_str(), index).is_some() {
            return None;
        }
    }
    Some(seen)
}

struct OrderedChanges<'a> {
    entries: Vec<(&'a str, Change)>,
    index: HashMap<&'a str, usize>,
}

impl<'a> OrderedChanges<'a> {
    fn new() -> Self {
        Self { entries: Vec::new(), index: HashMap::new() }
    }

    fn insert(&mut self, reference: &'a str, change: Change) {
        let slot = self.index.entry(reference).or_insert(self.entries.len());
        if *slot == self.entries.len() {
            self.entries.push((reference, change));
        } else {
            self.entries[*slot].1 = change;
        }
    }
}

struct WorkingDelta<'a> {
    display: &'a CurrentTree,
    new: NodeIndex<'a>,
    changes: OrderedChanges<'a>,
    missing: Vec<&'a str>,
    context: HashSet<&'a str>,
    expanded: HashSet<&'a str>,
}

impl WorkingDelta<'_> {
    fn has_changes(&self) -> bool {
        !self.changes.entries.is_empty() || !self.missing.is_empty()
    }

    fn finish(self) -> TreeDelta {
        TreeDelta::new(
            self.changes.entries.into_iter().map(|(reference, change)| (reference.to_owned(), change)).collect(),
            self.missing.into_iter().map(str::to_owned).collect(),
            self.context.into_iter().map(str::to_owned).collect(),
            self.expanded.into_iter().map(str::to_owned).collect(),
        )
    }
}

#[cfg(test)]
#[path = "diff/tests.rs"]
mod tests;

fn compare<'a>(
    before: &'a ComparisonTree,
    display: &'a CurrentTree,
    old: NodeIndex<'a>,
    new: NodeIndex<'a>,
) -> WorkingDelta<'a> {
    let after = &display.comparison;
    let mut changes = OrderedChanges::new();
    let mut context = HashSet::new();
    let mut expanded = HashSet::new();
    let kind = |reference: &str| {
        if old.contains_key(reference) { Change::Modified } else { Change::Added }
    };
    for node in &after.nodes {
        if old.get(node.reference.as_str()).map(|index| &before.nodes[*index]) != Some(node) {
            changes.insert(&node.reference, kind(&node.reference));
        }
    }
    let missing: Vec<_> = before.nodes.iter().map(|node| node.reference.as_str())
        .filter(|reference| !new.contains_key(reference)).collect();

    for root in &display.roots {
        let previous_version = before.versions.get(&root.reference).and_then(Option::as_ref);
        let current_version = after.versions.get(&root.reference).and_then(Option::as_ref);
        if before.options != after.options || previous_version != current_version {
            for reference in root.members.iter().filter(|reference| new.contains_key(reference.as_str())) {
                changes.insert(reference, kind(reference));
            }
        }
        let previous = before.root_contexts.get(&root.reference);
        let current = after.root_contexts.get(&root.reference);
        if previous != current {
            for reference in &root.members {
                if new.get(reference.as_str()).is_some_and(|index| after.nodes[*index].parent.is_none()) {
                    changes.insert(reference, kind(reference));
                }
            }
            for (owner, _) in previous.into_iter().chain(current).flatten() {
                if new.contains_key(owner.as_str()) {
                    context.insert(owner.as_str());
                }
            }
        }
    }

    let old_order = before.nodes.iter().map(|node| node.reference.as_str()).filter(|id| new.contains_key(id));
    let new_order = after.nodes.iter().map(|node| node.reference.as_str()).filter(|id| old.contains_key(id));
    for (before_ref, after_ref) in old_order.zip(new_order) {
        if before_ref != after_ref {
            changes.insert(before_ref, Change::Modified);
            changes.insert(after_ref, Change::Modified);
        }
    }

    let mut children: Vec<Vec<&str>> = vec![Vec::new(); after.nodes.len()];
    for (node, facts) in after.nodes.iter().zip(&display.context) {
        if let Some(parent) = &node.parent {
            children[new[parent.as_str()]].push(&node.reference);
        }
        if facts.partial {
            context.insert(&node.reference);
        }
    }
    let mut pending = Vec::new();
    for (reference, _) in &changes.entries {
        let current = new[reference];
        if let Some(previous) = old.get(reference) {
            if after.nodes[current].parent.is_some() && before.nodes[*previous].content != after.nodes[current].content {
                pending.extend(children[current].iter().copied());
                while let Some(child) = pending.pop() {
                    context.insert(child);
                    expanded.insert(child);
                    pending.extend(children[new[child]].iter().copied());
                }
            }
        }
    }
    let ancestors: Vec<_> = changes.entries.iter().map(|(id, _)| *id).chain(context.iter().copied()).collect();
    for reference in ancestors {
        let mut parent = after.nodes[new[reference]].parent.as_deref();
        while let Some(reference) = parent {
            context.insert(reference);
            parent = after.nodes[new[reference]].parent.as_deref();
        }
    }
    for reference in &missing {
        let mut parent = before.nodes[old[reference]].parent.as_deref();
        while let Some(reference) = parent {
            if new.contains_key(reference) {
                context.insert(reference);
            }
            parent = before.nodes[old[reference]].parent.as_deref();
        }
    }
    WorkingDelta { display, new, changes, missing, context, expanded }
}

fn relationships<'a>(tree: &WorkingDelta<'a>) -> Vec<Vec<&'a str>> {
    let nodes = &tree.display.comparison.nodes;
    let facts = &tree.display.context;
    let mut edges: Vec<Vec<&str>> = facts.iter().map(|row| {
        row.label_targets.iter().chain(&row.context_refs).chain(&row.related).map(String::as_str).collect()
    }).collect();
    let mut children = vec![Vec::new(); nodes.len()];
    for (index, node) in nodes.iter().enumerate() {
        if let Some(parent) = &node.parent {
            children[tree.new[parent.as_str()]].push(index);
        }
        for target in &facts[index].label_targets {
            if let Some(target) = tree.new.get(target.as_str()) {
                edges[*target].push(&node.reference);
            }
        }
    }
    for (index, members) in children.into_iter().enumerate() {
        if facts[index].anonymous_group_or_cell && members.len() > 1
            && members.iter().any(|member| facts[*member].static_text_or_heading)
        {
            edges[index].extend(members.iter().map(|member| nodes[*member].reference.as_str()));
        }
    }
    for outgoing in &mut edges {
        let mut seen = HashSet::new();
        outgoing.retain(|reference| seen.insert(*reference));
    }
    edges
}

fn related_context<'a>(deltas: &mut [Option<WorkingDelta<'a>>]) {
    // Rows are borrowed from the immutable current document, not the sets we extend.
    let mut locations = HashMap::<&str, Option<usize>>::new();
    let mut edges = Vec::with_capacity(deltas.len());
    let mut pending = Vec::new();
    for (index, delta) in deltas.iter().enumerate() {
        let Some(delta) = delta else {
            edges.push(Vec::new());
            continue;
        };
        for reference in delta.new.keys() {
            locations.entry(reference).and_modify(|value| *value = None).or_insert(Some(index));
        }
        pending.extend(delta.changes.entries.iter().map(|(reference, _)| (index, *reference)));
        pending.extend(delta.context.iter().map(|reference| (index, *reference)));
        edges.push(relationships(delta));
    }
    let mut visited = HashSet::new();
    while let Some((index, reference)) = pending.pop() {
        if !visited.insert((index, reference)) {
            continue;
        }
        let row = deltas[index].as_ref().expect("only compatible trees seed context").new[reference];
        for related in &edges[index][row] {
            let local = deltas[index].as_ref().expect("compatible tree").new.contains_key(related);
            let Some(target) = (if local { Some(index) } else { locations.get(related).copied().flatten() }) else {
                continue;
            };
            let delta = deltas[target].as_mut().expect("location belongs to a compatible tree");
            let mut ancestor = Some(*related);
            while let Some(reference) = ancestor {
                if delta.context.insert(reference) {
                    pending.push((target, reference));
                }
                ancestor = delta.display.comparison.nodes[delta.new[reference]].parent.as_deref();
            }
        }
    }
}

pub(crate) fn confirms_removal(previous: Option<&Coverage>, current: Option<&Coverage>, reference: &str) -> bool {
    let (Some(previous), Some(current)) = (previous, current) else { return false; };
    if current.parents.contains_key(reference) {
        return false;
    }
    let mut child = reference;
    // Captured containment is acyclic. A malformed detached baseline cannot
    // prove removal or keep the delivery planner walking forever.
    for _ in 0..previous.parents.len() {
        let Some(parent) = previous.parents.get(child).and_then(Option::as_deref) else { return false; };
        if current.parents.contains_key(parent) {
            return current.complete.contains(parent);
        }
        child = parent;
    }
    false
}

pub(crate) fn unconfirmed_missing(
    previous_refs: &Bindings,
    current_refs: &Bindings,
    previous_coverage: Option<&Coverage>,
    current_coverage: Option<&Coverage>,
) -> HashSet<String> {
    previous_refs.keys().filter(|reference| {
        !current_refs.contains_key(reference.as_str()) && !confirms_removal(previous_coverage, current_coverage, reference)
    }).cloned().collect()
}

pub(crate) fn plan_document_diff(
    before: Option<&ComparisonDocument>,
    after: Option<&[CurrentTree]>,
    previous_refs: &Bindings,
    current_refs: &Bindings,
    not_observed: &HashSet<String>,
) -> Result<DeltaPlan, ContextRowMismatch> {
    let (Some(before), Some(after)) = (before, after) else {
        return Ok(DeltaPlan::full(Reason::NoStructuredBaseline));
    };
    plan_document_diff_trees(
        &before.trees.iter().map(Arc::as_ref).collect::<Vec<_>>(), &after.iter().collect::<Vec<_>>(),
        previous_refs, current_refs, not_observed,
    )
}

pub(crate) fn plan_document_diff_trees(
    before: &[&ComparisonTree],
    after: &[&CurrentTree],
    previous_refs: &Bindings,
    current_refs: &Bindings,
    not_observed: &HashSet<String>,
) -> Result<DeltaPlan, ContextRowMismatch> {
    if before.is_empty() {
        return Ok(DeltaPlan::full(Reason::InitialObservation));
    }
    let mut by_scope = HashMap::<&Scope, Option<&ComparisonTree>>::new();
    for &tree in before {
        by_scope.entry(&tree.scope).and_modify(|prior| *prior = None).or_insert(Some(tree));
    }
    let mut deltas = Vec::with_capacity(after.len());
    for (tree, display) in after.iter().enumerate() {
        let comparison = &display.comparison;
        let Some(old) = by_scope.get(&comparison.scope).copied().flatten() else {
            deltas.push(None);
            continue;
        };
        let (Some(previous), Some(current)) = (index_nodes(old, previous_refs), index_nodes(comparison, current_refs)) else {
            deltas.push(None);
            continue;
        };
        if previous.keys().any(|reference| current.contains_key(reference)
            && previous_refs.get(*reference) != current_refs.get(*reference))
        {
            deltas.push(None);
            continue;
        }
        if comparison.nodes.len() != display.context.len() {
            return Err(ContextRowMismatch { tree, nodes: comparison.nodes.len(), rows: display.context.len() });
        }
        deltas.push(Some(compare(old, display, previous, current)));
    }
    if deltas.iter().all(Option::is_none) {
        return Ok(DeltaPlan::full(Reason::NewOrReplacedNativeScope));
    }
    related_context(&mut deltas);
    let changed = deltas.iter().any(|delta| delta.as_ref().is_none_or(WorkingDelta::has_changes));
    Ok(DeltaPlan {
        mode: if changed { Mode::Diff } else { Mode::Unchanged },
        trees: deltas.into_iter().map(|delta| delta.map(WorkingDelta::finish)).collect(),
        not_observed: not_observed.clone(),
        retained: current_refs.keys().cloned().collect(),
        reason: Reason::ComparedNativeScopes,
    })
}
