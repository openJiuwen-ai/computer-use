// Copyright (c) Huawei Technologies Co., Ltd. 2026. All rights reserved.

//! Common structural headers and exact source-backed outline detail slots.

use super::*;

fn numeric(value: &Value) -> Option<f64> {
    match value {
        Value::Bool(value) => Some(u8::from(*value).into()),
        Value::Int(value) => Some(*value as f64),
        Value::BigInt(value) => value.parse().ok(),
        Value::Float(value) => Some(*value),
        _ => None,
    }
}

fn integer_text(value: &Value) -> Option<String> {
    match value {
        Value::Int(value) => Some(value.to_string()),
        Value::BigInt(value) => Some(value.clone()),
        Value::Bool(value) => Some(u8::from(*value).to_string()),
        _ => None,
    }
}

fn values_equal(left: &Value, right: &Value) -> bool {
    match (left, right) {
        (Value::Map(left), Value::Map(right)) => {
            left.len() == right.len()
                && left.iter().all(|(key, value)| {
                    right
                        .iter()
                        .find(|(candidate, _)| candidate == key)
                        .is_some_and(|(_, other)| values_equal(value, other))
                })
        }
        (Value::Seq(left), Value::Seq(right)) => {
            left.len() == right.len() && left.iter().zip(right).all(|(a, b)| values_equal(a, b))
        }
        (Value::Float(left), Value::Float(right)) => left == right,
        (Value::Float(floating), integer) | (integer, Value::Float(floating))
            if integer_text(integer).is_some() =>
        {
            floating.is_finite()
                && floating.fract() == 0.0
                && numeric(integer) == Some(*floating)
                && integer_text(integer).is_some_and(|integer| {
                    integer == format!("{floating:.0}") || integer == "0" && *floating == 0.0
                })
        }
        _ => {
            left == right
                || matches!((integer_text(left), integer_text(right)), (Some(left), Some(right)) if left == right)
        }
    }
}

pub(super) fn typed_equal(left: &Value, right: &Value) -> bool {
    let same_type = std::mem::discriminant(left) == std::mem::discriminant(right)
        || matches!(
            (left, right),
            (Value::Int(_), Value::BigInt(_)) | (Value::BigInt(_), Value::Int(_))
        );
    same_type && values_equal(left, right)
}

fn atom_facts<'a>(atom: &'a Atom, slot: &'a str) -> impl Iterator<Item = &'a AtomFact> + 'a {
    atom.dispositions.iter().filter_map(move |disposition| {
        (disposition.kind == DispositionKind::Inline
            && disposition.slot == slot
            && disposition.target == atom.id)
            .then_some(&disposition.fact)
    })
}

fn facts_match<'a>(
    left: impl Iterator<Item = &'a AtomFact>,
    right: impl Iterator<Item = &'a AtomFact>,
) -> bool {
    let mut left = left;
    let mut right = right;
    loop {
        match (left.next(), right.next()) {
            (None, None) => return true,
            (Some(a), Some(b))
                if a.key.attribute == b.key.attribute && typed_equal(&a.value, &b.value) =>
            {
                ()
            }
            _ => return false,
        }
    }
}

fn slot<'a>(atom: &'a Atom, key: &str) -> Option<&'a Slot> {
    atom.slots.iter().find(|slot| slot.key == key)
}

fn shared_prefix(
    atoms: &AtomPlan,
    refs: Vec<NodeId>,
    structural: &[&str],
) -> Option<CollectionPrefixPlan> {
    if refs.len() < 2 || refs.iter().collect::<HashSet<_>>().len() != refs.len() {
        return None;
    }
    let atoms = refs
        .iter()
        .map(|reference| atoms.atoms.get(reference))
        .collect::<Option<Vec<_>>>()?;
    let first = atoms[0];
    if atoms[1..].iter().any(|atom| {
        atom.role != first.role
            || atom.source_description != first.source_description
            || !facts_match(atom_facts(first, "role"), atom_facts(atom, "role"))
    }) {
        return None;
    }
    let mut role_facts = Vec::new();
    for atom in &atoms {
        role_facts.extend(atom_facts(atom, "role").cloned());
    }
    let mut indices = vec![Vec::new(); atoms.len()];
    let mut states = Vec::new();
    let mut state_facts = Vec::new();
    for (first_index, label) in first.states.iter().enumerate() {
        let tag = format!("state:{label}");
        let mut selected = vec![first_index];
        for (position, atom) in atoms.iter().enumerate().skip(1) {
            let Some(index) = atom.states.iter().enumerate().find_map(|(index, state)| {
                (state == label
                    && !indices[position].contains(&index)
                    && facts_match(atom_facts(atom, &tag), atom_facts(first, &tag)))
                .then_some(index)
            }) else {
                break;
            };
            selected.push(index);
        }
        if selected.len() != atoms.len() {
            continue;
        }
        states.push(label.clone());
        let mut facts = Vec::new();
        for (atom, (indices, index)) in atoms.iter().zip(indices.iter_mut().zip(selected)) {
            indices.push(index);
            facts.extend(atom_facts(atom, &tag).cloned());
        }
        state_facts.push(facts);
    }
    let mut slots = Vec::new();
    for key in structural {
        let Some(candidates) = atoms
            .iter()
            .map(|atom| {
                slot(atom, key).filter(|slot| {
                    !slot.has_preview()
                        && slot.protection.is_none()
                        && matches!(slot.format, Format::Span | Format::Number)
                })
            })
            .collect::<Option<Vec<_>>>()
        else {
            continue;
        };
        let first = candidates[0];
        if candidates[1..].iter().any(|slot| {
            slot.label != first.label
                || slot.format != first.format
                || slot.placement != first.placement
                || !typed_equal(&slot.value, &first.value)
                || !facts_match(slot.facts.iter(), first.facts.iter())
        }) {
            continue;
        }
        let mut shared = first.clone();
        shared.facts = candidates
            .iter()
            .flat_map(|slot| slot.facts.iter().cloned())
            .collect();
        slots.push(shared);
    }
    let slot_keys: Vec<_> = slots.iter().map(|slot| slot.key.clone()).collect();
    let members = refs
        .iter()
        .copied()
        .zip(indices)
        .map(|(reference, state_indices)| CollectionMemberPrefix {
            reference,
            state_indices,
            slot_keys: slot_keys.clone(),
        })
        .collect();
    Some(CollectionPrefixPlan {
        member_refs: refs,
        role: first.role.clone(),
        source_description: first.source_description,
        role_facts,
        states,
        state_facts,
        slots,
        members,
    })
}

pub(super) fn passive_cell_chain(
    source: &Source<'_>,
    reference: NodeId,
    descendants: &[NodeId],
) -> Vec<NodeId> {
    let rejected = Vec::new();
    if !(1..=3).contains(&descendants.len()) {
        return rejected;
    }
    let mut previous = reference;
    for &child in descendants {
        let (Some(atom), Some(node), Some(native), Some(parent)) = (
            source.atom(child),
            source.node(child),
            source.native_node(child),
            source.node(previous),
        ) else {
            return rejected;
        };
        if parent.children != [child]
            || !matches!(text(native, "role"), Some("static_text" | "image"))
            || native.get("actions").and_then(Value::as_seq) != Some(&[])
            || !atom.actions.is_empty()
            || atom.actions_unavailable
            || atom.coverage.partial()
            || node
                .field("capabilities")
                .and_then(Value::as_map)
                .is_some_and(|caps| {
                    caps.iter()
                        .any(|(_, value)| value.as_str() == Some("available"))
                })
            || atom.slots.iter().any(|slot| {
                slot.value
                    .as_str()
                    .is_some_and(|value| value.contains(['\r', '\n']))
            })
        {
            return rejected;
        }
        previous = child;
    }
    if source
        .node(previous)
        .is_none_or(|node| !node.children.is_empty())
    {
        return rejected;
    }
    std::iter::once(reference)
        .chain(descendants.iter().copied())
        .collect()
}

pub(super) fn share_prefixes(plan: &mut TablePlan, source: &Source<'_>) {
    let indices: BTreeSet<_> = plan
        .rows
        .iter()
        .flat_map(|row| row.cells.iter().map(|cell| cell.column))
        .collect();
    let mut prefixes = Vec::new();
    let mut labels = Vec::new();
    for &index in &indices {
        let cells: Vec<_> = plan
            .rows
            .iter()
            .filter_map(|row| row.cells.iter().find(|cell| cell.column == index))
            .collect();
        let prefix = if cells.len() == plan.rows.len()
            && cells
                .iter()
                .all(|cell| cell.row_span == 1 && cell.column_span == 1)
        {
            shared_prefix(
                source.atoms,
                cells.iter().map(|cell| cell.reference).collect(),
                &["columnRange"],
            )
        } else {
            None
        };
        prefixes.push(prefix);
        let mut names = plan
            .columns
            .iter()
            .filter(|column| column.index == index)
            .filter_map(|column| {
                let name = text(source.native_node(column.reference)?, "name")
                    .filter(|name| !name.is_empty())?;
                let token = source.original_tokens.get(&column.reference)?;
                let path = ["nodes", token, "name"];
                let unavailable = source
                    .global_issues
                    .iter()
                    .copied()
                    .chain(
                        source
                            .issues_by_node
                            .get(token)
                            .into_iter()
                            .flatten()
                            .copied(),
                    )
                    .any(|issue| path_prefix(items(issue.get("path")), &path));
                (!unavailable).then_some(name)
            });
        let name = names
            .next()
            .filter(|_| names.next().is_none())
            .map(str::to_owned);
        labels.push(name);
    }
    plan.column_indices = indices.into_iter().collect();
    plan.column_labels = labels;
    plan.column_prefixes = prefixes;
    plan.row_prefix = shared_prefix(
        source.atoms,
        plan.rows.iter().map(|row| row.reference).collect(),
        &["disclosureLevel"],
    );
}

fn number(value: usize) -> Value {
    i64::try_from(value)
        .map(Value::Int)
        .unwrap_or_else(|_| Value::BigInt(value.to_string()))
}

fn retain_detail(
    atoms: &AtomPlan,
    fields: &mut Vec<CollectionDetailField>,
    reference: NodeId,
    key: &str,
    value: &Value,
) {
    let Some(slot) = atoms.atoms.get(&reference).and_then(|atom| slot(atom, key)) else {
        return;
    };
    if typed_equal(&slot.value, value)
        && !slot.facts.is_empty()
        && slot.facts.iter().all(|fact| {
            fact.key.source == reference
                && fact.key.attribute == key
                && typed_equal(&fact.value, value)
        })
    {
        fields.push(CollectionDetailField {
            reference,
            slot: slot.clone(),
        });
    }
}

pub(super) fn select_outline_layout(plan: &mut TablePlan, atoms: &AtomPlan) {
    if plan.kind != "outline" || plan.layout != "table" || plan.partial || plan.column_indices != [0]
        || !matches!(plan.column_count, None | Some(1)) || plan.rows.iter().any(|row| {
            !matches!(row.cells.as_slice(), [cell] if cell.row_span == 1 && cell.column_span == 1)
        }) { return; }
    let levels: HashSet<_> = plan
        .rows
        .iter()
        .filter_map(|row| row.outline_level)
        .collect();
    let mut fields = Vec::new();
    for row in &plan.rows {
        if let Some(index) = row.index {
            retain_detail(atoms, &mut fields, row.reference, "index", &number(index));
        }
        if let Some(level) = row.outline_level.filter(|_| levels.len() == 1) {
            retain_detail(
                atoms,
                &mut fields,
                row.reference,
                "disclosureLevel",
                &number(level),
            );
        }
        let cell = &row.cells[0];
        for (key, location, length) in [
            ("rowRange", cell.row, cell.row_span),
            ("columnRange", cell.column, cell.column_span),
        ] {
            let Some(slot) = atoms
                .atoms
                .get(&cell.reference)
                .and_then(|atom| slot(atom, key))
            else {
                continue;
            };
            if slot.value.as_map().is_some_and(|map| map.len() == 2)
                && number_equal(slot.value.get("location"), location)
                && number_equal(slot.value.get("length"), length)
            {
                retain_detail(atoms, &mut fields, cell.reference, key, &slot.value);
            }
        }
    }
    if fields.is_empty() {
        return;
    }
    let deferred: HashSet<_> = fields
        .iter()
        .map(|item| (item.reference, item.slot.key.as_str()))
        .collect();
    for prefix in std::iter::once(&mut plan.row_prefix)
        .chain(&mut plan.column_prefixes)
        .flatten()
    {
        prefix.slots.retain(|slot| {
            !prefix
                .member_refs
                .iter()
                .all(|id| deferred.contains(&(*id, slot.key.as_str())))
        });
        let keys: Vec<_> = prefix.slots.iter().map(|slot| slot.key.clone()).collect();
        for member in &mut prefix.members {
            member.slot_keys.clone_from(&keys);
        }
    }
    plan.layout = "outline";
    plan.detail_fields = fields;
}
