// Copyright (c) Huawei Technologies Co., Ltd. 2026. All rights reserved.

//! Terminal line layout for diagnostic and non-geometric captured presentations.

use std::collections::HashSet;

use super::{
    accounting::{self, AccountingPart, AccountingRequest},
    capture::PreparedCapture,
    diff::{self, CurrentTree, DisplayOptions, InvalidPreparedComparison, PreparedRoot, Scope},
    line_layout::{self, LayoutFrame, LayoutMode},
    projection::{Projection, ProjectionError, ProjectionOptions},
    render::{
        atoms::{plan_urls, AtomFinalizeOptions, Claim},
        details::DetailSource,
        fallback::{self, FailureStage, PresentationFailure},
        lines::{render_projection, LineOptions},
        RenderedPart, RenderedSection, RenderedSnapshot, TextError,
    },
    value::Value,
};

mod evidence;
mod line_inputs;

type Result<T> = std::result::Result<T, TextError>;

pub(crate) struct LegacySnapshot {
    capture: PreparedCapture,
}

struct LegacyRoot {
    section: RenderedSection,
    claims: Option<Vec<Claim>>,
    deferred_urls: Vec<Value>,
    published_columns: Vec<Value>,
}

impl LegacySnapshot {
    pub(crate) fn new(capture: PreparedCapture) -> Self {
        Self { capture }
    }

    pub(crate) fn projections(&self) -> impl Iterator<Item = &Projection> {
        self.capture.projections()
    }

    pub(crate) fn visible_refs(&self) -> std::result::Result<Vec<&str>, ProjectionError> {
        self.capture.visible_refs()
    }

    pub(crate) fn has_uncompressed_preparation(&self) -> bool {
        self.capture.branches.iter().any(|branch| branch.projection.is_err())
    }

    pub(crate) fn options(&self) -> &ProjectionOptions { &self.capture.options }

    pub(crate) fn inline_child_refs(&self) -> Result<Vec<&str>> {
        if !self.options().compact { return Ok(Vec::new()); }
        let mut output = Vec::new();
        for root in self.projections() {
            for reference in root.visible_order().map_err(|_| TextError::Invalid("legacy visible references"))? {
                let node = root.node(reference).ok_or(TextError::Invalid("legacy visible node"))?;
                let number = |name| node.field(name).and_then(Value::as_i64);
                let count = number("childCount").unwrap_or_default();
                let read = number("childrenRead").unwrap_or(node.native_children.len() as i64);
                let start = number("childrenStart");
                let unexpanded_menu = node.field("role").and_then(Value::as_str) == Some("menuBarItem")
                    && node.children.is_empty() && start == Some(0) && number("childrenEnd") == Some(0) && count > 0;
                let partial = node.field("partialReason").is_some_and(|value| !matches!(value, Value::Null)) || read < count;
                if unexpanded_menu || partial && start.is_some() && node.field("nextChildOffset").is_some() {
                    output.push(node.reference.as_str());
                }
            }
        }
        output.sort_unstable();
        Ok(output)
    }

    pub(crate) fn comparison(
        &self, scope: Scope, options: DisplayOptions, indent: i64,
    ) -> std::result::Result<CurrentTree, InvalidPreparedComparison> {
        let missing = || InvalidPreparedComparison::Evidence("legacy comparison root inventory");
        let outside = HashSet::new();
        let members = self.capture.branches.iter().map(|branch| {
            branch.member_refs().map_err(|_| missing())
        }).collect::<std::result::Result<Vec<_>, _>>()?;
        let roots = self.capture.branches.iter().zip(&members).map(|(branch, members)| {
            let reference = branch.reference().ok_or_else(missing)?;
            Ok(PreparedRoot {
                reference, members, projection: branch.projection.as_deref().ok(), interface: None,
                readable: None, groups: None, fields: None, atoms: None, outside_window_fields: &outside,
            })
        }).collect::<std::result::Result<Vec<_>, _>>()?;
        diff::prepare_comparison_tree(
            scope, options, indent, &self.capture.semantic, &self.capture.capture.snapshot.facts, &roots, None,
        )
    }

    fn render_root(&self, root: &Projection) -> Result<LegacyRoot> {
        let capture = &self.capture.capture;
        let snapshot = &capture.snapshot;
        let semantic = &self.capture.semantic;
        let options = &root.options;
        let tokens = capture.tokens();
        let focused = options.focused.and_then(|id| root.node(id)).map(|node| node.reference.as_str());
        let subtree = options.subtree.and_then(|id| root.node(id)).map(|node| node.reference.as_str());
        let selection = if options.compact {
            accounting::selection_summary_refs(snapshot, semantic, root, tokens)?
        } else {
            HashSet::new()
        };
        let summarized = selection.iter().filter_map(|id| root.node(*id)).map(|node| node.reference.clone()).collect();
        let readable = readable_text(semantic, &snapshot.facts);
        let mut seed = render_projection(root, &LineOptions {
            compact: options.compact, public_api: options.public_api, defer_identifiers: options.compact,
            record_fields: true, focused, subtree, readable_text: Some(&readable),
            summarized_selections: Some(&summarized),
            compression_evidence: options.compact.then_some(&self.capture.evidence),
            ..LineOptions::default()
        })?;
        line_inputs::populate_seed(&mut seed, root, focused);
        let empty_values = std::mem::take(&mut seed.empty_value_slots);
        line_inputs::populate_empty_values(&mut seed, root, focused, &empty_values);
        let deferred_urls = if options.compact && !seed.url_candidates.is_empty() {
            let mut urls = plan_urls(
                root, &AtomFinalizeOptions {
                    compact: true, snapshot: Some(snapshot), semantic: Some(semantic), tokens, ..Default::default()
                },
                &seed.url_order, &seed.url_parents, &seed.url_captions, &seed.url_candidates,
            )?;
            seed.defer_urls(&urls.selected)?;
            for destination in &mut urls.destinations {
                if let Value::Map(fields) = destination {
                    fields.push(("property".to_owned(), Value::Text("url".to_owned())));
                }
            }
            urls.destinations
        } else { Vec::new() };
        let mode = if subtree.is_some() { LayoutMode::Unpacked }
            else if options.compact { LayoutMode::Compact }
            else { LayoutMode::Full };
        let evidence = if mode == LayoutMode::Compact {
            evidence::capture_action_sources(&mut seed, root, snapshot, tokens);
            evidence::collect(snapshot, semantic, root, tokens)
        } else {
            evidence::PackingEvidence::default()
        };
        let frame = LayoutFrame {
            lines: std::mem::take(&mut seed.lines), mode, public_api: options.public_api,
            record_stages: options.trace, roots: &seed.roots, rendered_nodes: &seed.section.nodes,
            node_prefixes: &seed.node_prefixes, unexpanded_menus: &seed.unexpanded_menus,
            node_line_ranges: &seed.node_ranges, child_hints: &seed.child_hints,
            empty_value_lines: &seed.empty_values, action_sources: &seed.action_sources,
            text_parts: &seed.text_parts, identifier_sources: &evidence.identifiers,
            text_slice_sources: &evidence.text, table_evidence: &evidence.table,
            native_tables: &evidence.tables, field_prefix_sources: &evidence.fields, role_sources: &evidence.roles,
        };
        let packed = line_layout::pack(frame).map_err(|_| TextError::Invalid("native line layout reference"))?;
        let claims = std::mem::take(&mut seed.claims);
        let section = seed.finish(packed.lines);
        Ok(LegacyRoot { section, claims: Some(claims), deferred_urls, published_columns: packed.published_table_columns })
    }

    fn fallback_root(&self, members: &[String], failure: &PresentationFailure) -> Result<LegacyRoot> {
        let capture = &self.capture.capture;
        let snapshot = &capture.snapshot;
        let source = DetailSource::new(
            text(&snapshot.observation, "backend")?, text(&snapshot.observation, "session")?,
            text(&snapshot.observation, "id")?, &[], &snapshot.facts, seq(snapshot.observation.get("issues")),
            entries(self.capture.semantic.get("refs")).iter()
                .filter_map(|(reference, token)| token.as_str().map(|token| (reference.as_str(), token))),
        )?;
        let rendered = fallback::render(&source, members, failure)?;
        Ok(LegacyRoot {
            section: rendered.section, claims: None, deferred_urls: Vec::new(), published_columns: Vec::new(),
        })
    }

    pub(crate) fn render(&self, details: bool) -> Result<RenderedSnapshot> {
        let capture = &self.capture.capture;
        let semantic = &self.capture.semantic;
        let options = self.options();
        let mut roots = Vec::with_capacity(self.capture.branches.len());
        for branch in &self.capture.branches {
            let root = match &branch.projection {
                Err(failure) => self.fallback_root(&failure.references, &failure.failure)?,
                Ok(projection) => match self.render_root(projection) {
                    Ok(root) => root,
                    Err(error) => {
                        let (error, detail) = match error {
                            TextError::Invalid("nonfinite literal") => ("ValueError", "Out of range float values are not JSON compliant"),
                            TextError::Unsupported("binary JSON value") => ("TypeError", "Cannot serialize bytes"),
                            TextError::Invalid(detail) => ("ValueError", detail),
                            error @ TextError::Unsupported(_) => return Err(error),
                        };
                        let failure = PresentationFailure {
                            error, detail: detail.to_owned(), stage: FailureStage::Terminal,
                        };
                        let members = branch.member_refs().map_err(|_| TextError::Invalid("legacy source members"))?;
                        self.fallback_root(&members, &failure)?
                    }
                },
            };
            roots.push(root);
        }
        let accounting_parts: Vec<_> = self.capture.branches.iter().zip(&roots)
            .map(|(branch, root)| AccountingPart {
                members: &branch.members, projection: branch.projection.as_deref().ok(),
                atoms: None, claims: root.claims.as_deref().unwrap_or_default(),
                fields: None, collections: None, disclosure: None, groups: None, sharing: None, text_slices: None,
                deferred_native_fields: &root.deferred_urls, published_columns: &root.published_columns,
                uncompressed: root.claims.is_none(),
            }).collect();
        let compact = options.compact;
        let plan = accounting::plan(&AccountingRequest {
            snapshot: &capture.snapshot, semantic, tokens: capture.tokens(), parts: &accounting_parts,
            compact, public_api: options.public_api, share_descriptions: options.share_descriptions,
        })?;
        drop(accounting_parts);
        let mut body = RenderedSnapshot::default();
        let mut previous = None;
        for (branch, root) in self.capture.branches.iter().zip(roots) {
            let reference = branch.reference().ok_or(TextError::Invalid("legacy root"))?;
            let context = semantic.get("root_contexts").and_then(|contexts| contexts.get(reference));
            fallback::append_scoped(&mut body.parts, root.section, context, previous)?;
            previous = context;
        }
        if details {
            body.details = plan.diagnostic_prefixes.into_iter().chain([body.text()])
                .chain(plan.full_notes.iter().cloned()).filter(|part| !part.is_empty()).collect::<Vec<_>>().join("\n\n");
        }
        let notes = if compact { plan.compact_notes } else { plan.full_notes };
        for note in notes {
            if !body.parts.is_empty() {
                body.parts.push(RenderedPart::Text("\n\n".to_owned()));
            }
            body.parts.push(RenderedPart::Text(note));
        }
        if let Some((full, incremental)) = plan.table_warning {
            if !body.parts.is_empty() {
                body.parts.push(RenderedPart::Text("\n\n".to_owned()));
            }
            body.parts.push(RenderedPart::Literal { full, incremental });
        }
        Ok(body)
    }
}

fn entries(value: Option<&Value>) -> &[(String, Value)] {
    value.and_then(Value::as_map).unwrap_or_default()
}

fn seq(value: Option<&Value>) -> &[Value] {
    value.and_then(Value::as_seq).unwrap_or_default()
}

fn text<'a>(value: &'a Value, key: &str) -> Result<&'a str> {
    value.get(key).and_then(Value::as_str).ok_or(TextError::Invalid("captured observation identity"))
}

fn readable_text(semantic: &Value, facts: &Value) -> HashSet<String> {
    use std::collections::HashMap;

    let source_rows: HashMap<_, _> = seq(facts.get("nodes")).iter()
        .filter_map(|row| Some((row.get("node")?.as_str()?, row))).collect();
    entries(semantic.get("value_fields")).iter().filter_map(|(reference, field)| {
        let field = field.as_str()?;
        let token = semantic.get("refs")?.get(reference)?.as_str()?;
        let source = source_rows.get(token)?;
        source.get("values")?.get(field)?.as_str()?;
        let status = source.get("fieldStatus").and_then(|statuses| statuses.get(field));
        (status.is_none_or(|status| status.get("textRef").is_some())).then(|| reference.clone())
    }).collect()
}

#[cfg(test)]
mod tests {
    use super::*;
    use crate::{capture::ReadCapture, numbering::SessionAliases, wire::decode_json};

    #[test]
    fn compact_line_defers_the_proven_native_url_without_losing_diagnostic_details() {
        let raw = decode_json(r#"{"kind":"observation","session":"driver","platform":"macos","roots":["image"],
          "nodes":[{"id":"image","source":"macos.ax","kind":"element","nativeType":"AXUIElement","properties":[
            {"key":["macos.ax","AXRole"],"value":{"nativeType":"CFString","data":"AXImage"}},
            {"key":["macos.ax","AXDescription"],"value":{"nativeType":"CFString","data":"画面"}},
            {"key":["macos.ax","AXURL"],"value":{"nativeType":"CFURL","data":"https://example.test/image"}}
          ]}],"read":{"intervalNs":[1,2],"details":[
            ["surface",{"nativeType":"CFString","data":"surface"}],
            ["observation_id",{"nativeType":"CFString","data":"capture"}]
          ]}}"#.as_bytes()).unwrap();
        let capture = ReadCapture::from_wire_value(&raw, Some("window".to_owned()), Vec::new()).unwrap();
        let aliases = capture.initial_aliases(&mut SessionAliases::new("driver".to_owned())).unwrap();
        for compact in [true, false] {
            let capture = capture.prepare(&aliases, ProjectionOptions {
                compact, public_api: true, share_descriptions: false, ..Default::default()
            }).unwrap();
            let rendered = LegacySnapshot::new(capture).render(true).unwrap();
            let plain = "0 image Description: 画面, Secondary Actions: <unavailable>";
            if compact {
                assert_eq!(rendered.text(), plain);
                assert!(rendered.details.contains("\n\t0 url: \"https://example.test/image\""));
            } else {
                assert!(rendered.text().starts_with(&format!("{plain}, URL: https://example.test/image\n")));
                assert!(!rendered.details.contains("\n\t0 url:"));
            }
        }
    }
}
