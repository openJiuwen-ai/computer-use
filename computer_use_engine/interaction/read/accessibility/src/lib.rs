// Copyright (c) Huawei Technologies Co., Ltd. 2026. All rights reserved.

//! Shared accessibility read engine. Each stage uses Rust-owned data.

mod accounting;
mod capabilities;
mod capture;
mod collections;
mod compact;
mod content;
mod dense;
mod diff;
mod disclosure;
mod facts;
mod ffi;
mod geometric;
mod geometry;
mod json_cell;
mod legacy;
mod line_layout;
mod numbering;
mod projection;
mod publication;

mod render;
mod semantic;
mod sharing;
mod unicode_tables {
    include!(concat!(env!("OUT_DIR"), "/unicode_tables.rs"));
}
mod value;
mod wire;
mod wire_value;

use pyo3::prelude::*;

#[pymodule(gil_used = true)]
fn _ax_read(module: &Bound<'_, PyModule>) -> PyResult<()> {
    ffi::register(module)
}
