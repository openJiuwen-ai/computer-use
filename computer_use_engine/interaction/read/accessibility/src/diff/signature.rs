// Copyright (c) Huawei Technologies Co., Ltd. 2026. All rights reserved.

//! Stable comparison hashes compatible with the Python 3.11 baseline schema.

use std::{collections::HashMap, fmt::Write};

use super::Value;

#[path = "printable.rs"]
mod printable;
pub(crate) use printable::is_printable as python_is_printable;

pub(super) fn python_float_repr(value: f64) -> String {
    if value.is_nan() {
        return "nan".to_owned();
    }
    if value.is_infinite() {
        return if value.is_sign_negative() { "-inf" } else { "inf" }.to_owned();
    }

    super::super::value::decimal::float(value, true)
}

const STATE_FIELDS: [&str; 11] = [
    "enabled", "selected", "focused", "busy", "toggleState", "expanded", "disclosing", "visible", "showing",
    "modal", "_boundsOutsideWindow",
];

pub(super) type Digest = [u8; 32];

#[cfg(target_os = "macos")]
extern "C" {
    fn CC_SHA256(data: *const std::ffi::c_void, len: std::ffi::c_uint, md: *mut u8) -> *mut u8;
}

pub(crate) fn sha256(value: &[u8]) -> Digest {
    #[cfg(target_os = "macos")]
    if let Ok(length) = u32::try_from(value.len()) {
        let mut result = [0; 32];
        // Both buffers live through this call, and the output is exactly one SHA-256.
        unsafe { CC_SHA256(value.as_ptr().cast(), length, result.as_mut_ptr()) };
        return result;
    }
    use sha2::{Digest as _, Sha256};
    Sha256::digest(value).into()
}

fn hex(value: Digest) -> String {
    let mut text = String::with_capacity(64);
    for byte in value {
        write!(text, "{byte:02x}").expect("write to string");
    }
    text
}

pub(crate) fn python_str_repr(value: &str) -> String {
    let quote = if value.contains('\'') && !value.contains('"') { '"' } else { '\'' };
    let mut output = String::with_capacity(value.len() + 2);
    output.push(quote);
    for character in value.chars() {
        match character {
            '\\' => output.push_str("\\\\"),
            '\n' => output.push_str("\\n"),
            '\r' => output.push_str("\\r"),
            '\t' => output.push_str("\\t"),
            value if value == quote => {
                output.push('\\');
                output.push(value);
            }
            value if python_is_printable(value) => output.push(value),
            value => {
                let code = value as u32;
                match code {
                    0..=0xff => write!(output, "\\x{code:02x}"),
                    0x100..=0xffff => write!(output, "\\u{code:04x}"),
                    _ => write!(output, "\\U{code:08x}"),
                }.expect("write to string");
            }
        }
    }
    output.push(quote);
    output
}

fn bytes_repr(value: &[u8]) -> String {
    let quote = if value.contains(&b'\'') && !value.contains(&b'"') { b'"' } else { b'\'' };
    let mut output = String::with_capacity(value.len() + 3);
    output.push('b');
    output.push(char::from(quote));
    for &byte in value {
        match byte {
            b'\\' => output.push_str("\\\\"),
            b'\n' => output.push_str("\\n"),
            b'\r' => output.push_str("\\r"),
            b'\t' => output.push_str("\\t"),
            value if value == quote => {
                output.push('\\');
                output.push(char::from(value));
            }
            value @ b' '..=b'~' => output.push(char::from(value)),
            value => write!(output, "\\x{value:02x}").expect("write to string"),
        }
    }
    output.push(char::from(quote));
    output
}

fn scalar(tag: &[u8], representation: &str) -> Digest {
    let mut encoded = Vec::with_capacity(tag.len() + representation.len());
    encoded.extend_from_slice(tag);
    encoded.extend_from_slice(representation.as_bytes());
    sha256(&encoded)
}

fn mapping(pairs: impl IntoIterator<Item = (Digest, Digest)>, count: usize) -> Digest {
    let mut encoded = Vec::with_capacity(7 + count * 64);
    encoded.extend_from_slice(b"mapping");
    for (key, item) in pairs {
        encoded.extend_from_slice(&key);
        encoded.extend_from_slice(&item);
    }
    sha256(&encoded)
}

pub(super) fn sequence(values: &[Digest]) -> Digest {
    let mut encoded = Vec::with_capacity(8 + values.len() * 32);
    encoded.extend_from_slice(b"sequence");
    for value in values {
        encoded.extend_from_slice(value);
    }
    sha256(&encoded)
}

/// Hash the final prepared fields, with one memo for repeated strings in a tree.
#[derive(Default)]
pub(crate) struct Signatures<'a> {
    strings: HashMap<&'a str, Digest>,
}

impl<'a> Signatures<'a> {
    fn string(&mut self, value: &'a str) -> Digest {
        *self.strings.entry(value).or_insert_with(|| scalar(b"str:", &python_str_repr(value)))
    }

    pub(super) fn value(&mut self, value: &'a Value) -> Digest {
        match value {
            Value::Null => sha256(b"NoneType:None"),
            Value::Bool(value) => sha256(if *value { b"bool:True" } else { b"bool:False" }),
            Value::Int(value) => scalar(b"int:", &value.to_string()),
            Value::BigInt(value) => scalar(b"int:", value),
            Value::Float(value) => scalar(b"float:", &python_float_repr(*value)),
            Value::Text(value) => self.string(value),
            Value::Bytes(value) => scalar(b"bytes:", &bytes_repr(value)),
            Value::Seq(values) => {
                let mut encoded = Vec::with_capacity(8 + values.len() * 32);
                encoded.extend_from_slice(b"sequence");
                for value in values {
                    encoded.extend_from_slice(&self.value(value));
                }
                sha256(&encoded)
            }
            Value::Map(items) => {
                let mut pairs: Vec<_> = items.iter().map(|(key, value)| (self.string(key), self.value(value))).collect();
                pairs.sort_unstable();
                mapping(pairs, items.len())
            }
        }
    }

    pub(crate) fn capture(&mut self, fields: &'a [(String, Value)]) -> (String, String) {
        self.capture_with_layout(fields, None)
    }

    pub(super) fn capture_with_layout(
        &mut self, fields: &'a [(String, Value)], layout: Option<Digest>,
    ) -> (String, String) {
        let mut pairs = Vec::with_capacity(fields.len() + usize::from(layout.is_some()));
        let mut content_count = 0;
        for (key, value) in fields {
            if key == "preserveGroup" || key == "_interfaceLayout" && layout.is_some() {
                continue;
            }
            let content = !STATE_FIELDS.contains(&key.as_str());
            pairs.push(((self.string(key), self.value(value)), content));
            content_count += usize::from(content);
        }
        if let Some(layout) = layout {
            pairs.push(((self.string("_interfaceLayout"), layout), true));
            content_count += 1;
        }
        pairs.sort_unstable_by(|left, right| left.0.cmp(&right.0));
        let full = mapping(pairs.iter().map(|pair| pair.0), pairs.len());
        let content = if pairs.len() == content_count {
            full
        } else {
            mapping(pairs.into_iter().filter_map(|(pair, content)| content.then_some(pair)), content_count)
        };
        (hex(full), hex(content))
    }
}
