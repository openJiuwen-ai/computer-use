"""Preserve historical text fixtures through an explicitly scoped test renderer.

The frozen oracle contains formatted strings rather than original semantic facts.
These fixtures keep their known terminal output separate from comparison records.
Only ``fixture_renderer()`` substitutes their snapshot rendering; production facts,
new semantic-IR tests, planning, and final document formatting retain their normal paths.
"""

from __future__ import annotations

import re
from contextlib import contextmanager
from dataclasses import dataclass
from types import MappingProxyType
from typing import Mapping
from unittest.mock import patch

from computer_use.interaction.read.presentation.accounting.fields import RenderedResult
from computer_use.interaction.read.presentation.document import DisplayDocument, DisplayTree
from computer_use.interaction.read.presentation.ir import freeze
from computer_use.interaction.read.presentation.model import SnapshotRenderRequest
from computer_use.interaction.read.presentation.preparation import CapturedSnapshot, PreparedNode, PreparedSnapshot
from computer_use.interaction.read.presentation.rendering.formatting import field
from computer_use.interaction.read.presentation.rendering.layout.chains import expandTreeChains
from computer_use.interaction.read.presentation.rendering.layout.text_parts import expandTextParts
from computer_use.interaction.read.presentation.rendering.output import RenderedDocument, RenderedNode, RenderedSection
from computer_use.protocol.observation import NativeObservation
from computer_use.protocol.primitives import reference, trim, trimStart, utf16Length
from computer_use.protocol.records import Record


def parse(text: str, refs: Mapping[str, str]) -> None | Record:
    """Parse rendered reference lines and their indentation for diff comparison.

    Args:
        text: Observation or captured text; reading it does not dispatch input.
        refs: Display-to-native identity map owned by the observation/session service.
    """
    header, footer, nodes, stack, seen, expanded = [], [], [], [], set(), []
    for line in expandTreeChains(expandTextParts(text.split("\n"), field)):
        cells = re.match(
            (
                "^(\\t*)Empty values \\[(.+); ((?:(?:Click|Input): "
                '(?:available|unavailable|unknown), )*Value: "")\\]: ((?:[0-9]+ '
                "[A-Z]+[1-9][0-9]*)(?:, [0-9]+ "
                "[A-Z]+[1-9][0-9]*){2,})\\Z"
            ),
            line,
        )
        if not cells:
            expanded.append(line)
        else:
            for pair in cells[4].split(", "):
                ref, address = pair.split(" ")
                expanded.append(f"{cells[1]}{ref} {cells[2]} {address}, {cells[3]}")
    for line in expanded:
        if re.match(
            (
                "^(?:Windows: |Window documents: |Window relations: |Interaction: |Window: "
                "|Observation: |Document: |App: |Application menu: |Tree: |Screenshot: "
                "|Overview: |Overview windows |Overview bounds: |Window title omitted: "
                ")"
            ),
            line,
        ):
            if nodes:
                return None
            header.append(line)
            continue
        if re.match(
            (
                "^(?:Selected: |Selected menus: |The focused UI element is |Reported focus: "
                "|Focus flags: |Shared focus: |Multiple elements report focus: |Tree "
                "partial: |Other windows \\(not expanded\\): "
                "|\\[)"
            ),
            line,
        ):
            footer.append(line)
            continue
        match = re.match("^(\\t*)([^\\s]+) ", line)
        if match and reference(match[2]):
            identifier, depth_value = match[2], len(match[1])
            if identifier not in refs or identifier in seen or depth_value > len(stack):
                return None
            if footer:
                return None
            nodes.append(
                dict(
                    id=identifier, parent=stack[depth_value - 1] if depth_value else None, depth=depth_value, text=line
                )
            )
            seen.add(identifier)
            stack = stack[:depth_value] + [identifier]
        elif not nodes:
            header.append(line)
        elif (
            re.match("^\\t+(?:Value:|Selected text:|\\| |Omitted: |Children: |Continue: |Inspect subtree: )", line)
            and not footer
        ):
            nodes[-1]["text"] += "\n" + line
        else:
            footer.append(line)
    if not nodes or len(nodes) != len(refs):
        return None
    return dict(header="\n".join(header), footer=trim("\n".join(footer)), nodes=nodes)


@dataclass(frozen=True)
class FixturePreparation(PreparedSnapshot):
    "Comparison records with the historical oracle's known terminal snapshot output."

    fixture_rendered: RenderedResult


@contextmanager
def fixture_renderer():
    "Inject terminal fixture output only within an explicitly historical test scope."
    from computer_use.interaction.read.presentation.pipeline import RenderPreparedSnapshotPipeline
    from computer_use.interaction.read.presentation.rendering.document.passes import format_document as terminal

    original = RenderPreparedSnapshotPipeline.run
    original_format = terminal.format_document

    def render(pipeline, prepared, *args, **kwargs):
        if isinstance(prepared, FixturePreparation):
            return prepared.fixture_rendered
        return original(pipeline, prepared, *args, **kwargs)

    def format_fixture(document, snapshots, plan=None, *, public_api=False):
        if (
            len(document.parts) == 1
            and isinstance(document.parts[0], DisplayTree)
            and isinstance(document.parts[0].prepared, FixturePreparation)
        ):
            return _fixture_text(document.parts[0].prepared, plan, public_api)
        return original_format(document, snapshots, plan, public_api=public_api)

    with (
        patch.object(RenderPreparedSnapshotPipeline, "run", render),
        patch.object(terminal, "format_document", format_fixture),
    ):
        yield


_worker_renderer = None


def install_fixture_renderer():
    "Keep the fixture injection active for an explicitly historical child worker."
    import atexit

    global _worker_renderer
    if _worker_renderer is None:
        _worker_renderer = fixture_renderer()
        _worker_renderer.__enter__()
        atexit.register(_worker_renderer.__exit__, None, None, None)


def _fixture_text(prepared, plan, public_api):
    """Preserve the oracle's old header spacing and executable-hint size boundary.

    Structural decisions come from the production planner. This terminal fixture
    formatter retains historical bytes, including the empty header's leading newline.
    """
    section = prepared.fixture_rendered.document.parts[0]
    if plan is None or plan.mode == "full":
        return section.text
    delta = plan.trees[0]
    title = (
        f"Tree: {plan.mode} (not full; read(full=True) for full text)"
        if public_api
        else f'Tree: {plan.mode} (not full; read({{"full": True}}) for full text)'
    )
    if re.search("^Tree: ", section.prefix, re.MULTILINE):
        heading = re.sub("^Tree: .*$", lambda _: title, section.prefix, count=1, flags=re.MULTILINE)
    else:
        headings = section.prefix.split("\n")
        headings.insert(1, title)
        heading = "\n".join(headings)
    lines = [heading]
    removed = [ref for ref in delta.missing if ref not in plan.not_observed and ref not in plan.retained]
    unobserved = [ref for ref in delta.missing if ref in plan.not_observed or ref in plan.retained]
    if removed:
        lines.append("Removed element IDs: " + ", ".join(removed))
    if unobserved:
        lines.append("Not observed in this read (not proof of removal): " + ", ".join(unobserved))
    for item in section.nodes:
        if item.ref in delta.changes:
            lines.append(delta.changes[item.ref] + item.text)
        elif item.ref in delta.context:
            lines.append(" " + (item.text if item.ref in delta.expanded else item.text.split("\n", 1)[0]))
        if item.ref not in delta.changes:
            for hint in item.hints:
                if hint.lstrip("\t").startswith("Omitted: "):
                    lines.append(f"Element {item.ref}: {trimStart(hint)}")
                elif item.ref not in delta.expanded:
                    lines.append(hint)
    if section.suffix:
        lines.append(section.suffix)
    result = "\n".join(lines)
    hint_growth = len('read({"full": True})') - len("read({full:true})")
    return result if utf16Length(result) - hint_growth < utf16Length(section.text) else section.text


def with_document(value):
    """Map an old text-only fixture to comparison data and its separate final rendering.

    The fixture-specific fields deliberately make no claims about native role semantics.
    Real facts and source-preservation contracts are covered by native-batch IR tests.
    """
    parsed = parse(value["text"], value["refs"])
    if parsed is None:
        return dict(value, document=None)
    states = (
        "disabled|selected|selectable|clickable|submenu|focusable|expanded|collapsed|"
        "focused|reports focus|modal|non-modal|contains selected "
        "text"
    )
    nodes, comparison = [], []
    for node in parsed["nodes"]:
        first = node["text"].split("\n", 1)[0]
        content = re.sub(" \\((?:" + states + ")(?:, (?:" + states + "))*\\)", "", first, count=1)
        hints = tuple(
            line
            for line in node["text"].split("\n")[1:]
            if re.match("^\\t+(?:Omitted: |Children: |Continue: |Inspect subtree: )", line)
        )
        partial = bool(
            re.search("^\\t+Children: (?:showing [0-9]+ of [0-9]+ items; )?partial: ", node["text"], re.MULTILINE)
            or re.search(" \\((?:showing [0-9]+ of [0-9]+ items; )?partial: .+\\)\\Z", first)
        )
        related = tuple(value.get("contextRefs", {}).get(node["id"], ()))
        nodes.append(
            RenderedNode(node["id"], node["parent"], node["depth"], node["text"], content, hints, related, partial)
        )
        flags = re.search(" \\(((?:" + states + ")(?:, (?:" + states + "))*)\\)", first)
        fields = dict(
            role="historicalFixture",
            fixture_content=content,
            focused=tuple(node["text"].split("\n")[1:]),
            selected=flags[1] if flags else "",
            _contextRefs=related,
        )
        if partial:
            fields["partialReason"] = "historical fixture reports partial children"
        comparison.append(PreparedNode(node["id"], node["parent"], node["depth"], freeze(fields)))
    title = next((line for line in parsed["header"].split("\n") if re.match("^(?:Window: |App: )", line)), None)
    app = re.sub('^Window: "(?:[^"\\\\]|\\\\.)*", ', "", title, count=1) if title is not None else None
    window = re.search("^Windows: .*; current=([0-9]+|unknown)$", parsed["header"], re.MULTILINE)
    context = tuple(
        line
        for line in parsed["header"].split("\n")
        if line.startswith(("Document: ", "Interaction: ", "Window relations: ", "Application menu: "))
    )
    native = NativeObservation("macos", "historical-fixture", "historical-fixture", (0, 0), (), MappingProxyType({}))
    captured = CapturedSnapshot(
        native=native,
        facts=freeze(dict(observed=dict(id="historical-fixture"))),
        roots=(),
        refs=freeze(value["refs"]),
        annotations=(),
        fields=freeze({}),
        text_refs=freeze({}),
        scope_kind=None,
        root_contexts=freeze({}),
        value_fields=freeze({}),
        driver_features=frozenset(),
    )
    rendered = RenderedResult(
        dict(text=value["text"], details=value["text"]),
        document=RenderedDocument((RenderedSection(value["text"], tuple(nodes), parsed["header"], parsed["footer"]),)),
    )
    prepared = FixturePreparation(SnapshotRenderRequest(captured, compact=True), (), tuple(comparison), rendered)
    document = DisplayDocument((DisplayTree(prepared),), (app, window[1] if window else None, *context))
    return dict(value, document=document)
