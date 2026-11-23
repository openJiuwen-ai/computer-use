// Copyright (c) Huawei Technologies Co., Ltd. 2026. All rights reserved.

    #[test]
    fn native_field_claims_require_exact_values_and_captured_continuations() {
        let snapshot = FactSnapshot {
            observation: json(r#"{"backend":"macos","session":"session","id":"capture"}"#),
            facts: json(r#"{"nodes":[
                {"node":"a","values":{"raw":0,"note":"alpha","title":1,"range":{"location":1,"length":2},"line":"captured"},
                    "fieldStatus":{"line":{"textRef":"capture-token"}},"actions":{"state":"complete","items":[]}},
                {"node":"b","values":{"line":"captured"},"fieldStatus":{"line":{"state":"partial"}},
                    "actions":{"state":"complete","items":[]}}
            ]}"#),
            metadata: Value::Null,
            query: Value::Null,
        };
        let semantic = json(r#"{"refs":{"0":"a","1":"b"},"fields":[
            {"ref":"0","key":"raw","value":{"representation":"raw"}},
            {"ref":"0","key":"note","value":{"representation":"annotation"}},
            {"ref":"0","key":"title","value":{"representation":"field","field":"title","normalized":1}},
            {"ref":"0","key":"range","value":{"representation":"projection","projections":[
                {"field":"location","normalized":1},{"field":"length","normalized":2}]}},
            {"ref":"0","key":"line","value":{"representation":"field","field":"value","normalized":"captured"}},
            {"ref":"1","key":"line","value":{"representation":"field","field":"value","normalized":"captured"}}
        ]}"#);
        let tokens = ["a".to_owned(), "b".to_owned()];
        let request = AccountingRequest {
            snapshot: &snapshot, semantic: &semantic, tokens: &tokens, parts: &[],
            compact: true, public_api: true, share_descriptions: true,
        };
        let sources = Sources::new(&request).unwrap();
        let claim = |source, attribute: &str, value, kind, target| Claim {
            fact: crate::projection::FactKey { source: NodeId(source), attribute: attribute.to_owned() },
            value, kind, target: NodeId(target),
        };
        use ClaimKind::{Continuation, Display, Equivalent};
        let mut claims = [
            claim(0, "title", Value::Bool(true), Display, 9),
            claim(0, "location", Value::Int(1), Continuation, 9),
            claim(0, "location", Value::Int(1), Display, 0),
            claim(0, "length", Value::Int(2), Equivalent, 1),
            claim(0, "value", text("captured"), Continuation, 0),
            claim(1, "value", text("captured"), Continuation, 1),
        ];
        let members = [NodeId(0), NodeId(1)];
        let account = |claims: &[Claim]| {
            let part = AccountingPart { claims, uncompressed: false, ..AccountingPart::uncompressed(&members) };
            let mut groups = Groups::default();
            fields(&sources, &part, &mut groups).map(|ledger| (ledger, groups.rows))
        };
        let (ledger, groups) = account(&claims).unwrap();
        assert!(exact(&ledger, &json(r#"{"values":[
            {"ref":"0","property":"note","kind":"annotation"},
            {"ref":"0","property":"title","kind":"supplement"},
            {"ref":"0","property":"range","kind":"equivalent","projections":[
                {"field":"location","target":"0","kind":"display"},{"field":"length","target":"1","kind":"equivalent"}]},
            {"ref":"0","property":"line","kind":"continuation","target":"0"},
            {"ref":"1","property":"line","kind":"supplement"}
        ],"actions":[{"ref":"0","kind":"supplement"},{"ref":"1","kind":"supplement"}]}"#)));
        assert_eq!(groups, [
            ("title: 1".to_owned(), vec!["0".to_owned()]),
            ("Native actions: {\"state\":\"complete\",\"items\":[]}".to_owned(), vec!["0".to_owned(), "1".to_owned()]),
            ("line: \"captured\"".to_owned(), vec!["1".to_owned()]),
        ]);

        claims[0].value = Value::Int(1);
        assert!(matches!(account(&claims), Err(TextError::Invalid("native source index"))));
    }

    #[test]
    fn selection_summary_keeps_scope_order_and_wide_offsets() {
        let semantic = json(r#"{
            "refs":{"10":"first","2":"second","7":"shared"},
            "roots":[
                {"ref":"0","role":"window","children":[
                    {"ref":"10","role":"textField"},{"ref":"2","role":"textField"},
                    {"ref":"7","role":"textField"}]},
                {"ref":"1","role":"window","children":[{"ref":"7","role":"textField"}]}
            ],
            "fields":[]
        }"#);
        let tokens = ["first".to_owned(), "second".to_owned(), "shared".to_owned()];
        let facts_with_offset = |offset: &str| {
            let rows: Vec<_> = tokens.iter().map(|token| json(&format!(r#"{{
                "node":"{token}","observed":true,"fieldStatus":{{}},"values":{{
                    "role":"text_field","states.focused":false,"text.source":"provider",
                    "text.unit":"utf16_code_unit","text.selections":[{{"start":{offset},"end":{offset},"collapsed":true}}]
                }}
            }}"#))).collect();
            map([
                ("nodes", Value::Seq(rows)),
                ("relations", Value::Seq(vec![])),
                ("issues", json(r#"[{"path":["nodes","first"],"state":"partial"}]"#)),
            ])
        };
        let observation = json(r#"{"backend":"macos","session":"session","id":"capture","issues":[]}"#);
        let offset = "9223372036854775807";
        let snapshot = FactSnapshot {
            observation,
            facts: facts_with_offset(offset),
            metadata: Value::Null,
            query: Value::Null,
        };
        let request = AccountingRequest {
            snapshot: &snapshot, semantic: &semantic, tokens: &tokens, parts: &[],
            compact: true, public_api: true, share_descriptions: true,
        };
        let sources = Sources::new(&request).unwrap();
        let entries = summaries::selection_plan(&sources).unwrap();
        let inline = json(&format!(r#"{{"location":{offset},"length":0}}"#));
        assert!(summaries::summarized_interval(&entries[0], &inline));
        for wrong in [
            r#"{"location":9223372036854775807,"length":false}"#,
            r#"{"location":9223372036854775807,"length":0,"unit":"utf16_code_unit"}"#,
        ] {
            assert!(!summaries::summarized_interval(&entries[0], &json(wrong)));
        }
        assert!(summaries::read(&sources, &entries, true, false).unwrap().iter().any(|line|
            line.starts_with("Text selection [2, 10]")));
        assert_eq!(summaries::read(&sources, &entries, true, true).unwrap(),
            [format!("Selection [10]: [{offset},{offset}) UTF-16")]);
        let destinations = summaries::selection_destinations(&entries, &["7", "10", "2"], true).unwrap();
        assert_eq!(destinations.iter().map(|entry| field(entry, "ref").unwrap()).collect::<Vec<_>>(), ["7", "10", "2"]);
        assert_eq!(destinations.iter().map(|entry| field(entry, "visibility").unwrap()).collect::<Vec<_>>(), ["details", "public", "details"]);

        let huge_offset = "1427247692705959881058285969449495136382746624";
        let wide_snapshot = FactSnapshot {
            observation: snapshot.observation.clone(),
            facts: facts_with_offset(huge_offset),
            metadata: Value::Null,
            query: Value::Null,
        };
        let wide_request = AccountingRequest { snapshot: &wide_snapshot, ..request };
        let wide_sources = Sources::new(&wide_request).unwrap();
        let wide_entries = summaries::selection_plan(&wide_sources).unwrap();
        let wide_output = summaries::read(&wide_sources, &wide_entries, true, true).unwrap();
        assert!(wide_output.iter().any(|line| line.starts_with("Text selection [2, 10]")));
        assert!(wide_output.iter().any(|line| line.starts_with("Text selection [7]")));
        assert!(wide_output.iter().all(|line| line.contains(huge_offset)));
        assert!(wide_entries.iter().all(|entry| entry.inactive_interval().is_none()));
    }

    #[test]
    fn document_body_coverage_and_selection_evidence_are_independent() {
        let observation = json(r#"{"backend":"linux","session":"session","id":"capture","issues":[]}"#);
        let semantic = json(r#"{"refs":{"0":"doc"},"roots":[{"ref":"0","role":"document"}],"fields":[]}"#);
        let tokens = ["doc".to_owned()];
        for public_api in [false, true] {
            for share_descriptions in [false, true] {
                for content_timed_out in [false, true] {
                    for caret_active in [false, true] {
                        let status = if content_timed_out { r#"{"text.content":{"state":"timeout"}}"# } else { "{}" };
                        let facts = json(&format!(r#"{{"nodes":[{{"node":"doc","observed":true,"fieldStatus":{status},
                            "fieldPaths":{{"text.content":["text","content"]}},"values":{{
                                "role":"document","states.focused":false,"text.source":"provider","text.unit":"unicode_codepoint",
                                "text.caret_active":{caret_active},"text.selections":[{{"start":1,"end":1,"collapsed":true}}]
                            }}
                        }}],"relations":[]}}"#));
                        let snapshot = FactSnapshot {
                            observation: observation.clone(), facts, metadata: Value::Null, query: Value::Null,
                        };
                        let request = AccountingRequest {
                            snapshot: &snapshot, semantic: &semantic, tokens: &tokens, parts: &[],
                            compact: true, public_api, share_descriptions,
                        };
                        let output = plan(&request).unwrap();
                        let concise = public_api && share_descriptions;
                        let body = "Document text [0]: ".to_owned()
                            + if content_timed_out { "timeout; control values separate." } else { "not collected; control values separate." };
                        assert_eq!(output.compact_notes.contains(&body), !concise || content_timed_out);
                        let selection = if concise { "Selection [0]: [1,1) Unicode code points" }
                            else { "Text selection [0] (complete): [1, 1) Unicode code points; collapsed; range text not reported" };
                        assert_eq!(output.compact_notes.iter().any(|line| line == selection), !concise || caret_active);
                        assert!(output.full_notes.iter().any(|line| line.starts_with("Text selection of 0")));
                    }
                }
            }
        }
    }

    #[test]
    fn embedded_text_preview_requires_exact_native_boundaries() {
        let observation = json(r#"{"backend":"linux","session":"session","id":"capture","issues":[]}"#);
        let semantic = json(r#"{"refs":{"0":"root","2":"child"},"roots":[],"fields":[]}"#);
        let tokens = ["root".to_owned(), "child".to_owned()];
        for (unit, start, end, preview) in [
            ("utf16_code_unit", 1, 3, true), ("utf16_code_unit", 1, 2, false),
            ("utf8_byte", 1, 5, true), ("utf8_byte", 2, 5, false), ("unicode_codepoint", 1, 2, true),
        ] {
            let facts = json(&format!(r#"{{"nodes":[{{"node":"root","observed":true,"fieldStatus":{{}},"values":{{
                "text.source":"provider","text.unit":"{unit}","text.content":"A😀B",
                "text.embedded":[["child",{{"start":{start},"end":{end}}}]]
            }}}},{{"node":"child","observed":true,"values":{{}}}}],"relations":[]}}"#));
            let snapshot = FactSnapshot {
                observation: observation.clone(), facts, metadata: Value::Null, query: Value::Null,
            };
            let request = AccountingRequest {
                snapshot: &snapshot, semantic: &semantic, tokens: &tokens, parts: &[],
                compact: true, public_api: false, share_descriptions: false,
            };
            let output = plan(&request).unwrap();
            assert!(output.compact_notes[0].contains(&format!("2@{start}..{end}")));
            assert_eq!(output.compact_notes.len(), if preview { 2 } else { 1 });
            if preview {
                assert_eq!(output.compact_notes[1], "Reading sequence of 0: \"A\" → element 2 \"😀\" → \"B\"");
            }
        }
    }
