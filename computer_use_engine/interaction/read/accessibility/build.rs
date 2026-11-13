// Copyright (c) Huawei Technologies Co., Ltd. 2026. All rights reserved.

use std::{env, path::PathBuf, process::Command};

fn main() {
    println!("cargo:rerun-if-changed=build_unicode.py");
    println!("cargo:rerun-if-env-changed=PYO3_PYTHON");
    let default_python = if cfg!(windows) { "python" } else { "python3" };
    let python = env::var_os("PYO3_PYTHON").unwrap_or_else(|| default_python.into());
    let output = PathBuf::from(env::var_os("OUT_DIR").expect("Cargo build output directory"));
    let status = Command::new(python)
        .arg("build_unicode.py")
        .arg(output.join("unicode_tables.rs"))
        .status()
        .expect("start the Python Unicode table generator");
    assert!(
        status.success(),
        "Unicode table generation failed: {status}"
    );
}
