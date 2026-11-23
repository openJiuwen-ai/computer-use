// Copyright (c) Huawei Technologies Co., Ltd. 2026. All rights reserved.

#[test]
fn overlapping_collection_issues_cannot_prove_exact_cell_placement() {
    let (projection, facts) = fixture(false, 1, false, false, false);
    let issues = [
        ("error", vec!["nodes"], "logical table identity or source scope is unavailable"),
        ("unavailable", vec!["nodes"], "logical table identity or source scope is unavailable"),
        ("conflict", vec!["nodes"], "logical table identity or source scope is unavailable"),
        ("not_read", vec!["nodes"], "logical table identity or source scope is unavailable"),
        (
            "unavailable",
            vec!["nodes", "cell2", "structure", "row_index"],
            "a captured row has conflicting or unavailable cell structure",
        ),
    ];
    for (state, path, reason) in issues {
        let mut fields = facts.observation.as_map().unwrap().to_vec();
        let (_, issuer) = fields.iter_mut().find(|(key, _)| key == "issues").unwrap();
        *issuer = Value::Seq(vec![Value::Map(vec![
            ("state".to_owned(), Value::Text(state.to_owned())),
            ("path".to_owned(), Value::Seq(path.into_iter().map(|field| Value::Text(field.to_owned())).collect())),
        ])]);
        let candidate = FactSnapshot {
            observation: Value::Map(fields),
            facts: Value::Null,
            metadata: Value::Null,
            query: Value::Null,
        };
        let document = run(&projection, &candidate);
        assert!(document.tables.is_empty(), "{state} should block exact cell placement");
        assert_eq!(document.rejected.len(), 1);
        assert_eq!(document.rejected[0].reason, reason);
    }
}
