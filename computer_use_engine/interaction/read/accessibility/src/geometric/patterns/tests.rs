// Copyright (c) Huawei Technologies Co., Ltd. 2026. All rights reserved.

use super::*;
use crate::{
    geometric::Relation,
    geometry::{Box, Geometry},
    projection::{Projection, ProjectionOptions},
    wire::decode_json,
};

fn projection(source: &str, count: usize) -> Projection {
    let aliases = (0..count).map(|id| (id.to_string(), NodeId(id))).collect();
    Projection::prepare(decode_json(source.as_bytes()).unwrap(), &aliases, ProjectionOptions::default()).unwrap()
}

#[test]
fn logical_table_selects_ordered_source_cells_and_leaves_unrelated_children() {
    let projection = projection(r#"{"ref":"0","role":"table","children":[
        {"ref":"1","role":"row","children":[
          {"ref":"2","role":"cell","rowRange":{"location":0,"length":1},"columnRange":{"location":0,"length":1},"children":[]},
          {"ref":"3","role":"cell","rowRange":{"location":0,"length":1},"columnRange":{"location":2,"length":1},"children":[]}]},
        {"ref":"4","role":"row","children":[
          {"ref":"5","role":"cell","rowRange":{"location":1,"length":1},"columnRange":{"location":0,"length":1},"children":[]},
          {"ref":"6","role":"cell","rowRange":{"location":1,"length":1},"columnRange":{"location":2,"length":1},"children":[]}]},
        {"ref":"7","role":"button","children":[]}] }"#, 8);
    let geometry = Geometry::new();
    let context = GeometricContext { projection: &projection, geometry: &geometry, facts: None, atoms: None };
    let mut plan = GeometricPlan::seed(&context, true).unwrap();
    let owner = plan.roots[0];
    let original = plan.block(owner).unwrap().children.clone();
    apply_patterns(&mut plan, &context).unwrap();
    assert!(pattern_issues(&plan, &context).is_empty());
    assert_eq!(plan.block(owner).unwrap().children, original);
    let selected = &plan.geometric_patterns[0];
    assert_eq!(selected.kind, "table");
    assert_eq!(selected.column_count, 3);
    assert_eq!(selected.rows.iter().map(|row| row.reference).collect::<Vec<_>>(), [Some(NodeId(1)), Some(NodeId(4))]);
    assert_eq!(selected.rows[0].slots, [0, 2]);
    assert_eq!(selected.remaining_keys, [*original.last().unwrap()]);
    let value = selected.to_value(&plan, &context).unwrap();
    assert_eq!(value.get("owner_key").and_then(Value::as_str), Some("element:0"));
}

#[test]
fn measured_grid_keeps_whole_items_and_declines_conflicting_native_order() {
    let projection = projection(r#"{"ref":"0","role":"list","children":[
        {"ref":"1","role":"button","children":[]},{"ref":"2","role":"button","children":[]},
        {"ref":"3","role":"button","children":[]},{"ref":"4","role":"button","children":[]},
        {"ref":"5","role":"staticText","children":[]}] }"#, 6);
    let geometry = Geometry::from_iter([
        (NodeId(1), Box { space: "window".to_owned(), x: 0.0, y: 0.0, width: 10.0, height: 10.0 }),
        (NodeId(2), Box { space: "window".to_owned(), x: 40.0, y: 0.0, width: 10.0, height: 10.0 }),
        (NodeId(3), Box { space: "window".to_owned(), x: 0.0, y: 30.0, width: 10.0, height: 10.0 }),
        (NodeId(4), Box { space: "window".to_owned(), x: 40.0, y: 30.0, width: 10.0, height: 10.0 }),
    ]);
    let context = GeometricContext { projection: &projection, geometry: &geometry, facts: None, atoms: None };
    let mut plan = GeometricPlan::seed(&context, true).unwrap();
    let original = plan.block(plan.roots[0]).unwrap().children.clone();
    apply_patterns(&mut plan, &context).unwrap();
    assert!(pattern_issues(&plan, &context).is_empty());
    assert_eq!(plan.geometric_patterns[0].kind, "grid");
    assert_eq!(plan.geometric_patterns[0].rows[0].members, original[..2]);
    assert_eq!(plan.geometric_patterns[0].remaining_keys, original[4..]);
    let mut conflicting = GeometricPlan::seed(&context, true).unwrap();
    conflicting.relations.push(Relation {
        source: NodeId(1), property: "native".to_owned(), kind: "flows_from".to_owned(),
        targets: vec![NodeId(2)], complete: true, confirmed: true,
    });
    apply_patterns(&mut conflicting, &context).unwrap();
    assert!(conflicting.geometric_patterns.is_empty());
    assert_eq!(conflicting.block(conflicting.roots[0]).unwrap().children, original);
}
