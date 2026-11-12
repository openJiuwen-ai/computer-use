// Copyright (c) Huawei Technologies Co., Ltd. 2026. All rights reserved.

//! Opaque read values crossing into the SDK host. A frame transfers ownership
//! to a capture after scope admission. Numbered views share the original capture.

use std::{collections::{HashMap, HashSet}, sync::Arc};

use pyo3::{
    exceptions::{PyAttributeError, PyKeyError, PyMemoryError, PyOverflowError, PyTypeError, PyValueError},
    prelude::*,
    types::{PyBool, PyBytes, PyDict, PyFloat, PyInt, PyList, PyString, PyTuple},
    IntoPyObjectExt,
};

use super::{
    capture::{CaptureError, ReadCapture},
    compact::{CompactBranch, CompactError, CompactSnapshot},
    diff::DisplayOptions,
    disclosure::groups::WindowContext,
    facts::{query::{self, Query}, FactsError, FactsFailureKind},
    legacy::LegacySnapshot,
    numbering::{PartAliases, SessionAliases},
    projection::ProjectionOptions,
    publication::{self, loading, lookup, menu, progress, view, Binding, CapturedText, Continuation, ExpectedPage, PublicationError},
    render::details::DetailSource,
    value::{SemanticTree, Value},
    wire,
};

mod delivery;
#[path = "wire/diagnostic.rs"]
mod diagnostic;

pub(super) type Target = (String, Option<String>, Option<String>, Option<String>, Option<String>);
type PreviousPage = (String, i64, i64);
type Page = (String, i64, Option<PreviousPage>);
type Cursor = (String, String, Option<String>, String, String, i64, i64);
type Window = (u64, bool, Option<bool>, Vec<u64>, bool, bool);

fn target(binding: &Target) -> Binding<'_> {
    Binding {
        session: &binding.0,
        instance: binding.1.as_deref(),
        root_window: binding.2.as_deref(),
        surface: binding.3.as_deref(),
        node: binding.4.as_deref(),
    }
}

fn publication_error(error: PublicationError) -> PyErr {
    let message = match error {
        PublicationError::MissingRelationshipCoverage => "Native collection omitted requested relationship coverage",
        PublicationError::ChangedScopeOrBudget => "Native fact batch changed its requested scope or budget",
        PublicationError::MissingPage => "Native page omitted its requested relation",
        PublicationError::ChangedPageRangeOrBudget => "Native page changed its continuation range or budget",
        PublicationError::Invalid(message) => message,
    };
    PyValueError::new_err(message)
}

struct BoundaryError(PyErr);

impl From<CaptureError> for BoundaryError {
    fn from(error: CaptureError) -> Self {
        Self(match error {
            CaptureError::Facts(error) => facts_error(error),
            CaptureError::Wire(error) => facts_error(error.into()),
            error => PyValueError::new_err(format!("Native read capture: {error:?}")),
        })
    }
}

fn facts_error(error: FactsError) -> PyErr {
    let failure = error.failure();
    match failure.kind {
        FactsFailureKind::Value => PyValueError::new_err(failure.message),
        FactsFailureKind::Overflow => PyOverflowError::new_err(failure.message),
        FactsFailureKind::Memory => PyMemoryError::new_err(failure.message),
        FactsFailureKind::Attribute => PyAttributeError::new_err(failure.message),
        FactsFailureKind::Type => PyTypeError::new_err(failure.message),
        FactsFailureKind::Key(key) => Python::attach(|py| match value_to_py(py, &key) {
            Ok(key) => PyKeyError::new_err((key,)),
            Err(error) => error,
        }),
    }
}

fn capture_error(error: CaptureError) -> PyErr {
    BoundaryError::from(error).0
}

fn compact_error(error: CompactError) -> PyErr {
    PyValueError::new_err(format!("Native read presentation: {error:?}"))
}

pub(super) fn value_to_py(py: Python<'_>, value: &Value) -> PyResult<Py<PyAny>> {
    match value {
        Value::Null => Ok(py.None()),
        Value::Bool(value) => value.into_py_any(py),
        Value::Int(value) => value.into_py_any(py),
        Value::BigInt(value) => Ok(py.get_type::<PyInt>().call1((value,))?.unbind().into_any()),
        Value::Float(value) => value.into_py_any(py),
        Value::Text(value) => value.into_py_any(py),
        Value::Bytes(value) => Ok(PyBytes::new(py, value).unbind().into_any()),
        Value::Seq(values) => {
            let values = values.iter().map(|value| value_to_py(py, value)).collect::<PyResult<Vec<_>>>()?;
            Ok(PyList::new(py, values)?.unbind().into_any())
        }
        Value::Map(fields) => {
            let result = PyDict::new(py);
            for (key, value) in fields {
                result.set_item(key, value_to_py(py, value)?)?;
            }
            Ok(result.unbind().into_any())
        }
    }
}

pub(super) fn value_from_py(value: &Bound<'_, PyAny>) -> PyResult<Value> {
    if value.is_none() {
        return Ok(Value::Null);
    }
    if value.is_exact_instance_of::<PyBool>() {
        return value.extract().map(Value::Bool);
    }
    if value.is_exact_instance_of::<PyInt>() {
        return value.extract().map(Value::Int).or_else(|_| value.str()?.extract().map(Value::BigInt));
    }
    if value.is_exact_instance_of::<PyFloat>() {
        return value.extract().map(Value::Float);
    }
    if let Ok(text) = value.cast::<PyString>() {
        return text.extract().map(Value::Text);
    }
    if let Ok(bytes) = value.cast::<PyBytes>() {
        return Ok(Value::Bytes(bytes.as_bytes().to_vec()));
    }
    if let Ok(items) = value.cast::<PyList>() {
        return items.iter().map(|item| value_from_py(&item)).collect::<PyResult<Vec<_>>>().map(Value::Seq);
    }
    if let Ok(items) = value.cast::<PyTuple>() {
        return items.iter().map(|item| value_from_py(&item)).collect::<PyResult<Vec<_>>>().map(Value::Seq);
    }
    if let Ok(fields) = value.cast::<PyDict>() {
        return fields.iter().map(|(key, value)| Ok((key.extract()?, value_from_py(&value)?)))
            .collect::<PyResult<Vec<_>>>().map(Value::Map);
    }
    Err(PyTypeError::new_err("Unsupported host value in native read evidence"))
}

fn records<'a>(row: &'a Value, field: &str) -> &'a [Value] {
    row.get(field).and_then(Value::as_seq).unwrap_or_default()
}

fn text<'a>(row: &'a Value, field: &str) -> Option<&'a str> {
    row.get(field).and_then(Value::as_str)
}

fn checkpoint(callback: Option<&Py<PyAny>>) -> Result<(), BoundaryError> {
    if let Some(callback) = callback {
        Python::attach(|py| callback.call0(py)).map_err(BoundaryError)?;
    }
    Ok(())
}

#[derive(FromPyObject)]
enum AliasKey {
    Native(String),
    Scoped((Option<String>, Option<String>, String)),
}

#[pyclass(module = "computer_use.interaction.read.accessibility._ax_read")]
struct NativeReadReferences(SessionAliases);

#[pymethods]
impl NativeReadReferences {
    #[new]
    fn new(session: String) -> Self {
        Self(SessionAliases::new(session))
    }

    #[getter]
    fn session(&self) -> &str {
        self.0.session()
    }

    fn reference(&mut self, key: AliasKey) -> String {
        match key {
            AliasKey::Native(token) => self.0.native(token),
            AliasKey::Scoped((surface, kind, token)) => self.0.scoped(surface, kind, token),
        }.to_string()
    }

    fn reference_batch(&mut self, keys: Vec<AliasKey>) -> Vec<String> {
        keys.into_iter().map(|key| self.reference(key)).collect()
    }

    #[pyo3(signature = (surface, kind, tokens))]
    fn scoped_batch(&mut self, surface: Option<String>, kind: Option<String>, tokens: Vec<String>) -> Vec<String> {
        tokens.into_iter().map(|token| self.0.scoped(surface.clone(), kind.clone(), token).to_string()).collect()
    }
}

#[pyclass(module = "computer_use.interaction.read.accessibility._ax_read")]
pub(super) struct NativeReadFrame {
    query: Option<Query>,
    semantic: Option<Query>,
    diagnostic: Vec<u8>,
    jsonl_result: Option<Value>,
}

#[pymethods]
impl NativeReadFrame {
    fn initialize(&mut self, py: Python<'_>) -> PyResult<()> {
        if self.query.is_none() {
            let value = self.jsonl_result.as_ref()
                .ok_or_else(|| PyValueError::new_err("Native read frame has already been captured"))?;
            let (query, semantic) = py.detach(|| collect_read_queries(value)).map_err(facts_error)?;
            self.query = Some(query);
            self.semantic = semantic;
        }
        Ok(())
    }

    #[getter]
    fn has_semantic_urls(&self) -> bool {
        self.query.as_ref().is_some_and(|query| query.has_semantic_urls)
    }

    fn diagnostic_bytes<'py>(&self, py: Python<'py>) -> Bound<'py, PyBytes> {
        PyBytes::new(py, &self.diagnostic)
    }

    fn jsonl_diagnostic_bytes<'py>(&mut self, py: Python<'py>) -> PyResult<Bound<'py, PyBytes>> {
        if self.query.is_none() && self.jsonl_result.is_some() {
            self.initialize(py)?;
        }
        let result = self.jsonl_result.take();
        let raw = &self.diagnostic;
        let encoded = py.detach(|| {
            let result = match result {
                Some(result) => result,
                // The normal encoded callback consumes the admitted graph once.
                // An additional request can still use the original wire body.
                None => wire::decode_json(raw).map_err(|error| match error {
                    wire::WireError::Allocation => diagnostic::Error::Allocation,
                    _ => diagnostic::Error::UnsupportedValue,
                })?,
            };
            diagnostic::compact(&result, raw.len())
        })
            .map_err(|error| match error {
                diagnostic::Error::Allocation => PyMemoryError::new_err("cannot allocate JSONL observation diagnostic"),
                diagnostic::Error::Nonfinite => PyValueError::new_err("Out of range float values are not JSON compliant"),
                diagnostic::Error::UnsupportedValue => PyTypeError::new_err("Unsupported JSONL observation diagnostic value"),
            })?;
        PyBytes::new_with(py, encoded.len(), |bytes| {
            bytes.copy_from_slice(encoded.as_bytes());
            Ok(())
        })
    }

    fn raw_result(&self, py: Python<'_>) -> PyResult<Py<PyAny>> {
        // An ordinary host diagnostic callback explicitly expects the complete wire
        // record. The default encoded callback consumes the original bytes.
        let json = py.import("json")?;
        Ok(json.call_method1("loads", (PyBytes::new(py, &self.diagnostic),))?.unbind())
    }

    #[pyo3(signature = (session, instance, root_window, surface, node, platform, max_nodes, follow_relations, page))]
    #[allow(clippy::too_many_arguments)]
    fn admit_nodes(
        &mut self, py: Python<'_>, session: String, instance: Option<String>, root_window: Option<String>,
        surface: Option<String>, node: Option<String>, platform: &str, max_nodes: usize,
        follow_relations: Vec<String>, page: Option<Page>,
    ) -> PyResult<Vec<Cursor>> {
        self.initialize(py)?;
        let query = self.query.as_ref().expect("initialized read query");
        let binding = (session, instance, root_window, surface, node);
        let follow: Vec<_> = follow_relations.iter().map(String::as_str).collect();
        let page = page.as_ref().map(|(property, limit, previous)| ExpectedPage {
            property,
            limit: *limit,
            previous: previous.as_ref().map(|(cursor, offset, total)| Continuation {
                cursor,
                offset: *offset,
                total: *total,
            }),
        });
        let cursors = publication::admit_nodes(&query.facts, target(&binding), platform, max_nodes, &follow, page)
            .map_err(publication_error)?;
        Ok(cursors.into_iter().map(|cursor| (
            cursor.continuation.cursor.to_owned(), cursor.session.to_owned(), cursor.surface.map(str::to_owned),
            cursor.node.to_owned(), cursor.property.to_owned(), cursor.continuation.offset, cursor.continuation.total,
        )).collect())
    }

    #[pyo3(signature = (scope_kind, features, semantic_body=None))]
    fn capture(
        &mut self, py: Python<'_>, scope_kind: Option<String>, features: Vec<String>,
        semantic_body: Option<&Bound<'_, PyBytes>>,
    ) -> PyResult<NativeReadCapture> {
        self.initialize(py)?;
        let mut query = self.query.take().expect("initialized read query");
        let mut semantic = self.semantic.take();
        let original = self.jsonl_result.take();
        let body = semantic_body.map(|body| body.as_bytes());
        let diagnostic = &self.diagnostic;
        py.detach(|| -> PyResult<NativeReadCapture> {
            if let Some(body) = body {
                let modified = wire::decode_json(body).map_err(|error| facts_error(error.into()))?;
                if query.has_semantic_urls {
                    semantic = Some(query::collect(&modified).map_err(facts_error)?);
                } else {
                    let original = match original {
                        Some(original) => original,
                        None => wire::decode_json(diagnostic).map_err(|error| facts_error(error.into()))?,
                    };
                    let modified = replace_callback_nodes(original, modified)
                        .ok_or_else(|| PyValueError::new_err("Native read diagnostic changed indexed node identities"))?;
                    query.replace_properties(query::collect(&modified).map_err(facts_error)?);
                }
            }
            let capture = ReadCapture::from_query(query, semantic, scope_kind, features).map_err(capture_error)?;
            Ok(NativeReadCapture { capture })
        })
    }
}

#[pyfunction]
fn open_read_frame(
    py: Python<'_>, format: &str, body: &Bound<'_, PyBytes>, max_bytes: usize,
) -> PyResult<Option<NativeReadFrame>> {
    let body = body.as_bytes();
    py.detach(|| {
        let wire = match format {
            "cud2" => wire::decode_cud2(body, max_bytes),
            "cud1" => wire::decode_cud1(body, max_bytes),
            "cus2" => wire::decode_cus2(body, max_bytes),
            "json" if body.len() <= max_bytes => wire::decode_json(body)
                .map(|value| wire::WireFrame { value, diagnostic: body.to_vec() }),
            _ => return Ok(None),
        };
        let wire = wire.map_err(|error| facts_error(error.into()))?;
        if text(&wire.value, "kind") != Some("observation") {
            return Err(PyValueError::new_err("protocol: expected an observation frame result"));
        }
        let (query, semantic) = collect_read_queries(&wire.value).map_err(facts_error)?;
        Ok(Some(NativeReadFrame {
            query: Some(query), semantic, diagnostic: wire.diagnostic, jsonl_result: None,
        }))
    })
}

fn collect_read_queries(value: &Value) -> Result<(Query, Option<Query>), FactsError> {
    let query = query::collect_wire(value)?;
    let semantic = query.has_semantic_urls.then(|| query::collect(value)).transpose()?;
    Ok((query, semantic))
}

fn replace_callback_nodes(mut original: Value, modified: Value) -> Option<Value> {
    let nodes = original.get_mut("nodes")?;
    let before = nodes.as_seq()?;
    let after = modified.as_seq()?;
    if before.len() != after.len() || before.iter().zip(after).any(|(left, right)| text(left, "id") != text(right, "id")) {
        return None;
    }
    *nodes = modified;
    Some(original)
}

#[pyfunction]
fn open_read_envelope(
    py: Python<'_>, body: &Bound<'_, PyBytes>, max_bytes: usize,
) -> PyResult<Option<Py<PyDict>>> {
    let body = body.as_bytes();
    if body.len() > max_bytes {
        return Ok(None);
    }
    let Some(envelope) = py.detach(|| wire::decode_envelope(body)).map_err(|error| facts_error(error.into()))? else {
        return Ok(None);
    };
    let result = PyDict::new(py);
    result.set_item("version", value_to_py(py, &envelope.version)?)?;
    result.set_item("session", value_to_py(py, &envelope.session)?)?;
    result.set_item("id", value_to_py(py, &envelope.id)?)?;
    match envelope.body {
        wire::EnvelopeBody::Observation(frame) => result.set_item("result", Py::new(py, NativeReadFrame {
            query: None, semantic: None, diagnostic: frame.diagnostic, jsonl_result: Some(frame.value),
        })?)?,
        wire::EnvelopeBody::Failure(error) => result.set_item("error", value_to_py(py, &error)?)?,
    }
    Ok(Some(result.unbind()))
}

#[pyclass(frozen, module = "computer_use.interaction.read.accessibility._ax_read")]
#[derive(Clone)]
pub(super) struct NativeReadCapture {
    pub(super) capture: Arc<ReadCapture>,
}

#[pymethods]
impl NativeReadCapture {
    #[getter]
    fn session(&self) -> &str { self.capture.part().session }

    #[getter]
    fn backend(&self) -> &str { text(&self.capture.snapshot.observation, "backend").expect("admitted provider") }

    #[getter]
    fn id(&self) -> &str { text(&self.capture.snapshot.observation, "id").unwrap_or_default() }

    #[getter]
    fn surface(&self) -> Option<&str> { self.capture.part().surface }

    #[getter]
    fn scope_kind(&self) -> Option<&str> { self.capture.part().scope_kind }

    #[getter]
    fn change_token(&self) -> Option<&str> { text(&self.capture.snapshot.metadata, "change_token") }

    #[getter]
    fn focused_element(&self) -> Option<&str> { text(&self.capture.snapshot.metadata, "focused_element") }

    #[getter]
    fn roots(&self) -> Vec<&str> {
        records(&self.capture.snapshot.observation, "roots").iter().filter_map(Value::as_str).collect()
    }

    #[getter]
    fn interval_ns(&self) -> (i64, i64) {
        let interval = records(&self.capture.snapshot.observation, "interval_ns");
        (interval[0].as_i64().expect("admitted capture start"), interval[1].as_i64().expect("admitted capture end"))
    }

    #[getter]
    fn node_count(&self) -> usize { self.capture.tokens().len() }

    fn contains_observed(&self, token: &str) -> bool {
        records(&self.capture.snapshot.facts, "nodes").iter().any(|node| {
            text(node, "node") == Some(token) && node.get("observed").and_then(Value::as_bool) == Some(true)
        })
    }

    #[getter]
    fn observed(&self, py: Python<'_>) -> PyResult<Py<PyAny>> {
        value_to_py(py, self.capture.snapshot.facts.get("observed").unwrap_or(&Value::Null))
    }

    #[getter]
    fn source_tokens(&self) -> Vec<&str> {
        let part = self.capture.part();
        part.source_order.iter().map(|node| part.tokens[node.0].as_str()).collect()
    }

    fn busy_nodes(&self) -> Vec<&str> {
        loading::busy_nodes(&self.capture.snapshot)
    }

    fn refreshable(&self) -> bool {
        loading::refreshable(&self.capture.snapshot)
    }

    fn window_content_unexposed(&self) -> bool {
        loading::window_content_unexposed(&self.capture.snapshot)
    }

    #[pyo3(signature = (node, fields=None))]
    fn node_view(&self, py: Python<'_>, node: &str, fields: Option<Vec<String>>) -> PyResult<Py<PyAny>> {
        let value = self.capture.snapshot.observation.get("nodes").and_then(|nodes| nodes.get(node));
        match (value, fields) {
            (Some(value), Some(fields)) => {
                let result = PyDict::new(py);
                for field in fields {
                    if let Some(item) = value.get(&field) {
                        result.set_item(field, value_to_py(py, item)?)?;
                    }
                }
                Ok(result.into_any().unbind())
            }
            (value, _) => value_to_py(py, value.unwrap_or(&Value::Null)),
        }
    }

    fn lookup_evidence(&self, py: Python<'_>, fields: Vec<String>) -> PyResult<Py<PyAny>> {
        let query = &self.capture.snapshot.query;
        let selected: std::collections::HashSet<&str> = fields.iter().map(String::as_str).collect();
        let nodes = records(query, "nodes").iter().map(|row| {
            let keep = |key| Value::Map(row.get(key).and_then(Value::as_map).unwrap_or_default()
                .iter().filter(|(key, _)| selected.contains(key.as_str())).cloned().collect());
            let requested = records(row, "requestedFields").iter()
                .filter(|key| key.as_str().is_some_and(|key| selected.contains(key))).cloned().collect();
            Value::Map(vec![
                ("node".into(), row.get("node").cloned().unwrap_or(Value::Null)),
                ("values".into(), keep("values")), ("fieldStatus".into(), keep("fieldStatus")),
                ("requestedFields".into(), Value::Seq(requested)),
                ("actions".into(), row.get("actions").cloned().unwrap_or(Value::Null)),
            ])
        }).collect();
        let mut evidence: Vec<_> = ["session", "platform", "surface", "observed", "roots", "relations"]
            .into_iter().filter_map(|key| query.get(key).cloned().map(|value| (key.into(), value))).collect();
        evidence.push(("nodes".into(), Value::Seq(nodes)));
        value_to_py(py, &Value::Map(evidence))
    }

    fn collection_issues(&self) -> Vec<(&str, Option<&str>)> {
        menu::collection_issues(&self.capture.snapshot)
    }

    fn complete_content(&self, node: &str) -> bool {
        menu::complete_content(&self.capture.snapshot, node)
    }

    fn selected_click_target(&self, node: &str) -> Option<&str> {
        view::selected_click_target(&self.capture.snapshot, node)
    }

    fn selected_copy_target(&self) -> Option<&str> {
        view::selected_copy_target(&self.capture.snapshot)
    }

    fn menu_presentations(&self) -> Vec<(&str, &str)> {
        menu::presentations(&self.capture.snapshot)
    }

    fn menu_removed(&self, opener: &str, content: &str) -> bool {
        menu::removed(&self.capture.snapshot, opener, content)
    }

    fn new_local_menu(&self, after: &Self, target: &str, root: &str, published: Vec<String>) -> bool {
        let published: HashSet<_> = published.iter().map(String::as_str).collect();
        menu::new_local_menu(&self.capture.snapshot, &after.capture.snapshot, target, root, &published).is_some()
    }

    fn sharing_control_signature(&self, root: &str) -> Option<(&str, &str)> {
        menu::sharing_control_signature(&self.capture.snapshot, root)
    }

    fn capture_companion_evidence(&self, root: &str) -> Option<(&str, &str)> {
        menu::capture_companion_evidence(&self.capture.snapshot, root)
    }

    fn resolve_observed(
        &self, binding: Target, path: Vec<HashMap<String, String>>, capabilities: Vec<String>, remaining: &Bound<'_, PyAny>,
    ) -> PyResult<(&'static str, Option<String>, Option<String>)> {
        let path: Vec<_> = path.iter().map(|selector| lookup::Condition {
            role: selector.get("role").map(String::as_str),
            identifier: selector.get("identifier").map(String::as_str),
            name: selector.get("name").map(String::as_str),
        }).collect();
        match lookup::resolve(&self.capture.snapshot, target(&binding), &path, &capabilities, || remaining.call0().map(|_| ())) {
            Ok(node) => Ok(("matched", Some(node.to_owned()), None)),
            Err(lookup::Error::Checkpoint(error)) => Err(error),
            Err(lookup::Error::Invalid(error)) => Err(publication_error(error)),
            Err(lookup::Error::Unavailable) => Ok(("unavailable", None, None)),
            Err(lookup::Error::RootUnavailable) => Ok(("root_unavailable", None, None)),
            Err(lookup::Error::Failed { code, detail }) => Ok(("failed", Some(code.to_owned()), Some(detail))),
        }
    }

    fn same_content_scope(&self, after: &Self) -> bool {
        progress::same_content_scope(Some(&self.capture.snapshot), Some(&after.capture.snapshot))
    }

    fn has_content_progress(&self, after: &Self) -> bool {
        progress::has_content_progress(Some(&self.capture.snapshot), Some(&after.capture.snapshot))
    }

    fn prefer_earlier_budget_capture(&self, after: &Self) -> bool {
        progress::prefer_earlier_budget_capture(Some(&self.capture.snapshot), Some(&after.capture.snapshot))
    }

    fn with_aliases(&self, py: Python<'_>, aliases: HashMap<String, String>) -> PyResult<NumberedReadCapture> {
        if !matches!(self.capture.part().scope_kind, None | Some("window" | "applicationMenu" | "applicationContent")) {
            return Err(PyValueError::new_err("Unknown observation scope kind"));
        }
        let aliases = PartAliases::from_native(&self.capture.part(), &aliases)
            .ok_or_else(|| PyValueError::new_err("Invalid native read alias inventory"))?;
        let semantic = py.detach(|| self.capture.project(&aliases)).map_err(capture_error)?;
        Ok(NumberedReadCapture { capture: self.clone(), aliases, semantic: Arc::new(semantic) })
    }
}

#[pyclass(frozen, module = "computer_use.interaction.read.accessibility._ax_read")]
pub(super) struct NumberedReadCapture {
    pub(super) capture: NativeReadCapture,
    aliases: PartAliases,
    pub(super) semantic: Arc<SemanticTree>,
}

#[pymethods]
impl NumberedReadCapture {
    #[getter]
    fn capture(&self) -> NativeReadCapture { self.capture.clone() }

    #[getter]
    fn refs(&self, py: Python<'_>) -> PyResult<Py<PyAny>> {
        value_to_py(py, self.semantic.get("refs").unwrap_or(&Value::Null))
    }

    fn delivery_coverage(&self) -> (Vec<(&str, Option<&str>)>, Vec<&str>) {
        let coverage = view::delivery_coverage(&self.capture.capture.snapshot, &self.semantic);
        (coverage.parents, coverage.complete)
    }

    fn retained_bindings(
        &self, py: Python<'_>, binding: Target, include_public_pages: bool,
    ) -> PyResult<(Vec<Py<PyDict>>, Vec<Py<PyDict>>)> {
        let source = &self.capture.capture.snapshot;
        let binding = target(&binding);
        let retained = publication::retain_bindings(source, &self.semantic, binding, include_public_pages)
            .map_err(publication_error)?;
        let observed = retained.elements.first().map(|item| value_to_py(py, item.observed)).transpose()?;
        let mut elements = Vec::with_capacity(retained.elements.len());
        for item in retained.elements {
            let result = PyDict::new(py);
            result.set_item("reference", item.reference)?;
            result.set_item("node", item.target.node)?;
            result.set_item("container", item.container)?;
            if item.outside_window { result.set_item("native_window_outside", true)?; }
            result.set_item("scope_kind", item.scope_kind)?;
            result.set_item("observed", observed.as_ref().expect("element capture metadata"))?;
            if let Some(source) = item.text {
                let text = PyDict::new(py);
                match source {
                    CapturedText::Value(value) => text.set_item("value", value)?,
                    CapturedText::Reference { token, total_units } => {
                        text.set_item("text_ref", token)?;
                        text.set_item("total_units", value_to_py(py, total_units.unwrap_or(&Value::Null))?)?;
                    }
                }
                result.set_item("text", text)?;
            } else {
                result.set_item("text", py.None())?;
            }
            if let Some(source) = item.children {
                let children = PyDict::new(py);
                children.set_item("cursor", source.cursor)?;
                children.set_item("offset", source.offset)?;
                children.set_item("total", source.total)?;
                result.set_item("children", children)?;
            } else {
                result.set_item("children", py.None())?;
            }
            elements.push(result.unbind());
        }
        let mut pages = Vec::with_capacity(retained.pages.len());
        for item in retained.pages {
            let page = PyDict::new(py);
            page.set_item("reference", item.reference)?;
            page.set_item("kind", item.kind)?;
            page.set_item("node", item.target.node)?;
            page.set_item("property", item.property)?;
            page.set_item("cursor", item.continuation.cursor)?;
            page.set_item("offset", item.continuation.offset)?;
            page.set_item("total", item.continuation.total)?;
            pages.push(page.unbind());
        }
        Ok((elements, pages))
    }

    fn element_details(&self, reference: &str, scope: &Bound<'_, PyAny>) -> PyResult<String> {
        let scope = value_from_py(scope)?;
        let scope = scope.as_seq().ok_or_else(|| PyTypeError::new_err("Native element scope must be a sequence"))?;
        let snapshot = &self.capture.capture.snapshot;
        let references = self.semantic.get("refs").and_then(Value::as_map).unwrap_or_default().iter()
            .filter_map(|(reference, token)| token.as_str().map(|token| (reference.as_str(), token)));
        let source = DetailSource::new(self.capture.backend(), self.capture.session(), self.capture.id(),
            scope, &snapshot.facts, records(&snapshot.observation, "issues"), references)
            .map_err(|error| PyValueError::new_err(format!("Native captured detail source: {error:?}")))?
            .with_typed_alternatives(snapshot.metadata.get("issue_alternatives"));
        source.element_source(reference).map_err(|error| PyValueError::new_err(format!("Native captured element detail: {error:?}")))
    }

    #[pyo3(signature = (checkpoint=None, observer=None))]
    fn preview_tokens(
        &self, py: Python<'_>, checkpoint: Option<Py<PyAny>>, observer: Option<Py<PyAny>>,
    ) -> PyResult<Vec<&str>> {
        let mut events = Vec::new();
        let visible = py.detach(|| self.capture.capture.preview_projected_traced_checked(
            &self.aliases, &self.semantic, observer.as_ref().map(|_| &mut events),
            || self::checkpoint(checkpoint.as_ref()),
        ));
        if let Some(observer) = observer {
            let events: Vec<_> = events.into_iter()
                .map(|event| (event.name, event.phase, event.error, event.timestamp_ms)).collect();
            let _ = observer.call1(py, (events,));
        }
        let visible = visible.map_err(|error| error.0)?;
        Ok(visible.into_iter().map(|node| self.capture.capture.tokens()[node.0].as_str()).collect())
    }

    #[pyo3(signature = (window=None, recovery_prefix=0, checkpoint=None, compact=true, public_api=true, share_descriptions=true, trace=false))]
    #[allow(clippy::too_many_arguments)]
    fn prepare(
        &self, py: Python<'_>, window: Option<Window>, recovery_prefix: usize, checkpoint: Option<Py<PyAny>>,
        compact: bool, public_api: bool, share_descriptions: bool, trace: bool,
    ) -> PyResult<NativePrepared> {
        let window = window.map(|(window_ref, current, modal, confirmed_blockers, directory_complete, scope_confirmed)| {
            WindowContext { window_ref, current, modal, confirmed_blockers, directory_complete, scope_confirmed }
        });
        let geometric = compact && public_api && share_descriptions;
        let prepared = py.detach(|| {
            let prepared = self.capture.capture.prepare_projected_checked(
                &self.aliases, Arc::clone(&self.semantic),
                ProjectionOptions {
                    compact, public_api, share_descriptions, trace, defer_visual_composition: geometric,
                    ..ProjectionOptions::default()
                },
                || self::checkpoint(checkpoint.as_ref()),
            ).map_err(|error| error.0)?;
            if geometric {
                CompactSnapshot::prepare(prepared, window.as_ref(), recovery_prefix)
                    .map(PreparedRead::Compact).map_err(compact_error)
            } else {
                Ok(PreparedRead::Legacy(LegacySnapshot::new(prepared)))
            }
        })?;
        Ok(NativePrepared { prepared, recovery_prefix, compact, public_api })
    }
}

#[pyclass(frozen, module = "computer_use.interaction.read.accessibility._ax_read")]
pub(super) struct NativePrepared {
    prepared: PreparedRead,
    recovery_prefix: usize,
    compact: bool,
    public_api: bool,
}

enum PreparedRead {
    Compact(CompactSnapshot),
    Legacy(LegacySnapshot),
}

#[pymethods]
impl NativePrepared {
    fn has_uncompressed_preparation(&self) -> bool {
        match &self.prepared {
            PreparedRead::Compact(compact) => compact.roots.iter().any(|branch| matches!(branch, CompactBranch::Uncompressed(_))),
            PreparedRead::Legacy(legacy) => legacy.has_uncompressed_preparation(),
        }
    }

    fn refs(&self) -> PyResult<Vec<&str>> {
        match &self.prepared {
            PreparedRead::Compact(compact) => compact.visible_refs().map_err(compact_error),
            PreparedRead::Legacy(legacy) => legacy.visible_refs().map_err(|error| capture_error(error.into())),
        }
    }

    fn recovery_units(&self) -> usize {
        match &self.prepared {
            PreparedRead::Compact(compact) => compact.projected_roots().flat_map(|root| &root.groups.groups)
                .map(|group| group.recovery_units).max().unwrap_or(self.recovery_prefix),
            PreparedRead::Legacy(_) => self.recovery_prefix,
        }
    }

    fn comparison(&self, py: Python<'_>, scope: Vec<Option<String>>, indent: i64, details: bool) -> PyResult<delivery::NativeCurrent> {
        py.detach(|| match &self.prepared {
            PreparedRead::Compact(compact) => delivery::comparison(compact, scope, indent, details),
            PreparedRead::Legacy(legacy) => delivery::legacy_comparison(legacy, scope, indent, DisplayOptions {
                compact: self.compact, public_api: self.public_api, details,
            }),
        })
    }

    fn render(&self, py: Python<'_>, details: bool) -> PyResult<delivery::NativeRendered> {
        py.detach(|| match &self.prepared {
            PreparedRead::Compact(compact) => delivery::render(compact, details),
            PreparedRead::Legacy(legacy) => delivery::legacy_render(legacy, details),
        })
    }

    fn inline_child_refs(&self) -> PyResult<Vec<&str>> {
        match &self.prepared {
            PreparedRead::Compact(compact) => delivery::inline_child_refs(compact),
            PreparedRead::Legacy(legacy) => delivery::legacy_inline_child_refs(legacy),
        }
    }
}

pub(crate) fn register(module: &Bound<'_, PyModule>) -> PyResult<()> {
    module.add_class::<NativeReadReferences>()?;
    module.add_class::<NativeReadFrame>()?;
    module.add_class::<NativeReadCapture>()?;
    module.add_class::<NumberedReadCapture>()?;
    module.add_class::<NativePrepared>()?;
    delivery::register(module)?;
    module.add("NATIVE_READ_CONTRACT", 1)?;
    module.add("NATIVE_READ_CALLBACK_CONTRACT", 1)?;
    module.add_function(wrap_pyfunction!(open_read_frame, module)?)?;
    module.add_function(wrap_pyfunction!(open_read_envelope, module)?)
}

#[cfg(test)]
mod tests {
    use super::{replace_callback_nodes, wire, Value};

    #[test]
    fn ordinary_callback_uses_only_the_original_indexed_nodes() {
        let original = wire::decode_json(br#"{"nodes":[{"id":"root","properties":[]}],"read":{"capture":"original"}}"#).unwrap();
        let edited = wire::decode_json(br#"[{"id":"root","properties":[{"key":"new"}]}]"#).unwrap();
        let replaced = replace_callback_nodes(original.clone(), edited.clone()).unwrap();
        assert_eq!(replaced.get("read"), original.get("read"));
        assert_eq!(replaced.get("nodes"), Some(&edited));
        let other = wire::decode_json(br#"[{"id":"other","properties":[]}]"#).unwrap();
        assert!(replace_callback_nodes(original, other).is_none());
        assert!(replace_callback_nodes(replaced, Value::Null).is_none());
    }
}
