// Copyright (c) Huawei Technologies Co., Ltd. 2026. All rights reserved.

use super::*;

fn node(reference: &str, parent: Option<&str>, depth: i64) -> ComparisonNode {
    ComparisonNode {
        reference: reference.to_owned(),
        parent: parent.map(str::to_owned),
        depth,
        signature: "full".to_owned(),
        content: "content".to_owned(),
    }
}

fn tree(scope: &str, nodes: Vec<ComparisonNode>) -> CurrentTree {
    let context = vec![ContextNode::default(); nodes.len()];
    CurrentTree {
        comparison: ComparisonTree {
            scope: vec![Some(scope.to_owned())],
            nodes,
            ..ComparisonTree::default()
        }.into(),
        context,
        roots: Vec::new(),
    }
}

fn baseline(trees: &[CurrentTree]) -> ComparisonDocument {
    ComparisonDocument { trees: trees.iter().map(|tree| tree.comparison.clone()).collect() }
}

fn refs(trees: &[CurrentTree]) -> Bindings {
    trees.iter().flat_map(|tree| &tree.comparison.nodes).map(|node| {
        (node.reference.clone(), format!("native-{}", node.reference))
    }).collect()
}

fn plan(before: &[CurrentTree], after: &[CurrentTree]) -> DeltaPlan {
    plan_document_diff(Some(&baseline(before)), Some(after), &refs(before), &refs(after), &HashSet::new()).unwrap()
}

fn strings(items: &[&str]) -> HashSet<String> {
    items.iter().map(|item| (*item).to_owned()).collect()
}

#[test]
fn keeps_current_scope_slots_and_requires_one_matching_old_tree() {
    let old = [
        tree("main", vec![node("1", None, 0)]),
        tree("duplicate", vec![node("2", None, 0)]),
        tree("duplicate", vec![node("3", None, 0)]),
    ];
    let new = [
        tree("new", vec![node("4", None, 0)]),
        old[0].clone(),
        old[1].clone(),
        old[0].clone(),
    ];
    let output = plan(&old, &new);
    assert_eq!(output.mode, Mode::Diff);
    assert_eq!(output.reason.as_str(), "compared-native-scopes");
    assert_eq!(output.trees, [None, Some(TreeDelta::default()), None, Some(TreeDelta::default())]);
    assert_eq!(plan(&old, &new[..1]).reason, Reason::NewOrReplacedNativeScope);
    assert_eq!(plan(&[], &new).reason, Reason::InitialObservation);
    assert_eq!(
        plan_document_diff(None, Some(&new), &Bindings::new(), &refs(&new), &HashSet::new()).unwrap().reason,
        Reason::NoStructuredBaseline,
    );
}

#[test]
fn incompatible_binding_and_invalid_containment_refresh_only_their_trees() {
    let old = [
        tree("unchanged", vec![node("1", None, 0)]),
        tree("binding", vec![node("2", None, 0)]),
        tree("depth", vec![node("3", None, 0), node("4", Some("3"), 1)]),
    ];
    let mut new = old.clone();
    Arc::make_mut(&mut new[2].comparison).nodes[1].depth = 3;
    let mut new_refs = refs(&new);
    new_refs.insert("2".to_owned(), "replaced-token".to_owned());
    let output = plan_document_diff(Some(&baseline(&old)), Some(&new), &refs(&old), &new_refs, &HashSet::new()).unwrap();
    assert_eq!(output.mode, Mode::Diff);
    assert_eq!(output.trees, [Some(TreeDelta::default()), None, None]);
}

#[test]
fn preserves_old_missing_order_and_first_change_insertion() {
    let old = [tree("window", vec![
        node("1", None, 0),
        node("2", Some("1"), 1),
        node("3", Some("2"), 2),
        node("8", Some("2"), 2),
        node("4", Some("1"), 1),
        node("5", Some("1"), 1),
    ])];
    let new = [tree("window", vec![
        node("1", None, 0),
        node("4", Some("1"), 1),
        node("2", Some("4"), 2),
        node("5", Some("1"), 1),
        node("6", Some("1"), 1),
    ])];
    let output = plan(&old, &new);
    let delta = output.trees[0].as_ref().unwrap();
    let changes: Vec<_> = delta.changes().iter().map(|(reference, change)| (reference.as_str(), change.marker())).collect();
    assert_eq!(changes, [("2", '~'), ("6", '+'), ("4", '~')]);
    assert_eq!(delta.missing, ["3", "8"]);
    assert_eq!(delta.context, strings(&["1", "2", "4"]));
}

#[test]
fn content_changes_expand_current_non_root_descendants_only() {
    let old = [tree("window", vec![
        node("1", None, 0), node("2", Some("1"), 1), node("3", Some("2"), 2),
        node("4", Some("1"), 1), node("5", Some("4"), 2),
        node("6", None, 0), node("7", Some("6"), 1),
        node("8", Some("1"), 1),
    ])];
    let mut new = old.clone();
    let comparison = Arc::make_mut(&mut new[0].comparison);
    for index in [1, 3, 5] {
        comparison.nodes[index].signature = "changed".to_owned();
    }
    for index in [1, 5] {
        comparison.nodes[index].content = "changed".to_owned();
    }
    new[0].context[7].partial = true;
    let output = plan(&old, &new);
    let delta = output.trees[0].as_ref().unwrap();
    assert_eq!(delta.expanded, strings(&["3"]));
    assert_eq!(delta.context, strings(&["1", "2", "3", "8"]));
    assert_eq!(delta.change("4"), Some(Change::Modified));
    assert_eq!(delta.change("5"), None);
}

#[test]
fn root_context_refreshes_displayed_root_and_keeps_old_and_new_owners() {
    let mut old = [tree("window", vec![
        node("1", None, 0), node("2", Some("1"), 1), node("3", Some("1"), 1),
        node("4", None, 0), node("5", Some("4"), 1),
    ])];
    old[0].roots.push(DisplayRoot { reference: "folded".to_owned(), members: vec!["4".to_owned(), "5".to_owned()] });
    Arc::make_mut(&mut old[0].comparison).root_contexts.insert("folded".to_owned(), vec![("2".to_owned(), "rows".to_owned())]);
    let mut new = old.clone();
    Arc::make_mut(&mut new[0].comparison).root_contexts.insert("folded".to_owned(), vec![("3".to_owned(), "rows".to_owned())]);
    let output = plan(&old, &new);
    let delta = output.trees[0].as_ref().unwrap();
    assert_eq!(delta.changes(), [("4".to_owned(), Change::Modified)]);
    assert_eq!(delta.context, strings(&["1", "2", "3"]));
    Arc::make_mut(&mut new[0].comparison).versions.insert("folded".to_owned(), Some("geometric-2".to_owned()));
    assert_eq!(plan(&old, &new).trees[0].as_ref().unwrap().change("5"), Some(Change::Modified));
}

#[test]
fn related_context_prefers_local_aliases_and_closes_only_over_compatible_trees() {
    let mut old = [
        tree("left", vec![node("source", None, 0), node("shared", None, 0),
            node("labeler", None, 0), node("group", None, 0), node("label", Some("group"), 1),
            node("sibling", Some("group"), 1)]),
        tree("right", vec![node("shared", None, 0), node("ambiguous", None, 0),
            node("parent", None, 0), node("unique", Some("parent"), 1)]),
        tree("third", vec![node("ambiguous", None, 0)]),
    ];
    old[0].context[0].label_targets.push("shared".to_owned());
    old[0].context[0].context_refs.extend(["ambiguous", "unique", "foreign"].map(str::to_owned));
    old[0].context[0].related.push("group".to_owned());
    old[0].context[2].label_targets.push("source".to_owned());
    old[0].context[3].anonymous_group_or_cell = true;
    old[0].context[4].static_text_or_heading = true;
    old[1].context[3].related.push("source".to_owned());
    let mut new = old.to_vec();
    new.push(tree("new", vec![node("foreign", None, 0)]));
    Arc::make_mut(&mut new[0].comparison).nodes[0].signature = "changed".to_owned();
    let output = plan(&old, &new);
    assert_eq!(output.trees[0].as_ref().unwrap().context, strings(&["source", "shared", "labeler", "group", "label", "sibling"]));
    assert_eq!(output.trees[1].as_ref().unwrap().context, strings(&["parent", "unique"]));
    assert!(output.trees[2].as_ref().unwrap().context.is_empty());
    assert!(output.trees[3].is_none());
}

#[test]
fn partial_evidence_and_folded_references_do_not_prove_removal() {
    let old = [tree("window", vec![
        node("root", None, 0), node("parent", Some("root"), 1), node("folded", Some("parent"), 2),
        node("unseen", Some("parent"), 2), node("removed", Some("root"), 1),
    ])];
    let new = [tree("window", vec![node("root", None, 0), node("parent", Some("root"), 1)])];
    let previous = Coverage { parents: old[0].comparison.nodes.iter().map(|node| (node.reference.clone(), node.parent.clone())).collect(), complete: HashSet::new() };
    let current = Coverage {
        parents: HashMap::from([
            ("root".to_owned(), None),
            ("parent".to_owned(), Some("root".to_owned())),
            ("folded".to_owned(), Some("parent".to_owned())),
        ]),
        complete: strings(&["root"]),
    };
    let mut new_refs = refs(&new);
    new_refs.insert("folded".to_owned(), "native-folded".to_owned());
    let old_refs = refs(&old);
    let not_observed = unconfirmed_missing(&old_refs, &new_refs, Some(&previous), Some(&current));
    assert_eq!(not_observed, strings(&["unseen"]));
    assert!(!confirms_removal(Some(&previous), Some(&current), "folded"));
    let output = plan_document_diff(Some(&baseline(&old)), Some(&new), &old_refs, &new_refs, &not_observed).unwrap();
    assert_eq!(output.trees[0].as_ref().unwrap().missing, ["folded", "unseen", "removed"]);
    assert_eq!(output.not_observed, strings(&["unseen"]));
    assert_eq!(output.retained, strings(&["root", "parent", "folded"]));
}

fn value_map<const N: usize>(pairs: [(&str, Value); N]) -> Value {
    Value::Map(pairs.into_iter().map(|(key, value)| (key.to_owned(), value)).collect())
}

fn text(value: &str) -> Value {
    Value::Text(value.to_owned())
}

#[test]
fn coverage_uses_native_forest_and_exactly_one_confirmed_child_view() {
    fn item(reference: &str, children: Vec<Value>) -> Value {
        value_map([("ref", text(reference)), ("children", Value::Seq(children))])
    }
    let semantic = value_map([
        ("roots", Value::Seq(vec![item("1", vec![item("2", Vec::new())]), item("3", Vec::new())])),
        ("refs", value_map([("1", text("root")), ("2", text("hidden")), ("3", text("related"))])),
    ]);
    let relation = |source: &str, certainty: &str, state: &str, targets: Vec<Value>, offset: i64| value_map([
        ("source", text(source)), ("kind", text("children")), ("certainty", text(certainty)), ("state", text(state)),
        ("total", Value::Int(targets.len() as i64)), ("targets", Value::Seq(targets)), ("offset", Value::Int(offset)),
    ]);
    let mut facts = value_map([
        ("relations", Value::Seq(vec![
            relation("root", "confirmed", "complete", vec![text("hidden")], 0),
            relation("root", "unknown", "complete", Vec::new(), 0),
            relation("hidden", "confirmed", "complete", Vec::new(), 1),
            relation("related", "confirmed", "complete", vec![text("unread")], 0),
        ])),
        ("unread", Value::Seq(vec![text("unread")])),
    ]);
    let coverage = document_coverage([(&semantic, &facts)]).unwrap();
    assert_eq!(coverage.parents.get("2").and_then(Option::as_deref), Some("1"));
    assert_eq!(coverage.parents.get("3"), Some(&None));
    assert_eq!(coverage.complete, strings(&["1"]));

    let Some(Value::Seq(relations)) = facts.get_mut("relations") else { panic!("test has relations") };
    relations.push(relation("root", "confirmed", "unsupported", Vec::new(), 0));
    assert!(document_coverage([(&semantic, &facts)]).unwrap().complete.is_empty());
}

#[test]
fn context_uses_original_presence_truthiness_and_count_predicates() {
    let fields = value_map([
        ("role", text("cell")), ("name", text("")), ("partialReason", text("")),
        ("labelTargets", Value::Seq(vec![text("1"), text("1")])),
    ]);
    let result = ContextNode::from_fields(fields.as_map().unwrap()).unwrap();
    assert!(result.partial && result.anonymous_group_or_cell);
    assert_eq!(result.label_targets, ["1", "1"]);
    let fields = value_map([("nextChildOffset", Value::Null)]);
    assert!(ContextNode::from_fields(fields.as_map().unwrap()).unwrap().partial);
    let fields = value_map([("childrenRead", Value::Int(i64::MAX)), ("childCount", Value::BigInt("9223372036854775808".to_owned()))]);
    assert!(ContextNode::from_fields(fields.as_map().unwrap()).unwrap().partial);
    let fields = value_map([("partialReason", Value::Null), ("childOmissions", Value::Seq(Vec::new()))]);
    assert!(!ContextNode::from_fields(fields.as_map().unwrap()).unwrap().partial);
}

#[test]
fn python_float_repr_preserves_thresholds_roundtrips_and_special_values() {
    for (bits, expected) in [
        (0x0000000000000000, "0.0"),
        (0x8000000000000000, "-0.0"),
        (0x3ee4f8b588e368f1, "1e-05"),
        (0x3f1a36e2eb1c432d, "0.0001"),
        (0x430c6bf526340000, "1000000000000000.0"),
        (0x4341c37937e08000, "1e+16"),
        (0x54b249ad2594c37d, "1e+100"),
        (0x0000000000000001, "5e-324"),
        (0x0010000000000000, "2.2250738585072014e-308"),
        (0x7fefffffffffffff, "1.7976931348623157e+308"),
        (0x4339999999999999, "7205759403792793.0"),
        (0x3ff0000000000001, "1.0000000000000002"),
        // A shortest-decimal midpoint. Rust's unqualified shortest formatter
        // emits the other round-tripping neighbor ending in 5313.
        (0x3ff0000800000000, "1.0000076293945312"),
        (0x3fd3333333333334, "0.30000000000000004"),
        (0x3eb1d0d7b0ee452e, "1.0619039050067979e-06"),
        (0x3feb0e7009b61ce0, "0.8455124082255701"),
        (0x7ff0000000000000, "inf"),
        (0xfff0000000000000, "-inf"),
        (0xfff8000000000001, "nan"),
    ] {
        assert_eq!(signature::python_float_repr(f64::from_bits(bits)), expected, "bits {bits:#x}");
    }
}

#[test]
fn signatures_match_python_for_typed_nested_fields_and_unicode_14() {
    let fields = value_map([
        ("name", text("meeting's e\u{0301}\u{00a0}\u{2028}\u{1fae8}\u{f0000}")),
        ("focused", Value::Bool(true)),
        ("busy", Value::Bool(false)),
        ("nested", value_map([
            ("bytes", Value::Bytes(vec![b'\'', 0x00, 0x08, 0xff])),
            ("large", Value::BigInt("10000000000000000000000000".to_owned())),
            ("values", Value::Seq(vec![
                Value::Null, Value::Float(-0.0), Value::Float(0.0001), Value::Float(1e-5), Value::Float(1e16),
                Value::Float(f64::from_bits(1)), Value::Float(f64::NEG_INFINITY), Value::Float(f64::NAN),
            ])),
        ])),
        ("preserveGroup", Value::Bool(true)),
    ]);
    let mut signatures = Signatures::default();
    let (full, content) = signatures.capture(fields.as_map().unwrap());
    assert_eq!(full, "2eb559896e7d4048138afa3d99b257e487da7c86127363cd9c934117cd4fbcb6");
    assert_eq!(content, "8d646005cc4fb4dca8469f074a0eea1b9d41397cd1df5720c31ab9462f8f120e");
}
