// Copyright (c) Huawei Technologies Co., Ltd. 2026. All rights reserved.

//! Original source evidence for reversible legacy line packing.

use std::collections::{HashMap, HashSet};

use crate::{
    diff::python_is_printable,
    facts::FactSnapshot,
    geometric::truth,
    line_layout::{
        capture_row_collections, capture_table_grids, ActionCatalogue, IdentifierSource,
        NativeFieldSource, NativeRoleSource, NativeTableGrid, TableEvidence, TableNodeEvidence,
        TextSliceSource,
    },
    projection::{Projection, ProjectionNode},
    render::{
        field,
        lines::{actions, LineOptions, LineSeed},
        text::{json, JsonFormat},
    },
    value::Value,
};

#[derive(Default)]
pub(super) struct PackingEvidence {
    pub(super) table: TableEvidence,
    pub(super) tables: Vec<NativeTableGrid>,
    pub(super) fields: Vec<(String, NativeFieldSource)>,
    pub(super) roles: Vec<(String, NativeRoleSource)>,
    pub(super) identifiers: HashMap<String, IdentifierSource>,
    pub(super) text: HashMap<String, TextSliceSource>,
}

fn text<'a>(value: &'a Value, key: &str) -> Option<&'a str> {
    value.get(key).and_then(Value::as_str)
}

fn seq(value: Option<&Value>) -> &[Value] {
    value.and_then(Value::as_seq).unwrap_or_default()
}

fn entries(value: Option<&Value>) -> &[(String, Value)] {
    value.and_then(Value::as_map).unwrap_or_default()
}

#[derive(Default)]
struct SourceIssues<'a> {
    root: bool,
    nodes: bool,
    local: HashMap<&'a str, Vec<&'a [Value]>>,
}

impl<'a> SourceIssues<'a> {
    fn collect(observation: &'a Value) -> Self {
        let mut result = Self::default();
        for issue in seq(observation.get("issues")) {
            let path = seq(issue.get("path"));
            match path {
                [] => result.root = true,
                [group]
                    if group.as_str() == Some("nodes")
                        && text(issue, "state") != Some("partial") =>
                {
                    result.nodes = true;
                }
                [group, token, local @ ..] if group.as_str() == Some("nodes") => {
                    if let Some(token) = token.as_str() {
                        result.local.entry(token).or_default().push(local);
                    }
                }
                _ => {}
            }
        }
        result
    }

    fn paths(&self, token: &str) -> &[&[Value]] {
        self.local.get(token).map(Vec::as_slice).unwrap_or_default()
    }

    fn complete(&self, token: &str, field: &[&str]) -> bool {
        !self.paths(token).iter().any(|path| {
            path.iter()
                .zip(field)
                .all(|(part, expected)| part.as_str() == Some(*expected))
        })
    }
}

fn native_scope(session: &str, capture: &str, scope: &str) -> [String; 3] {
    [session.to_owned(), capture.to_owned(), scope.to_owned()]
}

fn point(node: &ProjectionNode, name: &str) -> Option<u64> {
    let value = node.field(name)?;
    (value.get("length")?.as_i64() == Some(1)).then_some(())?;
    let location = u64::try_from(value.get("location")?.as_i64()?).ok()?;
    Some(location + 1)
}

fn table_node(projection: &Projection, node: &ProjectionNode) -> TableNodeEvidence {
    let nonnegative = |field| {
        node.field(field)
            .and_then(Value::as_i64)
            .and_then(|n| u64::try_from(n).ok())
    };
    let name = node.field("name");
    let raw_name = name.and_then(Value::as_str).map(str::to_owned);
    let formatted_name = raw_name.as_deref().map(field);
    TableNodeEvidence {
        index: nonnegative("index"),
        outline: nonnegative("disclosureLevel"),
        row: point(node, "rowRange"),
        column: point(node, "columnRange"),
        has_name: truth(name),
        raw_name,
        formatted_name,
        boundary_subrole: matches!(
            node.field("subrole").and_then(Value::as_str),
            Some("dialog" | "systemDialog" | "popover")
        ),
        native_children: node
            .native_children
            .iter()
            .map(|id| projection.node(*id).map(|child| child.reference.clone()))
            .collect(),
    }
}

fn plain_role_description(value: &str) -> bool {
    // Python's category-based check admits every space separator, while
    // str.isprintable admits only the ASCII space from that category.
    !value.is_empty()
        && value.chars().all(|ch| {
            python_is_printable(ch)
                || matches!(
                    ch,
                    '\u{a0}' | '\u{1680}' | '\u{2000}'
                        ..='\u{200a}' | '\u{202f}' | '\u{205f}' | '\u{3000}'
                )
        })
}

fn code_subtype(node: &Value, paths: &[&[Value]]) -> bool {
    if text(node, "role") != Some("group")
        || !matches!(node.get("role_detail"), None | Some(Value::Null))
    {
        return false;
    }
    let mut supplements = seq(node.get("supplements"))
        .iter()
        .enumerate()
        .filter(|(_, item)| text(item, "key") == Some("provider.role_detail"));
    let Some((index, item)) = supplements.next() else {
        return false;
    };
    text(item, "value") == Some("AXCodeStyleGroup")
        && supplements.next().is_none()
        && !paths.iter().any(|path| match path {
            [field] => field.as_str() == Some("supplements"),
            [field, key, ..] if field.as_str() == Some("supplements") => {
                key.as_i64().and_then(|i| usize::try_from(i).ok()) == Some(index)
                    || matches!(key.as_str(), Some("provider.role_detail" | "AXSubrole"))
            }
            _ => false,
        })
}

fn text_containment<'a>(
    native: &'a [(String, Value)],
    issues: &SourceIssues<'_>,
) -> (HashMap<&'a str, HashSet<&'a str>>, HashSet<&'a str>) {
    let mut parents = HashMap::<_, HashSet<_>>::new();
    let mut unreliable = HashSet::new();
    for (token, node) in native {
        for (key, relation) in entries(node.get("relations")) {
            if text(relation, "kind") != Some("children") {
                continue;
            }
            let targets = seq(relation.get("targets"));
            let valid = issues.complete(token, &["relations", key])
                && matches!(text(relation, "evidence"), Some("reported" | "verified"))
                && text(relation, "enumeration") == Some("enumerated")
                && matches!(
                    relation.get("offset"),
                    None | Some(Value::Null | Value::Int(0))
                )
                && (matches!(relation.get("total"), None | Some(Value::Null))
                    || relation.get("total").and_then(Value::as_i64)
                        == i64::try_from(targets.len()).ok())
                && match relation.get("positions") {
                    None | Some(Value::Null) => true,
                    Some(Value::Seq(positions)) => {
                        positions.len() == targets.len()
                            && positions.iter().enumerate().all(|(index, position)| {
                                position.as_i64() == i64::try_from(index).ok()
                            })
                    }
                    _ => false,
                }
                && targets
                    .iter()
                    .filter_map(Value::as_str)
                    .collect::<HashSet<_>>()
                    .len()
                    == targets.len();
            for target in targets.iter().filter_map(Value::as_str) {
                parents.entry(target).or_default().insert(token.as_str());
                if !valid {
                    unreliable.insert(target);
                }
            }
        }
    }
    (parents, unreliable)
}

fn text_parent<'a>(
    token: &str,
    node: &Value,
    parents: &HashMap<&'a str, HashSet<&'a str>>,
    unreliable: &HashSet<&str>,
    issues: &SourceIssues<'_>,
) -> Option<&'a str> {
    let possible = parents.get(token)?;
    if possible.len() != 1 || unreliable.contains(token) {
        return None;
    }
    let parent = possible.iter().next().copied()?;
    for (key, relation) in entries(node.get("relations")) {
        if text(relation, "kind") != Some("parent") {
            continue;
        }
        if !issues.complete(token, &["relations", key])
            || !matches!(text(relation, "evidence"), Some("reported" | "verified"))
            || !matches!(seq(relation.get("targets")), [target] if target.as_str() == Some(parent))
        {
            return None;
        }
    }
    Some(parent)
}

fn cut_text_cycles(sources: &mut HashMap<String, TextSliceSource>) {
    let mut checked = HashSet::new();
    let mut cyclic = HashSet::new();
    for reference in sources.keys() {
        let mut current = Some(reference.as_str());
        let mut path = Vec::new();
        let mut positions = HashMap::new();
        while let Some(reference) = current.filter(|reference| !checked.contains(reference)) {
            let Some(source) = sources.get(reference) else {
                break;
            };
            if let Some(index) = positions.insert(reference, path.len()) {
                cyclic.extend(path[index..].iter().map(|item: &&str| (*item).to_owned()));
                break;
            }
            path.push(reference);
            current = source.parent.as_deref();
        }
        checked.extend(path);
    }
    for (reference, source) in sources {
        if cyclic.contains(reference) || *reference == source.scope[2] {
            source.parent = None;
        }
    }
}

pub(super) fn collect(
    snapshot: &FactSnapshot,
    semantic: &Value,
    projection: &Projection,
    tokens: &[String],
) -> PackingEvidence {
    let observation = &snapshot.observation;
    let alias_rows = entries(semantic.get("refs"));
    let refs: HashMap<_, _> = alias_rows
        .iter()
        .filter_map(|(reference, token)| Some((reference.clone(), token.as_str()?.to_owned())))
        .collect();
    let scopes: HashMap<_, _> = projection
        .display_order
        .iter()
        .filter_map(|id| {
            let node = projection.node(*id)?;
            let token = refs.get(&node.reference)?;
            (tokens.get(id.0) == Some(token)).then_some(())?;
            Some((
                node.reference.clone(),
                projection.node(node.scope)?.reference.clone(),
            ))
        })
        .collect();
    let mut result = PackingEvidence {
        table: TableEvidence {
            facts: projection
                .display_order
                .iter()
                .filter_map(|id| {
                    let node = projection.node(*id)?;
                    Some((node.reference.clone(), table_node(projection, node)))
                })
                .collect(),
            row_collections: capture_row_collections(observation, &refs),
        },
        tables: capture_table_grids(observation, &refs, &scopes),
        ..PackingEvidence::default()
    };
    let (Some(session), Some(capture)) = (text(observation, "session"), text(observation, "id"))
    else {
        return result;
    };
    let native = entries(observation.get("nodes"));
    let index: HashMap<_, _> = native
        .iter()
        .map(|(token, node)| (token.as_str(), node))
        .collect();
    let issues = SourceIssues::collect(observation);
    let mut counts = HashMap::<_, usize>::new();
    for token in refs.values() {
        *counts.entry(token.as_str()).or_default() += 1;
    }
    let inverse: HashMap<_, _> = refs
        .iter()
        .filter(|(_, token)| counts[token.as_str()] == 1)
        .map(|(reference, token)| (token.as_str(), reference.as_str()))
        .collect();
    let (parents, unreliable) = if issues.root {
        (HashMap::new(), HashSet::new())
    } else {
        text_containment(native, &issues)
    };
    for (reference, token) in alias_rows {
        let (Some(token), Some(scope)) = (token.as_str(), scopes.get(reference)) else {
            continue;
        };
        if counts.get(token) != Some(&1) {
            continue;
        }
        let Some(&node) = index.get(token) else {
            continue;
        };
        let scope = native_scope(session, capture, scope);
        let complete_text = |name| {
            text(node, name)
                .filter(|_| issues.complete(token, &[name]))
                .map(str::to_owned)
        };
        if !issues.root && !issues.nodes {
            if let Some(value) = complete_text("identifier") {
                result.identifiers.insert(
                    reference.clone(),
                    IdentifierSource {
                        value,
                        scope: scope.clone(),
                    },
                );
            }
            result.fields.push((
                reference.clone(),
                NativeFieldSource {
                    native: token.to_owned(),
                    scope: scope.clone(),
                    name: complete_text("name"),
                    description: complete_text("description"),
                    url: complete_text("url"),
                },
            ));
            if let (Some(native_role), Some(display_label)) =
                (text(node, "role"), complete_text("role_description"))
            {
                if issues.complete(token, &["role"]) && plain_role_description(&display_label) {
                    result.roles.push((
                        reference.clone(),
                        NativeRoleSource {
                            native: token.to_owned(),
                            scope: scope.clone(),
                            native_role: native_role.to_owned(),
                            display_label,
                            provider_role_detail: code_subtype(node, issues.paths(token))
                                .then(|| "AXCodeStyleGroup".to_owned()),
                        },
                    ));
                }
            }
        }
        if !issues.root {
            let parent = text_parent(token, node, &parents, &unreliable, &issues)
                .and_then(|parent| inverse.get(parent).copied())
                .filter(|parent| {
                    scopes
                        .get(*parent)
                        .is_some_and(|parent_scope| *parent_scope == scope[2])
                })
                .map(str::to_owned);
            let primary_control = semantic
                .get("value_fields")
                .and_then(|fields| text(fields, reference))
                == Some("control_value.value");
            let value = node
                .get("control_value")
                .and_then(|control| text(control, "value"))
                .filter(|_| primary_control && issues.complete(token, &["control_value", "value"]))
                .map(str::to_owned);
            result.text.insert(
                reference.clone(),
                TextSliceSource {
                    native: token.to_owned(),
                    scope,
                    parent,
                    name: complete_text("name"),
                    value,
                },
            );
        }
    }
    cut_text_cycles(&mut result.text);
    result
}

fn value_text(value: &str) -> Value {
    Value::Text(value.to_owned())
}

struct ActionSource<'a> {
    actions: &'a [Value],
    issues: &'a [&'a Value],
}

fn action_issue_index(issue: &Value) -> Option<usize> {
    let [_, _, _, index, attribute] = seq(issue.get("path")) else {
        return None;
    };
    (attribute.as_str() == Some("description")).then_some(())?;
    usize::try_from(index.as_i64()?).ok()
}

fn value_or_null(value: &Value, field: &str) -> Value {
    value.get(field).cloned().unwrap_or(Value::Null)
}

fn action_record(action: &Value) -> Value {
    Value::Map(
        [
            "available",
            "description",
            "key",
            "kind",
            "label",
            "shortcuts",
            "target",
        ]
        .into_iter()
        .map(|field| {
            let value = if field == "shortcuts" {
                Value::Seq(seq(action.get("shortcuts")).to_vec())
            } else {
                value_or_null(action, field)
            };
            (field.to_owned(), value)
        })
        .collect(),
    )
}

fn action_issue_record(issue: &Value, name: Option<&str>) -> Value {
    let path = seq(issue.get("path"));
    let local = name.map_or_else(
        || path[2..].to_vec(),
        |name| {
            std::iter::once(value_text(name))
                .chain(path[4..].iter().cloned())
                .collect()
        },
    );
    Value::Map(vec![
        (
            "alternatives".to_owned(),
            Value::Seq(seq(issue.get("alternatives")).to_vec()),
        ),
        (
            "continuation".to_owned(),
            value_or_null(issue, "continuation"),
        ),
        ("detail".to_owned(), value_or_null(issue, "detail")),
        ("path".to_owned(), Value::Seq(local)),
        ("state".to_owned(), value_or_null(issue, "state")),
    ])
}

impl ActionSource<'_> {
    fn complete(&self) -> bool {
        self.issues.iter().all(|issue| {
            action_issue_index(issue)
                .and_then(|index| self.actions.get(index))
                .is_some_and(|action| {
                    text(issue, "state") == Some("unsupported")
                        && matches!(action.get("description"), None | Some(Value::Null))
                        && matches!(issue.get("continuation"), None | Some(Value::Null))
                        && seq(issue.get("alternatives")).is_empty()
                })
        })
    }

    fn catalogue(&self, scope: [&str; 3], pairs: &[(String, String)]) -> Option<ActionCatalogue> {
        let names: HashSet<_> = pairs.iter().map(|(name, _)| name.as_str()).collect();
        if pairs.is_empty() || names.len() != pairs.len() {
            return None;
        }
        let mut by_name = HashMap::new();
        let mut selected = HashMap::new();
        for (index, action) in self.actions.iter().enumerate() {
            if let Some(name) = text(action, "key").filter(|name| names.contains(name)) {
                selected.insert(index, name);
                by_name.insert(name, action);
            }
        }
        let matched = by_name.len() == pairs.len() && selected.len() == pairs.len();
        let (actions, issues) = if matched {
            let actions = pairs
                .iter()
                .map(|(name, _)| action_record(by_name[name.as_str()]))
                .collect();
            let issues = self
                .issues
                .iter()
                .filter_map(|issue| {
                    let name = selected.get(&action_issue_index(issue)?)?;
                    Some(action_issue_record(issue, Some(name)))
                })
                .collect();
            (actions, issues)
        } else {
            let actions = self.actions.iter().map(action_record).collect();
            let issues = self
                .issues
                .iter()
                .map(|issue| action_issue_record(issue, None))
                .collect();
            (actions, issues)
        };
        let key = json(
            &Value::Seq(vec![
                Value::Seq(scope.into_iter().map(value_text).collect()),
                Value::Seq(actions),
                Value::Seq(pairs.iter().map(|(name, _)| value_text(name)).collect()),
                Value::Seq(pairs.iter().map(|(_, label)| value_text(label)).collect()),
                Value::Seq(issues),
            ]),
            JsonFormat::Plain,
        )
        .ok()?;
        Some(ActionCatalogue {
            key,
            labels: pairs.iter().map(|(_, label)| label.clone()).collect(),
        })
    }
}

pub(super) fn capture_action_sources(
    seed: &mut LineSeed,
    projection: &Projection,
    snapshot: &FactSnapshot,
    tokens: &[String],
) {
    if seed.inline_actions.is_empty() {
        return;
    }
    let observation = &snapshot.observation;
    let (Some(session), Some(capture)) = (text(observation, "session"), text(observation, "id"))
    else {
        return;
    };
    let native: HashMap<_, _> = entries(observation.get("nodes"))
        .iter()
        .map(|(token, node)| (token.as_str(), node))
        .collect();
    let facts: HashMap<_, _> = seq(snapshot.facts.get("nodes"))
        .iter()
        .filter_map(|row| Some((text(row, "node")?, row)))
        .collect();
    let mut issues = HashMap::<_, Vec<_>>::new();
    for issue in seq(observation.get("issues")) {
        if let [group, token, actions, ..] = seq(issue.get("path")) {
            if group.as_str() == Some("nodes") && actions.as_str() == Some("actions") {
                if let Some(token) = token.as_str() {
                    issues.entry(token).or_default().push(issue);
                }
            }
        }
    }
    let source = |node: &ProjectionNode| {
        let token = tokens.get(node.id.0)?.as_str();
        let actions = native.get(token)?.get("actions")?.as_seq()?;
        (facts
            .get(token)?
            .get("actions")
            .and_then(|row| text(row, "state"))
            == Some("complete"))
        .then_some(())?;
        let source = ActionSource {
            actions,
            issues: issues.get(token).map(Vec::as_slice).unwrap_or_default(),
        };
        source.complete().then_some(source)
    };
    let options = LineOptions {
        compact: true,
        ..LineOptions::default()
    };
    let composed: HashSet<_> = projection
        .folded_into
        .iter()
        .filter_map(|(id, owner)| {
            let folded = projection.node(*id)?;
            let proof = source(folded);
            let mut original = folded.clone();
            original.fields.clone_from(&folded.native_fields);
            if truth(original.field("actionsUnavailable"))
                || proof.is_none_or(|proof| {
                    proof.actions.iter().any(|action| {
                        action.get("available").and_then(Value::as_bool) != Some(false)
                    })
                })
                || actions(&original, &options).map_or(true, |(labels, _, _)| !labels.is_empty())
            {
                Some(*owner)
            } else {
                None
            }
        })
        .collect();
    for (reference, pairs) in &seed.inline_actions {
        let Some(node) = projection.by_reference(reference) else {
            continue;
        };
        let original = node
            .native_fields
            .iter()
            .find(|(name, _)| name == "actions")
            .map(|(_, value)| value);
        if composed.contains(&node.id)
            || truth(node.field("actionsUnavailable"))
            || node.field("actions") != original
        {
            continue;
        }
        let Some(scope) = projection.node(node.scope) else {
            continue;
        };
        if let Some(catalogue) = source(node)
            .and_then(|source| source.catalogue([session, capture, &scope.reference], pairs))
        {
            seed.action_sources.insert(reference.clone(), catalogue);
        }
    }
}

#[cfg(test)]
mod tests {
    use super::*;
    use crate::{numbering::NodeId, wire::decode_json};

    fn json(value: &str) -> Value {
        decode_json(value.as_bytes()).unwrap()
    }

    #[test]
    fn captures_only_complete_scalar_and_containment_evidence() {
        let root = json(
            r#"{"ref":"0","role":"window","children":[
            {"ref":"1","role":"group","children":[]},
            {"ref":"2","role":"textField","name":"alpha, beta","index":2,
             "rowRange":{"location":3,"length":1},"columnRange":{"location":4,"length":2},"children":[]}
        ]}"#,
        );
        let aliases = HashMap::from([
            ("0".to_owned(), NodeId(0)),
            ("1".to_owned(), NodeId(1)),
            ("2".to_owned(), NodeId(2)),
        ]);
        let projection = Projection::prepare(root, &aliases, Default::default()).unwrap();
        let semantic = json(
            r#"{"refs":{"0":"window","1":"group","2":"entry"},"value_fields":{"2":"control_value.value"}}"#,
        );
        let mut snapshot = FactSnapshot {
            observation: json(
                r#"{"session":"session","id":"capture","nodes":{
                "window":{"role":"window","relations":{"child-edge":{"kind":"children","evidence":"reported",
                    "enumeration":"enumerated","targets":["group","entry"],"positions":[0,1]}}},
                "group":{"role":"group","identifier":"group-id","role_description":"code group","supplements":[
                    {"key":"provider.role_detail","value":"AXCodeStyleGroup"}]},
                "entry":{"role":"text_field","name":"alpha, beta","url":"https://example.test/entry",
                    "control_value":{"value":"value"},"relations":{"parent-edge":{"kind":"parent",
                        "evidence":"verified","targets":["window"]}}}
            },"issues":[{"path":["nodes"],"state":"partial"},{"path":["nodes","entry","url"],"state":"error"},
                        {"path":["nodes","group","supplements",0],"state":"error"}]}"#,
            ),
            facts: Value::Null,
            metadata: Value::Null,
            query: Value::Null,
        };
        let tokens = ["window".to_owned(), "group".to_owned(), "entry".to_owned()];

        let evidence = collect(&snapshot, &semantic, &projection, &tokens);
        let entry = &evidence.table.facts["2"];
        assert_eq!(entry.index, Some(2));
        assert_eq!(entry.row, Some(4));
        assert_eq!(entry.column, None);
        assert_eq!(entry.formatted_name.as_deref(), Some("\"alpha, beta\""));
        assert_eq!(evidence.identifiers["1"].value, "group-id");
        assert_eq!(evidence.roles[0].1.provider_role_detail, None);
        assert_eq!(evidence.fields[2].1.url, None);
        assert_eq!(evidence.text["2"].parent.as_deref(), Some("0"));
        assert_eq!(evidence.text["2"].value.as_deref(), Some("value"));

        *snapshot.observation.get_mut("issues").unwrap() = json(
            r#"[
            {"path":["nodes"],"state":"error"},
            {"path":["nodes","window","relations","child-edge"],"state":"error"}
        ]"#,
        );
        let evidence = collect(&snapshot, &semantic, &projection, &tokens);
        assert!(
            evidence.fields.is_empty()
                && evidence.identifiers.is_empty()
                && evidence.roles.is_empty()
        );
        assert_eq!(evidence.text["2"].parent, None);
        assert_eq!(evidence.text["2"].name.as_deref(), Some("alpha, beta"));
    }

    #[test]
    fn cuts_only_cyclic_edges_and_containment_above_the_native_scope() {
        let source = |parent: Option<&str>| TextSliceSource {
            native: String::new(),
            scope: ["session".into(), "capture".into(), "scope".into()],
            parent: parent.map(str::to_owned),
            name: None,
            value: None,
        };
        let mut sources = HashMap::from([
            ("scope".to_owned(), source(Some("x"))),
            ("x".to_owned(), source(Some("y"))),
            ("y".to_owned(), source(Some("x"))),
            ("local".to_owned(), source(Some("y"))),
        ]);
        cut_text_cycles(&mut sources);
        assert_eq!(sources["scope"].parent, None);
        assert_eq!(sources["x"].parent, None);
        assert_eq!(sources["y"].parent, None);
        assert_eq!(sources["local"].parent.as_deref(), Some("y"));
        assert!(plain_role_description("code\u{a0}group"));
        assert!(!plain_role_description("code\u{2028}group"));
    }

    #[test]
    fn action_proof_uses_native_keys_instead_of_catalogue_positions() {
        let left =
            json(r#"[{"kind":"focus","available":false},{"key":"AXCancel","kind":"cancel"}]"#);
        let right =
            json(r#"[{"key":"AXCancel","kind":"cancel"},{"kind":"focus","available":true}]"#);
        let unsupported =
            json(r#"{"path":["nodes","left","actions",1,"description"],"state":"unsupported"}"#);
        let same =
            json(r#"{"path":["nodes","right","actions",0,"description"],"state":"unsupported"}"#);
        let different = json(
            r#"{"path":["nodes","right","actions",0,"description"],"state":"unsupported","detail":"provider"}"#,
        );
        let wrong_kind =
            json(r#"{"path":["nodes","right","actions",0,"description"],"state":"error"}"#);
        let left_issues = [&unsupported];
        let right_issues = [&same];
        let changed_issues = [&different];
        let error_issues = [&wrong_kind];
        let pairs = [("AXCancel".to_owned(), "Cancel".to_owned())];
        let scope = ["session", "capture", "0"];
        let left = ActionSource {
            actions: left.as_seq().unwrap(),
            issues: &left_issues,
        };
        let right = ActionSource {
            actions: right.as_seq().unwrap(),
            issues: &right_issues,
        };
        let changed = ActionSource {
            issues: &changed_issues,
            ..right
        };
        let invalid = ActionSource {
            issues: &error_issues,
            ..right
        };

        assert!(left.complete() && right.complete() && changed.complete());
        assert!(!invalid.complete());
        assert_eq!(
            left.catalogue(scope, &pairs).unwrap().key,
            right.catalogue(scope, &pairs).unwrap().key
        );
        assert_ne!(
            left.catalogue(scope, &pairs).unwrap().key,
            changed.catalogue(scope, &pairs).unwrap().key
        );
    }
}
