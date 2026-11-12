// Copyright (c) Huawei Technologies Co., Ltd. 2026. All rights reserved.

//! Owned semantic projection. Source containment and display containment have
//! separate edges. The latter can change without rebinding the native NodeId.

use std::borrow::Borrow;
use std::time::{SystemTime, UNIX_EPOCH};
use std::collections::{BTreeSet, HashMap, HashSet};

use super::{geometry::Geometry, numbering::NodeId, value::Value};

mod semantics;
mod captions;
mod derived;
mod editor;
mod evidence;
mod owned_content;
mod passive;
mod rules;
pub(crate) mod spatial;
mod value_lines;
mod whole;

#[cfg(test)]
mod tests;

type Fields = Vec<(String, Value)>;
type Result<T> = std::result::Result<T, ProjectionError>;

const DISPLAY_FIELDS: [&str; 7] = [
    "name", "description", "value", "selectionRange", "selectedText", "valueDescription", "rangeDescription",
];

#[derive(Clone, Debug, Eq, PartialEq)]
pub(crate) enum ProjectionError {
    Invalid(&'static str),
    MissingField(&'static str),
    UnknownReference(String),
    DuplicateReference(String),
    Unsupported(&'static str),
}

#[derive(Debug, Eq, PartialEq)]
pub(crate) enum BranchError<E> {
    Source(ProjectionError),
    Budget(E),
}

/// Validate the source branch before any fallible display transformation.
/// Check the same original work units while recording membership in preorder.
pub(crate) fn branch_members<E>(
    root: &Value, aliases: &HashMap<String, NodeId>, mut checkpoint: impl FnMut() -> std::result::Result<(), E>,
) -> std::result::Result<Vec<NodeId>, BranchError<E>> {
    checkpoint().map_err(BranchError::Budget)?;
    let mut work = 0;
    let mut tick = || {
        work += 1;
        if work % 128 == 0 {
            checkpoint().map_err(BranchError::Budget)?;
        }
        Ok(())
    };
    let mut members = Vec::new();
    let mut seen = HashSet::new();
    let mut pending = vec![root];
    while let Some(node) = pending.pop() {
        tick()?;
        let reference = node.get("ref").and_then(Value::as_str)
            .ok_or(BranchError::Source(ProjectionError::Invalid("semantic branch has no source identity")))?;
        let id = lookup(aliases, reference).map_err(BranchError::Source)?;
        if !seen.insert(id) {
            return Err(BranchError::Source(ProjectionError::DuplicateReference(reference.to_owned())));
        }
        members.push(id);
        let children = node.get("children").and_then(Value::as_seq)
            .ok_or(BranchError::Source(ProjectionError::Invalid("semantic branch has no ordered children")))?;
        for child in children.iter().rev() {
            tick()?;
            pending.push(child);
        }
    }
    Ok(members)
}

#[derive(Clone, Debug)]
pub(crate) struct ProjectionOptions {
    pub(crate) include_background: bool,
    pub(crate) native_facts: bool,
    pub(crate) app_view: bool,
    pub(crate) compact: bool,
    pub(crate) public_api: bool,
    pub(crate) trace: bool,
    pub(crate) share_descriptions: bool,
    pub(crate) defer_visual_composition: bool,
    pub(crate) subtree: Option<NodeId>,
    pub(crate) focused: Option<NodeId>,
    pub(crate) inherited_blocks: Vec<String>,
}

impl Default for ProjectionOptions {
    fn default() -> Self {
        Self {
            include_background: true,
            native_facts: true,
            app_view: false,
            compact: false,
            public_api: false,
            trace: false,
            share_descriptions: true,
            defer_visual_composition: false,
            subtree: None,
            focused: None,
            inherited_blocks: Vec::new(),
        }
    }
}

#[derive(Clone, Debug, Eq, Hash, PartialEq)]
pub(crate) struct FactKey {
    pub(crate) source: NodeId,
    pub(crate) attribute: String,
}

#[derive(Clone, Debug)]
pub(crate) struct ValuePart {
    pub(crate) value: Value,
    pub(crate) facts: Vec<FactKey>,
}

#[derive(Clone, Debug)]
pub(crate) struct PresentedValue {
    pub(crate) parts: Vec<ValuePart>,
    pub(crate) separator: Option<String>,
}

impl PresentedValue {
    fn original(value: &Value, source: NodeId, attribute: &str) -> Self {
        Self {
            parts: vec![ValuePart {
                value: value.clone(),
                facts: vec![FactKey { source, attribute: attribute.to_owned() }],
            }],
            separator: None,
        }
    }
}

#[derive(Clone, Debug)]
pub(crate) struct TextSource {
    pub(crate) fact: FactKey,
    pub(crate) field: String,
    pub(crate) text: Value,
}

#[derive(Debug, Default)]
pub(crate) struct CompressionEvidence {
    pub(crate) complete_text: HashSet<FactKey>,
    pub(crate) complete_descriptions: HashSet<FactKey>,
    pub(crate) provider_wrappers: HashSet<NodeId>,
    pub(crate) unlabelled_groups: HashSet<NodeId>,
    pub(crate) complete_children: HashMap<NodeId, Vec<NodeId>>,
    pub(crate) incomplete_children: HashSet<NodeId>,
    pub(crate) concrete_inputs: HashSet<NodeId>,
    pub(crate) semantic_regions: HashSet<NodeId>,
    pub(crate) contested_sources: HashSet<NodeId>,
    pub(crate) incomplete_content: HashSet<NodeId>,
    pub(crate) splice_conflicts: HashSet<NodeId>,
    pub(crate) control_value_lines: HashSet<NodeId>,
    pub(crate) caption_sources: HashMap<NodeId, PassiveCaptionSource>,
}

#[derive(Debug)]
pub(crate) struct PassiveCaptionSource {
    pub(crate) value: String,
    pub(crate) ancestors: Vec<NodeId>,
    pub(crate) reported_windows: Vec<(NodeId, Option<NodeId>)>,
}

#[derive(Debug)]
pub(crate) struct CaptionEquivalence {
    pub(crate) owner: NodeId,
    pub(crate) scope: NodeId,
    pub(crate) display_field: String,
    pub(crate) anchor: FactKey,
    pub(crate) composition: PresentedValue,
}

#[derive(Clone, Copy, Debug, Eq, PartialEq)]
pub(crate) enum ElisionKind {
    Fold,
    Splice,
    Empty,
}

#[derive(Clone, Debug)]
pub(crate) struct Elision {
    pub(crate) source: NodeId,
    pub(crate) kind: ElisionKind,
    pub(crate) destinations: Vec<NodeId>,
    pub(crate) rule: &'static str,
    pub(crate) evidence: Vec<FactKey>,
}

#[derive(Clone, Debug)]
pub(crate) struct ProjectionNode {
    pub(crate) id: NodeId,
    pub(crate) reference: String,
    pub(crate) parent: Option<NodeId>,
    pub(crate) scope: NodeId,
    pub(crate) native_fields: Fields,
    pub(crate) native_children: Vec<NodeId>,
    pub(crate) fields: Fields,
    pub(crate) children: Vec<NodeId>,
    pub(crate) source_refs: Vec<NodeId>,
    pub(crate) text_sources: Vec<TextSource>,
    pub(crate) provenance: Vec<(String, PresentedValue)>,
    pub(crate) shared_description_destinations: Vec<(String, NodeId)>,
    pub(crate) shared_descriptions: Vec<FieldDeclaration>,
    pub(crate) source_roles: Vec<FieldDeclaration>,
    source_display_size: usize,
}

#[derive(Clone, Debug)]
pub(crate) struct FieldDeclaration {
    pub(crate) attribute: String,
    pub(crate) value: String,
    pub(crate) facts: Vec<FactKey>,
}

impl ProjectionNode {
    pub(crate) fn field(&self, name: &str) -> Option<&Value> {
        field(&self.fields, name)
    }
}

/// The source order includes nodes suppressed by interaction scoping. The
/// display order includes every admitted node before compression.
#[derive(Debug)]
pub(crate) struct Projection {
    pub(crate) root: NodeId,
    pub(crate) roots: Vec<NodeId>,
    pub(crate) source_order: Vec<NodeId>,
    pub(crate) display_order: Vec<NodeId>,
    pub(crate) elisions: Vec<Elision>,
    pub(crate) folded_into: Vec<(NodeId, NodeId)>,
    pub(crate) resolved_elisions: Vec<(NodeId, Vec<NodeId>)>,
    pub(crate) targets: Vec<(String, Vec<String>)>,
    pub(crate) changes: Vec<Value>,
    pub(crate) caption_equivalences: Vec<CaptionEquivalence>,
    pub(crate) stages: Vec<StageTrace>,
    pub(crate) options: ProjectionOptions,
    nodes: Vec<ProjectionNode>,
    indexes: HashMap<NodeId, usize>,
    references: HashMap<String, NodeId>,
    active_stage: Option<usize>,
    lifecycle: Option<Vec<ProjectionEvent>>,
}

#[derive(Debug, Eq, PartialEq)]
pub(crate) struct ProjectionEvent {
    pub(crate) name: &'static str,
    pub(crate) phase: &'static str,
    pub(crate) error: Option<&'static str>,
    pub(crate) timestamp_ms: Option<u64>,
}

impl ProjectionEvent {
    fn timestamp_ms() -> Option<u64> {
        SystemTime::now().duration_since(UNIX_EPOCH).ok()?.as_millis().try_into().ok()
    }

    fn enter(name: &'static str) -> Self {
        Self { name, phase: "enter", error: None, timestamp_ms: Self::timestamp_ms() }
    }

    fn finish(name: &'static str, error: Option<&ProjectionError>) -> Self {
        let error = error.map(|error| match error {
            ProjectionError::MissingField(_) | ProjectionError::UnknownReference(_) => "KeyError",
            ProjectionError::Invalid(_) | ProjectionError::DuplicateReference(_) | ProjectionError::Unsupported(_) => "ValueError",
        });
        Self { name, phase: if error.is_some() { "error" } else { "exit" }, error, timestamp_ms: Self::timestamp_ms() }
    }

    pub(super) fn scope<T>(
        trace: &mut Option<&mut Vec<Self>>, name: &'static str,
        stage: impl FnOnce(&mut Option<&mut Vec<Self>>) -> Result<T>,
    ) -> Result<T> {
        if let Some(trace) = trace.as_deref_mut() {
            trace.push(Self::enter(name));
        }
        let result = stage(trace);
        if let Some(trace) = trace.as_deref_mut() {
            trace.push(Self::finish(name, result.as_ref().err()));
        }
        result
    }
}

pub(super) struct ProjectionSemantics(semantics::SemanticTree);

pub(super) struct ProjectionInput {
    source: ProjectionSemantics,
    root: NodeId,
    options: ProjectionOptions,
}

#[derive(Debug, Eq, PartialEq)]
pub(crate) struct StageTrace {
    pub(crate) name: &'static str,
    pub(crate) calls: usize,
    pub(crate) changes: usize,
    pub(crate) affected_refs: BTreeSet<String>,
}

impl Projection {
    pub(crate) fn prepare(
        root: impl Borrow<Value>,
        aliases: &HashMap<String, NodeId>,
        options: ProjectionOptions,
    ) -> Result<Self> {
        let semantic = Self::compose_display_semantics(root.borrow())?;
        let input = Self::build_projection_input(semantic, aliases, options)?;
        Self::build_presentation_ir(input, aliases)
    }

    pub(super) fn compose_display_semantics(root: &Value) -> Result<ProjectionSemantics> {
        let mut source = semantics::SemanticTree::parse(root)?;
        semantics::annotate_interactions(&mut source, &[])?;
        semantics::compose_controls(&mut source)?;
        Ok(ProjectionSemantics(source))
    }

    pub(super) fn build_projection_input(
        source: ProjectionSemantics, aliases: &HashMap<String, NodeId>, options: ProjectionOptions,
    ) -> Result<ProjectionInput> {
        let root = lookup(aliases, source.0.nodes[0].reference())?;
        Ok(ProjectionInput { source, root, options })
    }

    pub(super) fn build_presentation_ir(input: ProjectionInput, aliases: &HashMap<String, NodeId>) -> Result<Self> {
        let ProjectionInput { source: ProjectionSemantics(source), root: root_id, options } = input;
        let mut projection = Self {
            root: root_id,
            roots: vec![root_id],
            source_order: Vec::new(),
            display_order: Vec::new(),
            elisions: Vec::new(),
            folded_into: Vec::new(),
            resolved_elisions: Vec::new(),
            targets: Vec::new(),
            changes: Vec::new(),
            caption_equivalences: Vec::new(),
            stages: Vec::new(),
            options,
            nodes: Vec::new(),
            indexes: HashMap::new(),
            references: HashMap::new(),
            active_stage: None,
            lifecycle: None,
        };
        let mut display = source.clone();
        semantics::annotate_interactions(&mut display, &projection.options.inherited_blocks)?;
        if !projection.options.include_background {
            semantics::scope_interactions(&mut display)?;
        }
        projection.capture_source(source, aliases, root_id)?;
        projection.capture_display(display, aliases)?;
        projection.annotate_editor_owners()?;
        Ok(projection)
    }

    pub(crate) fn node(&self, id: NodeId) -> Option<&ProjectionNode> {
        self.indexes.get(&id).map(|&index| &self.nodes[index])
    }

    pub(crate) fn by_reference(&self, reference: &str) -> Option<&ProjectionNode> {
        self.references.get(reference).and_then(|&id| self.node(id))
    }

    pub(crate) fn protect_spatial_regions(&mut self, geometry: &Geometry) -> Result<()> {
        self.run_rule("protect_spatial_regions", |projection| spatial::protect_regions(projection, geometry))
    }

    pub(crate) fn organize_spatial(&mut self, geometry: &Geometry, evidence: &CompressionEvidence) -> Result<()> {
        self.run_rule("reconstruct_outlines", Self::reconstruct_outlines)?;
        self.lifecycle_scope("SpatialOrganizationPipeline", |projection| spatial::organize(projection, geometry, evidence))
    }

    /// Ordered, pure compression over one exact semantic branch. Evidence
    /// and geometry are taken from the original capture.
    pub(crate) fn compress(&mut self, evidence: &CompressionEvidence, geometry: &Geometry) -> Result<()> {
        self.run_rule("share_empty_descriptions", |projection| projection.share_empty_descriptions(evidence))?;
        self.run_rule("associate_labels", Self::associate_labels)?;
        self.protect_spatial_regions(geometry)?;
        self.run_rule("compose_passive_text_lines", |projection| projection.compose_passive_text_lines(evidence, geometry))?;
        self.lifecycle_scope("CompressionTraversal", |projection| projection.compress_local(evidence, geometry))?;
        self.organize_spatial(geometry, evidence)?;
        self.run_rule("deduplicate_row_captions", |projection| projection.deduplicate_row_captions(evidence))?;
        self.run_rule("deduplicate_derived_container_captions", |projection| projection.deduplicate_derived_captions(evidence))?;
        self.run_rule("fold_native_caption_components", |projection| projection.fold_native_caption_components(evidence))?;
        self.run_rule("merge_owned_passive_content", |projection| projection.merge_owned_passive_content(evidence))?;
        self.run_rule("finalize_sources", Self::finalize_sources)?;
        self.lifecycle_scope("validate_projection", |projection| projection.validate())
    }

    pub(crate) fn compress_with_lifecycle(
        &mut self, evidence: &CompressionEvidence, geometry: &Geometry, trace: &mut Vec<ProjectionEvent>,
    ) -> Result<()> {
        self.lifecycle = Some(std::mem::take(trace));
        let result = self.lifecycle_scope("CompressionIRLifetime", |projection| {
            projection.lifecycle_scope("CompressAndExport", |projection| projection.compress(evidence, geometry))
        });
        *trace = self.lifecycle.take().expect("active compression lifecycle");
        result
    }

    fn lifecycle_scope<T>(&mut self, name: &'static str, stage: impl FnOnce(&mut Self) -> Result<T>) -> Result<T> {
        if let Some(trace) = &mut self.lifecycle {
            trace.push(ProjectionEvent::enter(name));
        }
        let result = stage(self);
        if let Some(trace) = &mut self.lifecycle {
            trace.push(ProjectionEvent::finish(name, result.as_ref().err()));
        }
        result
    }

    fn annotate_editor_owners(&mut self) -> Result<()> {
        for id in self.display_order.clone() {
            let owner = self.display_node(id)?;
            let Some(control) = owner.field("control").filter(|value| {
                value.get("kind").and_then(Value::as_str) == Some("textInput")
            }) else { continue };
            let reference = owner.reference.clone();
            let members = control.get("members").and_then(Value::as_seq)
                .ok_or(ProjectionError::Invalid("text input control has no member list"))?;
            let editors: Vec<_> = members.iter().filter(|member| {
                member.get("part").and_then(Value::as_str) == Some("editor")
            }).filter_map(|member| {
                let node = self.by_reference(member.get("ref")?.as_str()?)?;
                (!node.fields.is_empty()).then_some(node.id)
            }).collect();
            for editor in editors {
                put(&mut self.node_mut(editor)?.fields, "editorOwnerRef", Value::Text(reference.clone()));
            }
        }
        Ok(())
    }

    fn run_rule<T>(&mut self, name: &'static str, rule: impl FnOnce(&mut Self) -> Result<T>) -> Result<T> {
        self.lifecycle_scope(name, |projection| projection.run_counted_rule(name, rule))
    }

    fn run_counted_rule<T>(&mut self, name: &'static str, rule: impl FnOnce(&mut Self) -> Result<T>) -> Result<T> {
        if !self.options.trace {
            return rule(self);
        }
        let index = self.stages.iter().position(|stage| stage.name == name).unwrap_or_else(|| {
            let index = self.stages.len();
            self.stages.push(StageTrace { name, calls: 0, changes: 0, affected_refs: BTreeSet::new() });
            index
        });
        self.stages[index].calls += 1;
        let previous = self.active_stage.replace(index);
        let start = self.changes.len();
        let result = rule(self);
        self.active_stage = previous;
        let changes = &self.changes[start..];
        self.stages[index].changes += changes.len();
        self.stages[index].affected_refs.extend(changes.iter().filter_map(|change| {
            change.get("sourceRef").and_then(Value::as_str).map(str::to_owned)
        }));
        result
    }

    fn node_mut(&mut self, id: NodeId) -> Result<&mut ProjectionNode> {
        let index = self.indexes.get(&id).ok_or(ProjectionError::Invalid("unknown projection source"))?;
        Ok(&mut self.nodes[*index])
    }

    fn capture_source(
        &mut self,
        mut tree: semantics::SemanticTree,
        aliases: &HashMap<String, NodeId>,
        root: NodeId,
    ) -> Result<()> {
        let mut pending = vec![(0, None, root)];
        while let Some((index, parent, inherited_scope)) = pending.pop() {
            let node = &tree.nodes[index];
            let reference = node.reference().to_owned();
            let id = lookup(aliases, &reference)?;
            let scope = if matches!(node.role(), "window" | "sheet" | "menuBar") { id } else { inherited_scope };
            if self.indexes.contains_key(&id) {
                return Err(ProjectionError::DuplicateReference(reference));
            }
            let children = node.children.iter()
                .map(|&child| lookup(aliases, tree.nodes[child].reference())).collect::<Result<Vec<_>>>()?;
            pending.extend(node.children.iter().rev().map(|&child| (child, Some(id), scope)));
            self.indexes.insert(id, self.nodes.len());
            self.references.insert(reference.clone(), id);
            self.source_order.push(id);
            self.nodes.push(ProjectionNode {
                id,
                reference,
                parent,
                scope,
                native_fields: std::mem::take(&mut tree.nodes[index].fields),
                native_children: children,
                fields: Vec::new(),
                children: Vec::new(),
                source_refs: vec![id],
                text_sources: Vec::new(),
                provenance: Vec::new(),
                shared_description_destinations: Vec::new(),
                shared_descriptions: Vec::new(),
                source_roles: Vec::new(),
                source_display_size: 0,
            });
        }
        Ok(())
    }

    fn capture_display(
        &mut self,
        mut tree: semantics::SemanticTree,
        aliases: &HashMap<String, NodeId>,
    ) -> Result<()> {
        let mut pending = vec![0];
        while let Some(index) = pending.pop() {
            let node = &mut tree.nodes[index];
            let id = lookup(aliases, node.reference())?;
            if self.options.native_facts {
                semantics::display_copy(node)?;
            }
            let node = &tree.nodes[index];
            let children = node.children.iter()
                .map(|&child| lookup(aliases, tree.nodes[child].reference())).collect::<Result<Vec<_>>>()?;
            pending.extend(node.children.iter().rev().copied());
            self.display_order.push(id);
            let projected = self.node_mut(id)?;
            projected.children = children;
            for name in ["name", "description"] {
                if let Some(value) = field(&node.fields, name).filter(|value| truthy(value)) {
                    projected.text_sources.push(TextSource {
                        fact: FactKey { source: id, attribute: name.to_owned() },
                        field: name.to_owned(),
                        text: value.clone(),
                    });
                }
            }
            for (name, value) in &node.fields {
                if DISPLAY_FIELDS.contains(&name.as_str()) {
                    projected.provenance.push((name.clone(), PresentedValue::original(value, id, name)));
                }
            }
            projected.source_display_size = node.fields.len();
            projected.fields = std::mem::take(&mut tree.nodes[index].fields);
        }
        Ok(())
    }

    pub(crate) fn visible_order(&self) -> Result<Vec<NodeId>> {
        let mut seen = HashSet::new();
        let mut ordered = Vec::new();
        let mut pending: Vec<_> = self.roots.iter().rev().copied().collect();
        while let Some(id) = pending.pop() {
            let node = self.node(id).ok_or(ProjectionError::Invalid("unknown projection source"))?;
            if !seen.insert(id) {
                return Err(ProjectionError::Invalid("projection contains a duplicate occurrence"));
            }
            ordered.push(id);
            pending.extend(node.children.iter().rev().copied());
        }
        Ok(ordered)
    }
}

fn lookup(aliases: &HashMap<String, NodeId>, reference: &str) -> Result<NodeId> {
    aliases.get(reference).copied().ok_or_else(|| ProjectionError::UnknownReference(reference.to_owned()))
}

fn field<'a>(fields: &'a Fields, name: &str) -> Option<&'a Value> {
    fields.iter().find(|(key, _)| key == name).map(|(_, value)| value)
}

fn put(fields: &mut Fields, name: &str, value: Value) {
    if let Some((_, current)) = fields.iter_mut().find(|(key, _)| key == name) {
        *current = value;
    } else {
        fields.push((name.to_owned(), value));
    }
}

fn remove(fields: &mut Fields, name: &str) {
    if let Some(index) = fields.iter().position(|(key, _)| key == name) {
        fields.remove(index);
    }
}

fn text(value: &Value) -> Option<&str> {
    match value {
        Value::Text(value) => Some(value),
        _ => None,
    }
}

fn sequence(value: &Value) -> Option<&[Value]> {
    match value {
        Value::Seq(value) => Some(value),
        _ => None,
    }
}

fn truthy(value: &Value) -> bool {
    match value {
        Value::Null => false,
        Value::Bool(value) => *value,
        Value::Int(value) => *value != 0,
        Value::BigInt(value) => value != "0",
        Value::Float(value) => *value != 0.0,
        Value::Text(value) => !value.is_empty(),
        Value::Bytes(value) => !value.is_empty(),
        Value::Seq(value) => !value.is_empty(),
        Value::Map(value) => !value.is_empty(),
    }
}
