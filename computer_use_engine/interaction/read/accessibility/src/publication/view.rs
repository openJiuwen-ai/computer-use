// Copyright (c) Huawei Technologies Co., Ltd. 2026. All rights reserved.

//! Narrow terminal facts needed by the public document composer.

use std::collections::{HashMap, HashSet};

use super::menu::{seq, text};
use crate::{
    capture::PreparedCapture,
    compact::CompactRoot,
    content::MemberMode,
    facts::FactSnapshot,
    render::{atoms::ClaimKind, RenderedPart, RenderedSnapshot},
    value::Value,
};

pub(crate) struct Coverage<'a> {
    pub(crate) parents: Vec<(&'a str, Option<&'a str>)>,
    pub(crate) complete: Vec<&'a str>,
}

pub(crate) fn delivery_coverage<'a>(
    snapshot: &'a FactSnapshot,
    semantic: &'a Value,
) -> Coverage<'a> {
    let mut parents = Vec::new();
    let mut pending: Vec<_> = seq(semantic, "roots")
        .iter()
        .map(|root| (root, None))
        .collect();
    while let Some((node, parent)) = pending.pop() {
        let Some(reference) = text(node, "ref") else {
            continue;
        };
        parents.push((reference, parent));
        pending.extend(
            seq(node, "children")
                .iter()
                .map(|child| (child, Some(reference))),
        );
    }
    let aliases: HashMap<_, _> = semantic
        .get("refs")
        .and_then(Value::as_map)
        .into_iter()
        .flatten()
        .filter_map(|(reference, token)| Some((token.as_str()?, reference.as_str())))
        .collect();
    let mut children: HashMap<&str, Option<&Value>> = HashMap::new();
    for relation in seq(&snapshot.facts, "relations") {
        if text(relation, "kind") == Some("children")
            && text(relation, "certainty") == Some("confirmed")
        {
            if let Some(source) = text(relation, "source") {
                children
                    .entry(source)
                    .and_modify(|row| *row = None)
                    .or_insert(Some(relation));
            }
        }
    }
    let unread: HashSet<_> = seq(&snapshot.facts, "unread")
        .iter()
        .filter_map(Value::as_str)
        .collect();
    let mut complete: Vec<_> = children
        .into_iter()
        .filter_map(|(source, relation)| {
            let relation = relation?;
            let targets = seq(relation, "targets");
            if text(relation, "state") != Some("complete")
                || relation
                    .get("offset")
                    .is_some_and(|value| value.as_i64() != Some(0))
                || relation
                    .get("total")
                    .is_some_and(|value| value.as_i64() != Some(targets.len() as i64))
                || targets
                    .iter()
                    .filter_map(Value::as_str)
                    .any(|target| unread.contains(target))
            {
                return None;
            }
            aliases.get(source).copied()
        })
        .collect();
    complete.sort_unstable();
    Coverage { parents, complete }
}

pub(crate) fn title_evidence<'a>(
    capture: &'a PreparedCapture,
    root: &'a CompactRoot,
    rendered: &RenderedSnapshot,
) -> Option<(&'a str, &'a str, &'a str)> {
    let native = &capture.capture.snapshot.observation;
    let id = root.projection.root;
    let node = root.projection.node(id)?;
    let reference = node.reference.as_str();
    let token = capture.capture.tokens().get(id.0)?.as_str();
    let [native_root] = seq(native, "roots") else {
        return None;
    };
    let original = native.get("nodes")?.get(token)?;
    let name = text(original, "name")?;
    if native_root.as_str() != Some(token)
        || !matches!(text(original, "role"), Some("window" | "dialog" | "sheet"))
        || node.parent.is_some()
        || node.field("name").and_then(Value::as_str) != Some(name)
    {
        return None;
    }
    for issue in seq(native, "issues") {
        let path = seq(issue, "path");
        if path.len() < 2 || path[0].as_str() != Some("nodes") {
            if !(path.len() == 1
                && path[0].as_str() == Some("nodes")
                && text(issue, "state") == Some("partial"))
            {
                return None;
            }
        } else if path[1].as_str() == Some(token)
            && (path.len() == 2 || matches!(path[2].as_str(), Some("role" | "name")))
        {
            return None;
        }
    }
    let source = seq(&capture.semantic, "fields")
        .iter()
        .find(|row| text(row, "ref") == Some(reference) && text(row, "key") == Some("name"))?
        .get("value")?;
    let [path] = seq(source, "path") else {
        return None;
    };
    if path.as_str() != Some("name")
        || text(source, "node") != Some(token)
        || text(source, "representation") != Some("field")
        || text(source, "normalized") != Some(name)
    {
        return None;
    }
    let mut claims = root.atoms.atoms.get(&id)?.claims.as_slice();
    if root.fields.fields.iter().any(|plan| {
        plan.members
            .iter()
            .any(|member| member.source == id && member.mode != MemberMode::Complete)
    }) {
        claims = &[];
    }
    let emitted = claims
        .iter()
        .chain(root.fields.fields.iter().flat_map(|plan| &plan.claims))
        .any(|claim| {
            claim.fact.source == id
                && claim.fact.attribute == "name"
                && claim.target == id
                && claim.value.as_str() == Some(name)
                && matches!(claim.kind, ClaimKind::Display | ClaimKind::Equivalent)
        });
    if !emitted
        || root.disclosure.get(id, "name").is_some()
        || root.slices.get(id, "name").is_some()
        || root
            .sharing
            .omitted_slots
            .get(&id)
            .is_some_and(|slots| slots.contains("name"))
        || root.groups.groups.iter().any(|group| {
            group.projected_refs.contains(&id)
                && !group
                    .background
                    .as_ref()
                    .is_some_and(|background| background.important_refs.contains(&id))
        })
        || root
            .collections
            .tables
            .values()
            .flat_map(|table| &table.detail_fields)
            .any(|field| field.reference == id && field.slot.key == "name")
    {
        return None;
    }
    rendered
        .parts
        .iter()
        .filter_map(|part| match part {
            RenderedPart::Section(section) => Some(section),
            _ => None,
        })
        .flat_map(|section| &section.nodes)
        .any(|node| node.reference == reference && node.parent.is_none())
        .then_some((reference, token, name))
}

pub(crate) fn selected_click_target<'a>(
    snapshot: &'a FactSnapshot,
    target: &str,
) -> Option<&'a str> {
    let nodes = snapshot.observation.get("nodes")?;
    let (token, source) = nodes.as_map()?.iter().find(|(token, _)| token == target)?;
    let states = source.get("states");
    if states.and_then(|states| states.get("has_popup")) == Some(&Value::Bool(true)) {
        return None;
    }
    if matches!(text(source, "role"), Some("text_field" | "text" | "image")) {
        let mut parents = nodes.as_map()?.iter().filter(|(_, node)| {
            if !matches!(text(node, "role"), Some("cell" | "row")) {
                return false;
            }
            node.get("relations")
                .and_then(|relations| relations.get("children"))
                .is_some_and(|children| {
                    text(children, "evidence") != Some("candidate")
                        && seq(children, "targets")
                            .iter()
                            .any(|item| item.as_str() == Some(target))
                })
        });
        if let Some((parent, node)) = parents.next() {
            if parents.next().is_none() {
                if let Some(selected) = node
                    .get("states")
                    .and_then(|states| states.get("selected"))
                    .and_then(Value::as_bool)
                {
                    return (!selected).then_some(parent.as_str());
                }
            }
        }
    }
    (states
        .and_then(|states| states.get("selected"))
        .and_then(Value::as_bool)
        == Some(false))
    .then_some(token.as_str())
}

pub(crate) fn selected_copy_target(snapshot: &FactSnapshot) -> Option<&str> {
    let [root] = seq(&snapshot.observation, "roots") else {
        return None;
    };
    let token = root.as_str()?;
    let states = snapshot
        .observation
        .get("nodes")?
        .get(token)?
        .get("states")?;
    (states.get("selected").and_then(Value::as_bool) == Some(true)).then_some(token)
}

#[cfg(test)]
mod tests {
    use super::*;

    fn word(value: &str) -> Value {
        Value::Text(value.to_owned())
    }

    fn map<const N: usize>(items: [(&str, Value); N]) -> Value {
        Value::Map(
            items
                .into_iter()
                .map(|(key, value)| (key.to_owned(), value))
                .collect(),
        )
    }

    fn click_capture(selected: bool, evidence: &str) -> FactSnapshot {
        let source = map([("role", word("text"))]);
        let cell = map([
            ("role", word("cell")),
            ("states", map([("selected", Value::Bool(selected))])),
            (
                "relations",
                map([(
                    "children",
                    map([
                        ("evidence", word(evidence)),
                        ("targets", Value::Seq(vec![word("label")])),
                    ]),
                )]),
            ),
        ]);
        FactSnapshot {
            observation: map([
                ("nodes", map([("label", source), ("cell", cell)])),
                ("roots", Value::Seq(vec![word("cell")])),
            ]),
            facts: Value::Null,
            metadata: Value::Null,
            query: Value::Null,
        }
    }

    #[test]
    fn selection_targets_follow_the_original_row_evidence() {
        let before = click_capture(false, "reported");
        assert_eq!(selected_click_target(&before, "label"), Some("cell"));
        assert_eq!(selected_click_target(&before, "cell"), Some("cell"));
        assert_eq!(selected_copy_target(&before), None);
        let after = click_capture(true, "reported");
        assert_eq!(selected_click_target(&after, "label"), None);
        assert_eq!(selected_copy_target(&after), Some("cell"));
        assert_eq!(
            selected_click_target(&click_capture(false, "candidate"), "label"),
            None
        );
    }
}
