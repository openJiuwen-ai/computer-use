// Copyright (c) Huawei Technologies Co., Ltd. 2026. All rights reserved.

use super::{
    super::{flag, has_value},
    capture_entries, evidence::{field, seq, uncertain}, outline_role, python_repr, section_key, text_fields,
    units, value_units, Background, BlockId, Classified, GroupError, GroupKind, GroupOverview, NodeId, Result,
    Value, WindowContext,
};
use crate::render::atoms::Format;

pub(super) fn notices(classified: &Classified<'_, '_>, refs: &[NodeId], context: &WindowContext) -> Result<Background> {
    let request = classified.request;
    let mut result = Background {
        window_ref: context.window_ref, blockers: context.confirmed_blockers.clone(),
        directory_complete: context.directory_complete, important_refs: Vec::new(), uncertain_refs: Vec::new(),
        nested_windows: Vec::new(), state_facts: Vec::new(),
    };
    let uncertain = uncertain(&request.snapshot.observation);
    for id in refs {
        let atom = request.atoms.atoms.get(id).ok_or(GroupError::MissingSource(*id))?;
        let view = request.projection.node(*id).ok_or(GroupError::MissingSource(*id))?;
        let native = classified.native(*id).ok_or(GroupError::MissingSource(*id))?;
        let role = field(native, "role");
        let state = |key| native.get("states").and_then(|states| states.get(key));
        let flags = ["error", "invalid", "modified", "unsaved"];
        let supplements = seq(native.get("supplements"));
        let input_target = matches!(role, Some("text_field" | "text_area" | "combo_box" | "search_field" | "spin_button"))
            || flag(state("editable"))
            || native.get("control_value").and_then(|value| value.get("read_only")) == Some(&Value::Bool(false))
            || ["typeText", "replaceText"].iter().any(|key| view.field("capabilities")
                .and_then(|value| field(value, key)) == Some("available"));
        let supplemental = supplements.iter().filter(|item|
            matches!(field(item, "key"), Some("modified" | "unsaved" | "invalid" | "error"))
                && has_value(item.get("value")));
        let error_relation = native.get("relations").and_then(Value::as_map).into_iter().flatten().any(|(_, relation)|
            field(relation, "kind") == Some("error") && !seq(relation.get("targets")).is_empty());
        if flags.iter().chain([&"busy"]).any(|key| has_value(view.field(key))) || flag(state("busy"))
            || flag(state("focused")) && input_target || flag(native.get("text").and_then(|text| text.get("caret_active")))
            || supplemental.clone().next().is_some() || error_relation
        {
            result.important_refs.push(*id);
            result.state_facts.extend(flags.iter().filter_map(|key| view.field(key).filter(|value| has_value(Some(value)))
                .map(|value| (*id, (*key).to_owned(), value.clone()))));
            result.state_facts.extend(supplemental.filter_map(|item| field(item, "key").zip(item.get("value"))
                .map(|(key, value)| (*id, key.to_owned(), value.clone()))));
        }
        for key in ["busy", "focused"] {
            if flag(state(key)) { result.state_facts.push((*id, format!("states.{key}"), Value::Bool(true))); }
        }
        if atom.coverage.partial() || atom.actions_unavailable || atom.slots.iter().any(|slot| slot.format == Format::Unavailable)
            || request.tokens.get(id.0).is_some_and(|token| uncertain.contains(token.as_str()))
        {
            result.uncertain_refs.push(*id);
        }
        if matches!(role, Some("window" | "sheet" | "dialog" | "alert")) { result.nested_windows.push(*id); }
    }
    Ok(result)
}

pub(super) fn candidate(
    classified: &Classified<'_, '_>, parent: BlockId, prefix: usize,
) -> Result<(Option<GroupOverview>, &'static str)> {
    let request = classified.request;
    let Some(context) = request.window.filter(|context|
        context.scope_confirmed && !context.confirmed_blockers.is_empty() && !context.current && context.modal != Some(true))
        else { return Ok((None, "background-requires-fixed-scope-and-confirmed-blocking")); };
    let root = request.interface.block(parent).ok_or(GroupError::MissingBlock(parent))?;
    let Some(reference) = root.primary_ref.filter(|_| !root.children.is_empty())
        else { return Ok((None, "background-root-has-no-native-child-scope")); };
    let Some(token) = request.tokens.get(reference.0) else { return Ok((None, "background-root-is-not-its-bound-native-window")); };
    let native = &request.snapshot.observation;
    if !seq(native.get("roots")).iter().any(|root| root.as_str() == Some(token))
        || !matches!(classified.native(reference).and_then(|node| field(node, "role")), Some("window" | "dialog"))
    {
        return Ok((None, "background-root-is-not-its-bound-native-window"));
    }
    let refs = classified.refs(&root.children)?;
    let recovery = classified.recovery(&refs)?;
    if refs.len() < 12 || recovery.iter().any(|id| classified.native(*id).is_none()) {
        return Ok((None, "background-too-small-or-source-unavailable"));
    }
    let entries = capture_entries(classified, &refs)?;
    let fields = || entries.iter().flat_map(|entry| &entry.fields);
    let background = notices(classified, &refs, context)?;
    let outline: Vec<_> = fields().filter(|item| {
        let role = classified.native(item.source).and_then(|node| field(node, "role"));
        matches!(item.slot.key.as_str(), "name" | "description")
            && (outline_role(role) || matches!(role, Some("window" | "sheet" | "dialog" | "alert")))
    }).cloned().collect();
    let mut full_units = 0;
    for id in &refs {
        let atom = request.atoms.atoms.get(id).ok_or(GroupError::MissingSource(*id))?;
        full_units += (atom.reference.len() + atom.role.len()).div_ceil(4) + 4;
        full_units += atom.slots.iter().map(|slot| value_units(&slot.value) + units(slot.label.unwrap_or_default()) + 1).sum::<usize>();
        full_units += atom.actions.iter().map(|action| units(&action.label) + 3).sum::<usize>();
    }
    let mut overview_units = 110;
    for item in &outline {
        overview_units += (classified.reference(item.source)?.len() + python_repr(&item.slot.value, false).len()).div_ceil(4) + 4;
    }
    for id in background.important_refs.iter().chain(&background.uncertain_refs).chain(&background.nested_windows) {
        overview_units += units(classified.reference(*id)?) + 1;
    }
    for id in &background.important_refs {
        let atom = request.atoms.atoms.get(id).ok_or(GroupError::MissingSource(*id))?;
        overview_units += atom.slots.iter().map(|slot| value_units(&slot.value) + 5).sum::<usize>();
    }
    let recovery_units = prefix + 2 * full_units;
    let saved = full_units.checked_sub(overview_units + (recovery_units as f64 * request.policy.recovery_weight).ceil() as usize);
    let Some(saved) = saved.filter(|saved| *saved >= 64 && (*saved as f64) >= full_units as f64 * 0.2)
        else { return Ok((None, "background-overview-has-insufficient-benefit")); };
    let captured_characters = text_fields(fields())?.iter().map(|source| source.value.as_str().unwrap_or_default().chars().count()).sum();
    let section = section_key("background", &classified.capture, classified.reference(reference)?);
    Ok((Some(GroupOverview {
        section_key: section, capture: classified.capture.clone(), parent, children: root.children.clone(),
        projected_refs: refs, recovery_refs: recovery, entries, outline, head: Vec::new(), tail: Vec::new(),
        kind: GroupKind::Background, captured_characters, captured_records: None,
        full_units, overview_units, recovery_units, estimated_saved_units: saved, background: Some(background),
    }), "beneficial-confirmed-blocked-background"))
}
