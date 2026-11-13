// Copyright (c) Huawei Technologies Co., Ltd. 2026. All rights reserved.

use super::*;
use crate::{
    facts::FactSnapshot, geometric::GeometricContext, projection::ProjectionOptions,
    render::atoms::{plan_atoms, AtomOptions}, wire::decode_json,
};

fn value(source: &str) -> Value { Value::Text(source.to_owned()) }
fn json(source: &str) -> Value { decode_json(source.as_bytes()).unwrap() }
fn map<const N: usize>(fields: [(&str, Value); N]) -> Value {
    Value::Map(fields.into_iter().map(|(key, value)| (key.to_owned(), value)).collect())
}

fn projection(root: Value, references: &[&str]) -> Projection {
    let ids = references.iter().enumerate().map(|(index, reference)| ((*reference).to_owned(), NodeId(index))).collect();
    Projection::prepare(root, &ids, ProjectionOptions { native_facts: false, ..ProjectionOptions::default() }).unwrap()
}

#[test]
fn only_equal_complete_occurrences_under_one_owner_are_shared() {
    let help = "Explain the full context for this selected operation and retain the exact native object identity";
    let action = "AXDetailedSourceSpecificOperation";
    let button = |reference: &str, name: &str| map([
        ("ref", value(reference)), ("role", value("button")), ("name", value(name)),
        ("help", value(help)), ("children", Value::Seq(Vec::new())),
        ("actions", Value::Seq(vec![map([("name", value(action)), ("kind", value("custom"))])])),
    ]);
    let projection = projection(map([
        ("ref", value("1")), ("role", value("window")),
        ("children", Value::Seq(vec![button("2", "One"), button("3", "Two")])),
    ]), &["1", "2", "3"]);
    let native_button = || map([
        ("help", value(help)),
        ("actions", Value::Seq(vec![map([("key", value(action)), ("kind", value("custom"))])])),
    ]);
    let fact = |token: &str| map([
        ("node", value(token)), ("actions", map([("state", value("complete"))])),
        ("values", map([("help", value(help))])),
        ("fieldPaths", map([("help", Value::Seq(vec![value("help")]))])),
    ]);
    let mut snapshot = FactSnapshot {
        observation: map([
            ("session", value("session")), ("id", value("capture")),
            ("nodes", map([("root", map([])), ("one", native_button()), ("two", native_button())])),
            ("issues", Value::Seq(Vec::new())),
        ]),
        facts: map([("nodes", Value::Seq(vec![fact("one"), fact("two")]))]),
        metadata: Value::Null, query: Value::Null,
    };
    let atoms = plan_atoms(&projection, &AtomOptions { compact: true, ..AtomOptions::default() }).unwrap();
    let geometry = HashMap::new();
    let interface = GeometricPlan::build(&GeometricContext {
        projection: &projection, geometry: &geometry, facts: None, atoms: None,
    }, false).unwrap();
    let excluded = HashSet::new();
    let plan = |snapshot: &FactSnapshot| {
        let evidence = capture_sharing_evidence(&projection, &atoms, snapshot, None, None).unwrap();
        plan_sharing(SharingRequest {
            projection: &projection, interface: &interface, atoms: &atoms, evidence: &evidence,
            compression: None, disclosure: None, excluded_sources: &excluded,
        }).unwrap()
    };
    let result = plan(&snapshot);
    let declaration = &result.by_block[&interface.roots[0]];
    assert_eq!(declaration.fields.len(), 1);
    assert_eq!(declaration.fields[0].refs, [NodeId(1), NodeId(2)]);
    assert_eq!(declaration.fields[0].source_slots[1].1.facts[0].key.source, NodeId(2));
    assert_eq!(declaration.actions.len(), 1);
    assert_eq!(declaration.actions[0].refs, [NodeId(1), NodeId(2)]);
    assert_eq!(result.omitted_actions[&NodeId(2)], HashSet::from([0]));
    assert_eq!(atoms.atoms[&NodeId(2)].actions[0].source, NodeId(2));

    *snapshot.observation.get_mut("issues").unwrap() = json(r#"[
        {"path":["nodes","one","actions",0,"description"],"state":"unsupported","detail":"one-sided evidence"}
    ]"#);
    let with_issue = plan(&snapshot);
    assert!(with_issue.omitted_actions.is_empty());
    assert_eq!(with_issue.omitted_slots, result.omitted_slots);
}

#[test]
fn text_reuse_requires_original_ancestry_and_keeps_exact_unicode_ranges() {
    let child = "開場🧪".repeat(64) + "\" complete original ending";
    let anchor = format!("🪶 {child} — end");
    let projection = projection(map([
        ("ref", value("1")), ("role", value("window")), ("name", value(&anchor)),
        ("children", Value::Seq(vec![map([
            ("ref", value("2")), ("role", value("staticText")), ("value", value(&child)),
            ("children", Value::Seq(Vec::new())),
        ])])),
    ]), &["1", "2"]);
    let mut snapshot = FactSnapshot {
        observation: map([("session", value("session")), ("id", value("capture")), ("nodes", map([
            ("root", map([
                ("name", value(&anchor)),
                ("relations", json(r#"{"children":{"kind":"children","targets":["leaf"],"evidence":"reported","enumeration":"enumerated"}}"#)),
            ])),
            ("leaf", map([
                ("control_value", map([("value", value(&child))])),
                ("relations", map([])),
            ])),
        ]))]),
        facts: Value::Null, metadata: Value::Null, query: Value::Null,
    };
    let atoms = plan_atoms(&projection, &AtomOptions::default()).unwrap();
    let selectors = json(r#"{"2":"control_value.value"}"#);
    let sources = HashSet::new();
    let slots = HashSet::new();
    let plan = |snapshot: &FactSnapshot| {
        plan_text_slices(TextSliceRequest {
            projection: &projection, atoms: &atoms, snapshot, value_fields: Some(&selectors),
            disclosure: None, excluded_sources: &sources, excluded_slots: &slots,
        }).unwrap()
    };
    let selected = plan(&snapshot);
    let excerpt = selected.get(NodeId(1), "value").unwrap();
    assert_eq!(excerpt.mode, TextSliceMode::Excerpt);
    assert_eq!(excerpt.anchor.reference, "1");
    assert_eq!(excerpt.start_utf16, 3);
    assert_eq!(excerpt.end_utf16, 3 + child.encode_utf16().count());
    assert_eq!(excerpt.fragments.len(), 2);
    assert_eq!(excerpt.fragments[0].start, 0);
    assert_eq!(excerpt.fragments[1].end, child.chars().count());
    let destination = selected.destinations().next().unwrap();
    assert_eq!(destination.get("scope"), Some(&Value::Seq(vec![value("session"), value("capture"), value("1")])));
    assert!(destination.get("statement").and_then(Value::as_str).unwrap().ends_with("captured details: element 2]"));
    assert_eq!(atoms.atoms[&NodeId(1)].slots.iter().find(|slot| slot.key == "value").unwrap().value, value(&child));

    *snapshot.observation.get_mut("nodes").unwrap().get_mut("leaf").unwrap().get_mut("relations").unwrap()
        = json(r#"{"parent":{"kind":"parent","targets":["other"],"evidence":"reported"}}"#);
    assert!(plan(&snapshot).plans.is_empty());
}
