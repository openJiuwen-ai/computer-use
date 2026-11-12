// Copyright (c) Huawei Technologies Co., Ltd. 2026. All rights reserved.

//! Decode the admitted transport into an ordered Rust tree and retain exact diagnostics.

use std::{collections::TryReserveError, ops::Range};

use super::{value::Value, wire_value::VALUE_DEPTH_LIMIT};
use crate::dense;

#[cfg(test)]
mod metadata_depth;

const MAX_JSON_BYTES: usize = 64 * 1024 * 1024;
// Each nested NativeValue may add its object, tagged data, entries array,
// and key/value pair. Leave space for the pool and response envelope.
const MAX_JSON_DEPTH: usize = 4 * VALUE_DEPTH_LIMIT + 16;
const MAX_JSON_VALUES: usize = 4_000_000;
const MAX_OBJECT_FIELDS: usize = 128;

#[derive(Debug, PartialEq, Eq)]
pub(super) enum WireError {
    Frame(dense::Error),
    Syntax,
    Invalid(&'static str),
    Unsupported(&'static str),
    Allocation,
}

impl From<TryReserveError> for WireError {
    fn from(_: TryReserveError) -> Self {
        Self::Allocation
    }
}

pub(super) type WireResult<T> = Result<T, WireError>;

pub(super) struct WireFrame {
    pub value: Value,
    pub diagnostic: Vec<u8>,
}

pub(super) struct WireEnvelope {
    pub version: Value,
    pub session: Value,
    pub id: Value,
    pub body: EnvelopeBody,
}

pub(super) enum EnvelopeBody {
    Observation(WireFrame),
    Failure(Value),
}

pub(super) fn decode_envelope(source: &[u8]) -> WireResult<Option<WireEnvelope>> {
    let (value, result) = decode_with_result(source, None, None, Position::Envelope)?;
    let Value::Map(mut fields) = value else { return Ok(None) };
    if fields.len() != 4 { return Ok(None); }
    let mut take = |name: &str| {
        let position = fields.iter().position(|(key, _)| key == name)?;
        Some(fields.swap_remove(position).1)
    };
    let (Some(version), Some(session), Some(id)) = (take("version"), take("session"), take("id")) else {
        return Ok(None);
    };
    let body = if let Some(error) = take("error") {
        EnvelopeBody::Failure(error)
    } else if let (Some(value), Some(result)) = (take("result"), result) {
        EnvelopeBody::Observation(WireFrame { value, diagnostic: source[result].to_vec() })
    } else {
        return Ok(None);
    };
    Ok(Some(WireEnvelope { version, session, id, body }))
}

pub(super) fn decode_cud2(body: &[u8], max_bytes: usize) -> WireResult<WireFrame> {
    decode_dense_frame(body, max_bytes, true)
}

pub(super) fn decode_cud1(body: &[u8], max_bytes: usize) -> WireResult<WireFrame> {
    decode_dense_frame(body, max_bytes, false)
}

pub(super) fn decode_cus2(body: &[u8], max_bytes: usize) -> WireResult<WireFrame> {
    let Some((diagnostic, bindings)) =
        dense::restore_segmented_original(body, max_bytes, MAX_JSON_DEPTH).map_err(WireError::Frame)? else {
        return Err(WireError::Unsupported("unproved segmented property slot"));
    };
    let (value, _) = decode_with_result(&diagnostic, None, Some(&bindings), Position::Root)?;
    Ok(WireFrame { value, diagnostic })
}

fn decode_dense_frame(body: &[u8], max_bytes: usize, pooled: bool) -> WireResult<WireFrame> {
    let Some((diagnostic, positions)) =
        dense::restore_original(body, max_bytes, pooled, MAX_JSON_DEPTH).map_err(WireError::Frame)? else {
        return Err(WireError::Unsupported("unproved dense property slot"));
    };
    let value = decode(&diagnostic, Some(&positions))?;
    Ok(WireFrame { value, diagnostic })
}

pub(super) fn decode_json(source: &[u8]) -> WireResult<Value> {
    decode(source, None)
}

fn decode<'a>(source: &'a [u8], packed: Option<&'a [usize]>) -> WireResult<Value> {
    decode_with_result(source, packed, None, Position::Root).map(|(value, _)| value)
}

fn decode_with_result<'a>(
    source: &'a [u8], packed: Option<&'a [usize]>, segmented: Option<&'a [dense::GapBinding]>,
    position: Position,
) -> WireResult<(Value, Option<Range<usize>>)> {
    if source.len() > MAX_JSON_BYTES {
        return Err(WireError::Unsupported("JSON byte limit"));
    }
    let source = std::str::from_utf8(source).map_err(|_| WireError::Syntax)?;
    let mut parser = Json {
        source, offset: 0, remaining: MAX_JSON_VALUES, packed, segmented, root_result: None, value_frames: Vec::new(),
    };
    let value = parser.value(0, position)?;
    parser.whitespace();
    if parser.peek().is_some() {
        return Err(WireError::Syntax);
    }
    if parser.packed.is_some_and(|positions| !positions.is_empty()) {
        return Err(WireError::Unsupported("unbound dense property array"));
    }
    if parser.segmented.is_some_and(|bindings| !bindings.is_empty()) {
        return Err(WireError::Unsupported("unbound segmented property slot"));
    }
    Ok((value, parser.root_result))
}

struct Json<'a> {
    source: &'a str,
    offset: usize,
    remaining: usize,
    packed: Option<&'a [usize]>,
    segmented: Option<&'a [dense::GapBinding]>,
    root_result: Option<Range<usize>>,
    value_frames: Vec<ValueFrame>,
}

#[derive(Clone, Copy)]
enum Position {
    Envelope,
    Root,
    Nodes,
    Node,
    Properties,
    Property,
    Other,
}

enum ValueStart {
    Scalar(Value),
    Sequence,
    Object,
}

enum ValueFrame {
    Sequence(Vec<Value>),
    Object { entries: Vec<(String, Value)>, key: String },
}

impl Json<'_> {
    fn peek(&self) -> Option<u8> {
        self.source.as_bytes().get(self.offset).copied()
    }

    fn consume(&mut self, byte: u8) -> bool {
        if self.peek() == Some(byte) {
            self.offset += 1;
            true
        } else {
            false
        }
    }

    fn expect(&mut self, byte: u8) -> WireResult<()> {
        self.consume(byte).then_some(()).ok_or(WireError::Syntax)
    }

    fn whitespace(&mut self) {
        while matches!(self.peek(), Some(b' ' | b'\t' | b'\r' | b'\n')) {
            self.offset += 1;
        }
    }

    fn literal(&mut self, literal: &str, value: Value) -> WireResult<Value> {
        if self.source[self.offset..].starts_with(literal) {
            self.offset += literal.len();
            Ok(value)
        } else {
            Err(WireError::Syntax)
        }
    }

    fn value_start(&mut self, depth: usize) -> WireResult<ValueStart> {
        if depth > MAX_JSON_DEPTH || self.remaining == 0 {
            return Err(WireError::Unsupported("JSON structure limit"));
        }
        self.remaining -= 1;
        self.whitespace();
        let scalar = match self.peek() {
            Some(b'n') => self.literal("null", Value::Null),
            Some(b't') => self.literal("true", Value::Bool(true)),
            Some(b'f') => self.literal("false", Value::Bool(false)),
            Some(b'"') => self.string().map(Value::Text),
            Some(b'-' | b'0'..=b'9') => self.number(),
            Some(b'[') => return Ok(ValueStart::Sequence),
            Some(b'{') => return Ok(ValueStart::Object),
            _ => Err(WireError::Syntax),
        }?;
        Ok(ValueStart::Scalar(scalar))
    }

    fn value(&mut self, depth: usize, position: Position) -> WireResult<Value> {
        match self.value_start(depth)? {
            ValueStart::Scalar(value) => Ok(value),
            start if matches!(position, Position::Other) => {
                let mut frames = std::mem::take(&mut self.value_frames);
                let result = self.nested_value(depth, start, &mut frames);
                self.value_frames = frames;
                result
            }
            ValueStart::Sequence => self.sequence(depth + 1, position),
            ValueStart::Object => self.object(depth + 1, position),
        }
    }

    fn sequence(&mut self, depth: usize, position: Position) -> WireResult<Value> {
        if let (Position::Properties, Some(positions)) = (position, self.packed) {
            match positions.split_first() {
                Some((&offset, rest)) if offset == self.offset => self.packed = Some(rest),
                _ => return Err(WireError::Unsupported("unbound dense property array")),
            }
        }
        let item_position = match position {
            Position::Nodes => Position::Node,
            Position::Properties => Position::Property,
            _ => Position::Other,
        };
        self.offset += 1;
        self.whitespace();
        let mut items = Vec::new();
        if !self.consume(b']') {
            loop {
                let value = self.value(depth, item_position)?;
                items.try_reserve(1)?;
                items.push(value);
                self.whitespace();
                if self.consume(b']') {
                    break;
                }
                self.expect(b',')?;
            }
        }
        Ok(Value::Seq(items))
    }

    fn object(&mut self, depth: usize, position: Position) -> WireResult<Value> {
        self.offset += 1;
        self.whitespace();
        let mut entries: Vec<(String, Value)> = Vec::new();
        if !self.consume(b'}') {
            loop {
                let key = self.key()?;
                let field_position = match (position, key.as_str()) {
                    (Position::Envelope, "result") => Position::Root,
                    (Position::Root, "nodes") => Position::Nodes,
                    (Position::Node, "properties") => Position::Properties,
                    _ => Position::Other,
                };
                let before_value = self.offset;
                self.whitespace();
                let start = self.offset;
                let bound_end = if let (Position::Property, Some(bindings), "key" | "read") =
                    (position, self.segmented, key.as_str())
                {
                    match bindings.split_first() {
                        Some((binding, rest)) if binding.read == (key == "read")
                            && (before_value..=start).contains(&binding.start) => {
                            self.segmented = Some(rest);
                            Some(binding.end)
                        }
                        _ => return Err(WireError::Unsupported("unbound segmented property slot")),
                    }
                } else {
                    None
                };
                let value = self.value(depth, field_position)?;
                if bound_end.is_some_and(|end| {
                    self.source.as_bytes().get(self.offset..end).is_none_or(|tail| {
                        !tail.iter().all(|byte| matches!(*byte, b' ' | b'\t' | b'\r' | b'\n'))
                    })
                }) {
                    return Err(WireError::Unsupported("partial segmented property slot"));
                }
                if matches!(position, Position::Envelope) && key == "result" {
                    self.root_result = Some(start..self.offset);
                }
                self.insert_field(&mut entries, key, value)?;
                self.whitespace();
                if self.consume(b'}') {
                    break;
                }
                self.expect(b',')?;
            }
        }
        Ok(Value::Map(entries))
    }

    fn key(&mut self) -> WireResult<String> {
        self.whitespace();
        let key = self.string()?;
        self.whitespace();
        self.expect(b':')?;
        Ok(key)
    }

    fn insert_field(&self, entries: &mut Vec<(String, Value)>, key: String, value: Value) -> WireResult<()> {
        // CPython keeps the first insertion position and the last value.
        if let Some((_, previous)) = entries.iter_mut().find(|(name, _)| *name == key) {
            if self.packed.is_some() || self.segmented.is_some() {
                return Err(WireError::Unsupported("duplicate compact key"));
            }
            *previous = value;
        } else {
            if entries.len() == MAX_OBJECT_FIELDS {
                return Err(WireError::Unsupported("wide JSON object"));
            }
            entries.try_reserve(1)?;
            entries.push((key, value));
        }
        Ok(())
    }

    // Positions carrying packed/segmented slots are handled by the shallow
    // observation grammar. Any nested value below that grammar uses this
    // reusable stack and shares the same primitive and object validation.
    fn nested_value(&mut self, depth: usize, mut next: ValueStart, frames: &mut Vec<ValueFrame>) -> WireResult<Value> {
        'parse: loop {
            let mut value = match next {
                ValueStart::Scalar(value) => value,
                ValueStart::Sequence => {
                    self.offset += 1;
                    self.whitespace();
                    if !self.consume(b']') {
                        frames.try_reserve(1)?;
                        frames.push(ValueFrame::Sequence(Vec::new()));
                        next = self.value_start(depth + frames.len())?;
                        continue 'parse;
                    }
                    Value::Seq(Vec::new())
                }
                ValueStart::Object => {
                    self.offset += 1;
                    self.whitespace();
                    if !self.consume(b'}') {
                        frames.try_reserve(1)?;
                        frames.push(ValueFrame::Object { entries: Vec::new(), key: self.key()? });
                        next = self.value_start(depth + frames.len())?;
                        continue 'parse;
                    }
                    Value::Map(Vec::new())
                }
            };
            loop {
                let Some(frame) = frames.last_mut() else { return Ok(value); };
                let close = match frame {
                    ValueFrame::Sequence(items) => {
                        items.try_reserve(1)?;
                        items.push(value);
                        b']'
                    }
                    ValueFrame::Object { entries, key } => {
                        self.insert_field(entries, std::mem::take(key), value)?;
                        b'}'
                    }
                };
                self.whitespace();
                if self.consume(close) {
                    value = match frames.pop().expect("completed value frame") {
                        ValueFrame::Sequence(items) => Value::Seq(items),
                        ValueFrame::Object { entries, .. } => Value::Map(entries),
                    };
                    continue;
                }
                self.expect(b',')?;
                if let ValueFrame::Object { key, .. } = frame {
                    *key = self.key()?;
                }
                next = self.value_start(depth + frames.len())?;
                continue 'parse;
            }
        }
    }

    fn digits(&mut self) -> WireResult<()> {
        let start = self.offset;
        while matches!(self.peek(), Some(b'0'..=b'9')) {
            self.offset += 1;
        }
        (self.offset != start).then_some(()).ok_or(WireError::Syntax)
    }

    fn number(&mut self) -> WireResult<Value> {
        let start = self.offset;
        self.consume(b'-');
        if !self.consume(b'0') {
            self.digits()?;
        }
        let mut floating = self.consume(b'.');
        if floating {
            self.digits()?;
        }
        if self.consume(b'e') || self.consume(b'E') {
            floating = true;
            if !self.consume(b'+') {
                self.consume(b'-');
            }
            self.digits()?;
        }
        let source = &self.source[start..self.offset];
        if floating {
            return source.parse().map(Value::Float).map_err(|_| WireError::Syntax);
        }
        if let Ok(number) = source.parse() {
            return Ok(Value::Int(number));
        }
        if source.trim_start_matches('-').len() > 4300 {
            return Err(WireError::Unsupported("large JSON integer"));
        }
        let mut number = String::new();
        number.try_reserve_exact(source.len())?;
        number.push_str(source);
        Ok(Value::BigInt(number))
    }

    fn string(&mut self) -> WireResult<String> {
        self.expect(b'"')?;
        let mut output = String::new();
        let mut start = self.offset;
        while let Some(byte) = self.peek() {
            match byte {
                b'"' | b'\\' => {
                    let raw = &self.source[start..self.offset];
                    output.try_reserve(raw.len())?;
                    output.push_str(raw);
                    self.offset += 1;
                    if byte == b'"' {
                        return Ok(output);
                    }
                    let escaped = self.escape()?;
                    output.try_reserve(escaped.len_utf8())?;
                    output.push(escaped);
                    start = self.offset;
                }
                0..=31 => return Err(WireError::Syntax),
                _ => self.offset += 1,
            }
        }
        Err(WireError::Syntax)
    }

    fn escape(&mut self) -> WireResult<char> {
        let byte = self.peek().ok_or(WireError::Syntax)?;
        self.offset += 1;
        match byte {
            b'"' | b'\\' | b'/' => Ok(char::from(byte)),
            b'b' => Ok('\u{8}'),
            b'f' => Ok('\u{c}'),
            b'n' => Ok('\n'),
            b'r' => Ok('\r'),
            b't' => Ok('\t'),
            b'u' => self.unicode_escape(),
            _ => Err(WireError::Syntax),
        }
    }

    fn hex_word(&mut self) -> WireResult<u16> {
        let mut word = 0;
        for _ in 0..4 {
            let digit = self.peek().and_then(|byte| char::from(byte).to_digit(16));
            word = (word << 4) | digit.ok_or(WireError::Syntax)? as u16;
            self.offset += 1;
        }
        Ok(word)
    }

    fn unicode_escape(&mut self) -> WireResult<char> {
        let first = self.hex_word()?;
        let code = match first {
            0xd800..=0xdbff => {
                if !self.consume(b'\\') || !self.consume(b'u') {
                    return Err(WireError::Unsupported("unpaired UTF-16 surrogate"));
                }
                let second = self.hex_word()?;
                if !(0xdc00..=0xdfff).contains(&second) {
                    return Err(WireError::Unsupported("unpaired UTF-16 surrogate"));
                }
                0x10000 + (u32::from(first - 0xd800) << 10) + u32::from(second - 0xdc00)
            }
            0xdc00..=0xdfff => return Err(WireError::Unsupported("unpaired UTF-16 surrogate")),
            _ => u32::from(first),
        };
        char::from_u32(code).ok_or(WireError::Syntax)
    }
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn decodes_ordered_values_with_cpython_duplicate_key_semantics() {
        let parsed = decode_json(br#"{"b":"old","a":[null,true,-0.0,9223372036854775808],"b":"\u96ea\ud83e\uddea"}"#)
            .unwrap();
        let Value::Map(entries) = parsed else { panic!("expected object") };
        assert_eq!(entries.iter().map(|entry| entry.0.as_str()).collect::<Vec<_>>(), ["b", "a"]);
        assert!(matches!(&entries[0].1, Value::Text(value) if value == "雪🧪"));
        let Value::Seq(items) = &entries[1].1 else { panic!("expected array") };
        assert!(matches!(items[..], [Value::Null, Value::Bool(true), Value::Float(number), Value::BigInt(ref big)]
            if number.to_bits() == (-0.0_f64).to_bits() && big == "9223372036854775808"));
    }

    #[test]
    fn declines_unrepresentable_strings_separately_from_invalid_syntax() {
        for invalid in [b"01".as_slice(), b"1e+", b"NaN", b"[1,]", b"{} {}", br#""\u01ZZ""#, b"\"\xff\""] {
            assert!(matches!(decode_json(invalid), Err(WireError::Syntax)), "{invalid:?}");
        }
        for surrogate in [br#""\ud800""#.as_slice(), br#""\udc00""#, br#""\ud800\u0000""#] {
            assert!(matches!(decode_json(surrogate), Err(WireError::Unsupported("unpaired UTF-16 surrogate"))));
        }
    }

    #[test]
    fn restores_cud2_body_before_decoding_without_changing_diagnostics() {
        let source = br#"{"kind":"observation","metadataValues":[{"nativeType":"CFString","data":"test"}]}"#;
        let mut body = b"CUD2".to_vec();
        for word in [0, 0, 0, source.len(), 0, source.len()] {
            body.extend_from_slice(&(word as u32).to_le_bytes());
        }
        body.extend_from_slice(source);
        let frame = decode_cud2(&body, 4096).unwrap();
        assert_eq!(frame.diagnostic, source);
        assert!(matches!(frame.value, Value::Map(fields)
            if fields.len() == 2 && fields[1].0 == "metadataValues"));
        assert!(matches!(decode_cud2(&body, body.len() - 1), Err(WireError::Frame(_))));
    }

    #[test]
    fn packed_arrays_must_bind_at_their_original_node_property_position() {
        let key = br#"["macos.ax","AXTitle"]"#;
        let read = br#"j{"complete":false}"#;
        let value = b"null";
        let state = br#""absent""#;
        let property = br#"[{"key":["macos.ax","AXTitle"],"read":{"complete":false},"state":"absent","value":null}]"#;
        let pool = |data: &[u8]| [(data.len() as u32).to_le_bytes().as_slice(), data].concat();
        let build = |prefix: &[u8], tail: &[u8], pooled: bool| {
            let pools = [pool(key), pool(read), pool(value)];
            let mut tape = Vec::new();
            tape.extend_from_slice(&(prefix.len() as u32).to_le_bytes());
            tape.extend_from_slice(&1_u32.to_le_bytes());
            tape.extend_from_slice(prefix);
            let value_word = if pooled { 1 << 31 } else { value.len() as u32 };
            for field in [0, 0, 0, state.len() as u32, value_word] {
                tape.extend_from_slice(&field.to_le_bytes());
            }
            tape.extend_from_slice(state);
            if !pooled {
                tape.extend_from_slice(value);
            }
            tape.extend_from_slice(tail);
            let expected = [prefix, property, tail].concat();
            let mut body = if pooled { b"CUD2".to_vec() } else { b"CUD1".to_vec() };
            let mut words = vec![pools[0].len(), pools[1].len()];
            if pooled {
                words.push(pools[2].len());
            }
            words.extend([tape.len(), 1, expected.len()]);
            for word in words {
                body.extend_from_slice(&(word as u32).to_le_bytes());
            }
            for part in pools.into_iter().take(if pooled { 3 } else { 2 }).chain([tape]) {
                body.extend(part);
            }
            (body, expected)
        };

        let prefix = br#"{"kind":"observation","nodes":[{"properties":"#;
        let tail = b"}]}";
        for (pooled, decode) in [(false, decode_cud1 as fn(&[u8], usize) -> WireResult<WireFrame>), (true, decode_cud2)] {
            let (body, expected) = build(prefix, tail, pooled);
            let frame = decode(&body, 4096).unwrap();
            assert_eq!(frame.diagnostic, expected);
            assert_eq!(frame.value, decode_json(&expected).unwrap());
        }

        for (prefix, tail) in [
            (br#"{"kind":"observation","nodes":[{"other":"#.as_slice(), tail.as_slice()),
            (prefix.as_slice(), br#","properties":null}]}"#.as_slice()),
        ] {
            let (body, expected) = build(prefix, tail, true);
            assert!(decode_json(&expected).is_ok());
            assert!(matches!(decode_cud2(&body, 4096), Err(WireError::Unsupported(_))));
        }
    }

    #[test]
    fn segmented_pool_slots_bind_at_the_original_property_cells() {
        let key = br#" ["macos.ax","AXTitle"] "#;
        let read_json = br#" {"complete":false} "#;
        let read = [b"j".as_slice(), read_json].concat();
        let between = br#","read":"#;
        let suffix = br#","value":null}]}]}"#;
        let pool = |data: &[u8]| [(data.len() as u32).to_le_bytes().as_slice(), data].concat();
        let build = |prefix: &[u8], first_tag: u32| {
            let literal = [prefix, between, suffix].concat();
            let key_pool = pool(key);
            let read_pool = pool(&read);
            let mut frame = b"CUS2".to_vec();
            for size in [literal.len(), key_pool.len(), read_pool.len(), 16] {
                frame.extend_from_slice(&(size as u32).to_le_bytes());
            }
            frame.extend(literal);
            frame.extend(key_pool);
            frame.extend(read_pool);
            for word in [prefix.len() as u32, first_tag, between.len() as u32, 1 - first_tag] {
                frame.extend_from_slice(&word.to_le_bytes());
            }
            frame
        };
        let prefix = br#"{"kind":"observation","nodes":[{"properties":[{"key": "#;
        let frame = build(prefix, 0);
        let expected = [
            prefix.as_slice(), key.as_slice(), between.as_slice(), read_json.as_slice(), suffix.as_slice(),
        ].concat();
        let decoded = decode_cus2(&frame, 4096).unwrap();
        assert_eq!(decoded.diagnostic, expected);
        assert_eq!(decoded.value, decode_json(&expected).unwrap());
        let misplaced = br#"{"kind":"observation","nodes":[{"properties":[{"other": "#;
        for bad in [build(prefix, 1), build(misplaced, 0)] {
            assert!(matches!(decode_cus2(&bad, 4096), Err(WireError::Unsupported(_))));
        }
    }
}
