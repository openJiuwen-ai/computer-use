// Copyright (c) Huawei Technologies Co., Ltd. 2026. All rights reserved.

#[test]
fn owned_multiline_value_preserves_a_published_caption_and_its_source() {
    let body = value("{\"editor.tabSize\":2}\n");
    let references = aliases(&["0", "1", "2"]);
    let evidence = CompressionEvidence {
        complete_text: [NodeId(1), NodeId(2)].into_iter().map(|source| FactKey {
            source, attribute: "value".to_owned(),
        }).collect(),
        complete_children: HashMap::from([(NodeId(0), vec![NodeId(1)]), (NodeId(1), vec![NodeId(2)])]),
        ..Default::default()
    };
    for published in [false, true] {
        let mut fields = vec![("value", body.clone())];
        if published {
            fields.extend([("visualLabelRef", value("2")), ("_contextRefs", Value::Seq(vec![value("2")]))]);
        }
        let caption = node("2", "staticText", vec![], &[("value", body.clone())]);
        let editor = node("1", "textArea", vec![caption], &fields);
        let root = node("0", "window", vec![editor], &[]);
        let options = ProjectionOptions { compact: true, public_api: true, ..Default::default() };
        let mut projection = Projection::prepare(&root, &references, options).unwrap();
        projection.merge_owned_passive_content(&evidence).unwrap();
        let editor = projection.node(NodeId(1)).unwrap();
        assert_eq!(editor.field("value"), Some(&body));
        assert_eq!(editor.native_children, [NodeId(2)]);
        assert_eq!(editor.field("visualLabelRef"), published.then_some(&value("2")));
        assert_eq!(projection.visible_order().unwrap().contains(&NodeId(2)), published);
        let source = projection.node(NodeId(2)).unwrap();
        assert_eq!(source.reference, "2");
        assert_eq!(field(&source.native_fields, "value"), Some(&body));
    }
}

fn derived_button_caption(values: &[&str], boundary: &str) -> (Projection, CompressionEvidence) {
    let actionable = |click| [
        ("capabilities", Value::Map(vec![("click".into(), value(click))])),
        ("actions", Value::Seq(vec![Value::Map(vec![("kind".into(), value("invoke"))])])),
    ];
    let mut children = Vec::new();
    let mut parts = Vec::new();
    let mut evidence = CompressionEvidence::default();
    evidence.unlabelled_groups.insert(NodeId(0));
    for attribute in ["name", "description"] {
        evidence.complete_text.insert(FactKey { source: NodeId(0), attribute: attribute.into() });
    }

    for (index, text) in values.iter().enumerate() {
        let id = NodeId(index + 1);
        let click = if boundary == "unavailable" && index + 1 == values.len() { "unavailable" } else { "available" };
        let mut fields = actionable(click).to_vec();
        fields.push(("value", value(text)));
        if boundary == "incomplete" && index + 1 == values.len() {
            fields.push(("childrenStart", Value::Int(0)));
        }
        let fact = FactKey { source: id, attribute: "value".into() };
        evidence.complete_text.insert(fact.clone());
        evidence.complete_children.insert(id, Vec::new());
        parts.push(TextSource { fact, field: "name".into(), text: value(text) });
        children.push(node(&id.0.to_string(), "staticText", Vec::new(), &fields));
    }
    evidence.complete_children.insert(NodeId(0), (1..=values.len()).map(NodeId).collect());

    let mut fields = actionable("available").to_vec();
    fields.extend([
        ("name", value("")), ("description", value("")), ("_factBacked", Value::Bool(true)),
        ("control", Value::Map(vec![("kind".into(), value("button"))])),
    ]);
    let options = ProjectionOptions {
        compact: true,
        public_api: true,
        share_descriptions: boundary != "unshared",
        subtree: (boundary == "subtree").then_some(NodeId(0)),
        focused: (boundary == "focus").then_some(NodeId(1)),
        ..ProjectionOptions::default()
    };
    let aliases = (0..=values.len()).map(|index| (index.to_string(), NodeId(index))).collect();
    let mut projection = Projection::prepare(node("0", "group", children, &fields), &aliases, options).unwrap();
    if boundary == "targeted" {
        projection.targets.push(("1".into(), vec!["0".into()]));
    }
    projection.adopt(NodeId(0), &parts, "name", " ", true).unwrap();
    (projection, evidence)
}

#[test]
fn derived_button_caption_retains_native_values_actions_and_destinations() {
    let long_caption = "😀".repeat(600);
    for (values, kind) in [
        (&["A"][..], "repeatedDerivedSingleChildName"),
        (&["B", "A"][..], "repeatedDerivedContainerName"),
        (&[long_caption.as_str(), "A"][..], "repeatedDerivedContainerName"),
    ] {
        let (mut projection, evidence) = derived_button_caption(values, "");
        let old_name = projection.node(NodeId(0)).unwrap().field("name").cloned().unwrap();
        projection.deduplicate_derived_captions(&evidence).unwrap();

        let root = projection.node(NodeId(0)).unwrap();
        assert_eq!(root.field("name"), Some(&value("")));
        let actions = root.field("actions").unwrap().as_seq().unwrap();
        assert_eq!(actions[0].get("kind"), Some(&value("invoke")));
        let [change] = projection.changes.as_slice() else { panic!("Expected a recorded caption suppression") };
        assert_eq!(change.get("kind"), Some(&value(kind)));
        assert_eq!(change.get("sourceRef"), Some(&value("0")));
        assert_eq!(change.get("field"), Some(&value("name")));
        assert_eq!(change.get("previous"), Some(&old_name));
        let destinations = change.get("destinations").unwrap().as_seq().unwrap();
        let targets: Vec<_> = (1..=values.len()).map(|index| value(&index.to_string())).collect();
        assert_eq!(change.get("primaryTargets"), Some(&Value::Seq(targets.clone())));
        assert_eq!(destinations.len(), values.len());
        for (index, destination) in destinations.iter().enumerate() {
            let original_value = value(values[index]);
            let child = projection.node(NodeId(index + 1)).unwrap();
            assert_eq!(child.field("value"), Some(&original_value));
            assert_eq!(destination.get("value"), Some(&original_value));
            assert_eq!(destination.get("sourceRef"), Some(&targets[index]));
            assert_eq!(destination.get("targetRef"), Some(&targets[index]));
            assert_eq!(destination.get("attribute"), Some(&value("value")));
            assert_eq!(destination.get("field"), Some(&value("value")));
        }
    }
}

#[test]
fn derived_button_caption_is_retained_without_exact_destinations() {
    let oversized_caption = format!("{}A", "😀".repeat(600));
    for (values, boundary) in [
        (&["A"][..], "focus"), (&["A"][..], "unshared"),
        (&["B", "A"][..], "unavailable"), (&["B", "A"][..], "incomplete"),
        (&["B", "A"][..], "targeted"), (&["B", "A"][..], "subtree"),
        (&[oversized_caption.as_str(), "B"][..], "oversized"), (&["A;BaSe64,B", "C"][..], "encoded"),
    ] {
        let (mut projection, evidence) = derived_button_caption(values, boundary);
        let old_name = projection.node(NodeId(0)).unwrap().field("name").cloned().unwrap();
        projection.deduplicate_derived_captions(&evidence).unwrap();
        assert_eq!(projection.node(NodeId(0)).unwrap().field("name"), Some(&old_name), "{boundary}");
        assert!(projection.changes.is_empty(), "{boundary}");
    }
}
