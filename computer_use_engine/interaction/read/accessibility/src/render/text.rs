// Copyright (c) Huawei Technologies Co., Ltd. 2026. All rights reserved.

//! Exact scalar text and the two JSON spellings used by captured details.

use std::fmt::Write;

use super::super::value::Value;

#[derive(Clone, Copy)]
pub(crate) enum JsonFormat {
    Plain,
    Literal,
    Diagnostic,
}

#[derive(Debug, Eq, PartialEq)]
pub(crate) enum TextError {
    Unsupported(&'static str),
    Invalid(&'static str),
}

type Result<T> = std::result::Result<T, TextError>;

pub(crate) fn write_quoted(output: &mut String, text: &str, format: JsonFormat) {
    output.push('"');
    for value in text.chars() {
        match value {
            '"' => output.push_str("\\\""),
            '\\' => output.push_str("\\\\"),
            '\n' => output.push_str("\\n"),
            '\r' => output.push_str("\\r"),
            '\t' => output.push_str("\\t"),
            '\u{8}' => output.push_str("\\b"),
            '\u{c}' => output.push_str("\\f"),
            '\0'..='\u{1f}' => {
                let _ = write!(output, "\\u{:04x}", value as u32);
            }
            '\u{85}' | '\u{2028}' | '\u{2029}' if !matches!(format, JsonFormat::Plain) => {
                let _ = write!(output, "\\u{:04x}", value as u32);
            }
            '\u{7f}'..='\u{9f}' if matches!(format, JsonFormat::Literal) => {
                let _ = write!(output, "\\u{:04x}", value as u32);
            }
            _ => output.push(value),
        }
    }
    output.push('"');
}

fn array_index(key: &str) -> Option<u32> {
    if key.is_empty() || key.len() > 1 && key.starts_with('0') || !key.bytes().all(|value| value.is_ascii_digit()) {
        return None;
    }
    let value = key.parse::<u32>().ok()?;
    (value < u32::MAX).then_some(value)
}

fn write_value(output: &mut String, value: &Value, format: JsonFormat, depth: usize) -> Result<()> {
    if depth > 256 {
        return Err(TextError::Unsupported("captured JSON depth"));
    }
    match value {
        Value::Null => output.push_str("null"),
        Value::Bool(value) => output.push_str(if *value { "true" } else { "false" }),
        Value::Int(value) => {
            let _ = write!(output, "{value}");
        }
        Value::BigInt(value) => output.push_str(value),
        Value::Float(value) => {
            if value.is_finite() {
                output.push_str(&float(*value, format));
            } else if matches!(format, JsonFormat::Diagnostic) {
                output.push_str("null");
            } else {
                return Err(TextError::Invalid("nonfinite literal"));
            }
        }
        Value::Text(value) => write_quoted(output, value, format),
        Value::Bytes(_) => return Err(TextError::Unsupported("binary JSON value")),
        Value::Seq(items) => {
            output.push('[');
            for (index, item) in items.iter().enumerate() {
                if index != 0 {
                    output.push(',');
                }
                write_value(output, item, format, depth + 1)?;
            }
            output.push(']');
        }
        Value::Map(items) => {
            output.push('{');
            let mut indexes = Vec::new();
            if matches!(format, JsonFormat::Diagnostic) {
                indexes.extend(items.iter().filter_map(|item| Some((array_index(&item.0)?, item))));
                indexes.sort_unstable_by_key(|(index, _)| *index);
            }
            let items = indexes.iter().map(|(_, item)| *item).chain(
                items
                    .iter()
                    .filter(|(key, _)| indexes.is_empty() || array_index(key).is_none()),
            );
            for (index, (key, item)) in items.enumerate() {
                if index != 0 {
                    output.push(',');
                }
                write_quoted(output, key, format);
                output.push(':');
                write_value(output, item, format, depth + 1)?;
            }
            output.push('}');
        }
    }
    Ok(())
}

pub(crate) fn json(value: &Value, format: JsonFormat) -> Result<String> {
    let mut output = String::new();
    write_value(&mut output, value, format, 0)?;
    Ok(output)
}

pub(crate) fn float(value: f64, format: JsonFormat) -> String {
    super::super::value::decimal::float(value, !matches!(format, JsonFormat::Diagnostic))
}
