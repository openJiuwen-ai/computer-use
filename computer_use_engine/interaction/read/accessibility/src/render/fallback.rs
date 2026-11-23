// Copyright (c) Huawei Technologies Co., Ltd. 2026. All rights reserved.

//! The terminal fallback over already verified branch membership. Acquisition,
//! source-identity validation, and budget errors belong to the caller.

use std::collections::HashSet;

use super::{details::DetailSource, indent, quoted, RenderedPart, RenderedSection, TextError};
use crate::value::Value;

#[derive(Clone, Copy, Debug, Eq, PartialEq)]
pub(crate) enum FailureStage {
    Preparation,
    Terminal,
}

#[derive(Clone, Debug, Eq, PartialEq)]
pub(crate) struct PresentationFailure {
    pub(crate) error: &'static str,
    pub(crate) detail: String,
    pub(crate) stage: FailureStage,
}

#[derive(Debug, PartialEq)]
pub(crate) struct RenderedFallback {
    pub(crate) section: RenderedSection,
    pub(crate) issue: Value,
}

impl RenderedFallback {
    /// The public root projection carries the same ordered membership and issue
    /// as its terminal document, independently of the snapshot's other roots.
    pub(crate) fn record(&self) -> Value {
        let refs = self
            .section
            .nodes
            .iter()
            .map(|node| Value::Text(node.reference.clone()))
            .collect();
        Value::Map(vec![
            ("text".to_owned(), Value::Text(self.section.text.clone())),
            ("refs".to_owned(), Value::Seq(refs)),
            ("presentation".to_owned(), Value::Text("uncompressed".to_owned())),
            ("issue".to_owned(), self.issue.clone()),
        ])
    }
}

pub(crate) fn render(
    source: &DetailSource<'_>,
    members: &[String],
    failure: &PresentationFailure,
) -> Result<RenderedFallback, TextError> {
    let root = members.first().ok_or(TextError::Invalid("fallback root"))?;
    // branch_members checks this before the caller even attempts projection.
    // Keep that invariant at this terminal boundary as well.
    if members.iter().collect::<HashSet<_>>().len() != members.len() {
        return Err(TextError::Invalid("Invalid source-tree identity before presentation"));
    }
    let section = source.fallback(
        members,
        failure.error,
        &failure.detail,
        failure.stage == FailureStage::Terminal,
    )?;
    let issue = Value::Map(vec![
        ("root".to_owned(), Value::Text(root.clone())),
        ("error".to_owned(), Value::Text(failure.error.to_owned())),
        ("detail".to_owned(), Value::Text(failure.detail.clone())),
    ]);
    Ok(RenderedFallback { section, issue })
}

fn related_heading(context: &Value) -> Result<String, TextError> {
    let rows = context.as_seq().ok_or(TextError::Invalid("related root context"))?;
    let mut subjects = Vec::with_capacity(rows.len());
    for row in rows {
        let Some([Value::Text(owner), Value::Text(kind)]) = row.as_seq() else {
            return Err(TextError::Invalid("related root subject"));
        };
        // Registered kinds are ASCII. Provider-specific kinds have a dotted
        // namespace, which cannot be an identifier regardless of the suffix.
        if !kind.is_ascii() && !kind.contains('.') {
            return Err(TextError::Invalid("non-ASCII related root kind"));
        }
        let mut bytes = kind.bytes();
        let identifier = bytes
            .next()
            .is_some_and(|byte| byte.is_ascii_alphabetic() || byte == b'_')
            && bytes.all(|byte| byte.is_ascii_alphanumeric() || byte == b'_');
        let kind = if identifier { kind.clone() } else { quoted(kind) };
        subjects.push(format!("{kind} of {owner}"));
    }
    Ok(format!("Related elements ({}):", subjects.join("; ")))
}

fn indent_section(section: &mut RenderedSection) {
    section.text = indent(&section.text, 1);
    section.prefix = indent(&section.prefix, 1);
    section.suffix = indent(&section.suffix, 1);
    for node in &mut section.nodes {
        node.depth += 1;
        node.text = indent(&node.text, 1);
        node.content = indent(&node.content, 1);
        for hint in &mut node.hints {
            *hint = indent(hint, 1);
        }
        for (_, text) in &mut node.layout {
            *text = indent(text, 1);
        }
    }
}

/// Add one regular or uncompressed section to the snapshot document. The
/// related heading is separate from the section prefix, and successive roots
/// with the same relation context stay under the same heading.
pub(crate) fn append_scoped(
    parts: &mut Vec<RenderedPart>,
    mut section: RenderedSection,
    context: Option<&Value>,
    previous: Option<&Value>,
) -> Result<(), TextError> {
    if let Some(context) = context {
        if Some(context) != previous || parts.is_empty() {
            let heading = related_heading(context)?;
            if !parts.is_empty() {
                parts.push(RenderedPart::Text("\n\n".to_owned()));
            }
            parts.push(RenderedPart::Text(heading));
        }
        indent_section(&mut section);
        parts.push(RenderedPart::Text("\n".to_owned()));
    } else if !parts.is_empty() {
        parts.push(RenderedPart::Text("\n\n".to_owned()));
    }
    parts.push(RenderedPart::Section(section));
    Ok(())
}

#[cfg(test)]
mod tests {
    use super::*;
    use crate::{
        numbering::NodeId,
        projection::{branch_members, BranchError},
        render::RenderedSnapshot,
        wire::decode_json,
    };
    use std::collections::HashMap;

    fn json(value: &str) -> Value {
        decode_json(value.as_bytes()).unwrap()
    }

    fn source(facts: &Value) -> DetailSource<'_> {
        DetailSource::new(
            "macos",
            "session",
            "capture",
            &[],
            facts,
            &[],
            [("7", "root"), ("8", "child"), ("9", "related")],
        )
        .unwrap()
    }

    #[test]
    fn exact_uncompressed_records_preserve_field_order_and_original_relation_targets() {
        let facts = json(
            r#"{
            "nodes":[
                {"node":"root","requestedFields":["name","role"],"values":{"role":"button","name":"open\u2028more"},
                 "fieldStatus":{"name":{"state":"partial","detail":"preview"}},
                 "actions":{"state":"partial","items":[{"key":"AXPress","kind":"invoke"}]}},
                {"node":"child","requestedFields":["name"],"values":{"name":"inside"},"fieldStatus":{},
                 "actions":{"state":"complete","items":[]}},
                {"node":"related","requestedFields":["name"],"values":{"name":"outside"},"fieldStatus":{},
                 "actions":{"state":"complete","items":[]}}
            ],
            "relations":[{"source":"root","property":"AXLinkedUIElements","recordKey":"links",
                          "kind":"related","state":"partial","certainty":"confirmed","total":3,
                          "targets":["related","unpublished"]}]
        }"#,
        );
        let members = ["7".into(), "8".into()];
        let failure = PresentationFailure {
            error: "ValueError",
            detail: "broken\nrecord".to_owned(),
            stage: FailureStage::Preparation,
        };
        let fallback = render(&source(&facts), &members, &failure).unwrap();
        let prefix = "Presentation fallback for 7: uncompressed collected facts (ValueError: \"broken\\nrecord\"). Relations: element references.";
        let root = concat!(
            "7 native element\n\t\"name\": \"open\\u2028more\"\n",
            "\t\"name\" status: {\"state\":\"partial\",\"detail\":\"preview\"}\n",
            "\t\"role\": \"button\"\n",
            "\tNative actions: {\"state\":\"partial\",\"items\":[{\"key\":\"AXPress\",\"kind\":\"invoke\"}]}\n",
            "\tRelation \"AXLinkedUIElements\": {\"recordKey\":\"links\",\"kind\":\"related\",\"state\":\"partial\",",
            "\"certainty\":\"confirmed\",\"total\":3,\"targets\":[\"9\",{\"native\":\"unpublished\",\"ref\":null}]}"
        );
        let child = "8 native element\n\t\"name\": \"inside\"\n\tNative actions: {\"state\":\"complete\",\"items\":[]}";
        assert_eq!(fallback.section.text, format!("{prefix}\n{root}\n{child}"));
        assert_eq!(fallback.section.prefix, prefix);
        assert!(fallback.section.suffix.is_empty());
        assert!(!fallback.section.refresh);
        assert_eq!(fallback.section.nodes[0].text, root);
        assert_eq!(fallback.section.nodes[1].text, child);
        assert_eq!(fallback.section.nodes[0].content, "7 native element");
        assert_eq!(fallback.section.nodes[1].depth, 0);
        assert_eq!(fallback.section.nodes[1].parent, None);
        assert_eq!(fallback.record().get("refs"), Some(&json(r#"["7","8"]"#)));
        assert_eq!(
            fallback.record().get("presentation").and_then(Value::as_str),
            Some("uncompressed")
        );
        assert_eq!(
            fallback.issue,
            json(r#"{"root":"7","error":"ValueError","detail":"broken\nrecord"}"#)
        );

        let terminal = PresentationFailure {
            stage: FailureStage::Terminal,
            ..failure
        };
        let refreshed = render(&source(&facts), &members, &terminal).unwrap();
        assert!(refreshed.section.refresh);
        assert_eq!(refreshed.record(), fallback.record());
    }

    #[test]
    fn scoped_sections_share_related_headings_and_keep_local_suffix_and_refresh() {
        let contexts = json(r#"[["7","related"],["8","display relation"],["9","provider.macos.关联"]]"#);
        let mut parts = Vec::new();
        let section = |text: &str, refresh| RenderedSection {
            text: format!("prefix\n{text}\nsuffix"),
            prefix: "prefix".to_owned(),
            suffix: "suffix".to_owned(),
            refresh,
            nodes: vec![crate::render::RenderedNode {
                reference: "8".to_owned(),
                depth: 2,
                text: text.to_owned(),
                content: text.to_owned(),
                hints: vec!["hint".to_owned()],
                ..Default::default()
            }],
        };
        append_scoped(&mut parts, section("first", true), Some(&contexts), None).unwrap();
        append_scoped(&mut parts, section("second", false), Some(&contexts), Some(&contexts)).unwrap();
        append_scoped(&mut parts, section("third", false), None, Some(&contexts)).unwrap();
        let output = RenderedSnapshot {
            parts,
            ..Default::default()
        };
        assert_eq!(
            output.text(),
            concat!(
                "Related elements (related of 7; \"display relation\" of 8; \"provider.macos.关联\" of 9):\n",
                "\tprefix\n\tfirst\n\tsuffix\n\tprefix\n\tsecond\n\tsuffix\n\n",
                "prefix\nthird\nsuffix"
            )
        );
        let RenderedPart::Section(first) = &output.parts[2] else {
            panic!("expected first section")
        };
        assert_eq!(first.prefix, "\tprefix");
        assert_eq!(first.suffix, "\tsuffix");
        assert_eq!(first.nodes[0].depth, 3);
        assert_eq!(first.nodes[0].hints, ["\thint"]);
        assert!(first.refresh);
        let RenderedPart::Section(second) = &output.parts[4] else {
            panic!("expected second section")
        };
        assert!(!second.refresh);
    }

    #[test]
    fn invalid_acquisition_identity_and_budget_remain_outside_fallback() {
        let aliases = HashMap::from([("7".to_owned(), NodeId(0))]);
        let duplicate = json(r#"{"ref":"7","children":[{"ref":"7","children":[]}]}"#);
        assert!(matches!(
            branch_members(&duplicate, &aliases, || Ok::<_, ()>(())),
            Err(BranchError::Source(_))
        ));
        assert!(matches!(
            branch_members(&duplicate, &aliases, || Err("expired")),
            Err(BranchError::Budget("expired"))
        ));
        let facts = json(r#"{"nodes":[],"relations":[]}"#);
        let error = PresentationFailure {
            error: "ValueError",
            detail: "presentation".to_owned(),
            stage: FailureStage::Terminal,
        };
        assert!(matches!(
            render(&source(&facts), &["7".into(), "7".into()], &error),
            Err(TextError::Invalid("Invalid source-tree identity before presentation"))
        ));
    }
}
