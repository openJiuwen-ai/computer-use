// Copyright (c) Huawei Technologies Co., Ltd. 2026. All rights reserved.

use super::*;

fn value(text: &str) -> Value {
    Value::Text(text.to_owned())
}

fn node(reference: &str, role: &str, children: Vec<Value>, attributes: &[(&str, Value)]) -> Value {
    let mut fields = vec![
        ("ref".to_owned(), value(reference)),
        ("role".to_owned(), value(role)),
        ("children".to_owned(), Value::Seq(children)),
    ];
    fields.extend(attributes.iter().map(|(name, value)| (name.to_string(), value.clone())));
    Value::Map(fields)
}

fn aliases(refs: &[&str]) -> HashMap<String, NodeId> {
    refs.iter().enumerate().map(|(id, name)| (name.to_string(), NodeId(id))).collect()
}

#[test]
fn deep_projection_keeps_every_named_source_on_a_read_worker_stack() {
    // The SDK permits a 2,000-object macOS read. Keep both the original source
    // path and the displayed node order at that supported depth.
    const COUNT: usize = 2000;
    let refs: HashMap<_, _> = (0..COUNT).map(|index| (index.to_string(), NodeId(index))).collect();
    let mut children = Vec::new();
    for index in (0..COUNT).rev() {
        let role = if index == 0 { "window" } else { "group" };
        children = vec![node(&index.to_string(), role, children, &[
            ("name", value(&format!("Layer {index}"))),
            ("preserveGroup", Value::Bool(true)),
        ])];
    }
    let root = children.pop().unwrap();
    std::thread::scope(|scope| {
        std::thread::Builder::new().stack_size(512 * 1024).spawn_scoped(scope, || {
            let mut projection = Projection::prepare(&root, &refs, ProjectionOptions::default()).unwrap();
            let expected: Vec<_> = (0..COUNT).map(NodeId).collect();
            assert_eq!(projection.source_order, expected);
            assert_eq!(projection.display_order, expected);
            for id in 1..COUNT {
                assert_eq!(projection.node(NodeId(id)).unwrap().parent, Some(NodeId(id - 1)));
            }
            projection.compress(&CompressionEvidence::default(), &Geometry::default()).unwrap();
            assert_eq!(projection.visible_order().unwrap(), expected);
        }).unwrap().join().unwrap();
    });
}

#[test]
fn modal_scope_keeps_native_membership_and_exact_blocking() {
    let background = node(
        "2", "button", vec![],
        &[("capabilities", Value::Map(vec![("click".to_owned(), value("available"))]))],
    );
    let modal = node("3", "sheet", vec![], &[("modal", Value::Bool(true))]);
    let root = node("1", "window", vec![background, modal], &[]);
    let options = ProjectionOptions { include_background: false, ..ProjectionOptions::default() };
    let result = Projection::prepare(root, &aliases(&["1", "2", "3"]), options).unwrap();
    let window = result.node(NodeId(0)).unwrap();
    assert_eq!(window.native_children, [NodeId(1), NodeId(2)]);
    assert_eq!(window.children, [NodeId(2)]);
    assert_eq!(window.field("partialReason"), Some(&value("background blocked by 3")));
    let background = result.node(NodeId(1)).unwrap();
    assert_eq!(background.parent, Some(NodeId(0)));
    assert!(background.fields.is_empty());
    assert_eq!(field(&background.native_fields, "blockedBy"), Some(&Value::Seq(vec![value("3")])));
    assert_eq!(
        field(&background.native_fields, "capabilities").unwrap().get("click"),
        Some(&value("unavailable"))
    );
    assert_eq!(result.visible_order().unwrap(), [NodeId(0), NodeId(2)]);
}

#[test]
fn exact_description_promotion_and_transitive_source_destinations() {
    let root = node(
        "1", "window",
        vec![
            node("2", "button", vec![], &[("description", value("Save"))]),
            node("3", "group", vec![], &[]),
        ],
        &[],
    );
    let mut result = Projection::prepare(root, &aliases(&["1", "2", "3"]), ProjectionOptions::default()).unwrap();
    result.promote_description(NodeId(1)).unwrap();
    let button = result.node(NodeId(1)).unwrap();
    assert_eq!(button.field("name"), Some(&value("Save")));
    assert_eq!(button.field("description"), Some(&value("")));
    assert_eq!(field(&button.native_fields, "description"), Some(&value("Save")));
    let name = button.provenance.iter().find(|(field, _)| field == "name").unwrap();
    assert_eq!(name.1.parts[0].facts[0].attribute, "description");
    result.fold(NodeId(2), NodeId(1), "example", "layout").unwrap();
    result.fold(NodeId(1), NodeId(0), "example", "layout").unwrap();
    result.finalize_folds().unwrap();
    assert_eq!(result.folded_into, [(NodeId(2), NodeId(0)), (NodeId(1), NodeId(0))]);
    assert_eq!(result.node(NodeId(0)).unwrap().source_refs, [NodeId(0), NodeId(1), NodeId(2)]);
    assert_eq!(
        result.resolved_elisions,
        [(NodeId(1), vec![NodeId(0)]), (NodeId(2), vec![NodeId(0)])]
    );
}

#[test]
fn confirmed_outline_keeps_original_edges_after_display_reconstruction() {
    let root = node(
        "1", "outline",
        vec![
            node("2", "row", vec![], &[("disclosureLevel", Value::Int(0))]),
            node("3", "row", vec![], &[("disclosureLevel", Value::Int(1)), ("disclosedBy", value("2"))]),
        ],
        &[],
    );
    let mut result = Projection::prepare(root, &aliases(&["1", "2", "3"]), ProjectionOptions::default()).unwrap();
    result.reconstruct_outlines().unwrap();
    assert_eq!(result.node(NodeId(0)).unwrap().children, [NodeId(1)]);
    assert_eq!(result.node(NodeId(1)).unwrap().children, [NodeId(2)]);
    let child = result.node(NodeId(2)).unwrap();
    assert_eq!(child.parent, Some(NodeId(0)));
    assert_eq!(child.field("outlineDepth"), Some(&Value::Int(1)));
}

#[test]
fn compression_preserves_passive_text_occurrences_and_native_sources() {
    use crate::geometry::Box;

    let root = node("1", "window", vec![node(
        "2", "group",
        vec![
            node("3", "staticText", vec![], &[("name", value("A"))]),
            node("4", "staticText", vec![], &[("name", value("A"))]),
        ],
        &[],
    )], &[]);
    let options = ProjectionOptions { compact: true, ..ProjectionOptions::default() };
    let mut projection = Projection::prepare(root, &aliases(&["1", "2", "3", "4"]), options).unwrap();
    let mut evidence = CompressionEvidence::default();
    evidence.complete_children.extend([
        (NodeId(1), vec![NodeId(2), NodeId(3)]), (NodeId(2), Vec::new()), (NodeId(3), Vec::new()),
    ]);
    evidence.complete_text.extend([NodeId(2), NodeId(3)].map(|source| FactKey { source, attribute: "name".to_owned() }));
    let mut geometry = Geometry::new();
    for (id, x, width) in [(1, 0.0, 20.0), (2, 0.0, 8.0), (3, 11.0, 8.0)] {
        geometry.insert(NodeId(id), Box { space: "screen".to_owned(), x, y: 0.0, width, height: 10.0 });
    }
    projection.compress(&evidence, &geometry).unwrap();
    let owner = projection.node(NodeId(1)).unwrap();
    assert_eq!(owner.field("name"), Some(&value("A A")));
    assert_eq!(owner.native_children, [NodeId(2), NodeId(3)]);
    assert!(owner.children.is_empty());
    assert_eq!(projection.folded_into, [(NodeId(2), NodeId(1)), (NodeId(3), NodeId(1))]);
    assert_eq!(owner.provenance.iter().find(|(name, _)| name == "name").unwrap().1.parts.len(), 2);
}

#[test]
fn evidence_distinguishes_complete_membership_from_contested_ownership() {
    use crate::{facts::FactSnapshot, wire::decode_json};

    let json = |source: &str| decode_json(source.as_bytes()).unwrap();
    let snapshot = FactSnapshot {
        observation: json(r#"{"nodes":{
          "w":{"role":"window","relations":{"children":{"kind":"children","targets":["a"],"total":1,"enumeration":"enumerated","evidence":"reported"}}},
          "a":{"role":"group","relations":{"parent":{"kind":"parent","targets":["b"],"total":1,"enumeration":"enumerated","evidence":"reported"}}},
          "b":{"role":"group"}
        },"issues":[{"path":["nodes","a","name"],"state":"timeout"}]}"#),
        facts: json(r#"{"nodes":[{"node":"w","values":{"name":"Window"},"fieldStatus":{}}]}"#),
        metadata: Value::Null,
        query: Value::Null,
    };
    let semantic = json(r#"{
      "refs":{"1":"w","2":"a","3":"b"},
      "roots":[{"ref":"1","role":"window","children":[{"ref":"2","role":"group","children":[]}]}],
      "fields":[{"ref":"1","key":"name","value":{"field":"name","path":["name"]}}]
    }"#);
    let evidence = CompressionEvidence::capture(&snapshot, &semantic, &aliases(&["1", "2", "3"])).unwrap();
    assert_eq!(evidence.complete_children[&NodeId(0)], [NodeId(1)]);
    assert!(evidence.complete_text.contains(&FactKey { source: NodeId(0), attribute: "name".to_owned() }));
    assert!(evidence.incomplete_content.contains(&NodeId(1)));
    assert_eq!(evidence.contested_sources, HashSet::from([NodeId(0), NodeId(1)]));
    assert!(evidence.splice_conflicts.is_empty());
}

#[test]
fn traces_whole_and_node_local_rules_in_first_invocation_order() {
    let root = node("1", "window", vec![
        node("2", "button", vec![], &[("description", value("Save"))]),
    ], &[]);
    let options = ProjectionOptions { trace: true, ..ProjectionOptions::default() };
    let mut projection = Projection::prepare(root, &aliases(&["1", "2"]), options).unwrap();
    let mut lifecycle = Vec::new();
    projection.compress_with_lifecycle(&CompressionEvidence::default(), &Geometry::new(), &mut lifecycle).unwrap();
    assert_eq!(lifecycle.first().map(|event| (event.name, event.phase)), Some(("CompressionIRLifetime", "enter")));
    assert_eq!(lifecycle.last().map(|event| (event.name, event.phase)), Some(("CompressionIRLifetime", "exit")));
    let mut active = Vec::new();
    let mut calls = HashMap::new();
    for event in &lifecycle {
        if event.phase == "enter" {
            active.push(event.name);
            *calls.entry(event.name).or_insert(0) += 1;
        } else {
            assert_eq!(active.pop(), Some(event.name));
            assert_eq!(event.phase, "exit");
            assert_eq!(event.error, None);
        }
    }
    assert!(active.is_empty());
    assert!(projection.stages.iter().all(|stage| calls[stage.name] == stage.calls));
    assert_eq!(projection.stages[0].name, "share_empty_descriptions");
    let promoted = projection.stages.iter().find(|stage| stage.name == "promote_description").unwrap();
    assert_eq!(promoted.calls, 2);
    assert_eq!(promoted.changes, 0);
    assert_eq!(promoted.affected_refs, BTreeSet::from(["2".to_owned()]));
    assert_eq!(projection.stages.last().unwrap().name, "finalize_sources");
}

#[test]
fn branch_budget_checks_entry_and_original_preorder_work() {
    let references: Vec<_> = (0..129).map(|id| id.to_string()).collect();
    let ids = references.iter().enumerate().map(|(id, reference)| (reference.clone(), NodeId(id))).collect();
    let root = node("0", "window", references[1..].iter().map(|ref_| node(ref_, "button", vec![], &[])).collect(), &[]);
    let mut checkpoints = 0;
    let members = branch_members(&root, &ids, || {
        checkpoints += 1;
        Ok::<_, ()>(())
    }).unwrap();
    assert_eq!(checkpoints, 3);
    assert_eq!(members, (0..129).map(NodeId).collect::<Vec<_>>());
    let mut checkpoints = 0;
    let stopped = branch_members(&root, &ids, || {
        checkpoints += 1;
        if checkpoints == 2 { Err("cancelled") } else { Ok(()) }
    });
    assert_eq!(stopped, Err(BranchError::Budget("cancelled")));
}
