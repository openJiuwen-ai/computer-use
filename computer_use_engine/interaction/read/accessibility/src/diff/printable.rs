// Copyright (c) Huawei Technologies Co., Ltd. 2026. All rights reserved.

use crate::unicode_tables::NON_PRINTABLE;

pub(crate) fn is_printable(value: char) -> bool {
    if value.is_ascii() {
        return matches!(value, ' '..='~');
    }
    NON_PRINTABLE.partition_point(|boundary| *boundary <= value as u32) % 2 == 0
}
