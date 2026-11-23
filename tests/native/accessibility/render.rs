// Copyright (c) Huawei Technologies Co., Ltd. 2026. All rights reserved.

#[test]
fn whole_document_deduplicates_detail_payloads_and_keeps_scope_local_refresh() {
    let detail = RenderedSnapshot {
        detail_supplement: Some("Details\r\nRaw 東京 😀".to_owned()),
        details: "Complete details".to_owned(),
        content_directory: Some(("Directory".to_owned(), "Payload".to_owned())),
        ..Default::default()
    };
    let fresh = RenderedSnapshot {
        parts: vec![RenderedPart::Text("Fresh".to_owned())],
        ..Default::default()
    };
    let changed = RenderedSnapshot {
        parts: vec![RenderedPart::Text("Changed\u{2028}line".to_owned())],
        ..Default::default()
    };
    let parts = [
        DocumentPart::Text { text: "Heading\nTree: unchanged\n".to_owned(), full_heading: None, detail_hint: false },
        DocumentPart::Tree { snapshot: 0, indent: 1, details: true },
        DocumentPart::Text { text: "\n".to_owned(), full_heading: None, detail_hint: false },
        DocumentPart::Tree { snapshot: 0, indent: 0, details: true },
        DocumentPart::Text { text: "\n".to_owned(), full_heading: None, detail_hint: false },
        DocumentPart::Tree { snapshot: 1, indent: 0, details: false },
        DocumentPart::Text { text: "\n".to_owned(), full_heading: None, detail_hint: false },
        DocumentPart::Tree { snapshot: 2, indent: 1, details: false },
    ];
    let snapshots = [detail, fresh, changed];
    let delta = || TreeDelta::new(Vec::new(), Vec::new(), HashSet::new(), HashSet::new());
    let plan = DeltaPlan {
        mode: Mode::Unchanged,
        trees: vec![Some(delta()), Some(delta()), None],
        not_observed: HashSet::new(),
        retained: HashSet::new(),
        reason: Reason::ComparedNativeScopes,
    };
    assert_eq!(
        document(&parts, &snapshots, None).unwrap(),
        concat!(
            "Directory\n\nPayload\n\nHeading\nTree: unchanged\n",
            "\tDetails\n\tRaw 東京 😀\nDetails\r\nRaw 東京 😀\nFresh\n\tChanged\n\tline",
        )
    );
    assert_eq!(
        document(&parts, &snapshots, Some(&plan)).unwrap(),
        concat!(
            "Heading\nTree: diff\n\tDetails\n\tRaw 東京 😀\nDetails\r\nRaw 東京 😀\n",
            "Full tree for this scope:\nFresh\nPresentation refresh for this scope:\n\tChanged\n\tline",
        )
    );
}

#[test]
fn whole_document_diff_keeps_section_layout_context_hints_and_refresh() {
    let layout = |subgroup: &str| vec![("group".to_owned(), "Group 東京".to_owned()), ("subgroup".to_owned(), subgroup.to_owned())];
    let nodes = vec![
        RenderedNode {
            reference: "1".to_owned(), text: "One\nchanged payload".to_owned(), layout: layout("First"),
            ..Default::default()
        },
        RenderedNode {
            reference: "2".to_owned(), text: "Two\nhidden context payload".to_owned(), layout: layout("Second"),
            hints: vec!["\t\tOmitted: captured 😀".to_owned()], ..Default::default()
        },
        RenderedNode {
            reference: "3".to_owned(), text: "Three\nexpanded context payload".to_owned(), layout: layout("Second"),
            ..Default::default()
        },
        RenderedNode {
            reference: "4".to_owned(), text: "Four".to_owned(), hints: vec!["continuation\u{2028}text".to_owned()],
            ..Default::default()
        },
    ];
    let snapshot = RenderedSnapshot {
        parts: vec![
            RenderedPart::Literal { full: "full-only note".to_owned(), incremental: "diff note 文\n".to_owned() },
            RenderedPart::Section(RenderedSection {
                text: "packed".to_owned(), prefix: "prefix".to_owned(), suffix: "suffix".to_owned(),
                nodes, ..Default::default()
            }),
            RenderedPart::Text("\n".to_owned()),
            RenderedPart::Section(RenderedSection {
                text: "refresh packed".to_owned(), refresh: true, nodes: vec![RenderedNode {
                    reference: "5".to_owned(), text: "Five".to_owned(),
                    hints: vec!["Omitted: refresh hint".to_owned()], ..Default::default()
                }], ..Default::default()
            }),
        ], ..Default::default()
    };
    let parts = [DocumentPart::Tree { snapshot: 0, indent: 0, details: false }];
    let delta = TreeDelta::new(
        vec![("1".to_owned(), Change::Added)],
        ["x", "b", "y", "a", "b"].into_iter().map(str::to_owned).collect(),
        HashSet::from(["2".to_owned(), "3".to_owned()]),
        HashSet::from(["3".to_owned()]),
    );
    let plan = DeltaPlan {
        mode: Mode::Unchanged, trees: vec![Some(delta)],
        not_observed: HashSet::from(["b".to_owned()]), retained: HashSet::from(["a".to_owned()]),
        reason: Reason::ComparedNativeScopes,
    };
    assert_eq!(
        document(&parts, &[snapshot], Some(&plan)).unwrap(),
        concat!(
            "diff note 文\nTree: diff\nprefix\nRemoved element IDs: x, y\nNot observed in this read: b, a, b\n",
            "Group 東京\nFirst\n+One\nchanged payload\nSecond\n Two\nElement 2: Omitted: captured 😀\n",
            " Three\nexpanded context payload\ncontinuation\u{2028}text\nsuffix\n~Five",
        )
    );
}

#[test]
fn nested_modal_windows_and_value_labels_retain_their_own_scope() {
    use super::super::numbering::NodeId;
    use super::super::projection::{Projection, ProjectionOptions};
    use super::lines::value_label;

    let tree = super::super::wire::decode_json(br#"{
        "ref":"0","role":"window","children":[
            {"ref":"1","role":"window","children":[{"ref":"2","role":"sheet","modal":true,"children":[]}]},
            {"ref":"3","role":"group","children":[{"ref":"4","role":"sheet","modal":true,"children":[]}]},
            {"ref":"5","role":"group","valueIsNumber":true,"children":[{"ref":"6","role":"staticText","value":"text","children":[]}]},
            {"ref":"7","role":"group","children":[{"ref":"8","role":"staticText","value":"text","children":[]}]},
            {"ref":"9","role":"textArea","valueKind":"document","children":[]}
        ]
    }"#).unwrap();
    let identities = (0..10).map(|id| (id.to_string(), NodeId(id))).collect();
    let projection = Projection::prepare(&tree, &identities, ProjectionOptions::default()).unwrap();
    for (owner, modal) in [(0, "4"), (1, "2"), (3, "4")] {
        let node = projection.node(NodeId(owner)).unwrap();
        assert_eq!(node.field("blockedBy"), Some(&Value::Seq(vec![v(modal)])));
    }
    assert!(tree.get("blockedBy").is_none());
    for (node, expected) in [(5, "Native value"), (7, "Value"), (9, "Document text")] {
        let node = projection.node(NodeId(node)).unwrap();
        assert_eq!(value_label(&projection, node).unwrap(), expected);
    }
}

#[test]
fn related_document_keeps_crlf_notes_hints_and_original_node_context() {
    let scope = Value::Seq(vec![Value::Seq(vec![v("1"), v("related")])]);
    let original = RenderedSection {
        text: "Body\n".to_owned(),
        nodes: vec![RenderedNode {
            reference: "2".to_owned(),
            parent: Some("1".to_owned()),
            depth: 1,
            text: "\t2 text\r\nvalue".to_owned(),
            content: "\t2 text".to_owned(),
            hints: vec!["more\u{85}next".to_owned()],
            related: vec!["1".to_owned()],
            partial: true,
            layout: vec![("heading".to_owned(), "Group\rleaf".to_owned())],
        }],
        prefix: "Prefix\r".to_owned(),
        suffix: "Suffix".to_owned(),
        refresh: true,
    };
    let mut parts = vec![RenderedPart::Literal {
        full: "Full\u{2028}東京\r\n".to_owned(),
        incremental: "Diff\r\n😀\n".to_owned(),
    }];
    fallback::append_scoped(&mut parts, original, Some(&scope), None).unwrap();
    let Some(RenderedPart::Section(section)) = parts.last() else {
        panic!("missing related section");
    };
    assert_eq!(section.text, "\tBody");
    assert_eq!(section.prefix, "\tPrefix");
    assert_eq!(section.suffix, "\tSuffix");
    assert!(section.refresh);
    let node = &section.nodes[0];
    assert_eq!(node.reference, "2");
    assert_eq!(node.parent.as_deref(), Some("1"));
    assert_eq!(node.depth, 2);
    assert_eq!(node.text, "\t\t2 text\n\tvalue");
    assert_eq!(node.content, "\t\t2 text");
    assert_eq!(node.hints, ["\tmore\n\tnext"]);
    assert_eq!(node.related, ["1"]);
    assert!(node.partial);
    assert_eq!(node.layout, [("heading".to_owned(), "\tGroup\n\tleaf".to_owned())]);

    let snapshot = RenderedSnapshot { parts, ..Default::default() };
    assert_eq!(
        tree_part(&snapshot, 1, false, None, 0).0,
        "\tFull\n\t東京\n\t\n\t\n\tRelated elements (related of 1):\n\t\tBody"
    );
    let plan = DeltaPlan {
        mode: Mode::Unchanged,
        trees: vec![Some(TreeDelta::new(Vec::new(), Vec::new(), HashSet::new(), HashSet::new()))],
        not_observed: HashSet::new(),
        retained: HashSet::new(),
        reason: Reason::ComparedNativeScopes,
    };
    let (text, refreshed) = tree_part(&snapshot, 0, false, Some(&plan), 0);
    assert!(refreshed);
    assert_eq!(
        text,
        "Diff\r\n😀\n\n\nRelated elements (related of 1):\n\tPrefix\n\tGroup\n\tleaf\n~\t\t2 text\n\tvalue\n\tSuffix"
    );
}
