// Copyright (c) Huawei Technologies Co., Ltd. 2026. All rights reserved.

//! Opaque terminal renders and detached comparison baselines.

use std::{collections::{HashMap, HashSet}, sync::Arc};

use pyo3::{
    exceptions::PyValueError,
    prelude::*,
    types::{PyDict, PyTuple},
};

use super::{compact_error, value_from_py, value_to_py};
use crate::{
    compact::CompactSnapshot,
    diff::{
        self, Bindings, ComparisonNode, ComparisonTree, ContextNode, Coverage, CurrentTree, DeltaPlan, DisplayOptions,
        DisplayRoot, Mode,
    },
    legacy::LegacySnapshot,
    publication::view::title_evidence,
    render::{self, text::{json, JsonFormat}, RenderedPart, RenderedSnapshot},
    value::Value,
    wire,
};

#[pyclass(frozen, module = "computer_use.interaction.read.accessibility._ax_read")]
pub(super) struct NativeCurrent(CurrentTree);

#[pyclass(frozen, module = "computer_use.interaction.read.accessibility._ax_read")]
struct NativeBaseline(Arc<ComparisonTree>);

#[pyclass(frozen, module = "computer_use.interaction.read.accessibility._ax_read")]
struct NativeDelta(DeltaPlan);

#[pyclass(frozen, module = "computer_use.interaction.read.accessibility._ax_read")]
pub(super) struct NativeRendered {
    body: RenderedSnapshot,
    title: Option<(String, String, String)>,
}

pub(super) fn comparison(
    compact: &CompactSnapshot, scope: Vec<Option<String>>, indent: i64, details: bool,
) -> PyResult<NativeCurrent> {
    let options = DisplayOptions { compact: true, public_api: true, details };
    comparison_result(compact.comparison(scope, options, indent))
}

fn comparison_result(result: Result<CurrentTree, diff::InvalidPreparedComparison>) -> PyResult<NativeCurrent> {
    result.map(NativeCurrent).map_err(|error| PyValueError::new_err(format!("Native read comparison: {error:?}")))
}

pub(super) fn legacy_comparison(
    snapshot: &LegacySnapshot, scope: Vec<Option<String>>, indent: i64, options: DisplayOptions,
) -> PyResult<NativeCurrent> {
    comparison_result(snapshot.comparison(scope, options, indent))
}

pub(super) fn legacy_render(snapshot: &LegacySnapshot, details: bool) -> PyResult<NativeRendered> {
    let body = snapshot.render(details).map_err(legacy_error)?;
    Ok(NativeRendered { body, title: None })
}

pub(super) fn legacy_inline_child_refs(snapshot: &LegacySnapshot) -> PyResult<Vec<&str>> {
    snapshot.inline_child_refs().map_err(legacy_error)
}

fn legacy_error(error: render::TextError) -> PyErr {
    PyValueError::new_err(format!("Native read presentation: {error:?}"))
}

pub(super) fn render(compact: &CompactSnapshot, details: bool) -> PyResult<NativeRendered> {
    let mut presentation = compact.render().map_err(compact_error)?;
    if details {
        compact.render_details(&mut presentation).map_err(compact_error)?;
    }
    let title = match compact.roots.as_slice() {
        [branch] if !presentation.body.parts.iter().any(|part|
            matches!(part, RenderedPart::Section(section) if section.refresh)
        ) => branch.projected().and_then(|root| title_evidence(compact.prepared_capture(), root, &presentation.body)),
        _ => None,
    }.map(|(reference, token, name)| (reference.to_owned(), token.to_owned(), name.to_owned()));
    Ok(NativeRendered { body: presentation.body, title })
}

pub(super) fn inline_child_refs(compact: &CompactSnapshot) -> PyResult<Vec<&str>> {
    let mut refs = Vec::new();
    for root in compact.projected_roots() {
        let visible = root.projection.visible_order()
            .map_err(|error| PyValueError::new_err(format!("Native read continuation coverage: {error:?}")))?;
        for id in visible {
            let node = root.projection.node(id)
                .ok_or_else(|| PyValueError::new_err("Native read projection omitted a visible node"))?;
            let Some(role) = node.field("role") else { continue };
            let count = node.field("childCount").and_then(Value::as_i64).unwrap_or_default();
            let read = node.field("childrenRead").and_then(Value::as_i64).unwrap_or(node.native_children.len() as i64);
            let start = node.field("childrenStart");
            let empty_menu = role.as_str() == Some("menuBarItem") && node.children.is_empty()
                && start.and_then(Value::as_i64) == Some(0)
                && node.field("childrenEnd").and_then(Value::as_i64) == Some(0) && count > 0;
            let partial = node.field("partialReason").is_some_and(|value| !matches!(value, Value::Null)) || read < count;
            if empty_menu || (partial && start.is_some() && node.field("nextChildOffset").is_some()) {
                refs.push(node.reference.as_str());
            }
        }
    }
    refs.sort_unstable();
    Ok(refs)
}

#[pymethods]
impl NativeCurrent {
    fn baseline(&self) -> NativeBaseline {
        NativeBaseline(Arc::clone(&self.0.comparison))
    }
}

fn text(value: &str) -> Value { Value::Text(value.to_owned()) }
fn optional(value: &Option<String>) -> Value { value.as_deref().map_or(Value::Null, text) }
fn map<const N: usize>(fields: [(&str, Value); N]) -> Value {
    Value::Map(fields.into_iter().map(|(key, value)| (key.to_owned(), value)).collect())
}

fn tree_data(tree: &ComparisonTree) -> Value {
    let nodes = tree.nodes.iter().map(|node| map([
        ("ref", text(&node.reference)),
        ("parent", optional(&node.parent)),
        ("depth", Value::Int(node.depth)),
        ("signature", text(&node.signature)),
        ("content", text(&node.content)),
    ])).collect();
    let mut contexts: Vec<_> = tree.root_contexts.iter().collect();
    contexts.sort_unstable_by_key(|(key, _)| *key);
    let contexts = contexts.into_iter().map(|(key, rows)| Value::Seq(vec![
        text(key), Value::Seq(rows.iter().map(|(owner, kind)| Value::Seq(vec![text(owner), text(kind)])).collect()),
    ])).collect();
    let mut versions: Vec<_> = tree.versions.iter().collect();
    versions.sort_unstable_by_key(|(key, _)| *key);
    let versions = versions.into_iter().map(|(key, version)| Value::Seq(vec![text(key), optional(version)])).collect();
    map([
        ("scope", Value::Seq(tree.scope.iter().map(optional).collect())),
        ("nodes", Value::Seq(nodes)),
        ("details", Value::Bool(tree.options.details)),
        ("compact", Value::Bool(tree.options.compact)),
        ("public_api", Value::Bool(tree.options.public_api)),
        ("root_contexts", Value::Seq(contexts)),
        ("versions", Value::Seq(versions)),
        ("failed", Value::Bool(tree.failed)),
    ])
}

fn string_size(text: &str) -> usize {
    2 + text.bytes().map(|byte| match byte {
        b'\\' | b'"' | b'\x08' | b'\x0c' | b'\n' | b'\r' | b'\t' => 2,
        0..=31 => 6,
        _ => 1,
    }).sum::<usize>()
}

fn optional_size(text: &Option<String>) -> usize { text.as_deref().map_or(4, string_size) }

fn sequence_size(count: usize, items: impl Iterator<Item = usize>) -> usize {
    2 + count.saturating_sub(1) + items.sum::<usize>()
}

fn tree_size(tree: &ComparisonTree) -> usize {
    let scope = sequence_size(tree.scope.len(), tree.scope.iter().map(optional_size));
    let nodes = sequence_size(tree.nodes.len(), tree.nodes.iter().map(|node| {
        r#"{"ref":,"parent":,"depth":,"signature":,"content":}"#.len()
            + string_size(&node.reference) + optional_size(&node.parent) + node.depth.to_string().len()
            + string_size(&node.signature) + string_size(&node.content)
    }));
    let contexts = sequence_size(tree.root_contexts.len(), tree.root_contexts.iter().map(|(key, rows)| {
        3 + string_size(key) + sequence_size(rows.len(), rows.iter().map(|(owner, kind)| {
            3 + string_size(owner) + string_size(kind)
        }))
    }));
    let versions = sequence_size(tree.versions.len(), tree.versions.iter().map(|(key, version)| {
        3 + string_size(key) + optional_size(version)
    }));
    r#"{"scope":,"nodes":,"details":,"compact":,"public_api":,"root_contexts":,"versions":,"failed":}"#.len()
        + scope + nodes + contexts + versions
        + [tree.options.details, tree.options.compact, tree.options.public_api, tree.failed].iter()
            .map(|flag| if *flag { 4 } else { 5 }).sum::<usize>()
}

#[pymethods]
impl NativeBaseline {
    fn to_data(&self, py: Python<'_>) -> PyResult<Py<PyAny>> {
        value_to_py(py, &tree_data(&self.0))
    }

    fn estimated_bytes(&self) -> usize {
        tree_size(&self.0)
    }

    fn to_spill(&self, py: Python<'_>) -> PyResult<String> {
        py.detach(|| json(&tree_data(&self.0), JsonFormat::Plain))
            .map_err(|error| PyValueError::new_err(format!("Native read baseline serialization: {error:?}")))
    }

    fn scope(&self) -> &[Option<String>] { &self.0.scope }
}

fn required<'py>(record: &Bound<'py, PyDict>, key: &'static str) -> PyResult<Bound<'py, PyAny>> {
    record.get_item(key)?.ok_or_else(|| PyValueError::new_err(format!("Missing native read comparison field {key}")))
}

type BaselineResult<T> = Result<T, &'static str>;

fn field<'a>(value: &'a Value, name: &'static str) -> BaselineResult<&'a Value> {
    value.get(name).ok_or(name)
}

fn sequence(value: &Value) -> BaselineResult<&[Value]> {
    value.as_seq().ok_or("expected a sequence")
}

fn string(value: &Value) -> BaselineResult<String> {
    value.as_str().map(str::to_owned).ok_or("expected text")
}

fn optional_string(value: &Value) -> BaselineResult<Option<String>> {
    match value {
        Value::Null => Ok(None),
        value => string(value).map(Some),
    }
}

fn pair(value: &Value) -> BaselineResult<(&Value, &Value)> {
    match sequence(value)? {
        [first, second] => Ok((first, second)),
        _ => Err("expected a pair"),
    }
}

fn tree_from_value(record: &Value) -> BaselineResult<ComparisonTree> {
    let nodes = sequence(field(record, "nodes")?)?.iter().map(|row| {
        Ok(ComparisonNode {
            reference: string(field(row, "ref")?)?,
            parent: optional_string(field(row, "parent")?)?,
            depth: field(row, "depth")?.as_i64().ok_or("depth")?,
            signature: string(field(row, "signature")?)?,
            content: string(field(row, "content")?)?,
        })
    }).collect::<BaselineResult<_>>()?;
    let root_contexts = sequence(field(record, "root_contexts")?)?.iter().map(|row| {
        let (reference, contexts) = pair(row)?;
        let contexts = sequence(contexts)?.iter().map(|entry| {
            let (owner, kind) = pair(entry)?;
            Ok((string(owner)?, string(kind)?))
        }).collect::<BaselineResult<_>>()?;
        Ok((string(reference)?, contexts))
    }).collect::<BaselineResult<_>>()?;
    let versions = sequence(field(record, "versions")?)?.iter().map(|row| {
        let (reference, version) = pair(row)?;
        Ok((string(reference)?, optional_string(version)?))
    }).collect::<BaselineResult<_>>()?;
    let flag = |name| field(record, name)?.as_bool().ok_or(name);
    Ok(ComparisonTree {
        scope: sequence(field(record, "scope")?)?.iter().map(optional_string).collect::<BaselineResult<_>>()?,
        nodes,
        options: DisplayOptions {
            compact: flag("compact")?,
            public_api: flag("public_api")?,
            details: flag("details")?,
        },
        root_contexts,
        versions,
        failed: flag("failed")?,
    })
}

fn baseline_error(error: &'static str) -> PyErr {
    PyValueError::new_err(format!("Invalid native read baseline: {error}"))
}

#[pyfunction]
fn restore_read_baseline(py: Python<'_>, serialized: &str) -> PyResult<NativeBaseline> {
    py.detach(|| {
        let record = wire::decode_json(serialized.as_bytes())
            .map_err(|error| PyValueError::new_err(format!("Native read baseline decoding: {error:?}")))?;
        tree_from_value(&record).map(|tree| NativeBaseline(Arc::new(tree))).map_err(baseline_error)
    })
}

fn read_tree(record: &Bound<'_, PyDict>) -> PyResult<ComparisonTree> {
    tree_from_value(&value_from_py(record.as_any())?).map_err(baseline_error)
}

fn read_current(record: &Bound<'_, PyDict>) -> PyResult<CurrentTree> {
    let comparison = read_tree(required(record, "comparison")?.cast::<PyDict>()?)?;
    let context = required(record, "context")?.try_iter()?.map(|row| {
        let (partial, anonymous_group_or_cell, static_text_or_heading, label_targets, context_refs, related) =
            row?.extract()?;
        Ok(ContextNode { partial, anonymous_group_or_cell, static_text_or_heading, label_targets, context_refs, related })
    }).collect::<PyResult<_>>()?;
    let roots = required(record, "roots")?.try_iter()?.map(|row| {
        let (reference, members) = row?.extract()?;
        Ok(DisplayRoot { reference, members })
    }).collect::<PyResult<_>>()?;
    Ok(CurrentTree { comparison: comparison.into(), context, roots })
}

enum ComparisonInput<'py> {
    Native(PyRef<'py, NativeBaseline>),
    Serialized(ComparisonTree),
}

impl ComparisonInput<'_> {
    fn tree(&self) -> &ComparisonTree {
        match self {
            Self::Native(handle) => &handle.0,
            Self::Serialized(tree) => tree,
        }
    }
}

enum CurrentInput<'py> {
    Native(PyRef<'py, NativeCurrent>),
    Host(CurrentTree),
}

impl CurrentInput<'_> {
    fn tree(&self) -> &CurrentTree {
        match self {
            Self::Native(handle) => &handle.0,
            Self::Host(tree) => tree,
        }
    }
}

type CoverageInput = (HashMap<String, Option<String>>, Vec<String>);

fn read_coverage((parents, complete): CoverageInput) -> Coverage {
    Coverage { parents, complete: complete.into_iter().collect() }
}

#[pyfunction]
#[pyo3(signature = (before, after, previous_refs, current_refs, not_observed, *, coverage=None))]
fn plan_read_document_diff(
    py: Python<'_>, before: &Bound<'_, PyTuple>, after: &Bound<'_, PyTuple>,
    previous_refs: Bindings, current_refs: Bindings,
    not_observed: Vec<String>,
    coverage: Option<(Option<CoverageInput>, Option<CoverageInput>)>,
) -> PyResult<NativeDelta> {
    let before = before.iter().map(|item| {
        if let Ok(handle) = item.extract::<PyRef<'_, NativeBaseline>>() {
            Ok(ComparisonInput::Native(handle))
        } else {
            read_tree(item.cast::<PyDict>()?).map(ComparisonInput::Serialized)
        }
    }).collect::<PyResult<Vec<_>>>()?;
    let after = after.iter().map(|item| {
        if let Ok(handle) = item.extract::<PyRef<'_, NativeCurrent>>() {
            Ok(CurrentInput::Native(handle))
        } else {
            read_current(item.cast::<PyDict>()?).map(CurrentInput::Host)
        }
    }).collect::<PyResult<Vec<_>>>()?;
    let before = before.iter().map(ComparisonInput::tree).collect::<Vec<_>>();
    let after = after.iter().map(CurrentInput::tree).collect::<Vec<_>>();
    py.detach(|| {
        let mut not_observed = not_observed.into_iter().collect::<HashSet<_>>();
        if let Some((previous, current)) = coverage {
            let previous = previous.map(read_coverage);
            let current = current.map(read_coverage);
            not_observed.extend(diff::unconfirmed_missing(
                &previous_refs, &current_refs, previous.as_ref(), current.as_ref(),
            ));
        }
        diff::plan_document_diff_trees(&before, &after, &previous_refs, &current_refs, &not_observed)
    }).map(NativeDelta).map_err(|error| PyValueError::new_err(format!("Native read diff context: {error:?}")))
}

#[pyfunction]
#[pyo3(signature = (previous, current, ignored, previous_coverage, current_coverage))]
fn has_unconfirmed_read_missing(
    py: Python<'_>, previous: Vec<String>, current: Vec<String>, ignored: Vec<String>,
    previous_coverage: Option<CoverageInput>, current_coverage: Option<CoverageInput>,
) -> bool {
    py.detach(|| {
        let current = current.into_iter().collect::<HashSet<_>>();
        let ignored = ignored.into_iter().collect::<HashSet<_>>();
        let previous_coverage = previous_coverage.map(read_coverage);
        let current_coverage = current_coverage.map(read_coverage);
        previous.iter().any(|reference| {
            !current.contains(reference) && !ignored.contains(reference)
                && !diff::confirms_removal(previous_coverage.as_ref(), current_coverage.as_ref(), reference)
        })
    })
}

type TreeData<'a> = (Vec<(&'a str, char)>, &'a [String], Vec<&'a str>, Vec<&'a str>);

#[pymethods]
impl NativeDelta {
    #[getter]
    fn mode(&self) -> &'static str {
        match self.0.mode {
            Mode::Full => "full",
            Mode::Diff => "diff",
            Mode::Unchanged => "unchanged",
        }
    }

    #[getter]
    fn reason(&self) -> &'static str { self.0.reason.as_str() }

    fn not_observed(&self) -> Vec<&str> {
        self.0.not_observed.iter().map(String::as_str).collect()
    }

    fn retained(&self) -> Vec<&str> {
        self.0.retained.iter().map(String::as_str).collect()
    }

    fn summaries(&self) -> Vec<Option<(usize, usize, usize)>> {
        self.0.trees.iter().map(|tree| tree.as_ref().map(|tree| {
            (tree.changes().len(), tree.missing.len(), tree.context.len())
        })).collect()
    }

    fn tree(&self, index: usize) -> Option<TreeData<'_>> {
        let tree = self.0.trees.get(index)?.as_ref()?;
        Some((
            tree.changes().iter().map(|(reference, change)| (reference.as_str(), change.marker())).collect(),
            &tree.missing,
            tree.context.iter().map(String::as_str).collect(),
            tree.expanded.iter().map(String::as_str).collect(),
        ))
    }
}

#[pymethods]
impl NativeRendered {
    fn text(&self) -> String { self.body.text() }

    fn details(&self) -> &str { &self.body.details }

    fn detail_supplement(&self) -> Option<&str> { self.body.detail_supplement.as_deref() }

    fn content_directory(&self) -> Option<(&str, &str)> {
        self.body.content_directory.as_ref().map(|(directory, payload)| (directory.as_str(), payload.as_str()))
    }

    fn title_evidence(&self) -> Option<(&str, &str, &str)> {
        self.title.as_ref().map(|(reference, token, name)| (reference.as_str(), token.as_str(), name.as_str()))
    }

    #[pyo3(signature = (indent, details, plan, tree_index))]
    fn part(
        &self, py: Python<'_>, indent: usize, details: bool, plan: Option<&NativeDelta>, tree_index: usize,
    ) -> (String, bool) {
        py.detach(|| render::tree_part(&self.body, indent, details, plan.map(|plan| &plan.0), tree_index))
    }
}

pub(super) fn register(module: &Bound<'_, PyModule>) -> PyResult<()> {
    module.add_class::<NativeCurrent>()?;
    module.add_class::<NativeBaseline>()?;
    module.add_class::<NativeDelta>()?;
    module.add_class::<NativeRendered>()?;
    module.add_function(wrap_pyfunction!(plan_read_document_diff, module)?)?;
    module.add_function(wrap_pyfunction!(restore_read_baseline, module)?)?;
    module.add_function(wrap_pyfunction!(has_unconfirmed_read_missing, module)?)
}


#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn detached_baseline_keeps_preparation_failure_and_its_exact_encoded_size() {
        for failed in [false, true] {
            let tree = ComparisonTree {
                scope: vec![Some("标题\u{2028}\n".to_owned()), None],
                nodes: vec![ComparisonNode {
                    reference: "0".to_owned(), parent: None, depth: 2,
                    signature: "signature".to_owned(), content: "content".to_owned(),
                }],
                root_contexts: HashMap::from([("0".to_owned(), vec![("1".to_owned(), "related".to_owned())])]),
                versions: HashMap::from([("0".to_owned(), Some("4".to_owned()))]),
                failed,
                ..Default::default()
            };
            let data = tree_data(&tree);
            assert_eq!(data.get("failed"), Some(&Value::Bool(failed)));
            let serialized = json(&data, JsonFormat::Plain).unwrap();
            assert_eq!(tree_size(&tree), serialized.len());
            assert_eq!(tree_from_value(&wire::decode_json(serialized.as_bytes()).unwrap()), Ok(tree));
        }
    }
}
