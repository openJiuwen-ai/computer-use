// Copyright (c) Huawei Technologies Co., Ltd. 2026. All rights reserved.

//! Build comparison evidence directly from the plans retained for rendering.

use std::collections::{HashMap, HashSet};

use super::{
    compare_rows, covers_selection, pair, sequence, ComparisonEvidence, ComparisonRoot, CurrentTree, Digest,
    DisplayOptions, InvalidPreparedComparison, Projection, Result, Scope, Signatures, Value,
};
use crate::{
    accounting::inactive_selection_intervals,
    capture::PreparedCapture,
    compact::{CompactBranch, CompactRoot},
    content::MemberMode,
    disclosure::groups::GroupKind,
    geometric::{BlockId, GeometricPlan},
    geometry::outside_window_fields,
    numbering::NodeId,
    render::atoms::{ClaimKind, Placement},
};

fn string(value: &str) -> Value { Value::Text(value.to_owned()) }

fn number<T>(value: T) -> Value
where
    T: Copy + ToString,
    i64: TryFrom<T>,
{
    i64::try_from(value).map(Value::Int).unwrap_or_else(|_| Value::BigInt(value.to_string()))
}

fn reference(projection: &Projection, id: NodeId) -> Result<&str> {
    projection.node(id).map(|node| node.reference.as_str())
        .ok_or(InvalidPreparedComparison::Evidence("comparison plan reference"))
}

fn references(projection: &Projection, refs: &[NodeId]) -> Result<Value> {
    Ok(Value::Seq(refs.iter().map(|id| reference(projection, *id).map(string)).collect::<Result<_>>()?))
}

fn optional_reference(projection: &Projection, id: Option<NodeId>) -> Result<Value> {
    id.map(|id| reference(projection, id).map(string)).transpose().map(|value| value.unwrap_or(Value::Null))
}

fn block_key(interface: &GeometricPlan, id: BlockId) -> Result<&str> {
    interface.block(id).map(|block| block.key.as_str())
        .ok_or(InvalidPreparedComparison::Evidence("comparison layout block"))
}

fn keys(interface: &GeometricPlan, ids: &[BlockId]) -> Result<Value> {
    Ok(Value::Seq(ids.iter().map(|id| block_key(interface, *id).map(string)).collect::<Result<_>>()?))
}

fn block_signatures(root: &CompactRoot, out: &mut HashMap<String, Digest>) -> Result<()> {
    enum Visit { Enter(BlockId), Exit(BlockId) }
    let interface = &root.interface;
    let projection = &root.projection;
    let patterns: HashMap<_, _> = interface.geometric_patterns.iter().map(|pattern| (pattern.owner, pattern)).collect();
    let mut path = Vec::new();
    let mut active = HashSet::new();
    let mut pending: Vec<_> = interface.roots.iter().rev().copied().map(Visit::Enter).collect();
    while let Some(visit) = pending.pop() {
        let id = match visit {
            Visit::Enter(id) => id,
            Visit::Exit(id) => {
                path.pop();
                active.remove(&id);
                continue;
            }
        };
        if !active.insert(id) { return Err(InvalidPreparedComparison::Evidence("cyclic comparison layout")); }
        let block = interface.block(id).ok_or(InvalidPreparedComparison::Evidence("comparison layout block"))?;
        let member_roles = block.member_roles.iter().map(|(id, role)| Ok(Value::Seq(vec![
            string(reference(projection, *id)?), string(role),
        ]))).collect::<Result<_>>()?;
        let mut parts = vec![
            pair("key", string(&block.key)),
            pair("kind", string(block.kind)),
            pair("layout", string(block.layout)),
            pair("primary_ref", optional_reference(projection, block.primary_ref)?),
            pair("source_refs", references(projection, &block.source_refs)?),
            pair("children", if block.primary_ref.is_none() { keys(interface, &block.children)? } else { Value::Seq(Vec::new()) }),
            pair("origin", string(block.origin)),
            pair("position", block.position.as_deref().map_or(Value::Null, string)),
            pair("label_ref", optional_reference(projection, block.label_ref)?),
            pair("shared_prefix_ref", optional_reference(projection, block.shared_prefix_ref)?),
            pair("columns", Value::Seq(block.columns.iter().map(|column| string(column)).collect())),
            pair("column_prefix_refs", Value::Seq(block.column_prefix_refs.iter().map(|id| optional_reference(projection, *id)).collect::<Result<_>>()?)),
            pair("region_role", block.region_role.map_or(Value::Null, string)),
            pair("field_target_ref", optional_reference(projection, block.field_target_ref)?),
            pair("member_roles", Value::Seq(member_roles)),
            pair("heading_style", string(block.heading_style)),
            pair("visual_unit", if block.visual_unit { string("control") } else { Value::Null }),
            pair("column_slots", Value::Seq(block.column_slots.iter().map(|slot| slot.map_or(Value::Null, number)).collect())),
            pair("column_alignments", Value::Seq(block.column_alignments.iter().map(|alignment| string(alignment)).collect())),
        ];
        if let Some(pattern) = patterns.get(&id) {
            let rows = pattern.rows.iter().map(|row| Ok(Value::Seq(vec![
                string(&row.key), optional_reference(projection, row.reference)?, keys(interface, &row.members)?,
                Value::Seq(row.slots.iter().copied().map(number).collect()),
            ]))).collect::<Result<_>>()?;
            parts.extend([
                pair("pattern_kind", string(pattern.kind)),
                pair("pattern_columns", number(pattern.column_count)),
                pair("pattern_rows", Value::Seq(rows)),
                pair("pattern_remaining", keys(interface, &pattern.remaining_keys)?),
            ]);
        }
        path.push(Signatures::default().value(&Value::Seq(parts)));
        let digest = sequence(&path);
        for id in &block.source_refs {
            out.insert(reference(projection, *id)?.to_owned(), digest);
        }
        pending.push(Visit::Exit(id));
        pending.extend(block.children.iter().rev().copied().map(Visit::Enter));
    }
    Ok(())
}

fn readable_signatures(root: &CompactRoot, out: &mut HashMap<String, Value>) -> Result<()> {
    for (id, style) in &root.readable.nodes {
        let projection = &root.projection;
        let style = Value::Seq(vec![
            Value::Seq(style.note_slots.iter().map(|key| string(key)).collect()),
            Value::Seq(style.block_slots.iter().map(|key| string(key)).collect()),
            Value::Seq(style.note_actions.iter().copied().map(number).collect()),
            style.toggle_slot.map_or(Value::Null, string),
            style.glyph.map_or(Value::Null, string),
            Value::Bool(style.notes_only),
            references(projection, &style.visual_row)?,
        ]);
        let headings = root.readable.headings.iter().filter(|(_, heading)| heading.reference == *id || heading.sources.contains(id))
            .map(|(key, heading)| Ok(Value::Seq(vec![
                string(block_key(&root.interface, *key)?),
                string(reference(projection, heading.reference)?),
                heading.caption.as_ref().map_or(Value::Null, |slot| Value::Seq(vec![string(&slot.key), slot.value.clone()])),
                references(projection, &heading.sources)?,
                heading.shared_index.map_or(Value::Null, number),
                Value::Bool(heading.transparent),
            ]))).collect::<Result<_>>()?;
        out.insert(reference(projection, *id)?.to_owned(), Value::Seq(vec![style, Value::Seq(headings)]));
    }
    Ok(())
}

fn group_signatures(root: &CompactRoot, out: &mut HashMap<String, Value>) -> Result<()> {
    let projection = &root.projection;
    for group in &root.groups.groups {
        let fragments = group.head.iter().chain(&group.tail).map(|part| Ok(Value::Seq(vec![
            string(reference(projection, part.source.source)?), string(&part.source.attribute),
            number(part.start), number(part.end),
        ]))).collect::<Result<_>>()?;
        let outline = group.outline.iter().map(|entry| Ok(Value::Seq(vec![
            string(reference(projection, entry.source)?), string(&entry.slot.key),
        ]))).collect::<Result<_>>()?;
        let mut signature = vec![
            string(match group.kind { GroupKind::Content => "content", GroupKind::Records => "records", GroupKind::Background => "background" }),
            references(projection, &group.projected_refs)?,
            group.captured_records.map_or(Value::Null, number),
            Value::Seq(fragments), Value::Seq(outline),
        ];
        if let Some(background) = &group.background {
            let states = background.state_facts.iter().map(|(source, key, value)| Ok(Value::Seq(vec![
                string(reference(projection, *source)?), string(key), value.clone(),
            ]))).collect::<Result<_>>()?;
            signature.extend([
                number(background.window_ref),
                Value::Seq(background.blockers.iter().copied().map(number).collect()),
                Value::Bool(background.directory_complete),
                references(projection, &background.important_refs)?,
                references(projection, &background.uncertain_refs)?,
                references(projection, &background.nested_windows)?,
                Value::Seq(states),
            ]);
        } else if group.kind == GroupKind::Background {
            return Err(InvalidPreparedComparison::Evidence("background comparison facts"));
        }
        let signature = Value::Seq(signature);
        for id in &group.projected_refs {
            out.insert(reference(projection, *id)?.to_owned(), signature.clone());
        }
    }
    Ok(())
}

fn field_signatures(root: &CompactRoot, out: &mut HashMap<String, Value>) -> Result<()> {
    for field in &root.fields.fields {
        let target = reference(&root.projection, field.target)?;
        let slots = field.slots.iter().map(|slot| Value::Seq(vec![
            string(&slot.key), slot.label.map_or(Value::Null, string), slot.value.clone(), string(slot.format.name()),
            string(match slot.placement { Placement::Caption => "caption", Placement::Inline => "inline", Placement::Block => "block" }),
        ])).collect();
        out.insert(target.to_owned(), Value::Seq(slots));
        for member in &field.members {
            let mode = match member.mode {
                MemberMode::Complete => "complete",
                MemberMode::ActionSource => "action-source",
                MemberMode::Composed => "composed",
                MemberMode::CaptionReference => "caption-reference",
            };
            let slots = member.consumed_slots.iter().map(|slot| string(slot)).collect();
            out.insert(reference(&root.projection, member.source)?.to_owned(), Value::Seq(vec![
                string(target), string(mode), Value::Seq(slots),
            ]));
        }
    }
    Ok(())
}

pub(crate) fn prepare_compact_comparison(
    scope: Scope,
    options: DisplayOptions,
    indent: i64,
    capture: &PreparedCapture,
    roots: &[CompactBranch],
) -> Result<CurrentTree> {
    if roots.len() != capture.branches.len() {
        return Err(InvalidPreparedComparison::Evidence("comparison root inventory"));
    }
    let snapshot = &capture.capture.snapshot;
    let use_inactive = roots.iter().filter_map(CompactBranch::projected).any(|root| {
        let options = &root.projection.options;
        options.compact && options.public_api && options.share_descriptions
    });
    let inactive = use_inactive.then(|| inactive_selection_intervals(snapshot, &capture.semantic, capture.capture.tokens()))
        .transpose().map_err(|_| InvalidPreparedComparison::Evidence("comparison selection summary"))?;
    let mut evidence = ComparisonEvidence::default();
    let invalid = || InvalidPreparedComparison::Evidence("comparison root inventory");
    let members = capture.branches.iter().map(|branch| branch.member_refs().map_err(|_| invalid()))
        .collect::<Result<Vec<_>>>()?;
    let mut comparison_roots = Vec::with_capacity(roots.len());
    for ((root, original), members) in roots.iter().zip(&capture.branches).zip(&members) {
        let reference_id = original.reference().ok_or_else(invalid)?;
        let projected = root.projected();
        comparison_roots.push(ComparisonRoot {
            reference: reference_id, members, projection: projected.map(|root| root.projection.as_ref()),
        });
        let version = projected.map(|_| "geometric-2".to_owned());
        evidence.versions.insert(reference_id.to_owned(), version);
        let Some(root) = projected else { continue };
        let projection = &root.projection;
        block_signatures(root, &mut evidence.layouts)?;
        readable_signatures(root, &mut evidence.readable_layouts)?;
        group_signatures(root, &mut evidence.group_layouts)?;
        field_signatures(root, &mut evidence.field_layouts)?;
        let options = &projection.options;
        if options.compact && options.public_api && options.share_descriptions {
            if capture.semantic.get("scope_kind").and_then(Value::as_str) == Some("window") {
                for id in outside_window_fields(&snapshot.observation, &capture.geometry, projection.root) {
                    if let Some(node) = projection.node(id) { evidence.outside.insert(node.reference.clone()); }
                }
            }
            let inactive = inactive.as_ref().expect("inactive proof requested for compact root");
            for atom in root.atoms.atoms.values() {
                if let Some(interval) = inactive.get(&atom.reference) {
                    if atom.claims.iter().any(|claim| {
                        claim.kind == ClaimKind::TextSelectionSummary && claim.fact.attribute == "selectionRange"
                            && claim.fact.source == atom.id && claim.target == atom.id && covers_selection(interval, &claim.value)
                    }) {
                        evidence.selected.insert(atom.reference.clone());
                    }
                }
            }
        }
    }
    compare_rows(scope, options, indent, &capture.semantic, &snapshot.facts, &comparison_roots, evidence, inactive.as_ref())
}

#[cfg(test)]
mod tests {
    use super::*;
    use crate::{
        capture::{FailedPreparation, ReadCapture},
        compact::CompactSnapshot,
        geometric::GeometricContext,
        numbering::SessionAliases,
        projection::ProjectionOptions,
        render::fallback::{FailureStage, PresentationFailure},
        wire::decode_json,
    };

    #[test]
    fn compact_comparison_reuses_the_exact_typed_block_paths() {
        let raw = decode_json(br#"{"kind":"observation","session":"driver","platform":"macos","roots":["window"],
        "nodes":[{"id":"window","source":"macos.ax","kind":"element","nativeType":"AXUIElement","properties":[
          {"key":["macos.ax","AXRole"],"value":{"nativeType":"CFString","data":"AXWindow"}},
          {"key":["macos.ax","AXTitle"],"value":{"nativeType":"CFString","data":"Comparison fixture"}}]}],
        "read":{"intervalNs":[1,2],"details":[["surface",{"nativeType":"CFString","data":"window-surface"}],
          ["observation_id",{"nativeType":"CFString","data":"capture"}]]}}"#).unwrap();
        let capture = ReadCapture::from_wire_value(&raw, Some("window".to_owned()), Vec::new()).unwrap();
        let mut reader = SessionAliases::new("driver".to_owned());
        let aliases = capture.initial_aliases(&mut reader).unwrap();
        let prepared = capture.prepare(&aliases, ProjectionOptions {
            compact: true, public_api: true, defer_visual_composition: true, ..Default::default()
        }).unwrap();
        let mut compact = CompactSnapshot::prepare(prepared, None, 0).unwrap();
        let root = compact.roots[0].projected().unwrap();
        let geometry = capture.geometry();
        let context = GeometricContext {
            projection: &root.projection, geometry: &geometry, facts: Some(&capture.snapshot), atoms: None,
        };
        let interface = root.interface.to_value(&context).unwrap();
        let mut from_records = HashMap::new();
        super::super::layout_signatures(&interface, &mut from_records).unwrap();
        let mut from_typed = HashMap::new();
        block_signatures(root, &mut from_typed).unwrap();
        assert_eq!(from_typed, from_records);

        let options = DisplayOptions { compact: true, public_api: true, details: false };
        let current = compact.comparison(vec![Some("window-surface".to_owned())], options, 2).unwrap();
        assert_eq!(current.comparison.nodes.len(), 1);
        assert_eq!(current.comparison.nodes[0].depth, 2);
        assert_eq!(current.roots[0].members, ["0"]);
        let mut fields = root.projection.node(root.projection.root).unwrap().fields.clone();
        fields.extend([
            ("sourceRefs".to_owned(), Value::Seq(vec![string("0")])),
            ("textSources".to_owned(), decode_json(br#"[{"sourceRef":"0","attribute":"name","field":"name","text":"Comparison fixture"}]"#).unwrap()),
        ]);
        let expected = Signatures::default().capture_with_layout(&fields, from_records.get("0").copied());
        assert_eq!(current.comparison.nodes[0].signature, expected.0);
        assert_eq!(current.comparison.nodes[0].content, expected.1);

        assert!(!current.comparison.failed);
        compact.roots[0] = CompactBranch::Uncompressed(FailedPreparation {
            references: vec!["0".to_owned()],
            failure: PresentationFailure {
                error: "ValueError",
                detail: "original projection contract".to_owned(),
                stage: FailureStage::Preparation,
            },
        });
        let fallback = compact.comparison(vec![Some("window-surface".to_owned())], options, 2).unwrap();
        assert!(fallback.comparison.failed);
        assert_eq!(fallback.comparison.versions.get("0"), Some(&None));
        assert_eq!(fallback.roots[0].members, ["0"]);
        assert_eq!(fallback.comparison.nodes[0].depth, 2);
        assert_eq!(fallback.comparison.nodes[0].parent, None);
        assert_ne!(fallback.comparison.nodes[0].signature, expected.0);
    }
}
