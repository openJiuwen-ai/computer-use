// Copyright (c) Huawei Technologies Co., Ltd. 2026. All rights reserved.

use super::*;
use crate::projection::ProjectionOptions;
use crate::wire::decode_json;

fn projection(raw: &[u8], refs: &[&str]) -> Projection {
    let aliases = refs.iter().enumerate().map(|(index, reference)| ((*reference).to_owned(), NodeId(index))).collect();
    let options = ProjectionOptions { compact: true, native_facts: false, ..ProjectionOptions::default() };
    Projection::prepare(decode_json(raw).unwrap(), &aliases, options).unwrap()
}

fn geometry(rows: &[(usize, [f64; 4])]) -> Geometry {
    rows.iter().map(|(id, [x, y, width, height])| {
        (NodeId(*id), Box { space: "space".to_owned(), x: *x, y: *y, width: *width, height: *height })
    }).collect()
}

fn text(value: &str) -> Value {
    Value::Text(value.to_owned())
}

#[test]
fn parallel_regions_keep_source_order_and_divider_binding() {
    let mut result = projection(br#"{
        "ref":"root","role":"window","children":[
            {"ref":"right","role":"group","children":[{"ref":"save","role":"button","children":[]}]},
            {"ref":"divider","role":"separator","children":[]},
            {"ref":"left","role":"group","children":[{"ref":"edit","role":"textField","children":[]}]}
        ]
    }"#, &["root", "right", "save", "divider", "left", "edit"]);
    let bounds = geometry(&[
        (0, [0.0, 0.0, 240.0, 150.0]), (1, [120.0, 0.0, 100.0, 150.0]),
        (3, [110.0, 0.0, 3.0, 150.0]), (4, [0.0, 0.0, 100.0, 150.0]),
    ]);
    protect_regions(&mut result, &bounds).unwrap();
    let root = result.node(NodeId(0)).unwrap();
    assert_eq!(root.children, [NodeId(1), NodeId(3), NodeId(4)]);
    assert_eq!(root.children, root.native_children);
    let left = result.node(NodeId(4)).unwrap();
    assert_eq!(left.field("spatialPosition"), Some(&text("left")));
    assert_eq!(left.field("preserveGroup"), Some(&Value::Bool(true)));
    let right = result.node(NodeId(1)).unwrap();
    assert_eq!(right.field("spatialPosition"), Some(&text("right")));
    assert_eq!(result.node(NodeId(3)).unwrap().field("spatialPosition"), None);
    assert_eq!(result.changes[0].get("geometrySources"), Some(&Value::Seq(vec![text("left"), text("right")])));
}

#[test]
fn repeated_rows_reorder_only_projected_children_and_add_caption_context() {
    let mut result = projection(br#"{
        "ref":"root","role":"window","children":[
            {"ref":"button2","role":"button","children":[]},
            {"ref":"label1","role":"staticText","children":[]},
            {"ref":"button1","role":"button","children":[]},
            {"ref":"label2","role":"staticText","children":[]}
        ]
    }"#, &["root", "button2", "label1", "button1", "label2"]);
    let bounds = geometry(&[
        (1, [80.0, 30.0, 20.0, 10.0]), (2, [0.0, 0.0, 20.0, 10.0]),
        (3, [80.0, 0.0, 20.0, 10.0]), (4, [0.0, 30.0, 20.0, 10.0]),
    ]);
    rows::group_rows(&mut result, &bounds).unwrap();
    let root = result.node(NodeId(0)).unwrap();
    assert_eq!(root.native_children, [NodeId(1), NodeId(2), NodeId(3), NodeId(4)]);
    assert_eq!(root.children, [NodeId(2), NodeId(3), NodeId(4), NodeId(1)]);
    assert_eq!(root.field("layoutGroups"), Some(&decode_json(br#"[
        {"kind":"row","members":["label1","button1"]},
        {"kind":"row","members":["label2","button2"]}
    ]"#).unwrap()));
    assert_eq!(result.node(NodeId(1)).unwrap().field("_contextRefs"), Some(&Value::Seq(vec![text("label2")])));
}

#[test]
fn passive_caption_keeps_its_identity_and_ambiguous_targets_veto_assignment() {
    let raw = br#"{
        "ref":"root","role":"window","children":[
            {"ref":"label","role":"staticText","value":"Title","_completeTextValues":["value"],"children":[]},
            {"ref":"entry","role":"textField","name":"Document","children":[]},
            {"ref":"competitor","role":"slider","children":[]}
        ]
    }"#;
    let refs = &["root", "label", "entry", "competitor"];
    let mut result = projection(raw, refs);
    let bounds = geometry(&[(1, [0.0, 0.0, 30.0, 20.0]), (2, [35.0, 0.0, 100.0, 20.0])]);
    association::associate_labels(&mut result, &bounds, &CompressionEvidence::default()).unwrap();
    assert_eq!(result.node(NodeId(2)).unwrap().field("visualLabelRef"), Some(&text("label")));
    assert_eq!(result.node(NodeId(2)).unwrap().field("name"), Some(&text("Document")));
    assert_eq!(result.node(NodeId(0)).unwrap().children, [NodeId(1), NodeId(2), NodeId(3)]);
    assert!(result.elisions.is_empty());

    let mut ambiguous = projection(raw, refs);
    let mut bounds = bounds;
    bounds.insert(NodeId(3), bounds[&NodeId(2)].clone());
    association::associate_labels(&mut ambiguous, &bounds, &CompressionEvidence::default()).unwrap();
    assert_eq!(ambiguous.node(NodeId(2)).unwrap().field("visualLabelRef"), None);
    assert!(ambiguous.changes.is_empty());
}
