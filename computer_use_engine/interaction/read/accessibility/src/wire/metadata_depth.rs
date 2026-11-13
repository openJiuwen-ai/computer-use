// Copyright (c) Huawei Technologies Co., Ltd. 2026. All rights reserved.

//! Read admission at the raw and typed depth boundaries.

mod tests {
    use super::super::{
        decode_cud1, decode_cud2, decode_cus2, decode_envelope, decode_json, EnvelopeBody, Value,
        WireError, WireFrame, WireResult, MAX_JSON_DEPTH,
    };
    use crate::facts;

    const LIMIT: usize = 64 * 1024 * 1024;

    fn nested_key(levels: usize) -> String {
        let mut value = r#"{"nativeType":"CFDictionary","data":{"entries":[["#.repeat(levels);
        value.push_str(r#"{"nativeType":"CFString","data":"leaf"}"#);
        value.push_str(&r#",{"nativeType":"CFNull","data":null}]]}}"#.repeat(levels));
        value
    }

    fn observation(pool: &str, reference: &str, properties: &str) -> Vec<u8> {
        format!(concat!(
            r#"{{"kind":"observation","session":"session","platform":"macos","roots":["root"],"#,
            r#""nodes":[{{"id":"root","source":"macos.ax","kind":"element","nativeType":"AXUIElement","properties":{properties}}}],"#,
            r#""read":{{"intervalNs":[1,2],"details":[["surface",{{"nativeType":"CFString","data":"window"}}],"#,
            r#"["observation_id",{{"nativeType":"CFString","data":"capture"}}]{reference}]}},"metadataValues":[{pool}]}}"#,
        ), properties = properties, reference = reference, pool = pool).into_bytes()
    }

    fn compact(magic: &[u8; 4], body: &[u8]) -> Vec<u8> {
        let size = u32::try_from(body.len()).unwrap();
        let words: &[u32] = match magic {
            b"CUS2" => &[size, 0, 0, 0],
            b"CUD1" => &[0, 0, size, 0, size],
            b"CUD2" => &[0, 0, 0, size, 0, size],
            _ => unreachable!(),
        };
        let mut frame = magic.to_vec();
        for word in words {
            frame.extend_from_slice(&word.to_le_bytes());
        }
        frame.extend_from_slice(body);
        frame
    }

    fn compact_property(magic: &[u8; 4], body: &[u8], value: &[u8]) -> Vec<u8> {
        let key = br#"["macos.ax","AXHelp"]"#;
        let word = |number: usize| u32::try_from(number).unwrap().to_le_bytes();
        let cell = |value: &[u8]| [word(value.len()).as_slice(), value].concat();
        let key_pool = cell(key);
        let property_marker = b"\"properties\":";
        let property_start = body
            .windows(property_marker.len())
            .position(|item| item == property_marker)
            .unwrap()
            + property_marker.len();
        let key_start = property_start + br#"[{"key":"#.len();
        let property_end =
            key_start + key.len() + br#","value":"#.len() + value.len() + b"}]".len();
        let mut result = magic.to_vec();
        if magic == b"CUS2" {
            let literal = [&body[..key_start], &body[key_start + key.len()..]].concat();
            for count in [literal.len(), key_pool.len(), 0, 8] {
                result.extend_from_slice(&word(count));
            }
            result.extend(literal);
            result.extend(key_pool);
            result.extend_from_slice(&word(key_start));
            result.extend_from_slice(&word(0));
            return result;
        }
        let pooled = magic == b"CUD2";
        let mut tape = [
            word(property_start).as_slice(),
            &word(1),
            &body[..property_start],
        ]
        .concat();
        for count in [
            0,
            u32::MAX,
            0,
            0,
            if pooled { 1 << 31 } else { value.len() as u32 },
        ] {
            tape.extend_from_slice(&count.to_le_bytes());
        }
        if !pooled {
            tape.extend_from_slice(value);
        }
        tape.extend_from_slice(&body[property_end..]);
        result.extend_from_slice(&word(key_pool.len()));
        result.extend_from_slice(&word(0));
        let value_pool = cell(value);
        if pooled {
            result.extend_from_slice(&word(value_pool.len()));
        }
        for count in [tape.len(), 1, body.len()] {
            result.extend_from_slice(&word(count));
        }
        result.extend(key_pool);
        if pooled {
            result.extend(value_pool);
        }
        result.extend(tape);
        result
    }

    fn assert_normalizes(value: &Value) {
        let snapshot = facts::normalize_query(facts::query::collect_wire(value).unwrap()).unwrap();
        assert!(snapshot
            .observation
            .get("nodes")
            .unwrap()
            .get("root")
            .is_some());
    }

    #[test]
    fn accepts_unreferenced_nested_pool_in_every_read_transport_on_small_stack() {
        std::thread::Builder::new()
            .stack_size(512 * 1024)
            .spawn(|| {
                let body = observation(&nested_key(95), "", "null");
                assert_normalizes(&decode_json(&body).unwrap());
                let decorated = nested_key(95).replace(
                    r#""nativeType":"CFDictionary","data""#,
                    r#""nativeType":"CFDictionary","raw":"AA==","metadata":[["unit",{"nativeType":"CFString","data":"test"}]],"data""#,
                );
                assert_normalizes(&decode_json(&observation(&decorated, "", "null")).unwrap());
                let line = [
                    br#"{"version":6,"session":"session","id":1,"result":"#.as_slice(),
                    &body,
                    b"}\n",
                ]
                .concat();
                let envelope = decode_envelope(&line).unwrap().unwrap();
                let EnvelopeBody::Observation(frame) = envelope.body else { panic!("expected an observation") };
                assert_eq!(frame.diagnostic, body);
                assert_normalizes(&frame.value);
                for (magic, decode) in [
                    (
                        b"CUS2",
                        decode_cus2 as fn(&[u8], usize) -> WireResult<WireFrame>,
                    ),
                    (b"CUD1", decode_cud1),
                    (b"CUD2", decode_cud2),
                ] {
                    let frame = decode(&compact(magic, &body), LIMIT).unwrap();
                    assert_eq!(frame.diagnostic, body);
                    assert_normalizes(&frame.value);
                }
            })
            .unwrap()
            .join()
            .unwrap();
    }

    #[test]
    fn admits_referenced_deep_metadata_and_rejects_malformed_pool_values() {
        std::thread::Builder::new()
            .stack_size(512 * 1024)
            .spawn(|| {
                let pool = nested_key(94);
                let reference = r#",["extra",{"metadataValue":0}]"#;
                assert_normalizes(&decode_json(&observation(&pool, reference, "null")).unwrap());
                let indirect = format!(
                    r#"{pool},{{"nativeType":"CFArray","data":{{"items":[{{"metadataValue":0}}]}}}}"#
                );
                let raw = decode_json(&observation(&indirect, "", "null")).unwrap();
                assert!(facts::query::collect_wire(&raw).is_err());
            })
            .unwrap()
            .join()
            .unwrap();
    }

    #[test]
    fn admits_deep_inline_values_in_proved_property_cells_on_small_stack() {
        std::thread::Builder::new()
            .stack_size(512 * 1024)
            .spawn(|| {
                // Decode the deep provider value before applying its shallow semantic override.
                let mut value = nested_key(95);
                value.pop();
                value.push_str(
                    r#","metadata":[["toolkit_value",{"nativeType":"CFString","data":"help"}]]}"#,
                );
                let property = format!(r#"[{{"key":["macos.ax","AXHelp"],"value":{value}}}]"#);
                let body = observation("", "", &property);
                let help = |value: &Value| {
                    let snapshot =
                        facts::normalize_query(facts::query::collect_wire(value).unwrap()).unwrap();
                    assert_eq!(
                        snapshot
                            .observation
                            .get("nodes")
                            .unwrap()
                            .get("root")
                            .unwrap()
                            .get("help")
                            .and_then(Value::as_str),
                        Some("help")
                    );
                };
                help(&decode_json(&body).unwrap());
                let line = [
                    br#"{"version":6,"session":"session","id":1,"result":"#.as_slice(),
                    &body,
                    b"}\n",
                ]
                .concat();
                let envelope = decode_envelope(&line).unwrap().unwrap();
                let EnvelopeBody::Observation(frame) = envelope.body else {
                    panic!("expected an observation")
                };
                help(&frame.value);
                for (magic, decode) in [
                    (
                        b"CUS2",
                        decode_cus2 as fn(&[u8], usize) -> WireResult<WireFrame>,
                    ),
                    (b"CUD1", decode_cud1),
                    (b"CUD2", decode_cud2),
                ] {
                    let compact = compact_property(magic, &body, value.as_bytes());
                    let frame = decode(&compact, LIMIT).unwrap();
                    assert_eq!(frame.diagnostic, body);
                    help(&frame.value);
                }
            })
            .unwrap()
            .join()
            .unwrap();
    }

    #[test]
    fn independently_bounds_raw_and_native_value_depth_on_small_stack() {
        std::thread::Builder::new()
            .stack_size(512 * 1024)
            .spawn(|| {
                // A direct observation's pool entry starts at JSON depth 2.
                let nested_objects = |levels: usize| {
                    format!("{}null{}", r#"{"x":"#.repeat(levels), "}".repeat(levels))
                };
                let body = observation(&nested_objects(MAX_JSON_DEPTH - 2), "", "null");
                let raw = decode_json(&body).unwrap();
                assert!(facts::query::collect_wire(&raw).is_err());
                let body = observation(&nested_objects(MAX_JSON_DEPTH - 1), "", "null");
                assert_eq!(
                    decode_json(&body),
                    Err(WireError::Unsupported("JSON structure limit"))
                );
                let body = observation(&nested_key(96), "", "null");
                let raw = decode_json(&body).unwrap();
                assert!(facts::query::collect_wire(&raw).is_err());
            })
            .unwrap()
            .join()
            .unwrap();
    }
}
