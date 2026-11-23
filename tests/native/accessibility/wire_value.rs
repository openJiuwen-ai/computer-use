// Copyright (c) Huawei Technologies Co., Ltd. 2026. All rights reserved.

    #[test]
    fn utf16_provider_values_keep_display_positions_and_diagnostic_bytes() {
        let raw = observation();
        let mut wire = WireValues::new(&raw, false).unwrap();
        // Matches AXNativeValueEncoding.string: a repaired preview plus the
        // original little-endian units, including an isolated high surrogate.
        let input = decode_json(br#"{
            "nativeType":"CFString","data":"A\ufffdB","raw":"QQAA2EIA",
            "metadata":[["raw_encoding",{"nativeType":"CFString","data":"utf-16-le"}]]
        }"#).unwrap();
        assert_eq!(
            wire.value(&input, 0),
            Ok(Value::Text("A\u{fffd}B".to_owned()))
        );
        assert_eq!(input.get("raw").and_then(Value::as_str), Some("QQAA2EIA"));
        let bstr = decode_json(br#"{
            "nativeType":"BSTR","data":{"bytes":"QQAA3EIA"},
            "metadata":[["string_encoding",{"nativeType":"BSTR","data":"utf-16le"}]]
        }"#).unwrap();
        assert_eq!(wire.value(&bstr, 0), Ok(Value::Text("A\u{fffd}B".to_owned())));
        let paired = decode_json(br#"{
            "nativeType":"CFString","data":"preview","raw":"PdgA3g==",
            "metadata":[["raw_encoding",{"nativeType":"CFString","data":"utf-16-le"}]]
        }"#).unwrap();
        assert_eq!(wire.value(&paired, 0), Ok(Value::Text("😀".to_owned())));
        assert_eq!(
            decode_utf16(&[b'A']),
            Err(WireError::Invalid("driver observation: invalid UTF-16 bytes"))
        );
    }
