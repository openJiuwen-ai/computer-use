// Copyright (c) Huawei Technologies Co., Ltd. 2026. All rights reserved.

#[test]
fn repeated_native_comparisons_keep_filtered_hashes_for_shared_typed_values() {
    let shared = Value::Seq(vec![
        text("a😀"),
        Value::Bool(false),
        Value::Null,
        Value::Bytes(vec![0x00, 0xff]),
        Value::Float(-0.0),
        Value::Int(9),
        Value::Seq(vec![text("mixed"), text("名称")]),
    ]);
    let focused = value_map([
        ("focused", Value::Bool(true)),
        ("value", shared.clone()),
        ("label", value_map([("nested", shared.clone())])),
    ]);
    let blurred = value_map([
        ("focused", Value::Bool(false)),
        ("value", shared.clone()),
        ("label", value_map([("nested", shared.clone())])),
    ]);
    let retained_group = value_map([
        ("value", shared),
        ("preserveGroup", Value::Bool(true)),
        ("focused", Value::Bool(false)),
    ]);
    // These digests come from the captured Python comparison for the same
    // representable native values, including mixed bytes and negative zero.
    let expected = [
        ("77b875e203f35e26d0b053c0a4462c5544f92bd53bd3049f07f90d0f78d42a48",
         "e318009930245e5b7114864f2310f85f56a5b3fe07fae0ae8c31f82be8eae3c1"),
        ("bf609d42dc2bca89db6c30843c92002044cf12ad2b4351056a36f9c76da194b7",
         "e318009930245e5b7114864f2310f85f56a5b3fe07fae0ae8c31f82be8eae3c1"),
        ("043963593bc4727e231e7f040721d10d79e5e3d5275ddb59c26cf188cf8615ee",
         "e75ec3da81a0ba97debfc190a7d4b61241edf3b29f386c14ec7b4c7cd82406f5"),
    ];
    let mut signatures = Signatures::default();
    for _ in 0..90 {
        for (row, (full, content)) in [&focused, &blurred, &retained_group].into_iter().zip(expected) {
            assert_eq!(
                signatures.capture(row.as_map().unwrap()),
                (full.to_owned(), content.to_owned())
            );
        }
    }
}
