"""Detail-hint extraction must not retain completed rendering graphs until cyclic GC."""

import asyncio
import gc
import weakref
from types import SimpleNamespace

import pytest
from computer_use.interaction.read.presentation.content import DisplayText, join, text
from computer_use.interaction.read.presentation.passes.assemble_rendered_part import _DeferredDetails
from computer_use.interaction.read.presentation.rendering.document.details import separate_details


class Payload:
    pass


@pytest.mark.parametrize("nested", [False, True])
def test_detail_extraction_releases_unrequested_deferred_render_graph(nested):
    gc.collect()

    def extract():
        payload = Payload()

        def deferred():
            return {"details": payload}

        rendered = _DeferredDetails({"text": "visible"}, document=None, detail_keys=("details",), details=deferred)
        snapshots = {1: rendered}
        value = join((text("literal"), join((text("other"),), ",")), "|") if nested else text("literal")
        assert separate_details(value, snapshots) == (value, ())
        # Extraction of an ordinary field must leave lazy details unrequested.
        assert rendered._details is deferred
        return weakref.ref(payload), weakref.ref(rendered)

    references = extract()
    assert all(reference() is None for reference in references)
    assert gc.isenabled()


def test_detail_extraction_preserves_nested_order_duplicates_and_omission():
    first, second, equal = Payload(), Payload(), Payload()
    omitted = SimpleNamespace(
        request=SimpleNamespace(compact=True, public_api=True, share_descriptions=True), roots=()
    )
    reads = []

    class Snapshots(dict):
        def __getitem__(self, key):
            reads.append(key)
            return super().__getitem__(key)

    snapshots = Snapshots(
        {
            id(first): {"text": "shown", "details": "more"},
            id(second): {"text": "shown", "details": "more"},
            id(equal): {"text": "same", "details": "same"},
            id(omitted): {},
        }
    )

    def hint(source, ref):
        return DisplayText("details_hint", (source, ref))

    literal = text("Read details for the literal application content")
    value = join(
        (literal, hint(first, 1), join((hint(second, 2), hint(first, 1)), ","), hint(equal, 3), hint(omitted, 4)), "|"
    )

    retained, roots = separate_details(value, snapshots)

    assert retained == join((literal, text(""), join((text(""), text("")), ","), text(""), text("")), "|")
    assert roots == ("1", "2", "1")
    assert reads == [id(first), id(second), id(first), id(equal), id(omitted)]
    assert value.values[1][0] is literal


@pytest.mark.parametrize("failure", [RuntimeError, asyncio.CancelledError])
def test_detail_extraction_releases_snapshots_after_error_or_cancellation(failure):
    gc.collect()

    class Snapshots(dict):
        def __getitem__(self, key):
            raise failure("detail extraction failed")

    def extract():
        payload = Payload()
        snapshots = Snapshots({1: payload})
        value = join((text("prefix"), join((DisplayText("details_hint", (Payload(), "1")),), ",")), "|")
        try:
            separate_details(value, snapshots)
        except (RuntimeError, asyncio.CancelledError) as error:
            assert type(error) is failure
            assert error.args == ("detail extraction failed",)
        else:
            pytest.fail("Expected the original extraction exception")
        return weakref.ref(payload), weakref.ref(snapshots)

    references = extract()
    assert all(reference() is None for reference in references)
    assert gc.isenabled()
