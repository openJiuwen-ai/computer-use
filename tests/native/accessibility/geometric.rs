// Copyright (c) Huawei Technologies Co., Ltd. 2026. All rights reserved.

const PROSE_AND_NAVIGATION: &str = r#"{"ref":"0","role":"window","children":[
    {"ref":"1","role":"webArea","children":[
        {"ref":"2","role":"group","children":[
            {"ref":"3","role":"heading","children":[]},
            {"ref":"4","role":"staticText","children":[]},
            {"ref":"5","role":"heading","children":[]},
            {"ref":"6","role":"paragraph","children":[]}
        ]}
    ]},
    {"ref":"7","role":"group","children":[{"ref":"8","role":"button","children":[]}]},
    {"ref":"9","role":"group","children":[
        {"ref":"10","role":"group","children":[
            {"ref":"11","role":"heading","children":[]}, {"ref":"12","role":"staticText","children":[]}
        ]},
        {"ref":"13","role":"group","children":[
            {"ref":"14","role":"heading","children":[]}, {"ref":"15","role":"staticText","children":[]}
        ]}
    ]}
]}"#;

#[test]
fn prose_scope_uses_original_ancestors_without_promoting_neighboring_controls() {
    let projection = projection(PROSE_AND_NAVIGATION, 16);
    let geometry = Geometry::default();
    let context = GeometricContext { projection: &projection, geometry: &geometry, facts: None, atoms: None };
    let plan = GeometricPlan::seed(&context, true).unwrap();

    for reference in [4, 7, 1, 10, 6, 9, 8, 0, 15, 2, 7, 4] {
        let block = plan.source_block(NodeId(reference)).unwrap();
        assert_eq!(scopes::document_flow_scope(&plan, &context, block), matches!(reference, 1 | 2 | 4 | 6));
    }
}

#[test]
fn prose_scope_uses_the_current_block_source_and_does_not_share_across_captures() {
    let original = projection(PROSE_AND_NAVIGATION, 16);
    let geometry = Geometry::default();
    let context = GeometricContext { projection: &original, geometry: &geometry, facts: None, atoms: None };
    let mut plan = GeometricPlan::seed(&context, true).unwrap();
    let key = plan.source_block(NodeId(4)).unwrap();
    assert!(scopes::document_flow_scope(&plan, &context, key));
    plan.block_mut(key).unwrap().primary_ref = Some(NodeId(7));
    assert!(!scopes::document_flow_scope(&plan, &context, key));

    let block = plan.block_mut(key).unwrap();
    block.primary_ref = None;
    block.label_ref = None;
    block.source_refs = vec![NodeId(6)];
    assert!(scopes::document_flow_scope(&plan, &context, key));

    let changed_source = PROSE_AND_NAVIGATION.replace(
        r#"{"ref":"4","role":"staticText","children":[]}"#,
        r#"{"ref":"4","role":"button","children":[]}"#,
    );
    let changed = projection(&changed_source, 16);
    let changed_context = GeometricContext { projection: &changed, ..context };
    let changed_plan = GeometricPlan::seed(&changed_context, true).unwrap();
    let changed_key = changed_plan.source_block(NodeId(2)).unwrap();
    assert!(!scopes::document_flow_scope(&changed_plan, &changed_context, changed_key));
    assert!(scopes::document_flow_scope(&plan, &context, key));
}
