// Copyright (c) Huawei Technologies Co., Ltd. 2026. All rights reserved.

use crate::unicode_tables::ALPHABETIC;

pub(super) fn alphabetic(value: char) -> bool {
    let code = value as u32;
    let index = ALPHABETIC.partition_point(|(_, end)| *end < code);
    ALPHABETIC.get(index).is_some_and(|(start, _)| *start <= code)
}
