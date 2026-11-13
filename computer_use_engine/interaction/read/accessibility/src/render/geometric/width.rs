// Copyright (c) Huawei Technologies Co., Ltd. 2026. All rights reserved.

use crate::unicode_tables::{WIDE, ZERO};

fn contains(character: char, ranges: &[(u32, u32)]) -> bool {
    let code = character as u32;
    let index = ranges.partition_point(|(_, end)| *end < code);
    ranges.get(index).is_some_and(|(start, _)| *start <= code)
}

pub(super) fn width(text: &str) -> usize {
    text.chars()
        .map(|character| {
            if contains(character, ZERO) {
                0
            } else if contains(character, WIDE) {
                2
            } else {
                1
            }
        })
        .sum()
}
