// Copyright (c) Huawei Technologies Co., Ltd. 2026. All rights reserved.

    use super::*;

    #[test]
    fn relation_annotations_keep_nested_fields_unicode_and_ordered_target_duplicates() {
        let plain = map([
            ("kind", text("related")),
            ("state", text("complete")),
            ("certainty", text("confirmed")),
        ]);
        let mut annotations = Residual::default();
        for (reference, targets) in [("0", vec!["2"]), ("1", vec!["3", "2", "3"])] {
            let mut entries = plain.as_map().unwrap().to_vec();
            entries.push(("total".to_owned(), Value::Int(targets.len() as i64)));
            annotations
                .add("linked", reference, Value::Map(entries), targets.into_iter().map(str::to_owned).collect())
                .unwrap();
        }
        let mut unusual = plain.as_map().unwrap().to_vec();
        unusual.push(("detail".to_owned(), text("😀\u{2028}")));
        unusual.push(("evidence".to_owned(), map([
            ("targets", Value::Seq(vec![text("a nested target")])),
            ("position", Value::Float(-0.0)),
        ])));
        annotations.add("nearby", "4", Value::Map(unusual), vec!["1".to_owned()]).unwrap();
        annotations.add("matching", "5", plain, vec!["2".to_owned()]).unwrap();
        assert_eq!(
            annotations.lines().unwrap(),
            [
                concat!(
                    "Relation \"linked\" ",
                    r#"{"kind":"related","state":"complete","certainty":"confirmed","total":"target-count"}:"#,
                ),
                "\t0 -> [\"2\"]; 1 -> [\"3\",\"2\",\"3\"]",
                concat!(
                    "Relation \"nearby\" ",
                    r#"{"kind":"related","state":"complete","certainty":"confirmed","detail":"😀\u2028","#,
                    r#""evidence":{"targets":["a nested target"],"position":-0.0}}:"#,
                ),
                "\t4 -> [\"1\"]",
                concat!("Relation \"matching\" ", r#"{"kind":"related","state":"complete","certainty":"confirmed"}:"#),
                "\t5 -> [\"2\"]",
            ]
        );
    }

    #[test]
    fn relation_annotation_groups_preserve_separate_totals_with_long_ordered_target_arrays() {
        let long: Vec<String> = (100..132).map(|target| target.to_string()).collect();
        let groups = [vec![], vec!["3".to_owned()], vec!["10".to_owned(), "2".to_owned(), "10".to_owned()], long];
        let mut annotations = Residual::default();
        for name in ["edge\n中\u{2028}", "same\"\\"] {
            for (index, targets) in groups.iter().enumerate() {
                let record = map([
                    ("total", Value::Int(targets.len() as i64)),
                    ("kind", text("related")),
                    ("evidence", map([("position", Value::Float(-0.0))])),
                ]);
                for offset in [2, 3, 4, 8, 9, 8] {
                    annotations.add(name, &(10 * index + offset).to_string(), record.clone(), targets.clone()).unwrap();
                }
            }
        }
        annotations.add(
            "separate",
            "0",
            map([("kind", text("related")), ("total", Value::Int(13))]),
            vec!["7".to_owned()],
        ).unwrap();
        let lines = annotations.lines().unwrap();
        assert_eq!(lines[0], concat!(
            r#"Relation ["edge\n中\u2028","same\"\\"] "#,
            r#"{"kind":"related","evidence":{"position":-0.0},"total":"target-count"}:"#,
        ));
        assert_eq!(lines[1], "\t22–24, 28, 29 -> [\"10\",\"2\",\"10\"]");
        let targets = literal(&Value::Seq(groups[3].iter().map(text).collect())).unwrap();
        assert_eq!(lines[2], format!("\t32–34, 38, 39 -> {targets}"));
        assert_eq!(lines[3], "\t12–14, 18, 19 -> [\"3\"]; 2–4, 8, 9 -> []");
        assert_eq!(lines[4], r#"Relation "separate" {"kind":"related","total":13}:"#);
        assert_eq!(lines[5], "\t0 -> [\"7\"]");
    }

    #[test]
    fn relation_annotation_keeps_the_scalar_from_a_valid_utf16_pair() {
        let metadata = crate::wire::decode_json(br#"{"detail":"\ud83d\ude00"}"#).unwrap();
        let mut annotations = Residual::default();
        annotations.add("edge", "0", metadata, vec!["1".to_owned()]).unwrap();
        assert_eq!(
            annotations.lines().unwrap(),
            [r#"Relation "edge" {"detail":"😀"}:"#, "\t0 -> [\"1\"]"],
        );
    }
