// Copyright (c) Huawei Technologies Co., Ltd. 2026. All rights reserved.

//! The worker's compact JSONL diagnostic spelling for admitted ordinary results.

use std::{collections::TryReserveError, fmt::Write, iter::Enumerate, slice};

use crate::{
    render::text::{float, write_quoted, JsonFormat},
    value::Value,
};

#[derive(Debug, PartialEq, Eq)]
pub(super) enum Error {
    Allocation,
    Nonfinite,
    UnsupportedValue,
}

impl From<TryReserveError> for Error {
    fn from(_: TryReserveError) -> Self {
        Self::Allocation
    }
}

type Fields<'a> = Enumerate<slice::Iter<'a, (String, Value)>>;
type Items<'a> = Enumerate<slice::Iter<'a, Value>>;

enum Pending<'a> {
    Value(&'a Value),
    Items(Items<'a>),
    Fields(Fields<'a>),
}

fn punctuation(output: &mut String, character: char) -> Result<(), Error> {
    output.try_reserve(1)?;
    output.push(character);
    Ok(())
}

fn quoted(output: &mut String, text: &str) -> Result<(), Error> {
    let escape_bytes = text.bytes().map(|byte| match byte {
        b'"' | b'\\' | b'\n' | b'\r' | b'\t' | 8 | 12 => 1,
        0..=31 => 5,
        _ => 0,
    }).sum::<usize>();
    output.try_reserve(text.len() + escape_bytes + 2)?;
    if escape_bytes == 0 {
        output.push('"');
        output.push_str(text);
        output.push('"');
    } else {
        write_quoted(output, text, JsonFormat::Plain);
    }
    Ok(())
}

/// The wire parser has already resolved duplicate keys in first-insertion
/// order, validated UTF-8, and retained CPython-compatible scalar types.
/// Walk the admitted graph iteratively so deep metadata remains publishable.
pub(super) fn compact(value: &Value, capacity: usize) -> Result<String, Error> {
    let mut output = String::new();
    output.try_reserve_exact(capacity)?;
    let mut pending = Vec::new();
    pending.try_reserve(1)?;
    pending.push(Pending::Value(value));
    while let Some(item) = pending.pop() {
        match item {
            Pending::Value(Value::Seq(items)) => {
                punctuation(&mut output, '[')?;
                pending.push(Pending::Items(items.iter().enumerate()));
            }
            Pending::Value(Value::Map(items)) => {
                punctuation(&mut output, '{')?;
                pending.push(Pending::Fields(items.iter().enumerate()));
            }
            Pending::Value(Value::Int(value)) => {
                output.try_reserve(20)?;
                let _ = write!(output, "{value}");
            }
            Pending::Value(Value::Text(value)) => quoted(&mut output, value)?,
            Pending::Value(value) => {
                let number;
                let text = match value {
                    Value::Null => "null",
                    Value::Bool(true) => "true",
                    Value::Bool(false) => "false",
                    Value::BigInt(value) => value,
                    Value::Float(value) if value.is_finite() => {
                        number = float(*value, JsonFormat::Plain);
                        &number
                    }
                    Value::Float(_) => return Err(Error::Nonfinite),
                    _ => return Err(Error::UnsupportedValue),
                };
                output.try_reserve(text.len())?;
                output.push_str(text);
            }
            Pending::Items(mut items) => {
                if let Some((index, value)) = items.next() {
                    if index != 0 {
                        punctuation(&mut output, ',')?;
                    }
                    pending.try_reserve(2)?;
                    pending.push(Pending::Items(items));
                    pending.push(Pending::Value(value));
                } else {
                    punctuation(&mut output, ']')?;
                }
            }
            Pending::Fields(mut fields) => {
                if let Some((index, (key, value))) = fields.next() {
                    if index != 0 {
                        punctuation(&mut output, ',')?;
                    }
                    quoted(&mut output, key)?;
                    punctuation(&mut output, ':')?;
                    pending.try_reserve(2)?;
                    pending.push(Pending::Fields(fields));
                    pending.push(Pending::Value(value));
                } else {
                    punctuation(&mut output, '}')?;
                }
            }
        }
    }
    Ok(output)
}

#[cfg(test)]
mod tests {
    use super::*;
    use crate::wire::decode_json;

    #[test]
    fn compacts_only_json_whitespace_and_keeps_cpython_scalar_spelling() {
        let raw = br#" { "b": "old", "2": "\u007f\u0085\u2028\u2029\u001f", "1": [9223372036854775808, -0, -0.0, 1E15, 1E-6, 5e-324, true], "b": "\u96ea\ud83e\uddea\r\n\b\f\t\/" } "#;
        let value = decode_json(raw).unwrap();
        let expected = concat!(
            "{\"b\":\"雪🧪\\r\\n\\b\\f\\t/\",\"2\":\"\u{7f}\u{85}\u{2028}\u{2029}\\u001f\",",
            "\"1\":[9223372036854775808,0,-0.0,1000000000000000.0,1e-06,5e-324,true]}",
        );
        assert_eq!(compact(&value, raw.len()).unwrap(), expected);
        let overflow = decode_json(br#"{"n":1e9999}"#).unwrap();
        assert_eq!(compact(&overflow, 0), Err(Error::Nonfinite));
    }

    #[test]
    fn admits_metadata_nesting_beyond_the_presentation_serializer_limit() {
        let levels = 398;
        let mut value = Value::Null;
        for _ in 0..levels {
            value = Value::Seq(vec![value]);
        }
        assert_eq!(compact(&value, 0).unwrap(), "[".repeat(levels) + "null" + &"]".repeat(levels));
    }
}
