// Copyright (c) Huawei Technologies Co., Ltd. 2026. All rights reserved.

//! Prepare a public compact root and retain its exact presentation plans.

use std::{collections::HashSet, sync::Arc};

use super::{
    accounting::{self, deferred_actions, selection_summary_refs, AccountingPart, AccountingRequest},
    capture::{FailedPreparation, PreparedCapture},
    collections::{self, CollectionDocument, CollectionError},
    content::{self, readable::ReadablePlan, ContentError, FieldContentPlan},
    diff::{self, CurrentTree, DisplayOptions, InvalidPreparedComparison, Scope},
    disclosure::{
        self, coverage::{self, CoverageBlock, CoverageError, CoveragePlan},
        groups::{self, GroupDisclosurePlan, GroupError, GroupPolicy, GroupRequest, WindowContext},
        DisclosureError, DisclosurePlan, DisclosurePolicy,
    },
    geometric::{BlockId, GeometricContext, GeometricPlan, GeometryError},
    projection::{Projection, ProjectionError},
    render::{
        atoms::{finalize_atoms, plan_atoms, policy, AtomFinalizeOptions, AtomOptions, AtomPlan, DispositionKind},
        details::DetailSource,
        fallback::{self, append_scoped, FailureStage, PresentationFailure},
        geometric::{groups as group_text, render_geometric, GeometricRendered, WriterOptions},
        RenderedPart, RenderedSection, RenderedSnapshot, TextError,
    },
    value::Value,
    sharing::{
        capture_sharing_evidence, plan_sharing, plan_text_slices, SharingError, SharingPlan, SharingRequest,
        TextSliceDocument, TextSliceRequest,
    },
};

#[derive(Debug)]
pub(crate) enum CompactError {
    MissingRoot,
    Source(ProjectionError),
    Geometry(GeometryError),
    Disclosure(DisclosureError),
    Content(ContentError),
    Collection(CollectionError),
    Group(GroupError),
    Sharing(SharingError),
    Coverage(CoverageError),
    Render(TextError),
}

pub(crate) struct CompactRoot {
    pub(crate) projection: Arc<Projection>,
    pub(crate) interface: GeometricPlan,
    pub(crate) atoms: AtomPlan,
    pub(crate) disclosure: DisclosurePlan,
    pub(crate) collections: CollectionDocument,
    pub(crate) fields: FieldContentPlan,
    pub(crate) groups: GroupDisclosurePlan,
    pub(crate) sharing: SharingPlan,
    pub(crate) coverage: CoveragePlan<BlockId>,
    pub(crate) slices: TextSliceDocument,
    pub(crate) readable: ReadablePlan,
    pub(crate) deferred_native_fields: Vec<Value>,
}

impl CompactRoot {
    pub(crate) fn prepare(
        capture: &PreparedCapture, root: usize, window: Option<&WindowContext>, recovery_prefix: usize,
    ) -> Result<Self, CompactError> {
        let projection = capture.branches.get(root).and_then(|branch| branch.projection.as_ref().ok())
            .ok_or(CompactError::MissingRoot)?;
        let snapshot = &capture.capture.snapshot;
        let summarized = selection_summary_refs(snapshot, &capture.semantic, projection, capture.capture.tokens())
            .map_err(CompactError::Render)?;
        let actions = deferred_actions(snapshot, &capture.semantic, projection, capture.capture.tokens())
            .map_err(CompactError::Render)?;
        let detail_policy = policy::plan(snapshot, &capture.semantic, projection, &capture.evidence, &summarized)
            .map_err(CompactError::Render)?;
        let options = AtomOptions {
            compact: true, captured_details: true, focused: projection.options.focused,
            summarized_selection: Some(&summarized), deferred: Some(&detail_policy.deferred),
            role_aliases: Some(&detail_policy.role_aliases), suppressed_actions: Some(&actions.projected),
            ..AtomOptions::default()
        };
        let mut atoms = plan_atoms(projection, &options).map_err(CompactError::Render)?;
        let actual: HashSet<_> = atoms.atoms.values().flat_map(|atom| &atom.dispositions)
            .filter(|item| item.kind == DispositionKind::Details).map(|item| &item.fact.key).collect();
        let deferred_native_fields = detail_policy.destinations.into_iter()
            .filter_map(|(fact, destination)| actual.contains(&fact).then_some(destination)).collect();
        let atom_evidence = atoms.layout_evidence();
        let context = GeometricContext {
            projection, geometry: &capture.geometry, facts: Some(snapshot), atoms: Some(&atom_evidence),
        };
        let interface = GeometricPlan::build(&context, true).map_err(CompactError::Geometry)?;
        let disclosure = disclosure::plan(
            snapshot, projection, &capture.semantic, capture.capture.tokens(), true, DisclosurePolicy::default(),
        ).map_err(CompactError::Disclosure)?;
        finalize_atoms(&mut atoms, projection, &AtomFinalizeOptions {
            compact: projection.options.compact, public_api: projection.options.public_api,
            share_descriptions: projection.options.share_descriptions, subtree: projection.options.subtree.is_some(),
            interface: Some(&interface), disclosure: Some(&disclosure), evidence: Some(&capture.evidence),
            snapshot: Some(snapshot), semantic: Some(&capture.semantic), tokens: capture.capture.tokens(),
        }).map_err(CompactError::Render)?;
        let collections = collections::prepare(&interface, projection, &atoms, snapshot)
            .map_err(CompactError::Collection)?;
        let fields = content::plan_fields(projection, &interface, &atoms, Some(&capture.evidence), true)
            .map_err(CompactError::Content)?;
        let groups = groups::plan(&GroupRequest {
            interface: &interface, projection, atoms: &atoms, snapshot, semantic: &capture.semantic,
            tokens: capture.capture.tokens(), enabled: true, policy: GroupPolicy::default(), window,
            field_disclosure: Some(&disclosure), deferred_actions: Some(&actions.native),
            recovery_prefix_units: recovery_prefix,
        }).map_err(CompactError::Group)?;
        let mut excluded_sources = fields.excluded_sources();
        excluded_sources.extend(groups.groups.iter().flat_map(|group| group.recovery_refs.iter().copied()));
        let sharing_evidence = capture_sharing_evidence(
            projection, &atoms, snapshot, Some(&capture.evidence), Some(&actions.projected),
        ).map_err(CompactError::Sharing)?;
        let sharing = plan_sharing(SharingRequest {
            projection, interface: &interface, atoms: &atoms, evidence: &sharing_evidence,
            compression: Some(&capture.evidence), disclosure: Some(&disclosure), excluded_sources: &excluded_sources,
        }).map_err(CompactError::Sharing)?;
        let coverage = coverage::plan(projection, &atoms, &interface.roots, &interface.target_refs, |id| {
            interface.block(id).map(|block| CoverageBlock {
                primary: block.primary_ref, source_refs: &block.source_refs, children: &block.children,
            })
        }).map_err(CompactError::Coverage)?;
        let excluded_slots = sharing.omitted_slots.iter().flat_map(|(source, slots)| {
            slots.iter().map(move |slot| (*source, slot.clone()))
        }).collect();
        let slices = plan_text_slices(TextSliceRequest {
            projection, atoms: &atoms, snapshot, value_fields: capture.semantic.get("value_fields"),
            disclosure: Some(&disclosure), excluded_sources: &excluded_sources, excluded_slots: &excluded_slots,
        }).map_err(CompactError::Sharing)?;
        let readable = content::readable::plan(projection, &interface, &atoms, &sharing).map_err(CompactError::Content)?;
        Ok(Self { projection: Arc::clone(projection), interface, atoms, disclosure, collections, fields, groups, sharing, coverage, slices, readable, deferred_native_fields })
    }

    pub(crate) fn render(&self) -> Result<GeometricRendered, CompactError> {
        let mut options = WriterOptions::from_plans(
            &self.interface, &self.atoms, Some(&self.disclosure), Some(&self.fields), Some(&self.readable), Some(&self.coverage),
        ).map_err(CompactError::Render)?.with_sharing(&self.atoms, Some(&self.sharing), Some(&self.slices))
            .map_err(CompactError::Render)?;
        options.groups = Some(&self.groups);
        options.collections = Some(&self.collections);
        render_geometric(&self.interface, &self.projection, &self.atoms, &options).map_err(CompactError::Render)
    }
}

pub(crate) struct CompactSnapshot {
    capture: PreparedCapture,
    pub(crate) roots: Vec<CompactBranch>,
}

pub(crate) enum CompactBranch {
    Projected(Box<CompactRoot>),
    Uncompressed(FailedPreparation),
}

impl CompactBranch {
    pub(crate) fn projected(&self) -> Option<&CompactRoot> {
        match self {
            Self::Projected(root) => Some(root),
            Self::Uncompressed(_) => None,
        }
    }
}

pub(crate) struct CompactPresentation {
    pub(crate) body: RenderedSnapshot,
    root_parts: usize,
    uncompressed: Vec<bool>,
}

impl CompactSnapshot {
    pub(crate) fn prepared_capture(&self) -> &PreparedCapture { &self.capture }

    pub(crate) fn projected_roots(&self) -> impl Iterator<Item = &CompactRoot> {
        self.roots.iter().filter_map(CompactBranch::projected)
    }

    pub(crate) fn visible_refs(&self) -> Result<Vec<&str>, CompactError> {
        let mut refs = Vec::new();
        for branch in &self.roots {
            match branch {
                CompactBranch::Projected(root) => {
                    for id in root.projection.visible_order().map_err(CompactError::Source)? {
                        let node = root.projection.node(id).ok_or(CompactError::MissingRoot)?;
                        refs.push(node.reference.as_str());
                    }
                }
                CompactBranch::Uncompressed(failure) => refs.extend(failure.references.iter().map(String::as_str)),
            }
        }
        Ok(refs)
    }

    pub(crate) fn prepare(
        capture: PreparedCapture, window: Option<&WindowContext>, mut recovery_prefix: usize,
    ) -> Result<Self, CompactError> {
        let mut roots = Vec::with_capacity(capture.branches.len());
        for (index, branch) in capture.branches.iter().enumerate() {
            if let Err(failure) = &branch.projection {
                roots.push(CompactBranch::Uncompressed(failure.clone()));
                continue;
            }
            match CompactRoot::prepare(&capture, index, window, recovery_prefix) {
                Ok(root) => {
                    recovery_prefix = root.groups.groups.iter().map(|group| group.recovery_units)
                        .max().unwrap_or(recovery_prefix);
                    roots.push(CompactBranch::Projected(Box::new(root)));
                }
                Err(error) => {
                    let failure = presentation_failure(error, FailureStage::Preparation)?;
                    let references = branch.member_refs().map_err(CompactError::Source)?;
                    roots.push(CompactBranch::Uncompressed(FailedPreparation { references, failure }));
                }
            }
        }
        Ok(Self { capture, roots })
    }

    pub(crate) fn comparison(
        &self, scope: Scope, options: DisplayOptions, indent: i64,
    ) -> Result<CurrentTree, InvalidPreparedComparison> {
        diff::prepare_compact_comparison(scope, options, indent, &self.capture, &self.roots)
    }

    pub(crate) fn render(&self) -> Result<CompactPresentation, CompactError> {
        let capture = &self.capture.capture;
        let native = &capture.snapshot.observation;
        let identity = ["backend", "session", "id"].map(|key| native.get(key).and_then(Value::as_str));
        let [Some(backend), Some(session), Some(observation)] = identity else {
            return Err(CompactError::Render(TextError::Invalid("captured observation identity")));
        };
        let mut parts = Vec::new();
        let mut directories = Vec::new();
        let mut payloads = Vec::new();
        let mut uncompressed = Vec::with_capacity(self.roots.len());
        let contexts = self.capture.semantic.get("root_contexts");
        let mut previous = None;
        for (root, branch) in self.roots.iter().zip(&self.capture.branches) {
            let reference = branch.reference().ok_or(CompactError::MissingRoot)?;
            let context = contexts.and_then(|contexts| contexts.get(reference));
            let (section, used_fallback) = match root {
                CompactBranch::Uncompressed(failure) => (
                    self.render_fallback([backend, session, observation], &failure.references, &failure.failure)?, true,
                ),
                CompactBranch::Projected(root) => match render_root(root, [backend, session, observation], capture.tokens()) {
                    Ok((section, catalogue)) => {
                        if let Some((directory, payload)) = catalogue {
                            directories.push(directory);
                            payloads.push(payload);
                        }
                        (section, false)
                    }
                    Err(error) => {
                        let failure = presentation_failure(error, FailureStage::Terminal)?;
                        let members = branch.member_refs().map_err(CompactError::Source)?;
                        (self.render_fallback([backend, session, observation], &members, &failure)?, true)
                    }
                }
            };
            uncompressed.push(used_fallback);
            append_scoped(&mut parts, section, context, previous).map_err(CompactError::Render)?;
            previous = context;
        }
        let root_parts = parts.len();
        let accounting_parts = self.accounting_parts(&uncompressed);
        let request = self.accounting_request(&accounting_parts);
        for note in accounting::compact_notes_for_request(&request, !uncompressed.contains(&true)).map_err(CompactError::Render)?
        {
            join_part(&mut parts, note, "\n\n");
        }
        let content_directory = (!directories.is_empty()).then(|| (directories.join("\n\n"), payloads.join("\n\n")));
        let body = RenderedSnapshot { parts, content_directory, ..Default::default() };
        Ok(CompactPresentation { body, root_parts, uncompressed })
    }

    fn render_fallback(
        &self, [backend, session, observation]: [&str; 3], members: &[String], failure: &PresentationFailure,
    ) -> Result<RenderedSection, CompactError> {
        let snapshot = &self.capture.capture.snapshot;
        let refs = self.capture.semantic.get("refs").and_then(Value::as_map).ok_or(CompactError::MissingRoot)?;
        let issues = snapshot.observation.get("issues").and_then(Value::as_seq).unwrap_or_default();
        let source = DetailSource::new(
            backend, session, observation, &[], &snapshot.facts, issues,
            refs.iter().filter_map(|(reference, token)| token.as_str().map(|token| (reference.as_str(), token))),
        ).map_err(CompactError::Render)?;
        fallback::render(&source, members, failure).map(|rendered| rendered.section).map_err(CompactError::Render)
    }

    fn accounting_parts(&self, uncompressed: &[bool]) -> Vec<AccountingPart<'_>> {
        self.roots.iter().zip(&self.capture.branches).zip(uncompressed).map(|((root, branch), fallback)| {
            match root.projected() {
                Some(root) if !fallback => AccountingPart::compact(root, &branch.members),
                _ => AccountingPart::uncompressed(&branch.members),
            }
        }).collect()
    }

    fn accounting_request<'s>(&'s self, parts: &'s [AccountingPart<'s>]) -> AccountingRequest<'s> {
        let capture = &self.capture.capture;
        AccountingRequest {
            snapshot: &capture.snapshot, semantic: &self.capture.semantic, tokens: capture.tokens(), parts,
            compact: true, public_api: true, share_descriptions: true,
        }
    }

    pub(crate) fn render_details(&self, rendered: &mut CompactPresentation) -> Result<(), CompactError> {
        let parts = self.accounting_parts(&rendered.uncompressed);
        let accounting = accounting::plan(&self.accounting_request(&parts)).map_err(CompactError::Render)?;
        let body = &mut rendered.body;
        let roots = body.parts[..rendered.root_parts].iter().map(RenderedPart::text).collect::<String>();
        let supplement = accounting.diagnostic_prefixes.into_iter().chain([roots])
            .chain(accounting.full_notes).filter(|part| !part.is_empty()).collect::<Vec<_>>().join("\n\n");
        if let Some((directory, payload)) = &body.content_directory {
            body.details = format!("{directory}\n\n{payload}\n\n{supplement}");
            body.detail_supplement = Some(supplement);
        } else {
            body.details = supplement;
        }
        Ok(())
    }
}

fn presentation_failure(error: CompactError, stage: FailureStage) -> Result<PresentationFailure, CompactError> {
    let detail = match error {
        CompactError::MissingRoot | CompactError::Source(_) | CompactError::Render(TextError::Unsupported(_)) => {
            return Err(error);
        }
        CompactError::Render(TextError::Invalid(detail)) => detail.to_owned(),
        error => format!("{error:?}"),
    };
    Ok(PresentationFailure { error: "ValueError", detail, stage })
}

fn render_root(
    root: &CompactRoot, capture: [&str; 3], tokens: &[String],
) -> Result<(RenderedSection, Option<(String, String)>), CompactError> {
    let GeometricRendered { section, background } = root.render()?;
    let catalogue = if root.groups.groups.is_empty() {
        None
    } else {
        Some((
            group_text::directory(&root.groups, &root.atoms).map_err(CompactError::Render)?,
            group_text::payload(&root.groups, &root.atoms, capture, tokens, &background).map_err(CompactError::Render)?,
        ))
    };
    Ok((section, catalogue))
}

fn join_part(parts: &mut Vec<RenderedPart>, text: String, separator: &str) {
    if !parts.is_empty() {
        parts.push(RenderedPart::Text(separator.to_owned()));
    }
    parts.push(RenderedPart::Text(text));
}
