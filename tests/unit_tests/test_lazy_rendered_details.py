"""Normal delivery keeps expensive native diagnostics captured until requested."""

from copy import deepcopy
from unittest.mock import patch

from computer_use.interaction.read.facts.normalize import normalize_facts
from computer_use.interaction.read.numbering.references import DisplayReferences
from computer_use.interaction.read.presentation.details import captured_read_issues
from computer_use.interaction.read.presentation.document import DisplayDocument
from computer_use.interaction.read.presentation.preparation import prepare_snapshot, render_prepared_snapshot
from computer_use.protocol.observation import NativeNode, NativeObservation, ReadIssue
from computer_use.session.observation import lazy_observation


def captured_render():
    native = NativeObservation(
        "macos",
        "session",
        "original-capture",
        (1, 2),
        ("window",),
        {"window": NativeNode(role="window", name="Captured window")},
        issues=(ReadIssue(("nodes", "window", "help"), "unsupported", detail="provider detail 中文"),),
    )
    snapshot = normalize_facts(native, DisplayReferences("session"))
    return prepare_snapshot(snapshot, compact=True, public_api=True)


def test_normal_body_does_not_format_raw_issues_and_explicit_details_remain_exact():
    prepared = captured_render()
    with patch(
        "computer_use.interaction.read.presentation.passes.assemble_rendered_part.captured_read_issues",
        wraps=captured_read_issues,
    ) as formatter:
        rendered = render_prepared_snapshot(prepared)
        assert "Captured window" in rendered["text"]
        assert "capturedReadIssues" in rendered
        formatter.assert_not_called()

        details = rendered["details"]
        assert "provider detail 中文" in details
        assert "original-capture" in details
        assert rendered["capturedReadIssues"][0]["ref"] == "0"
        assert rendered.get("details") is details
        formatter.assert_called_once()


def test_serializing_a_complete_render_materializes_details_once():
    prepared = captured_render()
    with patch(
        "computer_use.interaction.read.presentation.passes.assemble_rendered_part.captured_read_issues",
        wraps=captured_read_issues,
    ) as formatter:
        rendered = render_prepared_snapshot(prepared)
        formatter.assert_not_called()
        complete = dict(rendered)
        assert "provider detail 中文" in complete["details"]
        assert complete["capturedReadIssues"][0]["issue"]["path"] == ["nodes", "window", "help"]
        formatter.assert_called_once()


def test_copy_keeps_deferred_source_details():
    rendered = render_prepared_snapshot(captured_render())
    copied = deepcopy(rendered)
    assert "provider detail 中文" in copied["details"]
    assert rendered["capturedReadIssues"] == copied["capturedReadIssues"]
    assert copied["details"] == rendered["details"]


def test_sdk_details_pages_keep_exact_capture_without_eager_normal_delivery():
    prepared = captured_render()
    observation = lazy_observation(
        DisplayDocument.tree(prepared), details_document=DisplayDocument.tree(prepared, details=True)
    )
    with patch(
        "computer_use.interaction.read.presentation.passes.assemble_rendered_part.captured_read_issues",
        wraps=captured_read_issues,
    ) as formatter:
        assert "Captured window" in observation.text
        formatter.assert_not_called()
        parts, cursor = [], None
        while True:
            page = observation.read_details(cursor=cursor, length=97)
            parts.append(page.text)
            cursor = page.next
            if cursor is None:
                break
        complete = "".join(parts)
        assert "provider detail 中文" in complete and "original-capture" in complete
        assert "Captured window" in complete
        assert observation.read_details(length=97).text == parts[0]
        formatter.assert_called_once()
