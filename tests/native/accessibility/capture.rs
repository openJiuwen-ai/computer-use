// Copyright (c) Huawei Technologies Co., Ltd. 2026. All rights reserved.

    #[test]
    fn local_presentation_failure_retains_delta_and_reuses_the_captured_read() {
        let capture = |field_value: &str| {
            let wire = format!(
                r#"{{"kind":"observation","session":"driver","platform":"macos","roots":["window","related"],
                "nodes":[
                    {{"id":"window","source":"macos.ax","kind":"element","nativeType":"AXUIElement","properties":[
                        {{"key":["macos.ax","AXRole"],"value":{{"nativeType":"CFString","data":"AXWindow"}}}},
                        {{"key":["macos.ax","AXTitle"],"value":{{"nativeType":"CFString","data":"Form"}}}},
                        {{"key":["macos.ax","AXChildren"],"value":{{"nativeType":"CFArray","data":{{"items":[
                            {{"nativeType":"AXUIElement","data":{{"ref":"field"}}}},
                            {{"nativeType":"AXUIElement","data":{{"ref":"unchanged"}}}}
                        ]}}}},"read":{{"complete":true,"details":[
                            ["relation",{{"nativeType":"CFBoolean","data":true}}],
                            ["certainty",{{"nativeType":"CFString","data":"confirmed"}}],
                            ["state",{{"nativeType":"CFString","data":"complete"}}],
                            ["total",{{"nativeType":"CFNumber","data":2}}]
                        ]}}}}
                    ]}},
                    {{"id":"field","source":"macos.ax","kind":"element","nativeType":"AXUIElement","properties":[
                        {{"key":["macos.ax","AXRole"],"value":{{"nativeType":"CFString","data":"AXTextField"}}}},
                        {{"key":["macos.ax","AXTitle"],"value":{{"nativeType":"CFString","data":"Subject"}}}},
                        {{"key":["macos.ax","AXValue"],"value":{{"nativeType":"CFString","data":"{field_value}"}}}}
                    ]}},
                    {{"id":"unchanged","source":"macos.ax","kind":"element","nativeType":"AXUIElement","properties":[
                        {{"key":["macos.ax","AXRole"],"value":{{"nativeType":"CFString","data":"AXButton"}}}},
                        {{"key":["macos.ax","AXTitle"],"value":{{"nativeType":"CFString","data":"Unchanged control"}}}}
                    ]}},
                    {{"id":"related","source":"macos.ax","kind":"element","nativeType":"AXUIElement","properties":[
                        {{"key":["macos.ax","AXRole"],"value":{{"nativeType":"CFString","data":"AXButton"}}}},
                        {{"key":["macos.ax","AXTitle"],"value":{{"nativeType":"CFString","data":"Related option"}}}}
                    ]}}
                ],"read":{{"intervalNs":[1,2],"details":[
                    ["observation_id",{{"nativeType":"CFString","data":"{field_value}"}}],
                    ["surface",{{"nativeType":"CFString","data":"surface"}}]
                ]}}}}"#
            );
            ReadCapture::from_wire_value(&json(&wire), Some("window".into()), vec![]).unwrap()
        };
        let before = capture("Before");
        let after = capture("Changed");
        let aliases = before.initial_aliases(&mut SessionAliases::new("driver".into())).unwrap();
        let bindings = HashMap::from([
            ("0".into(), "window".into()), ("1".into(), "field".into()),
            ("2".into(), "unchanged".into()), ("3".into(), "related".into()),
        ]);
        let options = ProjectionOptions {
            compact: true, public_api: true, defer_visual_composition: true, ..ProjectionOptions::default()
        };
        let display = DisplayOptions { compact: true, public_api: true, details: false };
        let compare = |snapshot: &CompactSnapshot| {
            snapshot.comparison(vec![Some("surface".into())], display, 0, &mut Default::default()).unwrap()
        };
        let normal = |value: &Arc<ReadCapture>| {
            CompactSnapshot::prepare(value.prepare(&aliases, options.clone()).unwrap(), None, 0).unwrap()
        };
        let prior = ComparisonDocument { trees: vec![compare(&normal(&before)).comparison] };
        let render_delta = |baseline: &ComparisonDocument, snapshot: &CompactSnapshot, comparison: &CompactSnapshot| {
            let delta = diff::plan_document_diff(
                Some(baseline), Some(&[compare(comparison)]), &bindings, &bindings, &HashSet::new(),
            ).unwrap();
            let document = [DocumentPart::Tree { snapshot: 0, indent: 0, details: false }];
            render::document(&document, &[snapshot.render(&mut Default::default()).unwrap().body], Some(&delta)).unwrap()
        };

        // Fail only the second projection after the exact raw capture was admitted.
        let mut semantic = after.project(&aliases).unwrap();
        let Some(Value::Seq(roots)) = semantic.get_mut("roots") else { panic!("semantic roots") };
        let [_, Value::Map(related)] = roots.as_mut_slice() else { panic!("two source roots") };
        related.retain(|(name, _)| name != "role");
        let preparation = after.prepare_projected_checked(
            &aliases, Arc::new(semantic), options.clone(), || Ok::<_, CaptureError>(()),
        ).unwrap();
        let preparation = CompactSnapshot::prepare(preparation, None, 0).unwrap();

        // Separately fail only that root's terminal formatting after successful preparation.
        let mut terminal = normal(&after);
        let [_, CompactBranch::Projected(related)] = terminal.roots.as_mut_slice() else { panic!("two prepared roots") };
        related.interface.roots.push(BlockId(usize::MAX));

        let recovered = normal(&after);
        for (failed, comparison, preparation_failed) in
            [(&preparation, &preparation, true), (&terminal, &recovered, false)]
        {
            // A terminal renderer fault must not corrupt the original comparison layout.
            let diff = render_delta(&prior, failed, comparison);
            assert!(diff.contains("Tree: diff") && diff.contains("Changed") && diff.contains("Related option"), "{diff}");
            assert!(diff.contains("Presentation fallback") && !diff.contains("Unchanged control"), "{diff}");
            assert!(!diff.contains("Full tree"), "{diff}");
            let delivered = ComparisonDocument { trees: vec![compare(comparison).comparison] };
            if preparation_failed {
                let same_fault = render_delta(&delivered, failed, comparison);
                assert!(same_fault.contains("Tree: unchanged"), "{same_fault}");
                assert!(!same_fault.contains("Related option") && !same_fault.contains("Changed"), "{same_fault}");
            }
            // Retry preparation/rendering against the same Arc, retaining native refs and the committed diff baseline.
            let retry = render_delta(&delivered, &recovered, &recovered);
            assert!(!retry.contains("Full tree") && !retry.contains("Unchanged control"), "{retry}");
            assert!(!retry.contains("Presentation fallback") && !retry.contains("Changed"), "{retry}");
        }
    }
