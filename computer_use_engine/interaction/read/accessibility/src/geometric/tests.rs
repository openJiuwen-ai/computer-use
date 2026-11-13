// Copyright (c) Huawei Technologies Co., Ltd. 2026. All rights reserved.

use super::*;
use crate::{projection::ProjectionOptions, wire::decode_json};

fn projection(source: &str, count: usize) -> Projection {
    let aliases = (0..count).map(|id| (id.to_string(), NodeId(id))).collect();
    Projection::prepare(
        decode_json(source.as_bytes()).unwrap(),
        &aliases,
        ProjectionOptions::default(),
    )
    .unwrap()
}

fn geometry(boxes: &[(usize, f64, f64, f64, f64)]) -> Geometry {
    boxes
        .iter()
        .map(|&(id, x, y, width, height)| {
            (
                NodeId(id),
                Box {
                    space: "window".into(),
                    x,
                    y,
                    width,
                    height,
                },
            )
        })
        .collect()
}

#[test]
fn shallow_source_keeps_deep_heading_sections_on_an_sdk_worker_stack() {
    const HEADINGS: usize = 1_200;
    let mut children = (1..=HEADINGS).map(|level| format!(
        r#"{{"ref":"{level}","role":"heading","headingLevel":{level},"children":[]}}"#,
    )).collect::<Vec<_>>();
    children.push(format!(r#"{{"ref":"{}","role":"staticText","children":[]}}"#, HEADINGS + 1));
    let source = format!(r#"{{"ref":"0","role":"window","children":[{}]}}"#, children.join(","));
    let projection = projection(&source, HEADINGS + 2);
    let geometry = Geometry::default();
    let context = GeometricContext { projection: &projection, geometry: &geometry, facts: None, atoms: None };
    std::thread::scope(|scope| {
        std::thread::Builder::new().stack_size(512 * 1024).spawn_scoped(scope, || {
            let mut plan = GeometricPlan::seed(&context, true).unwrap();
            composition::group_heading_sections(&mut plan, &context).unwrap();
            let mut current = plan.block(plan.roots[0]).unwrap().children[0];
            for level in 1..=HEADINGS {
                let section = plan.block(current).unwrap();
                assert_eq!(section.label_ref, Some(NodeId(level)));
                assert_eq!(section.children.len(), 2);
                current = section.children[1];
            }
            assert_eq!(plan.block(current).unwrap().primary_ref, Some(NodeId(HEADINGS + 1)));
            assert_eq!(plan.block_sources(plan.roots[0]), (0..HEADINGS + 2).map(NodeId).collect::<Vec<_>>());
        }).unwrap().join().unwrap();
    });
}

#[test]
fn native_panes_keep_independent_sources_across_recursive_region_layout() {
    let projection = projection(
        r#"{"ref":"0","role":"window","children":[
      {"ref":"1","role":"scrollArea","children":[]},
      {"ref":"2","role":"scrollArea","children":[]}
    ]}"#,
        3,
    );
    let geometry = geometry(&[
        (0, 0.0, 0.0, 350.0, 200.0),
        (1, 210.0, 5.0, 120.0, 160.0),
        (2, 10.0, 5.0, 120.0, 160.0),
    ]);
    let context = GeometricContext {
        projection: &projection,
        geometry: &geometry,
        facts: None,
        atoms: None,
    };
    let plan = GeometricPlan::build(&context, true).unwrap();
    let root = plan.block(plan.roots[0]).unwrap();
    let children = root
        .children
        .iter()
        .map(|&id| plan.block(id).unwrap())
        .collect::<Vec<_>>();
    assert_eq!(
        children
            .iter()
            .map(|child| child.position.as_deref())
            .collect::<Vec<_>>(),
        [Some("left"), Some("right")]
    );
    assert_eq!(plan.block_sources(root.children[0]), [NodeId(2)]);
    assert_eq!(plan.block_sources(root.children[1]), [NodeId(1)]);
    assert_eq!(plan.target_refs, [NodeId(0), NodeId(1), NodeId(2)]);
    assert_eq!(
        children.iter().map(|child| child.key.as_str()).collect::<Vec<_>>(),
        [
            "geometric-region:99dd21d723ec1ed876ff6686",
            "geometric-region:221f3c9e8048e6712f16cbd5",
        ]
    );
    assert_eq!(
        plan.decisions,
        ["geometric-source-order:element:0", "geometric-regions:element:0"]
    );
    let value = plan.to_value(&context).unwrap();
    assert_eq!(value.get("format_version").and_then(Value::as_str), Some("geometric-2"));
}

#[test]
fn unmeasured_unit_is_a_local_barrier() {
    let projection = projection(
        r#"{"ref":"0","role":"window","children":[
      {"ref":"1","role":"button","name":"Right","children":[]},
      {"ref":"2","role":"button","name":"Unknown","children":[]},
      {"ref":"3","role":"button","name":"Left","children":[]}
    ]}"#,
        4,
    );
    let geometry = geometry(&[(1, 95.0, 0.0, 20.0, 20.0), (3, 0.0, 0.0, 20.0, 20.0)]);
    let context = GeometricContext {
        projection: &projection,
        geometry: &geometry,
        facts: None,
        atoms: None,
    };
    let plan = GeometricPlan::build(&context, true).unwrap();
    let root = plan.block(plan.roots[0]).unwrap();
    let children = root
        .children
        .iter()
        .map(|&id| plan.block(id).unwrap().primary_ref.unwrap())
        .collect::<Vec<_>>();
    assert_eq!(children, [NodeId(1), NodeId(2), NodeId(3)]);
    assert!(!plan.same_region(&[NodeId(1), NodeId(3)]));
    assert!(plan.decisions.is_empty());
}

#[test]
fn coincident_control_keeps_independent_native_action_owners() {
    let projection = projection(
        r#"{"ref":"0","role":"window","children":[
      {"ref":"1","role":"group","actions":[{"name":"AXPress","kind":"invoke"}],"children":[
        {"ref":"2","role":"button","name":"Edit","actions":[{"name":"AXPress","kind":"invoke"}],"children":[]}
      ]}
    ]}"#,
        3,
    );
    let geometry = geometry(&[(1, 10.0, 15.0, 90.0, 28.0), (2, 10.0, 15.0, 90.0, 28.0)]);
    let context = GeometricContext {
        projection: &projection,
        geometry: &geometry,
        facts: None,
        atoms: None,
    };
    let plan = GeometricPlan::build(&context, true).unwrap();
    let window = plan.block(plan.roots[0]).unwrap();
    let compound = plan.block(window.children[0]).unwrap();
    assert_eq!(compound.layout, "compound-control");
    assert_eq!(compound.field_target_ref, Some(NodeId(2)));
    assert_eq!(compound.member_roles, [(NodeId(1), "content"), (NodeId(2), "control")]);
    assert_eq!(plan.block_sources(window.children[0]), [NodeId(2), NodeId(1)]);
    assert_ne!(plan.ref_locations[&NodeId(1)], plan.ref_locations[&NodeId(2)]);
}

#[test]
fn invalid_optional_layout_recovers_only_the_closed_original_subtree() {
    let projection = projection(
        r#"{"ref":"0","role":"window","children":[
      {"ref":"1","role":"group","children":[{"ref":"2","role":"staticText","children":[]}]},
      {"ref":"3","role":"button","children":[]}
    ]}"#,
        4,
    );
    let geometry = Geometry::new();
    let context = GeometricContext {
        projection: &projection,
        geometry: &geometry,
        facts: None,
        atoms: None,
    };
    let mut plan = GeometricPlan::seed(&context, true).unwrap();
    let owner = plan.source_block(NodeId(1)).unwrap();
    let stable_sibling = plan.source_block(NodeId(3)).unwrap();
    plan.block_mut(owner).unwrap().layout = "compact-list";
    let plan = validation::finish(plan, &context).unwrap();
    assert_eq!(plan.source_block(NodeId(3)), Some(stable_sibling));
    let recovered = plan.source_block(NodeId(1)).unwrap();
    assert_eq!(plan.block(recovered).unwrap().layout, "tree");
    assert_eq!(plan.block_sources(recovered), [NodeId(1), NodeId(2)]);
    assert_eq!(
        plan.decisions,
        ["local-layout-fallback:1: compact list lacks its independent leaf targets"]
    );
}

#[test]
fn alignment_and_column_refinement_keep_sparse_rows_and_original_members() {
    let projection = projection(
        r#"{"ref":"0","role":"window","children":[
      {"ref":"1","role":"button","children":[]},{"ref":"2","role":"button","children":[]},
      {"ref":"3","role":"button","children":[]},{"ref":"4","role":"button","children":[]},
      {"ref":"5","role":"button","children":[]}
    ]}"#,
        6,
    );
    let geometry = geometry(&[
        (1, 0.0, 0.0, 20.0, 20.0),
        (2, 80.0, 0.0, 20.0, 20.0),
        (3, 0.0, 40.0, 20.0, 20.0),
        (4, 80.0, 40.0, 20.0, 20.0),
        (5, 80.0, 80.0, 20.0, 20.0),
    ]);
    let context = GeometricContext {
        projection: &projection,
        geometry: &geometry,
        facts: None,
        atoms: None,
    };
    let plan = GeometricPlan::seed(&context, true).unwrap();
    let items = [5, 3, 4, 2, 1].map(|id| alignment::LayoutItem {
        key: plan.source_block(NodeId(id)).unwrap(),
        bounds: geometry[&NodeId(id)].clone(),
    });
    let alignment = alignment::analyze(&items, &plan).unwrap();
    let columns = columns::project_columns(&alignment, &plan).unwrap();
    assert_eq!(columns.alignments, ["left", "left"]);
    assert_eq!(
        columns.rows.iter().map(|row| row.slots.as_slice()).collect::<Vec<_>>(),
        [&[Some(0), Some(1)][..], &[Some(0), Some(1)][..], &[Some(1)][..]]
    );
    assert_eq!(
        synthetic_key("visual-row", "element:0", &["element:2", "element:5"]),
        "visual-row:091b7f1b7d6e01fa9d53c1c0"
    );
}

fn atom(role: &str, caption: Option<&str>) -> AtomLayoutEvidence {
    AtomLayoutEvidence {
        role_label: role.to_owned(),
        source_description: None,
        state_labels: Vec::new(),
        has_actions: false,
        actions_unavailable: false,
        declarations: false,
        partial: false,
        focused: false,
        unexpanded_menu: false,
        action_cost: 0,
        slots: caption
            .map(|caption| AtomSlot {
                key: "name".into(),
                format: "text".into(),
                value: Value::Text(caption.into()),
                block: false,
                caption: true,
                protected: false,
                preview: false,
                label: None,
            })
            .into_iter()
            .collect(),
    }
}

#[test]
fn final_member_plans_keep_all_empty_and_named_controls_addressable() {
    let projection = projection(
        r#"{"ref":"0","role":"window","children":[
      {"ref":"1","role":"button","children":[]},{"ref":"2","role":"button","children":[]},
      {"ref":"3","role":"button","children":[]},
      {"ref":"4","role":"button","name":"One","children":[]},{"ref":"5","role":"button","name":"Two","children":[]},
      {"ref":"6","role":"button","name":"Three","children":[]}
    ]}"#,
        7,
    );
    let geometry = Geometry::new();
    let atoms = HashMap::from([
        (NodeId(0), atom("window", None)),
        (NodeId(1), atom("button", None)),
        (NodeId(2), atom("button", None)),
        (NodeId(3), atom("button", None)),
        (NodeId(4), atom("button", Some("One"))),
        (NodeId(5), atom("button", Some("Two"))),
        (NodeId(6), atom("button", Some("Three"))),
    ]);
    let context = GeometricContext {
        projection: &projection,
        geometry: &geometry,
        facts: None,
        atoms: Some(&atoms),
    };
    let plan = GeometricPlan::build(&context, true).unwrap();
    let children = &plan.block(plan.roots[0]).unwrap().children;
    assert_eq!(children.len(), 2);
    assert_eq!(plan.block(children[0]).unwrap().layout, "compact-list");
    assert_eq!(plan.block(children[1]).unwrap().layout, "inline");
    assert_eq!(plan.block_sources(children[0]), [NodeId(1), NodeId(2), NodeId(3)]);
    assert_eq!(plan.block_sources(children[1]), [NodeId(4), NodeId(5), NodeId(6)]);
    assert_eq!(plan.decisions, ["empty-atom-list:1,2,3", "inline-members:4,5,6"]);
}

#[test]
fn visual_chain_respects_the_total_typed_cost_without_rebinding_members() {
    let projection = projection(
        r#"{"ref":"0","role":"window","children":[
      {"ref":"1","role":"group","name":"Wrapper","visualChainChild":"2","children":[
        {"ref":"2","role":"button","name":"Target","children":[]}]}
    ]}"#,
        3,
    );
    let geometry = Geometry::new();
    let mut atoms = HashMap::from([
        (NodeId(0), atom("window", None)),
        (NodeId(1), atom("group", Some("Wrapper"))),
        (NodeId(2), atom("button", Some("Target"))),
    ]);
    let make_plan = |atoms: &HashMap<NodeId, AtomLayoutEvidence>| {
        GeometricPlan::build(
            &GeometricContext {
                projection: &projection,
                geometry: &geometry,
                facts: None,
                atoms: Some(atoms),
            },
            true,
        )
        .unwrap()
    };
    let compact = make_plan(&atoms);
    let key = compact.source_block(NodeId(1)).unwrap();
    assert_eq!(compact.block(key).unwrap().layout, "chain");
    assert_eq!(compact.block_sources(key), [NodeId(1), NodeId(2)]);
    atoms.get_mut(&NodeId(2)).unwrap().action_cost = 280;
    let expanded = make_plan(&atoms);
    let key = expanded.source_block(NodeId(1)).unwrap();
    assert_eq!(expanded.block(key).unwrap().layout, "tree");
    assert_eq!(expanded.block_sources(key), [NodeId(1), NodeId(2)]);
}
