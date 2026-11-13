// Copyright (c) Huawei Technologies Co., Ltd. 2026. All rights reserved.

use super::*;

const PREFIX: &[u8] = b"{\"kind\":\"observation\",\"nodes\":[{\"properties\":";
const TAIL: &[u8] = b"}]}";
const KEY: &[u8] = b"[\"macos.ax\",\"AXValue\"]";

fn put_u32(buffer: &mut Vec<u8>, value: usize) {
    buffer.extend_from_slice(&(value as u32).to_le_bytes());
}

fn fixture(arguments: &[u8], state: &[u8], value: &[u8], read: &[u8], original: &[u8]) -> Vec<u8> {
    let read_pool_size = if read.is_empty() { 0 } else { 4 + read.len() };
    let mut tape = Vec::new();
    put_u32(&mut tape, PREFIX.len());
    put_u32(&mut tape, 1);
    tape.extend_from_slice(PREFIX);
    put_u32(&mut tape, 0);
    put_u32(&mut tape, if read.is_empty() { u32::MAX as usize } else { 0 });
    for field in [arguments, state, value] {
        put_u32(&mut tape, field.len());
    }
    for field in [arguments, state, value, TAIL] {
        tape.extend_from_slice(field);
    }
    let mut frame = b"CUD1".to_vec();
    for field in [4 + KEY.len(), read_pool_size, tape.len(), 1, original.len()] {
        put_u32(&mut frame, field);
    }
    put_u32(&mut frame, KEY.len());
    frame.extend_from_slice(KEY);
    if !read.is_empty() {
        put_u32(&mut frame, read.len());
        frame.extend_from_slice(read);
    }
    frame.extend_from_slice(&tape);
    frame
}

fn original_property(arguments: &[u8], state: &[u8], value: &[u8], read: &[u8]) -> Vec<u8> {
    let mut parts = vec![PREFIX, b"[{" as &[u8]];
    if !arguments.is_empty() {
        parts.extend([b"\"arguments\":", arguments, b","]);
    }
    parts.extend([b"\"key\":", KEY]);
    if !read.is_empty() {
        parts.extend([b",\"read\":", read]);
    }
    if !state.is_empty() {
        parts.extend([b",\"state\":", state]);
    }
    parts.extend([b",\"value\":", value, b"}]", TAIL]);
    parts.concat()
}

#[test]
fn prepares_the_same_bytes_for_all_existing_read_layouts() {
    let arguments = br#"[{"data":-0.0,"nativeType":"provider"}]"#;
    let state = br#""unsupported""#;
    let value = br#"{"data":"literal \"quoted\" NaN \uD800","data":null,"nativeType":"provider"}"#;
    for (tag, completion) in [(b'b', ""), (b't', "true"), (b'f', "false"), (b'n', "null"), (b'j', "")] {
        let mut read = vec![tag];
        let rendered = if tag == b'j' {
            read.extend_from_slice(br#"{"details":[],"issues":[]}"#);
            read[1..].to_vec()
        } else {
            read.extend_from_slice(&u32::MAX.to_le_bytes());
            read.extend_from_slice(&0_u32.to_le_bytes());
            let details = r#"[["batch_owner",{"metadataValue":4294967295}],["batch_index",{"metadataValue":0}]]"#;
            if tag == b'b' {
                format!("{{\"details\":{details}}}").into_bytes()
            } else {
                read.extend_from_slice(b"[]");
                format!("{{\"complete\":{completion},\"details\":{details},\"issues\":[]}}").into_bytes()
            }
        };
        let expected = original_property(arguments, state, value, &rendered);
        let frame = fixture(arguments, state, value, &read, &expected);
        let result = prepare(&frame, MAX_BYTES).unwrap().unwrap();
        let dense = [PREFIX, b"[[\nNaN\n,", arguments, b",3,0,", state, b",", value, b"]]", TAIL].concat();
        assert_eq!(result.text, dense);
        assert_eq!(result.original, expected);
        assert_eq!(result.keys, [KEY]);
        assert_eq!(result.reads().collect::<Vec<_>>(), [read.as_slice()]);
        assert_eq!(result.counts, (1, 1));
        assert_eq!(result.widths, [1]);
    }
}

#[test]
fn restores_exact_old_marker_runs_even_when_lexical_proof_declines() {
    let value = b"1,2";
    let expected = original_property(b"", b"", value, b"null");
    let frame = fixture(b"", b"", value, b"jnull", &expected);
    assert!(prepare(&frame, MAX_BYTES).unwrap().is_none());
    let literal = [PREFIX, b"[{\"key\":,\"read\":,\"value\":1,2}]", TAIL].concat();
    let mut expected = b"CUS2".to_vec();
    for size in [literal.len(), KEY.len() + 4, 9, 16] {
        put_u32(&mut expected, size);
    }
    expected.extend_from_slice(&literal);
    put_u32(&mut expected, KEY.len());
    expected.extend_from_slice(KEY);
    put_u32(&mut expected, 5);
    expected.extend_from_slice(b"jnull");
    put_u32(&mut expected, PREFIX.len() + b"[{\"key\":".len());
    put_u32(&mut expected, 0);
    put_u32(&mut expected, b",\"read\":".len());
    put_u32(&mut expected, 1);
    assert_eq!(restore(&frame, MAX_BYTES).unwrap(), expected);
}

#[test]
fn rejects_bad_bounds_in_both_modes() {
    let expected = original_property(b"", b"", b"null", b"");
    let frame = fixture(b"", b"", b"null", b"", &expected);
    let mut bad = frame.clone();
    bad[20..24].copy_from_slice(&((expected.len() + 1) as u32).to_le_bytes());
    assert!(matches!(prepare(&bad, MAX_BYTES), Err(RESTORED)));
    assert_eq!(restore(&bad, MAX_BYTES), Err(RESTORED));
    assert!(matches!(prepare(&frame[..frame.len() - 1], MAX_BYTES), Err(BOUNDS)));
    assert_eq!(restore(&frame[..frame.len() - 1], MAX_BYTES), Err(BOUNDS));
    assert!(matches!(prepare(&frame, frame.len() - 1), Err(BOUNDS)));
    assert_eq!(restore(&frame, frame.len() - 1), Err(BOUNDS));
}

#[test]
fn declines_dense_only_expansion_over_the_limit() {
    let mut tape = Vec::new();
    let mut original = Vec::new();
    let nodes = 64;
    for index in 0..nodes {
        let prefix = if index == 0 { PREFIX } else { b"},{\"properties\":" };
        put_u32(&mut tape, prefix.len());
        put_u32(&mut tape, 0);
        tape.extend_from_slice(prefix);
        original.extend_from_slice(prefix);
        original.extend_from_slice(b"[]");
    }
    tape.extend_from_slice(TAIL);
    original.extend_from_slice(TAIL);
    let mut frame = b"CUD1".to_vec();
    for size in [0, 0, tape.len(), nodes, original.len()] {
        put_u32(&mut frame, size);
    }
    frame.extend_from_slice(&tape);
    assert!(prepare(&frame, frame.len()).unwrap().is_none());
    let legacy = restore(&frame, frame.len()).unwrap();
    assert_eq!(&legacy[CUS2_HEADER..], original);
}

#[test]
fn unused_pool_entries_are_rejected_before_any_json_parse() {
    let expected = original_property(b"", b"", b"null", b"");
    let mut frame = fixture(b"", b"", b"null", b"", &expected);
    let extra = b"[]";
    let key_pool_size = 4 + KEY.len();
    let mut entry = Vec::new();
    put_u32(&mut entry, extra.len());
    entry.extend_from_slice(extra);
    frame[4..8].copy_from_slice(&((key_pool_size + entry.len()) as u32).to_le_bytes());
    frame.splice(24 + key_pool_size..24 + key_pool_size, entry);
    assert!(matches!(prepare(&frame, MAX_BYTES), Err(UNUSED)));
    assert_eq!(restore(&frame, MAX_BYTES), Err(UNUSED));
}

#[test]
fn sparse_pooled_rows_retain_explicit_state_and_rich_cells() {
    let arguments = br#"[{"data":1,"nativeType":"provider"}]"#;
    let state = br#""absent""#;
    let value = br#"{"data":2,"nativeType":"provider"}"#;
    let mut tape = Vec::new();
    put_u32(&mut tape, PREFIX.len());
    put_u32(&mut tape, 3);
    tape.extend_from_slice(PREFIX);
    for (args, state, pool_index) in [
        (b"".as_slice(), b"".as_slice(), 0),
        (b"".as_slice(), state.as_slice(), 1),
        (arguments.as_slice(), b"".as_slice(), 0),
    ] {
        for word in [0, u32::MAX as usize, args.len(), state.len(), POOLED_VALUE as usize | pool_index] {
            put_u32(&mut tape, word);
        }
        tape.extend_from_slice(args);
        tape.extend_from_slice(state);
    }
    tape.extend_from_slice(TAIL);
    let expected = [
        PREFIX,
        br#"[{"key":"#,
        KEY,
        br#","value":"#,
        value,
        br#"},{"key":"#,
        KEY,
        br#","state":"#,
        state,
        br#","value":null},{"arguments":"#,
        arguments,
        br#","key":"#,
        KEY,
        br#","value":"#,
        value,
        b"}]",
        TAIL,
    ].concat();
    let mut frame = b"CUD2".to_vec();
    for size in [4 + KEY.len(), 0, 4 + value.len() + 8, tape.len(), 1, expected.len()] {
        put_u32(&mut frame, size);
    }
    for entry in [KEY, value, b"null"] {
        put_u32(&mut frame, entry.len());
        frame.extend_from_slice(entry);
    }
    frame.extend_from_slice(&tape);
    let ordinary = prepare_pooled(&frame, MAX_BYTES).unwrap().unwrap();
    let sparse = prepare_sparse(&frame, MAX_BYTES).unwrap().unwrap();
    assert_eq!(sparse.original, expected);
    assert_eq!(sparse.original, ordinary.original);
    assert_eq!(sparse.pool_text, ordinary.pool_text);
    assert_eq!(sparse.counts, (3, 0));
    assert_eq!(sparse.widths, [6]);
    assert_eq!(sparse.text, [PREFIX, b"[[\nNaN\n,", state, b",", arguments, b",2147483650,-1,null,0]]", TAIL].concat());
    let plan = sparse.sparse.unwrap();
    assert_eq!(plan.ends, [3]);
    assert_eq!(plan.rich_binds, [(0, 1, 0)]);
    assert_eq!(plan.bound, [true, false]);
    assert_eq!(plan.omitted + sparse.text.len(), ordinary.text.len());
}
