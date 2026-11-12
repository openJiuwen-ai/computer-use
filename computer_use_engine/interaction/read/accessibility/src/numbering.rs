// Copyright (c) Huawei Technologies Co., Ltd. 2026. All rights reserved.

//! Session reference allocation and the ordered passes that number a read.

use std::collections::HashMap;

#[derive(Clone, Copy, Debug, Eq, Hash, Ord, PartialEq, PartialOrd)]
pub(crate) struct NodeId(pub(crate) usize);

#[derive(Debug, Eq, Hash, PartialEq)]
enum ReferenceKey {
    Native(String),
    Scoped { surface: Option<String>, scope_kind: Option<String>, token: String },
}

/// Each native session and allocator namespace owns a separate instance.
pub(crate) struct SessionAliases {
    session: String,
    known: HashMap<ReferenceKey, usize>,
}

impl SessionAliases {
    pub(crate) fn new(session: String) -> Self {
        Self { session, known: HashMap::new() }
    }

    fn reference(&mut self, key: ReferenceKey) -> usize {
        let next = self.known.len();
        *self.known.entry(key).or_insert(next)
    }

    pub(super) fn session(&self) -> &str {
        &self.session
    }

    pub(super) fn native(&mut self, token: String) -> usize {
        self.reference(ReferenceKey::Native(token))
    }

    fn assign(&mut self, surface: Option<&str>, scope_kind: Option<&str>, token: &str) -> usize {
        self.scoped(surface.map(str::to_owned), scope_kind.map(str::to_owned), token.to_owned())
    }

    pub(super) fn scoped(&mut self, surface: Option<String>, scope_kind: Option<String>, token: String) -> usize {
        self.reference(ReferenceKey::Scoped { surface, scope_kind, token })
    }
}

pub(crate) struct Part<'a> {
    pub(crate) session: &'a str,
    pub(crate) surface: Option<&'a str>,
    pub(crate) scope_kind: Option<&'a str>,
    pub(crate) tokens: &'a [String],
    /// Native node ordinals in the existing source reference order.
    pub(crate) source_order: &'a [NodeId],
}

#[derive(Debug, Eq, PartialEq)]
pub(crate) enum NumberingError<E> {
    ForeignSession,
    MissingNode(NodeId),
    Preview(E),
}

#[derive(Debug, Default, Eq, PartialEq)]
pub(crate) struct PartAliases {
    ordered: Vec<(NodeId, usize)>,
    by_node: HashMap<NodeId, usize>,
}

impl PartAliases {
    pub(super) fn from_native(part: &Part<'_>, refs: &HashMap<String, String>) -> Option<Self> {
        let mut output = Self::default();
        let mut numbers = std::collections::HashSet::new();
        for &node in part.source_order {
            let token = part.tokens.get(node.0)?;
            let reference = refs.get(token)?;
            let number = reference.parse::<usize>().ok()?;
            if number.to_string() != *reference || !numbers.insert(number) {
                return None;
            }
            output.insert(node, number);
        }
        (refs.len() == output.ordered.len()).then_some(output)
    }

    pub(crate) fn ordered(&self) -> &[(NodeId, usize)] {
        &self.ordered
    }

    pub(crate) fn alias(&self, node: NodeId) -> Option<usize> {
        self.by_node.get(&node).copied()
    }

    fn insert(&mut self, node: NodeId, alias: usize) {
        self.by_node.insert(node, alias);
        self.ordered.push((node, alias));
    }

    fn assign<E>(
        &mut self, session: &mut SessionAliases, part: &Part<'_>, nodes: impl IntoIterator<Item = NodeId>,
    ) -> Result<(), NumberingError<E>> {
        for node in nodes {
            if !self.by_node.contains_key(&node) {
                let token = part.tokens.get(node.0).ok_or(NumberingError::MissingNode(node))?;
                let alias = session.assign(part.surface, part.scope_kind, token);
                self.insert(node, alias);
            }
        }
        Ok(())
    }
}

/// Allocate the acquisition reader's aliases. Observed nodes precede the
/// remaining source nodes, even when the latter are earlier in the input.
pub(crate) fn acquisition_aliases(
    session: &mut SessionAliases,
    part: &Part<'_>,
) -> Result<PartAliases, NumberingError<()>> {
    if part.session != session.session {
        return Err(NumberingError::ForeignSession);
    }
    let mut aliases = PartAliases::default();
    for &node in part.source_order {
        if aliases.alias(node).is_none() {
            let token = part.tokens.get(node.0).ok_or(NumberingError::MissingNode(node))?;
            let alias = session.native(token.clone());
            aliases.insert(node, alias);
        }
    }
    Ok(aliases)
}

/// Preview and assign each part before previewing the next. Allocate hidden
/// source references after every visible pass has completed.
pub(crate) fn number_parts<E>(
    session: &mut SessionAliases,
    parts: &[Part<'_>],
    mut preview: impl FnMut(usize, &Part<'_>) -> Result<Vec<NodeId>, E>,
) -> Result<Vec<PartAliases>, NumberingError<E>> {
    if parts.iter().any(|part| part.session != session.session) {
        return Err(NumberingError::ForeignSession);
    }
    let mut aliases = Vec::with_capacity(parts.len());
    for (index, part) in parts.iter().enumerate() {
        let visible = preview(index, part).map_err(NumberingError::Preview)?;
        let mut output = PartAliases::default();
        output.assign(session, part, visible)?;
        aliases.push(output);
    }
    for (part, output) in parts.iter().zip(&mut aliases) {
        output.assign(session, part, part.source_order.iter().copied())?;
    }
    Ok(aliases)
}

#[cfg(test)]
mod tests {
    use super::*;

    fn part<'a>(tokens: &'a [String], surface: Option<&'a str>) -> Part<'a> {
        Part { session: "driver", surface, scope_kind: Some("window"), tokens, source_order: &[NodeId(0), NodeId(1)] }
    }

    #[test]
    fn visible_parts_are_numbered_before_unrendered_references() {
        let mut session = SessionAliases::new("driver".into());
        let tokens = ["root".into(), "row".into()];
        let parts = [part(&tokens, Some("left")), part(&tokens, Some("right"))];
        let aliases = number_parts(&mut session, &parts, |_, _| Ok::<_, ()>(vec![NodeId(1), NodeId(1)])).unwrap();
        assert_eq!(aliases[0].ordered(), &[(NodeId(1), 0), (NodeId(0), 2)]);
        assert_eq!(aliases[1].ordered(), &[(NodeId(1), 1), (NodeId(0), 3)]);

        let repeated = number_parts(&mut session, &parts, |_, _| Ok::<_, ()>(vec![NodeId(0)])).unwrap();
        assert_eq!(repeated[0].ordered(), &[(NodeId(0), 2), (NodeId(1), 0)]);
        assert_eq!(repeated[1].alias(NodeId(0)), Some(3));
    }

    #[test]
    fn a_later_preview_failure_retains_prior_touches_without_allocating_hidden_nodes() {
        let mut session = SessionAliases::new("driver".into());
        let tokens = ["root".into(), "row".into()];
        let parts = [part(&tokens, None), part(&tokens, Some("right"))];
        let stopped = number_parts(&mut session, &parts, |index, _| {
            if index == 0 { Ok(vec![NodeId(1)]) } else { Err("stopped") }
        });
        assert_eq!(stopped, Err(NumberingError::Preview("stopped")));
        assert_eq!(session.known.len(), 1);
        assert_eq!(session.assign(None, Some("window"), "row"), 0);
        assert_eq!(session.assign(None, Some("window"), "root"), 1);
    }

    #[test]
    fn reject_foreign_sessions_before_preview_or_allocation() {
        let mut session = SessionAliases::new("different".into());
        let part = part(&[], None);
        let result = number_parts::<()>(&mut session, &[part], |_, _| panic!("must not preview"));
        assert_eq!(result, Err(NumberingError::ForeignSession));
        assert!(session.known.is_empty());
    }

    #[test]
    fn acquisition_aliases_use_the_observed_first_order_and_unscoped_keys() {
        let mut session = SessionAliases::new("driver".into());
        let tokens = ["unread".into(), "observed".into()];
        let mut capture = part(&tokens, None);
        capture.source_order = &[NodeId(1), NodeId(0)];
        let result = acquisition_aliases(&mut session, &capture).unwrap();
        assert_eq!(result.ordered(), &[(NodeId(1), 0), (NodeId(0), 1)]);
        assert_eq!(session.assign(None, None, "observed"), 2);
        assert_eq!(acquisition_aliases(&mut session, &capture).unwrap(), result);
    }
}
