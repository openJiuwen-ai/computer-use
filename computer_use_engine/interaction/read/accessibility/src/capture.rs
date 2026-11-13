// Copyright (c) Huawei Technologies Co., Ltd. 2026. All rights reserved.

//! A read retains one fact owner and one index of native identities across its
//! acquisition, preview, and formally numbered presentation.

use std::{collections::HashMap, sync::Arc};

use super::{
    capabilities::{operation_capabilities, input_containers},
    facts::{self, FactSnapshot, FactsError},
    geometry::{collect_geometry, Geometry},
    numbering::{acquisition_aliases, NodeId, NumberingError, Part, PartAliases, SessionAliases},
    projection::{branch_members, recursion::ProjectionBudget, BranchError, CompressionEvidence, Projection, ProjectionError, ProjectionEvent, ProjectionOptions},
    render::fallback::{FailureStage, PresentationFailure},
    semantic::{self, ReadError},
    value::{SemanticTree, Value},
    wire::{self, WireError},
};

#[derive(Debug)]
pub(crate) enum CaptureError {
    Invalid(&'static str),
    Wire(WireError),
    Facts(FactsError),
    Semantic(ReadError),
    Numbering(NumberingError<()>),
    Projection(ProjectionError),
}

impl From<ReadError> for CaptureError {
    fn from(error: ReadError) -> Self { Self::Semantic(error) }
}

impl From<FactsError> for CaptureError {
    fn from(error: FactsError) -> Self { Self::Facts(error) }
}

impl From<WireError> for CaptureError {
    fn from(error: WireError) -> Self { Self::Wire(error) }
}

impl From<NumberingError<()>> for CaptureError {
    fn from(error: NumberingError<()>) -> Self { Self::Numbering(error) }
}

impl From<ProjectionError> for CaptureError {
    fn from(error: ProjectionError) -> Self { Self::Projection(error) }
}

pub(crate) struct ReadCapture {
    pub(crate) snapshot: FactSnapshot,
    session: String,
    surface: Option<String>,
    scope_kind: Option<String>,
    tokens: Vec<String>,
    source_order: Vec<NodeId>,
    features: Vec<String>,
}

pub(crate) struct PreparedCapture {
    pub(crate) capture: Arc<ReadCapture>,
    pub(crate) options: ProjectionOptions,
    pub(crate) semantic: Arc<SemanticTree>,
    pub(crate) branches: Vec<PreparedBranch>,
    pub(crate) geometry: Geometry,
    pub(crate) evidence: CompressionEvidence,
}

pub(crate) struct PreparedBranch {
    pub(crate) members: Vec<NodeId>,
    pub(crate) projection: Result<Arc<Projection>, FailedPreparation>,
}

#[derive(Clone, Debug)]
pub(crate) struct FailedPreparation {
    pub(crate) references: Vec<String>,
    pub(crate) failure: PresentationFailure,
}

impl PreparedBranch {
    pub(crate) fn reference(&self) -> Option<&str> {
        match &self.projection {
            Ok(projection) => projection.node(projection.root).map(|node| node.reference.as_str()),
            Err(failure) => failure.references.first().map(String::as_str),
        }
    }

    pub(crate) fn member_refs(&self) -> Result<Vec<String>, ProjectionError> {
        match &self.projection {
            Ok(projection) => self.members.iter().map(|id| {
                projection.node(*id).map(|node| node.reference.clone())
                    .ok_or(ProjectionError::Invalid("prepared branch source member"))
            }).collect(),
            Err(failure) => Ok(failure.references.clone()),
        }
    }
}

impl PreparedCapture {
    pub(crate) fn projections(&self) -> impl Iterator<Item = &Projection> {
        self.branches.iter().filter_map(|branch| branch.projection.as_deref().ok())
    }

    pub(crate) fn visible_refs(&self) -> Result<Vec<&str>, ProjectionError> {
        let mut output = Vec::new();
        for branch in &self.branches {
            match &branch.projection {
                Ok(projection) => {
                    for id in projection.visible_order()? {
                        let node = projection.node(id).ok_or(ProjectionError::Invalid("prepared visible source"))?;
                        output.push(node.reference.as_str());
                    }
                }
                Err(failure) => output.extend(failure.references.iter().map(String::as_str)),
            }
        }
        Ok(output)
    }
}

impl ReadCapture {
    pub(crate) fn from_cud2(
        body: &[u8], max_bytes: usize, scope_kind: Option<String>, features: Vec<String>,
    ) -> Result<(Arc<Self>, Vec<u8>), CaptureError> {
        let wire = wire::decode_cud2(body, max_bytes)?;
        let capture = Self::from_wire_value(&wire.value, scope_kind, features)?;
        Ok((capture, wire.diagnostic))
    }

    pub(crate) fn from_wire_value(
        raw: &Value, scope_kind: Option<String>, features: Vec<String>,
    ) -> Result<Arc<Self>, CaptureError> {
        Self::from_wire_value_admitted(raw, scope_kind, features, |_| Ok(()))
    }

    pub(crate) fn from_wire_value_admitted<E: From<CaptureError>>(
        raw: &Value, scope_kind: Option<String>, features: Vec<String>,
        admit: impl FnOnce(&Value) -> Result<(), E>,
    ) -> Result<Arc<Self>, E> {
        let snapshot = facts::normalize_admitted(raw, admit, |error| CaptureError::from(error).into())?;
        Self::from_snapshot(snapshot, scope_kind, features).map_err(E::from)
    }

    pub(super) fn from_query(
        query: facts::query::Query, semantic: Option<facts::query::Query>,
        scope_kind: Option<String>, features: Vec<String>,
    ) -> Result<Arc<Self>, CaptureError> {
        let snapshot = if let Some(semantic) = semantic {
            let mut snapshot = facts::normalize_query(semantic)?;
            snapshot.query = query.facts;
            snapshot
        } else {
            facts::normalize_query(query)?
        };
        Self::from_snapshot(snapshot, scope_kind, features)
    }

    fn from_snapshot(
        mut snapshot: FactSnapshot, scope_kind: Option<String>, features: Vec<String>,
    ) -> Result<Arc<Self>, CaptureError> {
        Self::attach_operation_metadata(&mut snapshot, scope_kind.as_deref(), &features)?;
        Self::new(snapshot, scope_kind, features)
    }

    fn attach_operation_metadata(
        snapshot: &mut FactSnapshot, scope_kind: Option<&str>, features: &[String],
    ) -> Result<(), CaptureError> {
        let rows = snapshot.query.get("nodes").and_then(Value::as_seq)
            .ok_or(CaptureError::Invalid("missing query node evidence"))?;
        let platform = snapshot.observation.get("backend").and_then(Value::as_str)
            .ok_or(CaptureError::Invalid("missing provider"))?;
        let capabilities = operation_capabilities(platform, rows, features, scope_kind)
            .ok_or(CaptureError::Invalid("missing observed capabilities"))?;
        let containers = input_containers(platform, rows)
            .ok_or(CaptureError::Invalid("missing observed receiver evidence"))?;
        let Value::Map(metadata) = &mut snapshot.metadata else {
            return Err(CaptureError::Invalid("missing read metadata"));
        };
        metadata.push(("operation_capabilities".into(), capabilities));
        metadata.push(("input_containers".into(), containers));
        Ok(())
    }

    pub(crate) fn new(
        snapshot: FactSnapshot,
        scope_kind: Option<String>,
        features: Vec<String>,
    ) -> Result<Arc<Self>, CaptureError> {
        let session = snapshot.observation.get("session").and_then(Value::as_str)
            .ok_or(CaptureError::Invalid("missing native session"))?.to_owned();
        let surface = match snapshot.metadata.get("surface") {
            None | Some(Value::Null) => None,
            Some(Value::Text(value)) => Some(value.clone()),
            _ => return Err(CaptureError::Invalid("invalid observed surface")),
        };
        let native = snapshot.observation.get("nodes").and_then(Value::as_map)
            .ok_or(CaptureError::Invalid("missing native nodes"))?;
        let ordinal: HashMap<_, _> = native.iter().enumerate()
            .map(|(index, (name, _))| (name.as_str(), NodeId(index))).collect();
        let source_order = semantic::source_order(&snapshot)?.into_iter()
            .map(|name| ordinal.get(name).copied().ok_or(CaptureError::Invalid("unknown native identity")))
            .collect::<Result<_, _>>()?;
        let tokens = native.iter().map(|(name, _)| name.clone()).collect();
        Ok(Arc::new(Self { snapshot, session, surface, scope_kind, tokens, source_order, features }))
    }

    pub(crate) fn part(&self) -> Part<'_> {
        Part {
            session: &self.session,
            surface: self.surface.as_deref(),
            scope_kind: self.scope_kind.as_deref(),
            tokens: &self.tokens,
            source_order: &self.source_order,
        }
    }

    pub(crate) fn initial_aliases(&self, reader: &mut SessionAliases) -> Result<PartAliases, CaptureError> {
        Ok(acquisition_aliases(reader, &self.part())?)
    }

    pub(crate) fn display_index(&self, aliases: &PartAliases) -> HashMap<String, NodeId> {
        aliases.ordered().iter().map(|(node, number)| (number.to_string(), *node)).collect()
    }

    pub(crate) fn project(&self, aliases: &PartAliases) -> Result<SemanticTree, CaptureError> {
        let scope = self.scope_kind.clone().map_or(Value::Null, Value::Text);
        let features = Value::Seq(self.features.iter().cloned().map(Value::Text).collect());
        Ok(semantic::project_numbered(&self.snapshot, &self.tokens, aliases, &scope, &features)?.into())
    }

    pub(crate) fn geometry(&self) -> Geometry { collect_geometry(&self.snapshot.observation) }

    pub(crate) fn tokens(&self) -> &[String] { &self.tokens }

    pub(crate) fn prepare(
        self: &Arc<Self>, aliases: &PartAliases, options: ProjectionOptions,
    ) -> Result<PreparedCapture, CaptureError> {
        self.prepare_checked(aliases, options, || Ok(()))
    }

    pub(crate) fn prepare_checked<E: From<CaptureError>>(
        self: &Arc<Self>, aliases: &PartAliases, options: ProjectionOptions, checkpoint: impl FnMut() -> Result<(), E>,
    ) -> Result<PreparedCapture, E> {
        let semantic = Arc::new(self.project(aliases).map_err(E::from)?);
        self.prepare_projected_checked(aliases, semantic, options, checkpoint)
    }

    pub(super) fn prepare_projected_checked<E: From<CaptureError>>(
        self: &Arc<Self>, aliases: &PartAliases, semantic: Arc<SemanticTree>, options: ProjectionOptions,
        mut checkpoint: impl FnMut() -> Result<(), E>,
    ) -> Result<PreparedCapture, E> {
        checkpoint()?;
        let ids = self.display_index(aliases);
        let evidence = CompressionEvidence::capture(&self.snapshot, &semantic, &ids)
            .map_err(CaptureError::from).map_err(E::from)?;
        let geometry = self.geometry();
        let roots = semantic.get("roots").and_then(Value::as_seq)
            .ok_or(CaptureError::Invalid("missing semantic roots"))?;
        let mut branches = Vec::with_capacity(roots.len());
        for root in roots {
            let members = match branch_members(root, &ids, &mut checkpoint) {
                Ok(members) => members,
                Err(BranchError::Source(error)) => return Err(CaptureError::from(error).into()),
                Err(BranchError::Budget(error)) => return Err(error),
            };
            let projection = match prepare_root(root, &ids, options.clone(), &evidence, &geometry) {
                Ok(projection) => Ok(Arc::new(projection)),
                Err(error) => {
                    let failure = projection_failure(error).map_err(CaptureError::from)?;
                    let references = members.iter().map(|id| aliases.alias(*id).map(|reference| reference.to_string())
                        .ok_or(CaptureError::Invalid("prepared branch source alias")))
                        .collect::<Result<Vec<_>, _>>()?;
                    Err(FailedPreparation { references, failure })
                }
            };
            branches.push(PreparedBranch { members, projection });
        }
        checkpoint()?;
        Ok(PreparedCapture { capture: Arc::clone(self), options, semantic, branches, geometry, evidence })
    }

    pub(super) fn preview_projected_checked<E: From<CaptureError>>(
        &self, aliases: &PartAliases, semantic: &Value, checkpoint: impl FnMut() -> Result<(), E>,
    ) -> Result<Vec<NodeId>, E> {
        self.preview_projected_traced_checked(aliases, semantic, None, None, checkpoint)
    }

    pub(super) fn preview_projected_traced_checked<E: From<CaptureError>>(
        &self, aliases: &PartAliases, semantic: &Value, mut trace: Option<&mut Vec<ProjectionEvent>>,
        recursion: Option<ProjectionBudget>,
        mut checkpoint: impl FnMut() -> Result<(), E>,
    ) -> Result<Vec<NodeId>, E> {
        checkpoint()?;
        let ids = self.display_index(aliases);
        let roots = semantic.get("roots").and_then(Value::as_seq)
            .ok_or(CaptureError::Invalid("missing semantic roots"))?;
        let options = ProjectionOptions { compact: true, recursion, ..ProjectionOptions::default() };
        let mut evidence = None;
        let mut geometry = None;
        let mut order = Vec::new();
        for root in roots {
            let members = match branch_members(root, &ids, &mut checkpoint) {
                Ok(members) => members,
                Err(BranchError::Source(error)) => return Err(CaptureError::from(error).into()),
                Err(BranchError::Budget(error)) => return Err(error),
            };
            let evidence = evidence.get_or_insert_with(|| CompressionEvidence::capture(&self.snapshot, semantic, &ids));
            // Legacy numbering uses the uncompressed source order for a branch
            // whose presentation fails, then continues with any later branches.
            let projected = evidence.as_ref().map_err(Clone::clone).and_then(|evidence| {
                self.project_preview_root(root, &ids, options.clone(), evidence, &mut geometry, trace.as_deref_mut())
            }).and_then(|projection| projection.visible_order());
            let visible = match projected {
                Ok(visible) => visible,
                Err(error @ ProjectionError::Unsupported(_)) => return Err(CaptureError::from(error).into()),
                Err(_) => members,
            };
            for batch in visible.chunks(128) {
                checkpoint()?;
                order.extend_from_slice(batch);
            }
        }
        checkpoint()?;
        Ok(order)
    }

    fn project_preview_root(
        &self, root: &Value, ids: &HashMap<String, NodeId>, options: ProjectionOptions,
        evidence: &CompressionEvidence, geometry: &mut Option<Geometry>, mut trace: Option<&mut Vec<ProjectionEvent>>,
    ) -> Result<Projection, ProjectionError> {
        ProjectionEvent::scope(&mut trace, "PresentationPipeline", |trace| {
            let (input, geometry) = ProjectionEvent::scope(trace, "SemanticPipeline", |trace| {
                let semantic = ProjectionEvent::scope(trace, "compose_display_semantics", |_| {
                    Projection::compose_display_semantics(root)
                })?;
                let input = ProjectionEvent::scope(trace, "build_projection_input", |_| {
                    Projection::build_projection_input(semantic, ids, options)
                })?;
                let geometry = ProjectionEvent::scope(trace, "collect_geometry", |_| {
                    Ok(&*geometry.get_or_insert_with(|| self.geometry()))
                })?;
                Ok((input, geometry))
            })?;
            ProjectionEvent::scope(trace, "CompressionPipeline", |trace| {
                let mut projection = ProjectionEvent::scope(trace, "build_presentation_ir", |_| {
                    Projection::build_presentation_ir(input, ids)
                })?;
                if let Some(trace) = trace.as_deref_mut() {
                    projection.compress_with_lifecycle(evidence, geometry, trace)?;
                } else {
                    projection.compress(evidence, geometry)?;
                }
                Ok(projection)
            })
        })
    }

}

fn projection_failure(error: ProjectionError) -> Result<PresentationFailure, ProjectionError> {
    let (kind, detail) = match error {
        ProjectionError::MissingField(name) => ("KeyError", super::diff::python_str_repr(name)),
        ProjectionError::UnknownReference(reference) => ("KeyError", super::diff::python_str_repr(&reference)),
        ProjectionError::DuplicateReference(_) => ("ValueError", "Projection contains a duplicate occurrence".to_owned()),
        ProjectionError::Recursion(detail) => ("RecursionError", detail),
        ProjectionError::Invalid(detail) => {
            let detail = match detail {
                "projection removed an element without recording its source" => "Projection removed an element without recording its source",
                "projection contains a duplicate occurrence" => "Projection contains a duplicate occurrence",
                _ => detail,
            };
            ("ValueError", detail.to_owned())
        }
        error @ ProjectionError::Unsupported(_) => return Err(error),
    };
    Ok(PresentationFailure { error: kind, detail, stage: FailureStage::Preparation })
}

fn prepare_root(
    root: &Value, ids: &HashMap<String, NodeId>, options: ProjectionOptions,
    evidence: &CompressionEvidence, geometry: &Geometry,
) -> Result<Projection, ProjectionError> {
    let mut projection = Projection::prepare(root, ids, options)?;
    projection.compress(evidence, geometry)?;
    Ok(projection)
}

#[cfg(test)]
mod tests {
    use super::*;
    use std::collections::HashSet;
    use crate::{
        compact::{CompactBranch, CompactSnapshot},
        diff::{self, ComparisonDocument, DisplayOptions, Mode},
        geometric::BlockId,
        legacy::LegacySnapshot,
        publication::{admit_nodes, Binding},
        render::{
            self,
            details::{detail_slice, element_details, DetailSource},
            lines::{render_projection, LineOptions},
            DocumentPart, RenderedPart,
        },
    };
    use crate::wire::decode_json;
    use crate::numbering::number_parts;

    fn json(source: &str) -> Value { decode_json(source.as_bytes()).unwrap() }

    #[test]
    fn keeps_ordinals_independent_of_preview_and_display_order() {
        let capture = ReadCapture::new(FactSnapshot {
            observation: json(r#"{"session":"driver","nodes":{"unread":{},"window":{},"label":{}}}"#),
            facts: json(r#"{"nodes":[{"node":"unread","observed":false},{"node":"label","observed":true},{"node":"window","observed":true}]}"#),
            metadata: json(r#"{"surface":"native-window"}"#),
            query: Value::Null,
        }, Some("window".into()), vec![]).unwrap();
        let mut reader = SessionAliases::new("driver".into());
        let aliases = capture.initial_aliases(&mut reader).unwrap();
        assert_eq!(capture.part().source_order, &[NodeId(2), NodeId(1), NodeId(0)]);
        assert_eq!(capture.display_index(&aliases)["0"], NodeId(2));
        assert_eq!(capture.display_index(&aliases)["2"], NodeId(0));
        assert_eq!(capture.part().surface, Some("native-window"));
    }

    #[test]
    fn preview_uses_source_order_for_failed_branches_and_keeps_budget_and_identity_errors() {
        let capture = ReadCapture::new(FactSnapshot {
            observation: json(r#"{"session":"driver","nodes":{"a":{},"b":{},"c":{}}}"#),
            facts: json(r#"{"nodes":[{"node":"a","observed":true},{"node":"b","observed":true},{"node":"c","observed":true}]}"#),
            metadata: Value::Map(vec![]),
            query: Value::Null,
        }, Some("window".into()), vec![]).unwrap();
        let mut reader = SessionAliases::new("driver".into());
        let aliases = capture.initial_aliases(&mut reader).unwrap();
        let invalid_role = json(r#"{
            "refs":{"0":"a","1":"b","2":"c"},
            "roots":[{"ref":"1","children":[{"ref":"0","role":"button","children":[]}]},
                     {"ref":"2","role":"window","children":[]}]
        }"#);
        let result = capture.preview_projected_checked(&aliases, &invalid_role, || Ok::<_, CaptureError>(())).unwrap();
        assert_eq!(result, [NodeId(1), NodeId(0), NodeId(2)]);
        let prepared = capture.prepare_projected_checked(
            &aliases, Arc::new(invalid_role.clone().into()), ProjectionOptions::default(), || Ok::<_, CaptureError>(()),
        ).unwrap();
        assert_eq!(prepared.visible_refs().unwrap(), ["1", "0", "2"]);
        let [failed, retained] = prepared.branches.as_slice() else {
            panic!("both source branches must remain in order");
        };
        let failure = failed.projection.as_ref().err().expect("source-preserving preparation failure");
        assert_eq!(failed.members, [NodeId(1), NodeId(0)]);
        assert_eq!(failure.references, ["1", "0"]);
        assert_eq!(failure.failure.error, "KeyError");
        assert_eq!(failure.failure.detail, "'role'");
        assert_eq!(failure.failure.stage, FailureStage::Preparation);
        assert!(retained.projection.is_ok());

        let invalid_identity = json(r#"{
            "refs":{"0":"a","1":"b","2":"c"},"roots":[{"ref":"9","children":[]}]
        }"#);
        let result = capture.preview_projected_checked(&aliases, &invalid_identity, || Ok::<_, CaptureError>(()));
        assert!(matches!(result, Err(CaptureError::Projection(ProjectionError::UnknownReference(reference))) if reference == "9"));

        let mut checks = 0;
        let stopped = capture.preview_projected_checked(&aliases, &invalid_role, || {
            checks += 1;
            if checks == 3 { Err(CaptureError::Invalid("original deadline")) } else { Ok(()) }
        });
        assert!(matches!(stopped, Err(CaptureError::Invalid("original deadline"))));
    }

    #[test]
    fn deeply_nested_raw_observation_keeps_native_presentation_on_the_worker_stack() {
        use std::fmt::Write;

        const COUNT: usize = 512;
        std::thread::Builder::new().stack_size(512 * 1024).spawn(|| {
            let mut body = String::from(r#"{"kind":"observation","session":"driver","platform":"macos","roots":["ax0"],"nodes":["#);
            for index in 0..COUNT {
                if index > 0 { body.push(','); }
                let role = if index == 0 { "AXWindow" } else { "AXGroup" };
                write!(body, concat!(
                    r#"{{"id":"ax{index}","source":"macos.ax","kind":"element","nativeType":"AXUIElement","properties":["#,
                    r#"{{"key":["macos.ax","AXRole"],"value":{{"nativeType":"CFString","data":"{role}"}}}},"#,
                    r#"{{"key":["macos.ax","AXTitle"],"value":{{"nativeType":"CFString","data":"Layer {index}"}}}}"#,
                ), index = index, role = role).unwrap();
                if index + 1 < COUNT {
                    body.push_str(r#",{"key":["macos.ax","AXChildren"],"value":{"nativeType":"CFArray","data":{"items":["#);
                    write!(body, r#"{{"nativeType":"AXUIElement","data":{{"ref":"ax{}"}}}}"#, index + 1).unwrap();
                    body.push_str(concat!(
                        r#"]}},"read":{"complete":true,"details":[["relation",{"nativeType":"CFBoolean","data":true}],"#,
                        r#"["certainty",{"nativeType":"CFString","data":"confirmed"}],"#,
                        r#"["state",{"nativeType":"CFString","data":"complete"}],"#,
                        r#"["total",{"nativeType":"CFNumber","data":1}]]}}"#,
                    ));
                }
                body.push_str("]}");
            }
            body.push_str(r#"],"read":{"intervalNs":[1,2],"details":[["surface",{"nativeType":"CFString","data":"surface"}],["observation_id",{"nativeType":"CFString","data":"deep"}]]}}"#);

            let capture = ReadCapture::from_wire_value(&decode_json(body.as_bytes()).unwrap(), Some("window".into()), vec![])
                .unwrap();
            let aliases = capture.initial_aliases(&mut SessionAliases::new("driver".into())).unwrap();
            let semantic = capture.project(&aliases).unwrap();
            assert_eq!(semantic.get("roots").and_then(Value::as_seq).unwrap().len(), 1);
            let expected: Vec<_> = (0..COUNT).map(NodeId).collect();
            let preview = capture.preview_projected_checked(&aliases, &semantic, || Ok::<_, CaptureError>(())).unwrap();
            assert_eq!(preview, expected);

            let prepared = capture.prepare_projected_checked(
                &aliases, Arc::new(semantic), ProjectionOptions {
                    compact: true, public_api: true, defer_visual_composition: true, ..Default::default()
                }, || Ok::<_, CaptureError>(()),
            ).unwrap();
            let compact = CompactSnapshot::prepare(prepared, None, 0).unwrap();
            assert!(matches!(compact.roots.as_slice(), [CompactBranch::Projected(_)]));
            let comparison = compact.comparison(
                vec![Some("surface".into())], DisplayOptions { compact: true, public_api: true, details: false }, 0,
            ).unwrap();
            assert_eq!(comparison.comparison.nodes.len(), COUNT);
            let leaf = format!("Layer {}", COUNT - 1);
            let mut rendered = compact.render().unwrap();
            let text = rendered.body.text();
            assert!(text.contains(&leaf));
            assert!(!text.contains("Presentation fallback"));
            compact.render_details(&mut rendered).unwrap();
            assert!(rendered.body.details.contains(&leaf));
        }).unwrap().join().unwrap();
    }

    #[test]
    fn native_capture_supplies_the_numbered_projection_without_exporting_a_tree() {
        let prefix = br#"{"kind":"observation","session":"session","platform":"macos","roots":["root"],"nodes":[{"id":"root","source":"macos.ax","kind":"element","nativeType":"AXUIElement","properties":"#;
        let suffix = br#"}],"read":{"intervalNs":[1,2],"details":[["surface",{"nativeType":"CFString","data":"surface"}],["observation_id",{"nativeType":"CFString","data":"capture"}]]}}"#;
        let properties = br#"[{"key":["macos.ax","AXRole"],"value":{"nativeType":"CFString","data":"AXWindow"}},{"key":["macos.ax","AXTitle"],"value":{"nativeType":"CFString","data":"Rust fixture"}}]"#;
        let original = [prefix.as_slice(), properties.as_slice(), suffix.as_slice()].concat();
        let keys = [br#"["macos.ax","AXRole"]"#.as_slice(), br#"["macos.ax","AXTitle"]"#.as_slice()];
        let values = [
            br#"{"nativeType":"CFString","data":"AXWindow"}"#.as_slice(),
            br#"{"nativeType":"CFString","data":"Rust fixture"}"#.as_slice(),
        ];
        let mut key_pool = Vec::new();
        for key in keys {
            key_pool.extend_from_slice(&(key.len() as u32).to_le_bytes());
            key_pool.extend_from_slice(key);
        }
        let mut tape = Vec::new();
        tape.extend_from_slice(&(prefix.len() as u32).to_le_bytes());
        tape.extend_from_slice(&2u32.to_le_bytes());
        tape.extend_from_slice(prefix);
        for (index, value) in values.iter().enumerate() {
            for word in [index as u32, u32::MAX, 0, 0, value.len() as u32] {
                tape.extend_from_slice(&word.to_le_bytes());
            }
            tape.extend_from_slice(value);
        }
        tape.extend_from_slice(suffix);
        let mut frame = b"CUD2".to_vec();
        for word in [key_pool.len(), 0, 0, tape.len(), 1, original.len()] {
            frame.extend_from_slice(&(word as u32).to_le_bytes());
        }
        frame.extend_from_slice(&key_pool);
        frame.extend_from_slice(&tape);

        let (capture, diagnostic) = ReadCapture::from_cud2(&frame, 4096, Some("window".into()), vec![]).unwrap();
        assert_eq!(diagnostic, original);
        let raw = decode_json(&original).unwrap();
        let admitted = ReadCapture::from_wire_value_admitted(&raw, Some("window".into()), vec![], |query| {
            let target = Binding {
                session: "session", instance: None, root_window: None, surface: Some("surface"), node: Some("root"),
            };
            assert!(admit_nodes(query, target, "macos", 1, &[], None).unwrap().is_empty());
            Err::<(), _>(CaptureError::Invalid("stop at query admission"))
        });
        assert!(matches!(admitted, Err(CaptureError::Invalid("stop at query admission"))));
        assert!(capture.geometry().is_empty());
        let mut native_refs = SessionAliases::new("session".into());
        let initial = capture.initial_aliases(&mut native_refs).unwrap();
        let expired = capture.prepare_checked(&initial, ProjectionOptions::default(), || {
            Err::<(), _>(CaptureError::Invalid("stop at branch budget check"))
        });
        assert!(matches!(expired, Err(CaptureError::Invalid("stop at branch budget check"))));
        let preview = capture.prepare(&initial, ProjectionOptions { compact: true, ..ProjectionOptions::default() }).unwrap();
        let visible = capture.preview_projected_checked(&initial, &preview.semantic, || Ok::<_, CaptureError>(())).unwrap();
        assert_eq!(visible, preview.projections().next().unwrap().visible_order().unwrap());
        let mut display_refs = SessionAliases::new("session".into());
        let numbers = number_parts(&mut display_refs, &[capture.part()], |_, _| Ok::<_, CaptureError>(visible.clone())).unwrap();
        let formal = capture.prepare(&numbers[0], ProjectionOptions::default()).unwrap();
        let seed = render_projection(formal.projections().next().unwrap(), &LineOptions::default()).unwrap();
        let section = seed.finish_unpacked();
        assert_eq!(section.text, "0 window Rust fixture, Secondary Actions: <unavailable>");
        assert_eq!(section.nodes[0].reference, "0");
        assert_eq!(formal.semantic.get("refs").unwrap().get("0").and_then(Value::as_str), Some("root"));
        check_captured_details(&capture);
        let changed_raw = decode_json(std::str::from_utf8(&original).unwrap().replace("Rust fixture", "Updated").as_bytes()).unwrap();
        let changed = ReadCapture::from_wire_value(&changed_raw, Some("window".into()), vec![]).unwrap();
        check_compact_document(&capture, &changed, &numbers[0]);
        check_preparation_fallback(&capture, &numbers[0]);
    }

    fn check_captured_details(capture: &ReadCapture) {
        let snapshot = &capture.snapshot;
        let source = DetailSource::new(
            "macos", "session", "capture", &[], &snapshot.facts,
            snapshot.observation.get("issues").unwrap().as_seq().unwrap(), [("0", "root")],
        ).unwrap();
        let detail = element_details("0", &[source]).unwrap();
        let issue = r#"{"path":["nodes","root","actions"],"state":"not_read","detail":"Action catalogue was not read","alternatives":[]}"#;
        let expected = format!(concat!(
            "Captured element 0:\n\n",
            "Source: {{\"backend\":\"macos\",\"session\":\"session\",\"observation\":\"capture\",",
            "\"node\":\"root\",\"scope\":[],\"observed\":{{\"id\":\"capture\",\"startedAt\":0,\"completedAt\":0}}}}\n",
            "0 native element\n\t\"role\": \"window\"\n\t\"name\": \"Rust fixture\"\n",
            "\t\"actions\" status: {{\"state\":\"not_read\",\"issues\":[{0}],\"detail\":\"Action catalogue was not read\"}}\n",
            "\tNative actions: {{\"state\":\"not_read\",\"items\":[],\"issues\":[{0}]}}\n",
            "Native read issues {{\"session\":\"session\",\"observation\":\"capture\"}}:\n",
            "\t{{\"index\":0,\"ref\":\"0\",\"issue\":{{\"path\":[\"nodes\",\"root\",\"actions\"],\"state\":\"not_read\",",
            "\"detail\":\"Action catalogue was not read\",\"continuation\":null,\"alternatives\":[]}}}}",
        ), issue);
        assert_eq!(detail, expected);
        let (first, next, total) = detail_slice(&detail, 0, 100).unwrap();
        let (last, end, _) = detail_slice(&detail, next, total).unwrap();
        assert_eq!(format!("{first}{last}"), detail);
        assert_eq!(end, total);
    }

    fn check_compact_document(capture: &Arc<ReadCapture>, changed: &Arc<ReadCapture>, aliases: &PartAliases) {
        let options = ProjectionOptions {
            compact: true, public_api: true, defer_visual_composition: true, ..ProjectionOptions::default()
        };
        let prepared = capture.prepare(aliases, options.clone()).unwrap();
        let mut compact = CompactSnapshot::prepare(prepared, None, 0).unwrap();
        let rendered = compact.render().unwrap().body;
        let scope = vec![Some("surface".into())];
        let display = DisplayOptions { compact: true, public_api: true, details: false };
        let comparison = compact.comparison(scope.clone(), display, 0).unwrap();
        let baseline = ComparisonDocument { trees: vec![comparison.comparison.clone()] };
        let bindings = HashMap::from([("0".into(), "root".into())]);
        let delta = diff::plan_document_diff(Some(&baseline), Some(&[comparison]), &bindings, &bindings, &HashSet::new()).unwrap();
        assert_eq!(delta.mode, Mode::Unchanged);
        let document = [DocumentPart::Tree { snapshot: 0, indent: 0, details: false }];
        let rendered = [rendered];
        assert_eq!(render::document(&document, &rendered, None).as_deref(), Some("0 window Rust fixture, Secondary Actions: <unavailable>"));
        assert_eq!(render::document(&document, &rendered, Some(&delta)).as_deref(), Some("Tree: unchanged"));
        let next = changed.prepare(aliases, options).unwrap();
        let next = CompactSnapshot::prepare(next, None, 0).unwrap();
        let comparison = next.comparison(scope, display, 0).unwrap();
        let delta = diff::plan_document_diff(Some(&baseline), Some(&[comparison]), &bindings, &bindings, &HashSet::new()).unwrap();
        assert_eq!(delta.mode, Mode::Diff);
        let incremental = render::document(&document, &[next.render().unwrap().body], Some(&delta)).unwrap();
        assert_eq!(incremental, "~0 window Updated, Secondary Actions: <unavailable>\nTree: diff");

        // A terminal layout failure keeps the original root capture and binding.
        let CompactBranch::Projected(root) = &mut compact.roots[0] else {
            panic!("original root should have a geometric projection");
        };
        root.interface.roots.push(BlockId(usize::MAX));
        let mut fallback = compact.render().unwrap();
        let [RenderedPart::Section(section), ..] = &fallback.body.parts[..] else {
            panic!("uncompressed root section was not retained");
        };
        assert!(section.refresh);
        assert_eq!(section.nodes[0].reference, "0");
        assert!(section.text.contains("0 native element\n\t\"role\": \"window\"\n\t\"name\": \"Rust fixture\""));
        compact.render_details(&mut fallback).unwrap();
        assert!(fallback.body.details.contains("0 native element"));
    }

    fn check_preparation_fallback(capture: &Arc<ReadCapture>, aliases: &PartAliases) {
        let mut semantic = capture.project(aliases).unwrap();
        let Some(Value::Seq(roots)) = semantic.get_mut("roots") else { panic!("fixture roots"); };
        let [Value::Map(root)] = roots.as_mut_slice() else { panic!("fixture root"); };
        root.retain(|(key, _)| key != "role");
        let semantic = Arc::new(semantic);
        let prepare = |public_api| capture.prepare_projected_checked(
            aliases, Arc::clone(&semantic), ProjectionOptions { compact: true, public_api, ..Default::default() },
            || Ok::<_, CaptureError>(()),
        ).unwrap();
        let legacy = LegacySnapshot::new(prepare(false));
        let legacy_body = legacy.render(true).unwrap();
        let compact = CompactSnapshot::prepare(prepare(true), None, 0).unwrap();
        let compact_body = compact.render().unwrap().body;
        for body in [&legacy_body, &compact_body] {
            let [RenderedPart::Section(section), ..] = body.parts.as_slice() else {
                panic!("prepared fallback must retain the original branch");
            };
            assert!(!section.refresh);
            assert_eq!(section.nodes[0].reference, "0");
            assert!(section.text.contains("KeyError: \"'role'\""));
            assert!(section.text.contains("0 native element\n\t\"role\": \"window\"\n\t\"name\": \"Rust fixture\""));
        }
        let comparison = compact.comparison(
            vec![Some("surface".into())], DisplayOptions { compact: true, public_api: true, details: false }, 0,
        ).unwrap();
        assert_eq!(comparison.comparison.nodes[0].reference, "0");
        assert_eq!(comparison.comparison.versions.get("0"), Some(&None));
    }
}
