// Copyright (c) Huawei Technologies Co., Ltd. 2026. All rights reserved.

    #[test]
    fn geometry_restrictions_use_only_paths_overlapping_bound_or_space_inputs() {
        let issues = json(r#"[
            {"path":["nodes","usable","states","visible"],"state":"unsupported"},
            {"path":["nodes","usable","relations","children"],"state":"unsupported"},
            {"path":["nodes","usable","help"],"state":"unsupported"},
            {"path":["nodes","usable","geometry","center"],"state":"unsupported"},
            {"path":["nodes"],"state":"partial"},
            {"path":["nodes","bad-bounds","geometry","bounds","width"],"state":"error"},
            {"path":["coordinate_spaces","bad-space","unit"],"state":"error"},
            {"path":["coordinate_spaces","desktop","unrelated"],"state":"error"}
        ]"#);
        let restrictions = Restrictions::collect(issues.as_seq().unwrap());
        assert_eq!(restrictions.nodes, HashSet::from(["bad-bounds"]));
        assert!(!restrictions.all_nodes);
        assert!(restrictions.affects_space("bad-space", &["unit"]));
        assert!(!restrictions.affects_space("bad-space", &["owner"]));
        assert!(!restrictions.affects_space("desktop", &["unit", "origin", "owner"]));

        let spaces = json(r#"{"bad-space":{"y_direction":"down"},"desktop":{"y_direction":"down"}}"#);
        let bad = json(r#"{"space":"bad-space","x":1,"y":2,"width":3,"height":4}"#);
        let usable = json(r#"{"space":"desktop","x":1,"y":2,"width":3,"height":4}"#);
        assert!(comparable_box(&bad, &spaces, &restrictions).is_none());
        assert!(comparable_box(&usable, &spaces, &restrictions).is_some());
    }

    #[test]
    fn geometry_restrictions_preserve_short_paths_and_exact_source_tokens() {
        let issues = json(r#"[
            {"path":["nodes","node","states","visible"],"state":"unsupported"},
            {"path":["nodes","Ω-node"],"state":"timeout"},
            {"path":["nodes","node-prefix","geometry"],"state":"partial"},
            {"path":["nodes","node2","geometry","bounds"],"state":"timeout"}
        ]"#);
        let restrictions = Restrictions::collect(issues.as_seq().unwrap());
        assert_eq!(restrictions.nodes, HashSet::from(["Ω-node", "node-prefix", "node2"]));
        assert!(!restrictions.nodes.contains("node"));
        assert!(!restrictions.nodes.contains("Ω-node2"));
        assert!(!restrictions.all);
    }
