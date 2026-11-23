// Copyright (c) Huawei Technologies Co., Ltd. 2026. All rights reserved.

    #[test]
    fn repeated_native_value_lines_keep_distinct_sources_and_utf16_ranges() {
        let raw = decode_json(br#"{
            "ref":"0","role":"textArea","value":"\ufffd\ud83d\ude00\r\nA\u0085\ud83d\ude00\u2028A\u2029",
            "children":[
                {"ref":"1","role":"staticText","value":"A","children":[]},
                {"ref":"2","role":"staticText","value":"\ud83d\ude00","children":[]},
                {"ref":"3","role":"staticText","value":"A","children":[]}
            ]
        }"#).unwrap();
        let ids = (0..4).map(|id| (id.to_string(), NodeId(id))).collect();
        let options = ProjectionOptions { native_facts: false, ..ProjectionOptions::default() };
        let projection = Projection::prepare(raw, &ids, options).unwrap();
        let owner = projection.node(NodeId(0)).unwrap();
        let mut evidence = CompressionEvidence::default();
        evidence.control_value_lines.insert(NodeId(0));
        evidence.complete_children.insert(NodeId(0), (1..4).map(NodeId).collect());
        for id in 0..4 {
            evidence.complete_text.insert(FactKey { source: NodeId(id), attribute: "value".into() });
            if id != 0 {
                evidence.complete_children.insert(NodeId(id), Vec::new());
            }
        }
        let parts: Vec<_> = (1..4).flat_map(|id| rules::text_sources(projection.node(NodeId(id)).unwrap())).collect();
        let records = covered_value_lines(&projection, owner, &parts, &evidence).unwrap().unwrap();
        assert_eq!(records, decode_json(br#"[
            {"sourceRef":"1","attribute":"value","targetRef":"0","field":"value","start":5,"end":6,"unit":"utf16_code_unit"},
            {"sourceRef":"2","attribute":"value","targetRef":"0","field":"value","start":7,"end":9,"unit":"utf16_code_unit"},
            {"sourceRef":"3","attribute":"value","targetRef":"0","field":"value","start":10,"end":11,"unit":"utf16_code_unit"}
        ]"#).unwrap().as_seq().unwrap());
        let repeated = [parts[0].clone(), parts[0].clone()];
        assert!(covered_value_lines(&projection, owner, &repeated, &evidence).unwrap().is_none());
    }
