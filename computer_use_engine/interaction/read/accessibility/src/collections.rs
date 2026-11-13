// Copyright (c) Huawei Technologies Co., Ltd. 2026. All rights reserved.

//! Logical table and outline plans backed by the original captured membership.

use std::collections::{BTreeSet, HashMap, HashSet};

use super::{
    facts::FactSnapshot,
    geometric::{role, GeometricPlan},
    line_layout::{
        capture_row_collections, capture_table_grids, NativeTableGrid, TableRowCollection,
    },
    numbering::NodeId,
    projection::{Projection, ProjectionNode},
    render::atoms::{Atom, AtomFact, AtomPlan, DispositionKind, Format, Slot},
    value::Value,
};

mod presentation;

#[derive(Debug, Eq, PartialEq)]
pub(crate) struct CollectionError(pub(crate) &'static str);

type Result<T> = std::result::Result<T, CollectionError>;
type Selection<T> = std::result::Result<T, &'static str>;

#[derive(Debug)]
pub(crate) struct TableCellPlan {
    pub(crate) reference: NodeId,
    pub(crate) row: usize,
    pub(crate) column: usize,
    pub(crate) row_span: usize,
    pub(crate) column_span: usize,
    pub(crate) children: Vec<NodeId>,
    pub(crate) descendant_refs: Vec<NodeId>,
    pub(crate) action_refs: Vec<NodeId>,
    pub(crate) partial: bool,
    pub(crate) inline_chain: Vec<NodeId>,
}

#[derive(Debug)]
pub(crate) struct TableRowPlan {
    pub(crate) reference: NodeId,
    pub(crate) index: Option<usize>,
    pub(crate) outline_level: Option<usize>,
    pub(crate) cells: Vec<TableCellPlan>,
    pub(crate) action_refs: Vec<NodeId>,
    pub(crate) partial: bool,
}

#[derive(Debug)]
pub(crate) struct TableColumnPlan {
    pub(crate) reference: NodeId,
    pub(crate) index: usize,
    pub(crate) cell_refs: Vec<NodeId>,
    pub(crate) action_refs: Vec<NodeId>,
}

#[derive(Debug)]
pub(crate) struct CollectionMemberPrefix {
    pub(crate) reference: NodeId,
    pub(crate) state_indices: Vec<usize>,
    pub(crate) slot_keys: Vec<String>,
}

#[derive(Debug)]
pub(crate) struct CollectionPrefixPlan {
    pub(crate) member_refs: Vec<NodeId>,
    pub(crate) role: String,
    pub(crate) role_facts: Vec<AtomFact>,
    pub(crate) source_description: bool,
    pub(crate) states: Vec<String>,
    pub(crate) state_facts: Vec<Vec<AtomFact>>,
    pub(crate) slots: Vec<Slot>,
    pub(crate) members: Vec<CollectionMemberPrefix>,
}

#[derive(Debug)]
pub(crate) struct CollectionDetailField {
    pub(crate) reference: NodeId,
    pub(crate) slot: Slot,
}

#[derive(Debug)]
pub(crate) struct TablePlan {
    pub(crate) reference: NodeId,
    pub(crate) kind: &'static str,
    pub(crate) scope: [String; 3],
    pub(crate) rows: Vec<TableRowPlan>,
    pub(crate) columns: Vec<TableColumnPlan>,
    pub(crate) remaining_refs: Vec<NodeId>,
    pub(crate) consumed_refs: Vec<NodeId>,
    pub(crate) action_refs: Vec<NodeId>,
    pub(crate) row_count: Option<usize>,
    pub(crate) column_count: Option<usize>,
    pub(crate) partial: bool,
    pub(crate) layout: &'static str,
    pub(crate) column_indices: Vec<usize>,
    pub(crate) column_labels: Vec<Option<String>>,
    pub(crate) row_prefix: Option<CollectionPrefixPlan>,
    pub(crate) column_prefixes: Vec<Option<CollectionPrefixPlan>>,
    pub(crate) detail_fields: Vec<CollectionDetailField>,
}

#[derive(Debug, Eq, PartialEq)]
pub(crate) struct CollectionRejection {
    pub(crate) reference: NodeId,
    pub(crate) reason: &'static str,
}

#[derive(Debug, Default)]
pub(crate) struct CollectionDocument {
    pub(crate) tables: HashMap<NodeId, TablePlan>,
    pub(crate) order: Vec<NodeId>,
    pub(crate) rejected: Vec<CollectionRejection>,
}

fn items(value: Option<&Value>) -> &[Value] {
    value.and_then(Value::as_seq).unwrap_or_default()
}

fn text<'a>(value: &'a Value, name: &str) -> Option<&'a str> {
    value.get(name).and_then(Value::as_str)
}

fn nonnegative(value: Option<&Value>) -> Option<usize> {
    match value? {
        Value::Int(value) => usize::try_from(*value).ok(),
        Value::BigInt(value) => value.parse().ok(),
        _ => None,
    }
}

fn number_equal(value: Option<&Value>, expected: usize) -> bool {
    match value {
        Some(Value::Bool(value)) => usize::from(*value) == expected,
        Some(Value::Float(value)) => *value == expected as f64,
        _ => nonnegative(value) == Some(expected),
    }
}

fn path_equal(left: &[Value], right: &[&str]) -> bool {
    left.len() == right.len() && path_prefix(left, right)
}

fn path_prefix(left: &[Value], right: &[&str]) -> bool {
    left.iter()
        .zip(right)
        .all(|(item, name)| item.as_str() == Some(name))
}

struct Source<'a> {
    interface: &'a GeometricPlan,
    projection: &'a Projection,
    atoms: &'a AtomPlan,
    native: &'a Value,
    nodes: &'a [(String, Value)],
    aliases: HashMap<&'a str, NodeId>,
    original_tokens: HashMap<NodeId, &'a str>,
    global_issues: Vec<&'a Value>,
    issues_by_node: HashMap<&'a str, Vec<&'a Value>>,
}

impl<'a> Source<'a> {
    fn new(
        interface: &'a GeometricPlan,
        projection: &'a Projection,
        atoms: &'a AtomPlan,
        native: &'a FactSnapshot,
    ) -> Self {
        let native = &native.observation;
        let nodes = native
            .get("nodes")
            .and_then(Value::as_map)
            .unwrap_or_default();
        let mut aliases = HashMap::new();
        let mut original_tokens = HashMap::new();
        for (&id, reference) in &atoms.source_references {
            if !projection
                .by_reference(reference)
                .is_some_and(|node| node.id == id)
            {
                continue;
            }
            if let Some((token, _)) = nodes.get(id.0) {
                aliases.insert(token.as_str(), id);
                original_tokens.insert(id, token.as_str());
            }
        }
        let mut global_issues = Vec::new();
        let mut issues_by_node: HashMap<&str, Vec<&Value>> = HashMap::new();
        for issue in items(native.get("issues")) {
            match items(issue.get("path")) {
                [] => global_issues.push(issue),
                [node] if node.as_str() == Some("nodes") => global_issues.push(issue),
                [node, token, ..] if node.as_str() == Some("nodes") => {
                    if let Some(token) = token.as_str() {
                        issues_by_node.entry(token).or_default().push(issue);
                    }
                }
                _ => {}
            }
        }
        Self {
            interface,
            projection,
            atoms,
            native,
            nodes,
            aliases,
            original_tokens,
            global_issues,
            issues_by_node,
        }
    }

    fn node(&self, reference: NodeId) -> Option<&'a ProjectionNode> {
        self.projection.node(reference)
    }

    fn atom(&self, reference: NodeId) -> Option<&'a Atom> {
        self.atoms.atoms.get(&reference)
    }

    fn native_node(&self, reference: NodeId) -> Option<&'a Value> {
        let (token, node) = self.nodes.get(reference.0)?;
        (self.aliases.get(token.as_str()) == Some(&reference)).then_some(node)
    }

    fn scope(&self, reference: NodeId) -> Option<NodeId> {
        self.interface
            .evidence
            .get(&reference)
            .map(|item| item.scope)
    }

    fn overlapping(&self, reference: NodeId, field: &[&str], allow_prefix: bool) -> bool {
        let Some(&token) = self.original_tokens.get(&reference) else {
            return true;
        };
        let mut path = vec!["nodes", token];
        path.extend_from_slice(field);
        self.global_issues
            .iter()
            .copied()
            .chain(
                self.issues_by_node
                    .get(token)
                    .into_iter()
                    .flatten()
                    .copied(),
            )
            .any(|issue| {
                let issue_path = items(issue.get("path"));
                let partial = text(issue, "state") == Some("partial");
                path_prefix(issue_path, &path)
                    && !(partial
                        && (path_equal(issue_path, &["nodes"])
                            || allow_prefix && path_equal(issue_path, &path)))
            })
    }

    fn clean(&self, reference: NodeId, field: &[&str]) -> bool {
        !self.overlapping(reference, field, false)
    }

    fn relation(&self, reference: NodeId, kind: &str, allow_prefix: bool) -> Option<Vec<NodeId>> {
        let native = self.native_node(reference)?;
        let mut matches = native
            .get("relations")?
            .as_map()?
            .iter()
            .filter(|(_, relation)| text(relation, "kind") == Some(kind));
        let (key, edge) = matches.next()?;
        let targets = edge.get("targets")?.as_seq()?;
        let total = nonnegative(edge.get("total"))?;
        let positions = edge.get("positions")?.as_seq()?;
        if matches.next().is_some()
            || self.overlapping(reference, &["relations", key], allow_prefix)
            || !matches!(
                text(edge, "evidence").unwrap_or("reported"),
                "reported" | "verified"
            )
            || text(edge, "enumeration").unwrap_or("enumerated") != "enumerated"
            || edge
                .get("offset")
                .is_some_and(|offset| *offset != Value::Null && !number_equal(Some(offset), 0))
            || if allow_prefix {
                total < targets.len()
            } else {
                total != targets.len()
            }
            || positions.len() != targets.len()
            || !positions
                .iter()
                .enumerate()
                .all(|(index, item)| number_equal(Some(item), index))
        {
            return None;
        }
        let mut unique = HashSet::new();
        targets
            .iter()
            .map(|target| {
                let id = *self.aliases.get(target.as_str()?)?;
                unique.insert(id).then_some(id)
            })
            .collect()
    }

    fn children_match(&self, reference: NodeId, allow_prefix: bool) -> bool {
        self.node(reference).is_some_and(|node| {
            self.relation(reference, "children", allow_prefix)
                .as_deref()
                == Some(&node.children)
        })
    }

    fn actions(&self, references: impl IntoIterator<Item = NodeId>) -> Vec<NodeId> {
        let mut seen = HashSet::new();
        references
            .into_iter()
            .filter_map(|id| self.atom(id))
            .flat_map(|atom| atom.actions.iter().map(|action| action.source))
            .filter(|reference| seen.insert(*reference))
            .collect()
    }

    fn descendants(&self, reference: NodeId) -> Option<Vec<NodeId>> {
        let mut result = Vec::new();
        let mut seen = HashSet::from([reference]);
        let mut pending: Vec<_> = self
            .node(reference)?
            .children
            .iter()
            .rev()
            .map(|&child| (child, reference))
            .collect();
        while let Some((child, parent)) = pending.pop() {
            if !seen.insert(child)
                || self.atom(child)?.parent != Some(parent)
                || self.scope(child) != self.scope(reference)
                || self.interface.projected_parents.get(&child) != Some(&parent)
            {
                return None;
            }
            result.push(child);
            pending.extend(
                self.node(child)?
                    .children
                    .iter()
                    .rev()
                    .map(|&member| (member, child)),
            );
        }
        Some(result)
    }

    fn unread_structure(&self, reference: NodeId) -> bool {
        let mut pending = vec![reference];
        let mut seen = HashSet::new();
        while let Some(current) = pending.pop() {
            if !seen.insert(current) || self.atom(current).is_none() {
                continue;
            }
            if let Some(node) = self.node(current) {
                pending.extend(&node.children);
            }
            let Some(token) = self.original_tokens.get(&current) else {
                continue;
            };
            if self.issues_by_node.get(token).is_some_and(|issues| {
                issues.iter().any(|issue| {
                    matches!(text(issue, "state"), Some("timeout" | "not_read"))
                        && match items(issue.get("path")) {
                            [_, _] => true,
                            [_, _, field, ..] => {
                                matches!(field.as_str(), Some("role" | "structure" | "relations"))
                            }
                            _ => false,
                        }
                })
            }) {
                return true;
            }
        }
        false
    }
}

fn cell(source: &Source<'_>, reference: NodeId, row_ref: NodeId) -> Option<TableCellPlan> {
    let native = source.native_node(reference)?;
    if !matches!(text(native, "role"), Some("cell" | "column_header"))
        || !source.clean(reference, &["role"])
    {
        return None;
    }
    let structure = native.get("structure")?;
    let atom = source.atom(reference)?;
    if atom.parent != Some(row_ref) || source.scope(reference) != source.scope(row_ref) {
        return None;
    }
    if ["row_index", "column_index", "row_span", "column_span"]
        .into_iter()
        .any(|field| !source.clean(reference, &["structure", field]))
    {
        return None;
    }
    let row = nonnegative(structure.get("row_index"))?;
    let column = nonnegative(structure.get("column_index"))?;
    let row_span = nonnegative(structure.get("row_span")).filter(|&span| span > 0)?;
    let column_span = nonnegative(structure.get("column_span")).filter(|&span| span > 0)?;
    let node = source.node(reference)?;
    for (key, position, span) in [
        ("rowRange", row, row_span),
        ("columnRange", column, column_span),
    ] {
        let value = node.field(key)?;
        if !number_equal(value.get("location"), position)
            || !number_equal(value.get("length"), span)
        {
            return None;
        }
    }
    let descendants = source.descendants(reference)?;
    let members = std::iter::once(reference).chain(descendants.iter().copied());
    let action_refs = source.actions(members.clone());
    let partial = members
        .filter_map(|id| source.atom(id))
        .any(|atom| atom.coverage.partial());
    let inline_chain = presentation::passive_cell_chain(source, reference, &descendants);
    Some(TableCellPlan {
        reference,
        row,
        column,
        row_span,
        column_span,
        children: node.children.clone(),
        descendant_refs: descendants,
        action_refs,
        partial,
        inline_chain,
    })
}

fn row(source: &Source<'_>, reference: NodeId, table: NodeId) -> Option<TableRowPlan> {
    let native = source.native_node(reference)?;
    if text(native, "role") != Some("row") || !source.clean(reference, &["role"]) {
        return None;
    }
    let atom = source.atom(reference)?;
    if atom.parent != Some(table)
        || source.scope(reference) != source.scope(table)
        || !source.children_match(reference, false)
    {
        return None;
    }
    let children = &source.node(reference)?.children;
    let cells = children
        .iter()
        .map(|&child| cell(source, child, reference))
        .collect::<Option<Vec<_>>>()?;
    let first = cells.first()?;
    if cells.iter().any(|cell| cell.row != first.row)
        || cells.windows(2).any(|pair| pair[0].column > pair[1].column)
    {
        return None;
    }
    let position = |field| {
        source
            .clean(reference, &["structure", field])
            .then(|| native.get("structure")?.get(field))
            .flatten()
            .and_then(|value| nonnegative(Some(value)))
    };
    let partial = atom.coverage.partial() || cells.iter().any(|cell| cell.partial);
    Some(TableRowPlan {
        reference,
        index: position("index"),
        outline_level: position("level"),
        cells,
        action_refs: source.actions([reference]),
        partial,
    })
}

fn aligned(rows: &[TableRowPlan]) -> bool {
    let columns = || rows[0].cells.iter().map(|cell| cell.column);
    rows.iter().all(|row| {
        row.cells.iter().map(|cell| cell.column).eq(columns())
            && row
                .cells
                .iter()
                .all(|cell| cell.row_span == 1 && cell.column_span == 1)
    })
}

fn compact_cells(source: &Source<'_>, rows: &[TableRowPlan]) -> bool {
    if !aligned(rows) {
        return false;
    }
    for row in rows {
        let mut total = 0usize;
        for cell in &row.cells {
            if cell.partial || cell.descendant_refs.len() > 6 {
                return false;
            }
            let mut size = 0usize;
            for reference in
                std::iter::once(cell.reference).chain(cell.descendant_refs.iter().copied())
            {
                let Some(atom) = source.atom(reference) else {
                    return false;
                };
                for slot in &atom.slots {
                    if let Some(value) = slot.value.as_str() {
                        if value.contains(['\r', '\n']) {
                            return false;
                        }
                        size += value.chars().count();
                    }
                }
                size += atom.actions.len() * 24 + atom.states.len() * 12 + 12;
            }
            if size > 320 {
                return false;
            }
            total += size;
        }
        if total > 640 {
            return false;
        }
    }
    true
}

fn matches_names(source: &Source<'_>, ids: &[NodeId], names: &[String]) -> bool {
    ids.iter()
        .map(|&id| source.node(id).map(|node| node.reference.as_str()))
        .eq(names.iter().map(|name| Some(name.as_str())))
}

fn extent(position: usize, length: usize) -> u128 {
    position as u128 + length as u128
}

fn overlaps(left: &TableCellPlan, right: &TableCellPlan) -> bool {
    (left.row as u128) < extent(right.row, right.row_span)
        && (right.row as u128) < extent(left.row, left.row_span)
        && (left.column as u128) < extent(right.column, right.column_span)
        && (right.column as u128) < extent(left.column, left.column_span)
}

fn columns(
    source: &Source<'_>,
    name: &str,
    rows: &[NodeId],
    grids: &[NativeTableGrid],
) -> Vec<TableColumnPlan> {
    let mut matching = grids.iter().filter(|grid| grid.reference == name);
    let Some(grid) = matching.next() else {
        return Vec::new();
    };
    if matching.next().is_some()
        || !rows
            .iter()
            .map(|&id| source.node(id).map(|node| node.reference.as_str()))
            .eq(grid.rows.iter().map(|row| Some(row.reference.as_str())))
    {
        return Vec::new();
    }
    grid.columns
        .iter()
        .filter_map(|column| {
            let reference = source.projection.by_reference(&column.reference)?.id;
            source.atom(reference)?;
            let index = usize::try_from(column.column.checked_sub(1)?).ok()?;
            let cell_refs = column
                .targets
                .iter()
                .map(|name| source.projection.by_reference(name).map(|node| node.id))
                .collect::<Option<_>>()?;
            Some(TableColumnPlan {
                reference,
                index,
                cell_refs,
                action_refs: source.actions([reference]),
            })
        })
        .collect()
}

fn table(
    source: &Source<'_>,
    reference: NodeId,
    outlines: &HashMap<String, TableRowCollection>,
    grids: &[NativeTableGrid],
) -> Selection<TablePlan> {
    const IDENTITY: &str = "logical table identity or source scope is unavailable";
    let native = source.native_node(reference).ok_or(IDENTITY)?;
    let role_name = text(native, "role")
        .filter(|role| matches!(*role, "table" | "tree"))
        .ok_or(IDENTITY)?;
    let scope = source
        .scope(reference)
        .and_then(|id| source.node(id))
        .ok_or(IDENTITY)?;
    let node = source.node(reference).ok_or(IDENTITY)?;
    if !source.clean(reference, &["role"]) {
        return Err(IDENTITY);
    }
    if !source.children_match(reference, true) {
        return Err("captured native children differ from the projected table");
    }
    let children = &node.children;
    let row_refs: Vec<_> = children
        .iter()
        .copied()
        .filter(|&child| source.node(child).is_some_and(|node| role(node) == "row"))
        .collect();
    if row_refs.is_empty() {
        return Err("no captured native rows");
    }
    if role_name == "tree" {
        let valid = if let Some(proof) = outlines.get(&node.reference) {
            matches_names(source, &row_refs, &proof.rows)
                && matches_names(source, children, &proof.children)
        } else {
            source.relation(reference, "rows", true).as_deref() == Some(row_refs.as_slice())
                && children
                    .iter()
                    .copied()
                    .filter(|&child| {
                        source
                            .native_node(child)
                            .and_then(|node| text(node, "role"))
                            == Some("row")
                    })
                    .eq(row_refs.iter().copied())
        };
        if !valid {
            return Err("outline row membership is unconfirmed");
        }
    }
    let mut rows: Vec<TableRowPlan> = Vec::new();
    for &row_ref in &row_refs {
        let Some(row) = row(source, row_ref, reference) else {
            if !source.unread_structure(row_ref) {
                return Err("a captured row has conflicting or unavailable cell structure");
            }
            break;
        };
        if row.partial && rows.first().is_some_and(|row| !row.partial) {
            break;
        }
        rows.push(row);
    }
    if rows.is_empty() {
        return Err("no complete initial row has available cell structure");
    }
    let planned: Vec<_> = rows.iter().map(|row| row.reference).collect();
    if rows
        .iter()
        .map(|row| row.cells[0].row)
        .collect::<HashSet<_>>()
        .len()
        != rows.len()
    {
        return Err("multiple original rows claim the same logical row");
    }
    let consumed: Vec<_> = rows
        .iter()
        .flat_map(|row| {
            std::iter::once(row.reference).chain(row.cells.iter().flat_map(|cell| {
                std::iter::once(cell.reference).chain(cell.descendant_refs.iter().copied())
            }))
        })
        .collect();
    if consumed.iter().collect::<HashSet<_>>().len() != consumed.len() {
        return Err("row content has repeated projected identities");
    }
    let cells: Vec<_> = rows.iter().flat_map(|row| &row.cells).collect();
    if cells
        .iter()
        .enumerate()
        .any(|(index, left)| cells[index + 1..].iter().any(|right| overlaps(left, right)))
    {
        return Err("reported logical cell extents overlap");
    }
    let columns = columns(source, &node.reference, &planned, grids);
    let count = |field| {
        source
            .clean(reference, &["structure", field])
            .then(|| native.get("structure")?.get(field))
            .flatten()
            .and_then(|value| nonnegative(Some(value)))
    };
    let row_count = count("row_count");
    let column_count = count("column_count");
    if cells.iter().any(|cell| {
        row_count.is_some_and(|count| extent(cell.row, cell.row_span) > count as u128)
            || column_count
                .is_some_and(|count| extent(cell.column, cell.column_span) > count as u128)
    }) {
        return Err("cell extents exceed the reported logical table size");
    }
    let atom = source.atom(reference).ok_or(IDENTITY)?;
    let partial = atom.coverage.partial()
        || rows.len() < row_refs.len()
        || rows.iter().any(|row| row.partial)
        || row_count.is_some_and(|count| rows.len() < count);
    let layout = if !rows.iter().any(|row| row.partial) && compact_cells(source, &rows) {
        "table"
    } else {
        "rows"
    };
    let action_refs = source.actions(std::iter::once(reference).chain(consumed.iter().copied()));
    let mut plan = TablePlan {
        reference,
        kind: if role_name == "tree" {
            "outline"
        } else {
            "table"
        },
        scope: [
            text(source.native, "session").ok_or(IDENTITY)?.to_owned(),
            text(source.native, "id").ok_or(IDENTITY)?.to_owned(),
            scope.reference.clone(),
        ],
        rows,
        columns,
        remaining_refs: children
            .iter()
            .copied()
            .filter(|id| !planned.contains(id))
            .collect(),
        consumed_refs: consumed,
        action_refs,
        row_count,
        column_count,
        partial,
        layout,
        column_indices: Vec::new(),
        column_labels: Vec::new(),
        row_prefix: None,
        column_prefixes: Vec::new(),
        detail_fields: Vec::new(),
    };
    presentation::share_prefixes(&mut plan, source);
    presentation::select_outline_layout(&mut plan, source.atoms);
    Ok(plan)
}

pub(crate) fn prepare(
    interface: &GeometricPlan,
    projection: &Projection,
    atoms: &AtomPlan,
    native: &FactSnapshot,
) -> Result<CollectionDocument> {
    let source = Source::new(interface, projection, atoms, native);
    let refs: HashMap<_, _> = source
        .original_tokens
        .iter()
        .filter_map(|(&id, &token)| {
            projection
                .node(id)
                .map(|node| (node.reference.clone(), token.to_owned()))
        })
        .collect();
    let scopes: HashMap<_, _> = projection
        .source_order
        .iter()
        .filter_map(|&id| {
            let node = projection.node(id)?;
            let scope = projection.node(node.scope)?;
            Some((node.reference.clone(), scope.reference.clone()))
        })
        .collect();
    let outlines = capture_row_collections(&native.observation, &refs);
    let grids = capture_table_grids(&native.observation, &refs, &scopes);
    let visual: HashSet<_> = interface
        .geometric_patterns
        .iter()
        .filter(|plan| matches!(plan.kind, "grid" | "cards"))
        .map(|plan| plan.owner_ref)
        .collect();
    let mut document = CollectionDocument::default();
    for &reference in &interface.target_refs {
        if visual.contains(&reference)
            || source.atom(reference).is_none()
            || !source
                .node(reference)
                .is_some_and(|node| matches!(role(node), "table" | "outline"))
        {
            continue;
        }
        match table(&source, reference, &outlines, &grids) {
            Ok(plan) => {
                document.order.push(reference);
                document.tables.insert(reference, plan);
            }
            Err(reason) => document
                .rejected
                .push(CollectionRejection { reference, reason }),
        }
    }
    validate(&document)?;
    Ok(document)
}

fn column_refs(plan: &TablePlan, index: usize, single_span: bool) -> Vec<NodeId> {
    plan.rows
        .iter()
        .flat_map(|row| &row.cells)
        .filter(|cell| cell.column == index && (!single_span || cell.column_span == 1))
        .map(|cell| cell.reference)
        .collect()
}

fn validate_prefix(prefix: Option<&CollectionPrefixPlan>, references: &[NodeId]) -> Result<()> {
    let Some(prefix) = prefix else {
        return Ok(());
    };
    if prefix.member_refs != references
        || !prefix
            .members
            .iter()
            .map(|member| member.reference)
            .eq(references.iter().copied())
    {
        return Err(CollectionError(
            "A collection prefix changed its original member sequence",
        ));
    }
    let keys: Vec<_> = prefix.slots.iter().map(|slot| slot.key.as_str()).collect();
    if references.len() < 2
        || references.iter().collect::<HashSet<_>>().len() != references.len()
        || keys.iter().collect::<HashSet<_>>().len() != keys.len()
    {
        return Err(CollectionError(
            "A collection prefix lacks distinct members or fields",
        ));
    }
    for member in &prefix.members {
        if member.state_indices.len() != prefix.states.len()
            || member.state_indices.iter().collect::<HashSet<_>>().len()
                != member.state_indices.len()
            || !member
                .slot_keys
                .iter()
                .map(String::as_str)
                .eq(keys.iter().copied())
        {
            return Err(CollectionError(
                "A collection prefix has inconsistent member field destinations",
            ));
        }
    }
    Ok(())
}

fn validate(document: &CollectionDocument) -> Result<()> {
    const DETAIL: CollectionError =
        CollectionError("An outline detail field lacks its original structural source");
    for &reference in &document.order {
        let plan = document.tables.get(&reference).ok_or(CollectionError(
            "A table plan lacks its original identity or rows",
        ))?;
        if plan.reference != reference || plan.rows.is_empty() {
            return Err(CollectionError(
                "A table plan lacks its original identity or rows",
            ));
        }
        if matches!(plan.layout, "table" | "outline") && !aligned(&plan.rows) {
            return Err(CollectionError(
                "A compact collection requires aligned unmerged rows",
            ));
        }
        let mut members = Vec::new();
        for row in &plan.rows {
            if row
                .cells
                .first()
                .is_none_or(|first| row.cells.iter().any(|cell| cell.row != first.row))
            {
                return Err(CollectionError(
                    "A logical row has inconsistent cell coordinates",
                ));
            }
            members.push(row.reference);
            for cell in &row.cells {
                if cell.row_span == 0 || cell.column_span == 0 {
                    return Err(CollectionError(
                        "A cell lacks a valid original logical extent",
                    ));
                }
                if cell
                    .children
                    .iter()
                    .any(|child| !cell.descendant_refs.contains(child))
                {
                    return Err(CollectionError(
                        "A cell lost one of its original content branches",
                    ));
                }
                let original =
                    std::iter::once(cell.reference).chain(cell.descendant_refs.iter().copied());
                if !cell.inline_chain.is_empty()
                    && !cell.inline_chain.iter().copied().eq(original.clone())
                {
                    return Err(CollectionError(
                        "An inline cell chain changed its original member sequence",
                    ));
                }
                members.extend(original);
            }
        }
        let member_set: HashSet<_> = members.iter().copied().collect();
        if members != plan.consumed_refs || member_set.len() != members.len() {
            return Err(CollectionError(
                "A table layout lost or duplicated an original member",
            ));
        }
        if member_set.contains(&plan.reference)
            || plan
                .remaining_refs
                .iter()
                .any(|reference| member_set.contains(reference))
        {
            return Err(CollectionError(
                "A table layout consumed an unrelated or parent object",
            ));
        }
        let mut seen_details = HashSet::new();
        if !plan.detail_fields.is_empty()
            && (plan.layout != "outline"
                || plan.kind != "outline"
                || plan.column_indices != [0]
                || plan.partial)
        {
            return Err(DETAIL);
        }
        for field in &plan.detail_fields {
            let key = field.slot.key.as_str();
            if !seen_details.insert((field.reference, key))
                || !member_set.contains(&field.reference)
                || !matches!(
                    key,
                    "index" | "disclosureLevel" | "rowRange" | "columnRange"
                )
                || field.slot.facts.is_empty()
                || field.slot.facts.iter().any(|fact| {
                    fact.key.source != field.reference
                        || fact.key.attribute != key
                        || !presentation::typed_equal(&fact.value, &field.slot.value)
                })
            {
                return Err(DETAIL);
            }
        }
        let indices: BTreeSet<_> = plan
            .rows
            .iter()
            .flat_map(|row| row.cells.iter().map(|cell| cell.column))
            .collect();
        if plan.column_indices.len() != plan.column_labels.len()
            || plan.column_indices.len() != plan.column_prefixes.len()
            || !plan.column_indices.iter().copied().eq(indices)
        {
            return Err(CollectionError(
                "A table has inconsistent column prefix slots",
            ));
        }
        validate_prefix(
            plan.row_prefix.as_ref(),
            &plan
                .rows
                .iter()
                .map(|row| row.reference)
                .collect::<Vec<_>>(),
        )?;
        for (&index, prefix) in plan.column_indices.iter().zip(&plan.column_prefixes) {
            validate_prefix(prefix.as_ref(), &column_refs(plan, index, false))?;
        }
        for column in &plan.columns {
            if column_refs(plan, column.index, true) != column.cell_refs
                || !plan.remaining_refs.contains(&column.reference)
            {
                return Err(CollectionError(
                    "A native column no longer matches its captured cell sequence",
                ));
            }
        }
    }
    Ok(())
}

#[cfg(test)]
mod tests;
