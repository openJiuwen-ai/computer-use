// Copyright (c) Huawei Technologies Co., Ltd. 2026. All rights reserved.

use std::collections::HashSet;

use super::super::diff::{Change, DeltaPlan, Mode, Reason, TreeDelta};
use super::super::value::Value;
use super::details::{detail_slice, element_details, DetailSource};
use super::text::{float, json, JsonFormat};
use super::*;

#[test]
fn formats_compact_fields_and_python_lines() {
    assert_eq!(indent("a\r\nb\u{85}\u{2028}z\u{1d}", 2), "\t\ta\n\t\tb\n\t\t\n\t\tz");
    assert_eq!(indent("\n\n", 1), "\t\n\t");
    assert_eq!(field("价格😀"), "价格😀");
    assert_eq!(field("x (partial:y"), "\"x (partial:y\"");
    assert_eq!(field("a\u{85}b"), "\"a\\u0085b\"");
    assert_eq!(field("\u{feff}x"), "\"\u{feff}x\"");
    assert_eq!(detail_slice("中😀a\r\n", 1, 2), Some(("😀a", 3, 5)));
}

#[test]
fn preserves_full_and_incremental_rendering_boundaries() {
    let snapshots = [RenderedSnapshot {
        parts: vec![
            RenderedPart::Section(RenderedSection {
                text: "Window\n0 pane\n1 first\n2 second\n3 third".to_owned(),
                prefix: "Window".to_owned(),
                nodes: vec![
                    RenderedNode {
                        reference: "0".to_owned(),
                        text: "0 pane\noptional annotation".to_owned(),
                        hints: vec!["\tOmitted: 9 chars".to_owned()],
                        ..RenderedNode::default()
                    },
                    RenderedNode {
                        reference: "1".to_owned(),
                        text: "1 first".to_owned(),
                        layout: vec![("key".to_owned(), "Group".to_owned())],
                        ..RenderedNode::default()
                    },
                    RenderedNode {
                        reference: "2".to_owned(),
                        text: "2 second".to_owned(),
                        layout: vec![("key".to_owned(), "Group".to_owned())],
                        ..RenderedNode::default()
                    },
                ],
                ..RenderedSection::default()
            }),
            RenderedPart::Text("\n\n".to_owned()),
            RenderedPart::Literal {
                full: "Full note".to_owned(),
                incremental: "Diff note".to_owned(),
            },
        ],
        details: "entire detail".to_owned(),
        detail_supplement: Some("detail supplement".to_owned()),
        content_directory: Some(("Directory".to_owned(), "Payload".to_owned())),
    }];
    let parts = [
        DocumentPart::Text {
            text: "App: A\nTree: full\n".to_owned(),
            full_heading: Some("App: B\nTree: full\n".to_owned()),
            detail_hint: false,
        },
        DocumentPart::Tree {
            snapshot: 0,
            indent: 0,
            details: false,
        },
        DocumentPart::Text {
            text: "\n".to_owned(),
            full_heading: None,
            detail_hint: true,
        },
        DocumentPart::Tree {
            snapshot: 0,
            indent: 1,
            details: true,
        },
    ];
    assert_eq!(
        document(&parts, &snapshots, None).unwrap(),
        "Directory\n\nPayload\n\nApp: B\nTree: full\nWindow\n0 pane\n1 first\n2 second\n3 third\n\nFull note\n\tdetail supplement\nCaptured details available."
    );

    let delta = TreeDelta::new(
        vec![("1".to_owned(), Change::Modified), ("2".to_owned(), Change::Added)],
        vec!["7".to_owned(), "8".to_owned(), "9".to_owned()],
        HashSet::from(["0".to_owned()]),
        HashSet::new(),
    );
    let plan = DeltaPlan {
        mode: Mode::Diff,
        trees: vec![Some(delta), None],
        not_observed: HashSet::from(["8".to_owned()]),
        retained: HashSet::from(["9".to_owned()]),
        reason: Reason::ComparedNativeScopes,
    };
    assert_eq!(
        document(&parts, &snapshots, Some(&plan)).unwrap(),
        "App: A\nTree: diff\nWindow\nRemoved element IDs: 7\nNot observed in this read: 8, 9\n 0 pane\nElement 0: Omitted: 9 chars\nGroup\n~1 first\n+2 second\n\nDiff note\nFull tree for this scope:\n\tdetail supplement\nCaptured details available."
    );
}

fn v(value: &str) -> Value {
    Value::Text(value.to_owned())
}

fn map(items: &[(&str, Value)]) -> Value {
    Value::Map(
        items
            .iter()
            .map(|(key, value)| ((*key).to_owned(), value.clone()))
            .collect(),
    )
}

#[test]
fn captured_element_retains_uncollected_targets_and_issue_ordinals() {
    let facts = map(&[
        ("observed", map(&[])),
        (
            "nodes",
            Value::Seq(vec![map(&[
                ("node", v("original")),
                ("values", map(&[("name", v("名称")), ("role", v("button"))])),
                ("fieldStatus", map(&[("name", v("retained"))])),
                ("requestedFields", Value::Seq(vec![v("name"), v("role")])),
                ("actions", Value::Seq(Vec::new())),
                ("observed", Value::Bool(false)),
            ])]),
        ),
        (
            "relations",
            Value::Seq(vec![map(&[
                ("source", v("original")),
                ("property", v("AXChildren")),
                ("recordKey", v("edge")),
                ("targets", Value::Seq(vec![v("uncollected"), v("original")])),
                ("kind", v("children")),
            ])]),
        ),
    ]);
    let issues = [
        map(&[
            ("path", Value::Seq(vec![v("nodes"), v("elsewhere")])),
            ("state", v("partial")),
        ]),
        map(&[
            ("path", Value::Seq(vec![v("nodes"), v("original")])),
            ("state", v("partial")),
        ]),
        map(&[
            ("path", Value::Seq(vec![v("roots")])),
            ("state", v("unavailable")),
            ("detail", v("old\u{85}new")),
        ]),
    ];
    let source = DetailSource::new("test", "s", "i", &[], &facts, &issues, [("5", "original")]).unwrap();
    assert_eq!(
        element_details("5", &[source]).unwrap(),
        concat!(
            "Captured element 5:\n\n",
            "Source: {\"backend\":\"test\",\"session\":\"s\",\"observation\":\"i\",\"node\":\"original\",\"scope\":[],\"observed\":{}}\n",
            "Collection: referenced element was not expanded; retained boundary facts follow.\n",
            "5 native element\n\t\"role\": \"button\"\n\t\"name\": \"名称\"\n\t\"name\" status: \"retained\"\n",
            "\tNative actions: []\n\tRelation \"AXChildren\": {\"recordKey\":\"edge\",\"kind\":\"children\",\"targets\":[{\"native\":\"uncollected\",\"ref\":null},\"5\"]}\n",
            "Native read issues {\"session\":\"s\",\"observation\":\"i\"}:\n",
            "\t{\"index\":1,\"ref\":\"5\",\"issue\":{\"path\":[\"nodes\",\"original\"],\"state\":\"partial\",\"detail\":null,\"continuation\":null,\"alternatives\":[]}}\n",
            "\t{\"index\":2,\"ref\":null,\"issue\":{\"path\":[\"roots\"],\"state\":\"unavailable\",\"detail\":\"old\\u0085new\",\"continuation\":null,\"alternatives\":[]}}",
        )
    );
}

#[test]
fn diagnostic_and_literal_values_keep_their_distinct_number_and_key_rules() {
    let value = map(&[
        ("10", Value::Float(-0.0)),
        ("a", v("\u{7f}")),
        ("2", Value::Float(1e-5)),
    ]);
    assert_eq!(
        json(&value, JsonFormat::Literal).unwrap(),
        r#"{"10":-0.0,"a":"\u007f","2":1e-05}"#
    );
    assert_eq!(
        json(&value, JsonFormat::Diagnostic).unwrap(),
        "{\"2\":0.00001,\"10\":0,\"a\":\"\u{7f}\"}"
    );
    assert_eq!(float(1e16, JsonFormat::Literal), "1e+16");
    assert_eq!(float(1e16, JsonFormat::Diagnostic), "10000000000000000");
    assert_eq!(float(1e-4, JsonFormat::Literal), "0.0001");
    assert_eq!(float(5e-324, JsonFormat::Literal), "5e-324");
}

#[test]
fn projected_lines_preserve_value_paragraphs_focus_and_action_uncertainty() {
    use std::collections::HashMap;

    use super::super::numbering::NodeId;
    use super::super::projection::{Projection, ProjectionOptions};
    use super::lines::{render_projection, LineOptions};

    let tree = map(&[
        ("ref", v("0")),
        ("role", v("window")),
        ("name", v("Demo")),
        (
            "children",
            Value::Seq(vec![
                map(&[
                    ("ref", v("1")),
                    ("role", v("textField")),
                    ("name", v("名称")),
                    ("value", v("a\r\nb")),
                    ("focused", Value::Bool(false)),
                    (
                        "selectionRange",
                        map(&[("location", Value::Int(0)), ("length", Value::Int(1))]),
                    ),
                    (
                        "capabilities",
                        map(&[
                            ("click", v("available")),
                            ("focus", v("available")),
                            ("typeText", v("available")),
                            ("replaceText", v("available")),
                        ]),
                    ),
                    ("children", Value::Seq(vec![])),
                ]),
                map(&[
                    ("ref", v("2")),
                    ("role", v("button")),
                    ("name", v("OK")),
                    ("enabled", Value::Bool(false)),
                    (
                        "capabilities",
                        map(&[
                            ("click", v("unknown")),
                            ("focus", v("unknown")),
                            ("typeText", v("unavailable")),
                            ("replaceText", v("unavailable")),
                        ]),
                    ),
                    ("children", Value::Seq(vec![])),
                ]),
            ]),
        ),
    ]);
    let ids = HashMap::from([
        ("0".to_owned(), NodeId(0)),
        ("1".to_owned(), NodeId(1)),
        ("2".to_owned(), NodeId(2)),
    ]);
    let projection = Projection::prepare(tree, &ids, ProjectionOptions::default()).unwrap();
    let options = LineOptions {
        app: Some("Test"),
        focused: Some("1"),
        compact: true,
        ..LineOptions::default()
    };
    let render = render_projection(&projection, &options).unwrap();
    assert_eq!(
        render.section.text,
        concat!(
            "Window: \"Demo\", App: Test.\n",
            "Tree: full (current UI only)\n",
            "0 window Demo\n",
            "\t1 text field (text-input, focused) 名称, Focus flag: false, Selection: 0:1\n",
            "\t\tValue:\n",
            "\t\t| a\n",
            "\t\t| b\n",
            "\t2 button (reported disabled) OK, Click: deferred\n",
            "\nReported focus: 1 text field 名称; text selection: 1 UTF-16 units at offset 0",
        )
    );
    assert_eq!(
        render.section.nodes[1].content,
        "\t1 text field 名称, Focus flag: false, Selection: 0:1"
    );
}

#[test]
fn geometric_row_preserves_independent_target_and_region_context() {
    use std::collections::HashMap;

    use super::super::geometric::{Block, GeometricContext, GeometricPlan};
    use super::super::numbering::NodeId;
    use super::super::projection::{Projection, ProjectionOptions};
    use super::atoms::{plan_atoms, AtomOptions};
    use super::geometric::{render_geometric, WriterOptions};

    let leaf = |reference, name| {
        map(&[
            ("ref", v(reference)),
            ("role", v("button")),
            ("name", v(name)),
            ("children", Value::Seq(vec![])),
        ])
    };
    let tree = map(&[
        ("ref", v("0")),
        ("role", v("window")),
        ("name", v("Demo")),
        (
            "children",
            Value::Seq(vec![map(&[
                ("ref", v("1")),
                ("role", v("toolbar")),
                ("name", v("Tools")),
                ("children", Value::Seq(vec![leaf("2", "Open"), leaf("3", "保存")])),
            ])]),
        ),
    ]);
    let ids = HashMap::from([
        ("0".to_owned(), NodeId(0)),
        ("1".to_owned(), NodeId(1)),
        ("2".to_owned(), NodeId(2)),
        ("3".to_owned(), NodeId(3)),
    ]);
    let projection = Projection::prepare(tree, &ids, ProjectionOptions::default()).unwrap();
    let atoms = plan_atoms(
        &projection,
        &AtomOptions {
            compact: true,
            ..AtomOptions::default()
        },
    )
    .unwrap();
    let geometry = HashMap::new();
    let evidence = atoms.layout_evidence();
    let context = GeometricContext {
        projection: &projection,
        geometry: &geometry,
        facts: None,
        atoms: Some(&evidence),
    };
    let mut plan = GeometricPlan::seed(&context, true).unwrap();
    let toolbar = plan.source_block(NodeId(1)).unwrap();
    let children = plan.block(toolbar).unwrap().children.clone();
    let mut row = Block::new("row:fixture".to_owned(), "row", None, children);
    row.layout = "visual-row";
    row.origin = "visual";
    let row = plan.insert(row);
    plan.block_mut(toolbar).unwrap().children = vec![row];
    let plan = plan.freeze().unwrap();
    let options = WriterOptions::from_plans(&plan, &atoms, None, None, None, None).unwrap();
    let rendered = render_geometric(&plan, &projection, &atoms, &options).unwrap().section;
    assert_eq!(
        rendered.text,
        "0 window Demo\n\tToolbar: 1 toolbar Tools\n\t\t2 button Open | 3 button 保存"
    );
    assert_eq!(rendered.nodes[3].text, "\t\t3 button 保存");
    assert_eq!(rendered.nodes[3].parent.as_deref(), Some("1"));
    assert_eq!(
        rendered.nodes[3].layout,
        vec![("element:1".to_owned(), "\tToolbar: 1 toolbar Tools".to_owned())]
    );
}

#[test]
fn atom_finalization_uses_complete_captions_for_same_url_peers() {
    use std::collections::{HashMap, HashSet};

    use super::super::facts::FactSnapshot;
    use super::super::numbering::NodeId;
    use super::super::projection::{CompressionEvidence, FactKey, Projection, ProjectionOptions};
    use super::atoms::{finalize_atoms, plan_atoms, AtomFinalizeOptions, AtomOptions};

    let url = "https://example.test/destination";
    let link = |reference| {
        map(&[
            ("ref", v(reference)),
            ("role", v("link")),
            ("name", v("Caption")),
            ("url", v(url)),
            ("children", Value::Seq(vec![])),
        ])
    };
    let tree = map(&[
        ("ref", v("0")),
        ("role", v("window")),
        ("children", Value::Seq(vec![link("1"), link("2")])),
    ]);
    let aliases = HashMap::from([
        ("0".to_owned(), NodeId(0)),
        ("1".to_owned(), NodeId(1)),
        ("2".to_owned(), NodeId(2)),
    ]);
    let projection = Projection::prepare(tree, &aliases, ProjectionOptions::default()).unwrap();
    let evidence = CompressionEvidence {
        complete_text: HashSet::from([
            FactKey {
                source: NodeId(1),
                attribute: "name".to_owned(),
            },
            FactKey {
                source: NodeId(2),
                attribute: "name".to_owned(),
            },
        ]),
        ..CompressionEvidence::default()
    };
    let tokens = ["window".to_owned(), "first".to_owned(), "second".to_owned()];
    let original = map(&[
        ("session", v("session")),
        ("id", v("observation")),
        (
            "nodes",
            map(&[
                ("window", map(&[])),
                ("first", map(&[("url", v(url))])),
                ("second", map(&[("url", v(url))])),
            ]),
        ),
    ]);
    let row = |reference, token| {
        map(&[
            ("ref", v(reference)),
            ("key", v("url")),
            (
                "value",
                map(&[
                    ("field", v("url")),
                    ("node", v(token)),
                    ("path", Value::Seq(vec![v("url")])),
                    ("normalized", v(url)),
                ]),
            ),
        ])
    };
    let semantic = map(&[
        (
            "refs",
            map(&[("0", v("window")), ("1", v("first")), ("2", v("second"))]),
        ),
        ("fields", Value::Seq(vec![row("1", "first"), row("2", "second")])),
    ]);
    let mut snapshot = FactSnapshot {
        observation: original,
        facts: map(&[("nodes", Value::Seq(vec![]))]),
        metadata: Value::Null,
        query: Value::Null,
    };

    for conflicting in [false, true] {
        if conflicting {
            let Value::Map(fields) = &mut snapshot.observation else {
                unreachable!()
            };
            fields.push((
                "issues".to_owned(),
                Value::Seq(vec![map(&[
                    ("state", v("unavailable")),
                    ("path", Value::Seq(vec![v("nodes"), v("second"), v("url")])),
                ])]),
            ));
        }
        let mut atoms = plan_atoms(
            &projection,
            &AtomOptions {
                compact: true,
                ..AtomOptions::default()
            },
        )
        .unwrap();
        let options = AtomFinalizeOptions {
            compact: true,
            snapshot: Some(&snapshot),
            semantic: Some(&semantic),
            evidence: Some(&evidence),
            tokens: &tokens,
            ..AtomFinalizeOptions::default()
        };
        finalize_atoms(&mut atoms, &projection, &options).unwrap();
        for id in [NodeId(1), NodeId(2)] {
            assert_eq!(atoms.atoms[&id].slots.iter().any(|slot| slot.key == "url"), conflicting);
        }
        if conflicting {
            assert!(atoms.deferred_native_urls.is_empty());
        } else {
            assert_eq!(atoms.deferred_native_urls.len(), 2);
            assert_eq!(atoms.deferred_native_url_peers.len(), 1);
        }
    }
}

#[test]
fn multiline_finalization_requires_complete_original_provenance() {
    use std::collections::{HashMap, HashSet};

    use super::super::numbering::NodeId;
    use super::super::projection::{CompressionEvidence, FactKey, Projection, ProjectionOptions};
    use super::atoms::{finalize_atoms, plan_atoms, AtomFinalizeOptions, AtomOptions, Placement};

    let tree = map(&[
        ("ref", v("0")),
        ("role", v("window")),
        (
            "children",
            Value::Seq(vec![map(&[
                ("ref", v("1")),
                ("role", v("textField")),
                ("value", v("甲\n乙\r\n丙\n丁")),
                ("children", Value::Seq(vec![])),
            ])]),
        ),
    ]);
    let ids = HashMap::from([("0".to_owned(), NodeId(0)), ("1".to_owned(), NodeId(1))]);
    let projection = Projection::prepare(tree, &ids, ProjectionOptions::default()).unwrap();
    let evidence = CompressionEvidence {
        complete_text: HashSet::from([FactKey {
            source: NodeId(1),
            attribute: "value".to_owned(),
        }]),
        ..CompressionEvidence::default()
    };
    let mut atoms = plan_atoms(
        &projection,
        &AtomOptions {
            compact: true,
            ..AtomOptions::default()
        },
    )
    .unwrap();
    assert_eq!(atoms.atoms[&NodeId(1)].slots[0].placement, Placement::Block);
    finalize_atoms(
        &mut atoms,
        &projection,
        &AtomFinalizeOptions {
            evidence: Some(&evidence),
            ..AtomFinalizeOptions::default()
        },
    )
    .unwrap();
    assert_eq!(atoms.atoms[&NodeId(1)].slots[0].placement, Placement::Inline);
}

#[test]
fn common_role_alias_requires_exact_same_capture_evidence() {
    use std::collections::{HashMap, HashSet};

    use super::super::facts::FactSnapshot;
    use super::super::numbering::NodeId;
    use super::super::projection::{CompressionEvidence, Projection, ProjectionOptions};
    use super::atoms::{plan_atoms, policy, AtomOptions};

    let tree = map(&[
        ("ref", v("0")),
        ("role", v("window")),
        ("roleDescription", v("标准窗口")),
        ("subrole", v("standard")),
        ("children", Value::Seq(vec![])),
    ]);
    let aliases = HashMap::from([("0".to_owned(), NodeId(0))]);
    let options = ProjectionOptions {
        compact: true,
        public_api: true,
        share_descriptions: true,
        ..ProjectionOptions::default()
    };
    let projection = Projection::prepare(tree, &aliases, options).unwrap();
    let native = map(&[
        ("role", v("window")),
        ("role_description", v("标准窗口")),
        ("role_detail", v("standard")),
    ]);
    let mut snapshot = FactSnapshot {
        observation: map(&[
            ("backend", v("macos")),
            ("session", v("s")),
            ("id", v("obs")),
            ("nodes", map(&[("token", native.clone())])),
        ]),
        facts: map(&[(
            "nodes",
            Value::Seq(vec![map(&[("node", v("token")), ("values", native)])]),
        )]),
        metadata: Value::Null,
        query: Value::Null,
    };
    let evidence = |property, attribute, value| {
        map(&[
            ("ref", v("0")),
            ("key", v(property)),
            (
                "value",
                map(&[
                    ("node", v("token")),
                    ("representation", v("field")),
                    ("path", Value::Seq(vec![v(property)])),
                    ("field", v(attribute)),
                    ("normalized", v(value)),
                ]),
            ),
        ])
    };
    let semantic = map(&[
        ("refs", map(&[("0", v("token"))])),
        (
            "fields",
            Value::Seq(vec![
                evidence("role", "role", "window"),
                evidence("role_description", "roleDescription", "标准窗口"),
                evidence("role_detail", "subrole", "standard"),
            ]),
        ),
    ]);
    for restricted in [false, true] {
        if restricted {
            let Value::Map(fields) = &mut snapshot.observation else {
                unreachable!()
            };
            fields.push((
                "issues".to_owned(),
                Value::Seq(vec![map(&[
                    ("state", v("unavailable")),
                    ("path", Value::Seq(vec![v("nodes"), v("token"), v("role_description")])),
                ])]),
            ));
        }
        let detail = policy::plan(
            &snapshot,
            &semantic,
            &projection,
            &CompressionEvidence::default(),
            &HashSet::new(),
        )
        .unwrap();
        let options = AtomOptions {
            compact: true,
            captured_details: true,
            deferred: Some(&detail.deferred),
            role_aliases: Some(&detail.role_aliases),
            ..AtomOptions::default()
        };
        let atoms = plan_atoms(&projection, &options).unwrap();
        assert_eq!(
            atoms.atoms[&NodeId(0)].role,
            if restricted { "标准窗口" } else { "standard window" }
        );
        assert_eq!(detail.destinations.len(), usize::from(!restricted));
    }
}
