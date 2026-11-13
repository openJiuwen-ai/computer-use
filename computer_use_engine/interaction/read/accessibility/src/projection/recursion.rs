// Copyright (c) Huawei Technologies Co., Ltd. 2026. All rights reserved.

//! Carry the old field projection's interpreter budget across the Rust bridge.

use pyo3::{
    exceptions::PyRecursionError,
    ffi, prelude::*,
    types::{PyDict, PyList},
};

use crate::{
    ffi::value_to_py,
    projection::{semantics::SemanticTree, ProjectionError},
    wire_value::VALUE_DEPTH_LIMIT,
};

const VALUES: &str = "computer_use.interaction.read.accessibility._projection_values";

#[derive(Clone, Copy, Debug)]
pub(crate) enum ProjectionStage {
    Numbering,
    Publication,
}

#[derive(Clone, Copy, Debug)]
pub(crate) struct ProjectionBudget {
    remaining: usize,
    freeze_frames: usize,
    export_frames: usize,
    probe: fn(&SemanticTree, usize, usize) -> Result<Option<String>, ProjectionError>,
}

impl ProjectionBudget {
    pub(crate) fn capture(py: Python<'_>, stage: ProjectionStage) -> PyResult<Self> {
        // Match the legacy stack at ProjectionInput.build, including its
        // presentation and semantic pipeline calls. The preview adapter and
        // publication adapter replace different portions of that stack.
        let (freeze, export) = match stage {
            ProjectionStage::Numbering => (9, 13),
            ProjectionStage::Publication => (6, 13),
        };
        // CPython 3.11 charges the native entry to the same recursion limit.
        // On 3.12 and later that entry uses the separate native stack guard.
        let native_entry = usize::from(py.version_info().minor == 11);
        let limit = unsafe { ffi::Py_GetRecursionLimit() } as usize;
        Ok(Self {
            remaining: limit.saturating_sub(python_frame_depth(py)?),
            freeze_frames: freeze - native_entry,
            export_frames: export - native_entry,
            probe: probe_fields,
        })
    }

    pub(super) fn check(self, source: &SemanticTree) -> Result<Option<String>, ProjectionError> {
        // The retired recursion traversed a node and its child sequence with
        // at most four Python frames per level. Leave room for native field
        // containers and interpreter calls. Shallow trees finish this check
        // inside Rust.
        if source.depth.saturating_mul(4) + 2 * VALUE_DEPTH_LIMIT + self.export_frames + 16 < self.remaining {
            return Ok(None);
        }
        (self.probe)(source, self.freeze_frames, self.export_frames)
    }
}

fn probe_fields(
    source: &SemanticTree, freeze_frames: usize, export_frames: usize,
) -> Result<Option<String>, ProjectionError> {
    Python::attach(|py| {
        let error = |error| field_error(py, error);
        let values = py.import(VALUES).map_err(error)?;
        let at_depth = values.getattr("at_depth").map_err(error)?;
        let freeze = values.getattr("freeze").map_err(error)?;
        let thaw = values.getattr("thaw").map_err(error)?;
        let shaped = field_tree(py, source).map_err(error)?;
        let frozen = at_depth.call1((freeze, shaped, freeze_frames)).map_err(error)?;
        match at_depth.call1((thaw, frozen, export_frames)) {
            Ok(_) => Ok(None),
            Err(error) => match field_error(py, error) {
                ProjectionError::Recursion(detail) => Ok(Some(detail)),
                other => Err(other),
            },
        }
    })
}

fn python_frame_depth(py: Python<'_>) -> PyResult<usize> {
    // Ported from the full migration's frame accounting. Both CPython frame
    // calls return a strong reference or null while Python is attached.
    let mut depth = 0;
    let mut frame = unsafe { ffi::PyThreadState_GetFrame(ffi::PyThreadState_Get()) };
    while !frame.is_null() {
        depth += 1;
        let parent = unsafe { ffi::PyFrame_GetBack(frame) };
        unsafe { ffi::Py_DECREF(frame.cast()) };
        frame = parent;
    }
    if let Some(error) = PyErr::take(py) {
        return Err(error);
    }
    Ok(depth)
}

fn field_tree(py: Python<'_>, source: &SemanticTree) -> PyResult<Py<PyDict>> {
    // The owned tree is already in preorder. Assemble the interpreter's
    // transient field shape bottom-up, retaining the original key order.
    let mut rows = Vec::<Py<PyDict>>::with_capacity(source.nodes.len());
    for node in source.nodes.iter().rev() {
        let row = PyDict::new(py);
        for (key, value) in &node.fields[..node.children_index] {
            row.set_item(key, value_to_py(py, value)?)?;
        }
        let children = node.children.iter().map(|&child| rows[source.nodes.len() - child - 1].bind(py));
        row.set_item("children", PyList::new(py, children)?)?;
        for (key, value) in &node.fields[node.children_index..] {
            row.set_item(key, value_to_py(py, value)?)?;
        }
        rows.push(row.unbind());
    }
    Ok(rows.pop().expect("a semantic branch has a root"))
}

fn field_error(py: Python<'_>, error: PyErr) -> ProjectionError {
    if error.is_instance_of::<PyRecursionError>(py) {
        return ProjectionError::Recursion(
            error.value(py).str().map_or_else(|_| "maximum recursion depth exceeded".to_owned(), |detail| detail.to_string_lossy().into_owned()),
        );
    }
    ProjectionError::Invalid("could not inspect the original projection recursion budget")
}
