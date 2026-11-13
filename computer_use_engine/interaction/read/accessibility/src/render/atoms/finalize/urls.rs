// Copyright (c) Huawei Technologies Co., Ltd. 2026. All rights reserved.

//! Move a URL to details only if every same-caption peer is known and identical.

use std::collections::{HashMap, HashSet};

use crate::{
    numbering::NodeId,
    projection::{FactKey, Projection},
    render::lines::{role, Fields},
    value::Value,
};

use super::super::Disposition;
use super::{complete_slot, AtomFinalizeOptions, AtomPlan, DispositionKind, Result, TextError};

struct Source<'a> {
    token: &'a str,
    scope: NodeId,
    url: Option<&'a str>,
    complete: bool,
}

fn string(value: &str) -> Value {
    Value::Text(value.to_owned())
}

fn map<const N: usize>(items: [(&str, Value); N]) -> Value {
    Value::Map(
        items
            .into_iter()
            .map(|(key, value)| (key.to_owned(), value))
            .collect(),
    )
}

fn capture_sources<'a>(
    projection: &Projection,
    options: &'a AtomFinalizeOptions<'a>,
) -> Result<HashMap<NodeId, Source<'a>>> {
    let Some(snapshot) = options.snapshot else {
        return Ok(HashMap::new());
    };
    let Some(semantic) = options.semantic else {
        return Ok(HashMap::new());
    };
    let refs = semantic
        .get("refs")
        .and_then(Value::as_map)
        .ok_or(TextError::Invalid("URL reference index"))?;
    let mut counts = HashMap::new();
    for (_, token) in refs {
        if let Some(token) = token.as_str() {
            *counts.entry(token).or_insert(0) += 1;
        }
    }
    let refs: HashMap<_, _> = refs
        .iter()
        .filter_map(|(reference, token)| token.as_str().map(|token| (reference.as_str(), token)))
        .collect();
    let facts: HashMap<_, _> = snapshot
        .facts
        .seq("nodes")
        .iter()
        .filter_map(|node| Some((node.text("node")?, node)))
        .collect();
    let native: HashMap<_, _> = snapshot
        .observation
        .get("nodes")
        .and_then(Value::as_map)
        .ok_or(TextError::Invalid("URL native nodes"))?
        .iter()
        .map(|(token, node)| (token.as_str(), node))
        .collect();
    let normalized: HashMap<_, _> = semantic
        .seq("fields")
        .iter()
        .filter_map(|row| {
            (row.text("key") == Some("url")).then_some(())?;
            Some((row.text("ref")?, row.get("value")?))
        })
        .collect();
    let mut global_gap = false;
    let mut blocked = HashSet::new();
    for issue in snapshot.observation.seq("issues") {
        let path = issue.seq("path");
        if path.is_empty()
            || matches!(path, [group] if group.as_str() == Some("nodes"))
                && issue.text("state") != Some("partial")
        {
            global_gap = true;
        }
        if path.len() >= 2
            && path[0].as_str() == Some("nodes")
            && (path.len() == 2 || path[2].as_str() == Some("url"))
        {
            if let Some(token) = path[1].as_str() {
                blocked.insert(token);
            }
        }
    }
    let mut sources = HashMap::new();
    for id in &projection.source_order {
        let node = projection
            .node(*id)
            .ok_or(TextError::Invalid("URL source node"))?;
        let Some(&token) = refs.get(node.reference.as_str()) else {
            continue;
        };
        if options.tokens.get(id.0).map(String::as_str) != Some(token) {
            return Err(TextError::Invalid("URL source identity"));
        }
        let url = native.get(token).and_then(|node| node.text("url"));
        let status = facts
            .get(token)
            .and_then(|node| node.get("fieldStatus"))
            .and_then(|statuses| statuses.get("url"));
        let complete = !global_gap
            && !blocked.contains(token)
            && counts.get(token) == Some(&1)
            && url.is_some()
            && status.is_none()
            && normalized.get(node.reference.as_str()).is_some_and(|row| {
                row.text("field") == Some("url")
                    && row.text("node") == Some(token)
                    && matches!(row.seq("path"), [key] if key.as_str() == Some("url"))
                    && row.text("normalized") == url
            });
        sources.insert(
            *id,
            Source {
                token,
                scope: node.scope,
                url,
                complete,
            },
        );
    }
    Ok(sources)
}

#[derive(Clone)]
pub(crate) struct Caption {
    pub(crate) field: String,
    pub(crate) text: String,
    pub(crate) facts: Vec<FactKey>,
    pub(crate) complete: bool,
}

#[derive(Default)]
pub(crate) struct UrlPlan {
    pub(crate) destinations: Vec<Value>,
    pub(crate) peer_groups: Vec<Value>,
    pub(crate) selected: Vec<NodeId>,
}

fn caption_complete(
    projection: &Projection,
    id: NodeId,
    slot: &super::Slot,
    value: &str,
    options: &AtomFinalizeOptions<'_>,
) -> (Vec<FactKey>, bool) {
    if options
        .disclosure
        .is_some_and(|disclosure| disclosure.get(id, &slot.key).is_some())
        || slot.has_preview()
    {
        return (Vec::new(), false);
    }
    let presented = projection
        .node(id)
        .and_then(|node| node.provenance.iter().find(|(name, _)| name == &slot.key));
    let Some((_, presented)) = presented else {
        return (Vec::new(), false);
    };
    let facts = presented
        .parts
        .iter()
        .flat_map(|part| part.facts.iter().cloned())
        .collect::<Vec<_>>();
    let same = if let Some(separator) = &presented.separator {
        let parts = presented
            .parts
            .iter()
            .map(|part| part.value.as_str())
            .collect::<Option<Vec<_>>>();
        parts.is_some_and(|parts| parts.join(separator) == value)
    } else {
        presented.parts.first().and_then(|part| part.value.as_str()) == Some(value)
    };
    (
        facts,
        same && complete_slot(projection, id, slot, options.evidence),
    )
}

fn context(
    id: NodeId,
    projection: &Projection,
    parents: &HashMap<NodeId, Option<NodeId>>,
    captions: &HashMap<NodeId, Vec<Caption>>,
    contexts: &mut HashMap<NodeId, Option<NodeId>>,
) -> Result<Option<NodeId>> {
    if let Some(context) = contexts.get(&id) {
        return Ok(*context);
    }
    let mut trail = Vec::new();
    let mut current = *parents
        .get(&id)
        .ok_or(TextError::Invalid("URL projected parent"))?;
    while let Some(parent) = current {
        let node = projection
            .node(parent)
            .ok_or(TextError::Invalid("URL peer parent"))?;
        if role(node) != "group"
            || captions.contains_key(&parent)
            || node
                .text("spatialPosition")
                .is_some_and(|value| !value.is_empty())
        {
            break;
        }
        if let Some(context) = contexts.get(&parent) {
            current = *context;
            break;
        }
        trail.push(parent);
        current = *parents
            .get(&parent)
            .ok_or(TextError::Invalid("URL peer hierarchy"))?;
    }
    contexts.insert(id, current);
    contexts.extend(trail.into_iter().map(|parent| (parent, current)));
    Ok(current)
}

type PeerKey = (NodeId, Option<NodeId>, Vec<String>);

fn published_ref(projection: &Projection, id: NodeId) -> Result<&str> {
    projection
        .node(id)
        .map(|node| node.reference.as_str())
        .ok_or(TextError::Invalid("URL reference"))
}

fn scope(projection: &Projection, options: &AtomFinalizeOptions<'_>, id: NodeId) -> Result<Value> {
    let snapshot = options
        .snapshot
        .ok_or(TextError::Invalid("URL source capture"))?;
    let session = snapshot
        .observation
        .text("session")
        .ok_or(TextError::Invalid("URL session"))?;
    let observation = snapshot
        .observation
        .text("id")
        .ok_or(TextError::Invalid("URL observation"))?;
    Ok(Value::Seq(vec![
        string(session),
        string(observation),
        string(published_ref(projection, id)?),
    ]))
}

/// Compare the original URL evidence against captions emitted by a specific
/// renderer. The producer retains ownership of its caption text and field slots.
pub(crate) fn plan_urls(
    projection: &Projection,
    options: &AtomFinalizeOptions<'_>,
    order: &[NodeId],
    parents: &HashMap<NodeId, Option<NodeId>>,
    captions: &HashMap<NodeId, Vec<Caption>>,
    candidates: &[NodeId],
) -> Result<UrlPlan> {
    if !options.compact {
        return Ok(UrlPlan::default());
    }
    let sources = capture_sources(projection, options)?;
    plan_url_sources(
        projection, options, order, parents, captions, candidates, &sources, 0,
    )
}

#[allow(clippy::too_many_arguments)]
fn plan_url_sources(
    projection: &Projection,
    options: &AtomFinalizeOptions<'_>,
    order: &[NodeId],
    parents: &HashMap<NodeId, Option<NodeId>>,
    captions: &HashMap<NodeId, Vec<Caption>>,
    candidates: &[NodeId],
    sources: &HashMap<NodeId, Source<'_>>,
    previous_groups: usize,
) -> Result<UrlPlan> {
    if sources.is_empty() {
        return Ok(UrlPlan::default());
    }
    let mut contexts = HashMap::new();
    let mut groups: HashMap<PeerKey, Vec<NodeId>> = HashMap::new();
    let mut keys = HashMap::new();
    for id in order {
        let (Some(source), Some(fragments)) = (sources.get(id), captions.get(id)) else {
            continue;
        };
        let parent = context(*id, projection, parents, captions, &mut contexts)?;
        let key = (
            source.scope,
            parent,
            fragments
                .iter()
                .map(|caption| caption.text.clone())
                .collect(),
        );
        groups.entry(key.clone()).or_default().push(*id);
        keys.insert(*id, key);
    }
    groups.retain(|_, peers| {
        let first = &sources[&peers[0]];
        first.complete
            && peers.iter().all(|peer| {
                let source = &sources[peer];
                source.complete && source.url == first.url
            })
    });

    let mut published = HashMap::new();
    let mut plan = UrlPlan::default();
    for &id in candidates {
        let node = projection
            .node(id)
            .ok_or(TextError::Invalid("URL candidate node"))?;
        let (Some(source), Some(fragments), Some(key)) =
            (sources.get(&id), captions.get(&id), keys.get(&id))
        else {
            continue;
        };
        if !matches!(role(node), "link" | "image")
            || !source.complete
            || source.url.is_none_or(str::is_empty)
            || source.url != node.text("url")
            || !fragments.iter().any(|fragment| fragment.complete)
        {
            continue;
        }
        let Some(peers) = groups.get(key) else {
            continue;
        };
        let scope = scope(projection, options, key.0)?;
        let index = if let Some(index) = published.get(key) {
            *index
        } else {
            let index = (previous_groups + plan.peer_groups.len()) as i64;
            let context = key
                .1
                .map(|id| published_ref(projection, id))
                .transpose()?
                .map_or(Value::Null, string);
            let caption = Value::Seq(key.2.iter().map(|part| string(part)).collect());
            let peers = peers
                .iter()
                .map(|peer| {
                    let source = &sources[peer];
                    Ok(map([
                        ("ref", string(published_ref(projection, *peer)?)),
                        ("native", string(source.token)),
                        ("url", source.url.map_or(Value::Null, string)),
                        ("readState", string("complete")),
                    ]))
                })
                .collect::<Result<Vec<_>>>()?;
            plan.peer_groups.push(map([
                ("index", Value::Int(index)),
                ("scope", scope.clone()),
                ("context", context),
                ("caption", caption),
                ("peers", Value::Seq(peers)),
            ]));
            published.insert(key.clone(), index);
            index
        };
        let captions = fragments
            .iter()
            .map(|caption| {
                let facts = caption
                    .facts
                    .iter()
                    .map(|fact| {
                        Ok(map([
                            ("ref", string(published_ref(projection, fact.source)?)),
                            ("field", string(&fact.attribute)),
                        ]))
                    })
                    .collect::<Result<Vec<_>>>()?;
                Ok(map([
                    ("field", string(&caption.field)),
                    ("text", string(&caption.text)),
                    ("complete", Value::Bool(caption.complete)),
                    ("facts", Value::Seq(facts)),
                ]))
            })
            .collect::<Result<Vec<_>>>()?;
        plan.destinations.push(map([
            ("ref", string(published_ref(projection, id)?)),
            ("native", string(source.token)),
            ("scope", scope),
            ("path", Value::Seq(vec![string("url")])),
            ("url", source.url.map_or(Value::Null, string)),
            ("readState", string("complete")),
            ("status", Value::Null),
            ("caption", Value::Seq(captions)),
            ("peerGroup", Value::Int(index)),
        ]));
        plan.selected.push(id);
    }
    Ok(plan)
}

pub(super) fn finalize_urls(
    atoms: &mut AtomPlan,
    projection: &Projection,
    options: &AtomFinalizeOptions<'_>,
) -> Result<()> {
    if !options.compact {
        return Ok(());
    }
    let sources = capture_sources(projection, options)?;
    if sources.is_empty() {
        return Ok(());
    }
    let parents: HashMap<_, _> = atoms
        .atoms
        .iter()
        .map(|(id, atom)| (*id, atom.parent))
        .collect();
    let mut captions = HashMap::new();
    let mut candidates = Vec::new();
    for id in &atoms.order {
        let atom = atoms.atoms.get(id).ok_or(TextError::Invalid("URL atom"))?;
        let mut fragments = Vec::new();
        for slot in &atom.slots {
            if slot.key == "url" {
                candidates.push(*id);
            }
            if !matches!(slot.key.as_str(), "name" | "description" | "value") {
                continue;
            }
            let Some(value) = slot.value.as_str().filter(|value| !value.is_empty()) else {
                continue;
            };
            let (facts, complete) = caption_complete(projection, *id, slot, value, options);
            fragments.push(Caption {
                field: slot.key.clone(),
                text: value.to_owned(),
                facts,
                complete,
            });
        }
        if !fragments.is_empty() {
            captions.insert(*id, fragments);
        }
    }
    let plan = plan_url_sources(
        projection,
        options,
        &atoms.order,
        &parents,
        &captions,
        &candidates,
        &sources,
        atoms.deferred_native_url_peers.len(),
    )?;
    atoms.deferred_native_urls.extend(plan.destinations);
    atoms.deferred_native_url_peers.extend(plan.peer_groups);
    for id in plan.selected {
        let atom = atoms
            .atoms
            .get_mut(&id)
            .ok_or(TextError::Invalid("selected URL atom"))?;
        let index = atom
            .slots
            .iter()
            .position(|slot| slot.key == "url")
            .ok_or(TextError::Invalid("selected URL slot"))?;
        let slot = atom.slots.remove(index);
        atom.dispositions.retain(|item| item.slot != "url");
        atom.dispositions
            .extend(slot.facts.into_iter().map(|fact| Disposition {
                fact,
                target: id,
                slot: "url".to_owned(),
                kind: DispositionKind::Details,
            }));
        atom.claims
            .retain(|claim| claim.fact.source != id || claim.fact.attribute != "url");
    }
    Ok(())
}
