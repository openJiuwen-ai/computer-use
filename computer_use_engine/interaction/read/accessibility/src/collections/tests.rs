// Copyright (c) Huawei Technologies Co., Ltd. 2026. All rights reserved.

use super::*;
use crate::{
    geometric::GeometricContext,
    geometry::Geometry,
    projection::ProjectionOptions,
    render::atoms::{plan_atoms, AtomOptions},
    wire::decode_json,
};

fn decode(value: &str) -> Value {
    decode_json(value.as_bytes()).unwrap()
}

fn fixture(
    outline: bool,
    count: usize,
    overlap: bool,
    missing_last: bool,
    unread_issue: bool,
) -> (Projection, FactSnapshot) {
    let display_role = if outline { "outline" } else { "table" };
    let native_role = if outline { "tree" } else { "table" };
    let mut rows = Vec::new();
    let mut native = Vec::new();
    let mut native_rows = Vec::new();
    let mut positions = Vec::new();
    for index in 0..count {
        let row = 1 + index * 2;
        let cell = row + 1;
        let span = if index == 0 && overlap { 2 } else { 1 };
        let selected = index == 1;
        rows.push(format!(r#"{{"ref":"{row}","role":"row","index":{index},"disclosureLevel":1,"selected":{selected},"children":[
            {{"ref":"{cell}","role":"cell","rowRange":{{"location":{index},"length":{span}}},
             "columnRange":{{"location":0,"length":1}},"name":"Cell {cell}","children":[]}}
        ]}}"#));
        native_rows.push(format!(r#""row{row}""#));
        positions.push(index.to_string());
        native.push(format!(r#""row{row}":{{"role":"row","structure":{{"index":{index},"level":1}},"relations":{{
            "children":{{"kind":"children","targets":["cell{cell}"],"positions":[0],"total":1,"evidence":"reported","enumeration":"enumerated"}}
        }}}}"#));
        let structure = if missing_last && index + 1 == count {
            String::new()
        } else {
            format!(
                r#", "structure":{{"row_index":{index},"column_index":0,"row_span":{span},"column_span":1}}"#
            )
        };
        native.push(format!(
            r#""cell{cell}":{{"role":"cell","name":"Cell {cell}"{structure}}}"#
        ));
    }
    let row_names = native_rows.join(",");
    let row_positions = positions.join(",");
    let row_edges = |kind| {
        format!(
            r#""{kind}":{{"kind":"{kind}","targets":[{row_names}],"positions":[{row_positions}],"total":{count},"enumeration":"enumerated","evidence":"reported"}}"#
        )
    };
    let relations = if outline {
        [row_edges("children"), row_edges("rows")].join(",")
    } else {
        row_edges("children")
    };
    native.insert(0, format!(r#""table":{{"role":"{native_role}","structure":{{"row_count":{count},"column_count":1}},"relations":{{{relations}}}}}"#));
    let root = format!(
        r#"{{"ref":"0","role":"{display_role}","children":[{}]}}"#,
        rows.join(",")
    );
    let aliases = (0..=count * 2)
        .map(|id| (id.to_string(), NodeId(id)))
        .collect();
    let projection =
        Projection::prepare(decode(&root), &aliases, ProjectionOptions::default()).unwrap();
    let issues = if unread_issue {
        format!(
            r#"{{"state":"timeout","path":["nodes","cell{}","structure"]}}"#,
            count * 2
        )
    } else {
        String::new()
    };
    let observation = decode(&format!(
        r#"{{"session":"read-session","id":"read-id","nodes":{{{}}},"issues":[{issues}]}}"#,
        native.join(",")
    ));
    let facts = FactSnapshot {
        observation,
        facts: Value::Null,
        metadata: Value::Null,
        query: Value::Null,
    };
    (projection, facts)
}

fn run(projection: &Projection, facts: &FactSnapshot) -> CollectionDocument {
    let geometry = Geometry::new();
    let context = GeometricContext {
        projection,
        geometry: &geometry,
        facts: Some(facts),
        atoms: None,
    };
    let interface = GeometricPlan::seed(&context, true).unwrap();
    let atoms = plan_atoms(
        projection,
        &AtomOptions {
            compact: true,
            captured_details: true,
            ..AtomOptions::default()
        },
    )
    .unwrap();
    prepare(&interface, projection, &atoms, facts).unwrap()
}

#[test]
fn complete_native_table_keeps_independent_rows_cells_and_exact_common_prefixes() {
    let (projection, facts) = fixture(false, 2, false, false, false);
    let document = run(&projection, &facts);
    assert_eq!(document.order, [NodeId(0)]);
    assert!(document.rejected.is_empty());
    let table = &document.tables[&NodeId(0)];
    assert_eq!(table.layout, "table");
    assert_eq!(table.scope, ["read-session", "read-id", "0"]);
    assert_eq!(
        table.consumed_refs,
        [NodeId(1), NodeId(2), NodeId(3), NodeId(4)]
    );
    assert!(table.remaining_refs.is_empty());
    assert!(!table.partial);
    let prefix = table.row_prefix.as_ref().unwrap();
    assert_eq!(prefix.member_refs, [NodeId(1), NodeId(3)]);
    assert!(!prefix.states.contains(&"selected".to_owned()));
    assert_eq!(prefix.members[0].slot_keys, ["disclosureLevel"]);
    let column = table.column_prefixes[0].as_ref().unwrap();
    assert_eq!(column.member_refs, [NodeId(2), NodeId(4)]);
    assert_eq!(column.slots[0].key, "columnRange");
    assert_eq!(
        column.slots[0]
            .facts
            .iter()
            .map(|fact| fact.key.source)
            .collect::<Vec<_>>(),
        [NodeId(2), NodeId(4)]
    );
}

#[test]
fn simple_outline_defers_only_exact_original_fields_and_removes_shared_deferred_slots() {
    let (projection, facts) = fixture(true, 2, false, false, false);
    let document = run(&projection, &facts);
    let table = &document.tables[&NodeId(0)];
    assert_eq!(table.layout, "outline");
    assert_eq!(table.column_indices, [0]);
    assert_eq!(
        table
            .detail_fields
            .iter()
            .map(|item| (item.reference, item.slot.key.as_str()))
            .collect::<Vec<_>>(),
        [
            (NodeId(1), "index"),
            (NodeId(1), "disclosureLevel"),
            (NodeId(2), "rowRange"),
            (NodeId(2), "columnRange"),
            (NodeId(3), "index"),
            (NodeId(3), "disclosureLevel"),
            (NodeId(4), "rowRange"),
            (NodeId(4), "columnRange"),
        ]
    );
    assert!(table.row_prefix.as_ref().unwrap().slots.is_empty());
    assert!(table.column_prefixes[0].as_ref().unwrap().slots.is_empty());
}

#[test]
fn unavailable_suffix_can_keep_complete_prefix_but_unproved_or_overlapping_extents_cannot() {
    let (projection, facts) = fixture(true, 3, false, true, true);
    let document = run(&projection, &facts);
    let table = &document.tables[&NodeId(0)];
    assert!(table.partial);
    assert_eq!(table.layout, "table");
    assert_eq!(table.rows.len(), 2);
    assert_eq!(table.remaining_refs, [NodeId(5)]);
    assert!(table.detail_fields.is_empty());

    let (projection, facts) = fixture(true, 3, false, true, false);
    let document = run(&projection, &facts);
    assert!(document.tables.is_empty());
    assert_eq!(
        document.rejected[0].reason,
        "a captured row has conflicting or unavailable cell structure"
    );

    let (projection, facts) = fixture(false, 2, true, false, false);
    let document = run(&projection, &facts);
    assert!(document.tables.is_empty());
    assert_eq!(
        document.rejected[0].reason,
        "reported logical cell extents overlap"
    );
}
