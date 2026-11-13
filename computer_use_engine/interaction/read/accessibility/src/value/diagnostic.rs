// Copyright (c) Huawei Technologies Co., Ltd. 2026. All rights reserved.

//! Semantic JSON encoding for one captured diagnostic value.

use super::Value;

pub(crate) fn tagged(kind: &str, key: &str, value: String) -> Value {
    Value::Map(vec![
        ("$type".into(), Value::Text(kind.into())),
        (key.into(), Value::Text(value)),
    ])
}

pub(crate) fn record(items: Vec<(String, Value)>) -> Value {
    if items.iter().any(|(key, _)| key == "$type") {
        Value::Map(vec![
            ("$type".into(), Value::Text("map".into())),
            ("entries".into(), Value::Seq(items.into_iter()
                .map(|(key, item)| Value::Seq(vec![Value::Text(key), item])).collect())),
        ])
    } else {
        Value::Map(items)
    }
}

pub(crate) fn encode(value: &Value) -> Value {
    const SAFE_INTEGER: u64 = 9_007_199_254_740_991;
    match value {
        Value::Int(value) if value.unsigned_abs() > SAFE_INTEGER => tagged("integer", "value", value.to_string()),
        Value::BigInt(value) => tagged("integer", "value", value.clone()),
        Value::Float(value) if value.is_nan() => tagged("float", "value", "nan".into()),
        Value::Float(value) if *value == f64::INFINITY => tagged("float", "value", "inf".into()),
        Value::Float(value) if *value == f64::NEG_INFINITY => tagged("float", "value", "-inf".into()),
        Value::Float(value) if value.to_bits() == (-0.0_f64).to_bits() => tagged("float", "value", "-0".into()),
        Value::Seq(items) => Value::Seq(items.iter().map(encode).collect()),
        Value::Map(items) => record(items.iter().map(|(key, item)| (key.clone(), encode(item))).collect()),
        value => value.clone(),
    }
}
