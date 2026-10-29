# coding: utf-8
# Copyright (c) Huawei Technologies Co., Ltd. 2026. All rights reserved.

"""One bounded public body and optional image, retaining native read evidence."""

from __future__ import annotations

from collections.abc import Iterator
from typing import TYPE_CHECKING, Any, cast

from computer_use.interaction.query.instances.scope import allowed
from computer_use.interaction.read.presentation.content import DisplayText, join, template
from computer_use.interaction.read.presentation.content import json_value as quoted
from computer_use.interaction.read.presentation.document import DisplayDocument
from computer_use.interaction.read.presentation.preparation import prepare_snapshot
from computer_use.protocol.primitives import frozen
from computer_use.session.events import observation_completed
from computer_use.session.interfaces import ApplicationEntry
from computer_use.session.observation import lazy_observation
from computer_use.session.observation_data import retain_observation_data

if TYPE_CHECKING:
    from computer_use.interaction.controller import SessionController
    from computer_use.interaction.read.facts.snapshot import SemanticSnapshot
    from computer_use.interaction.read.presentation.preparation import PreparedSnapshot
    from computer_use.interaction.records import UIHandle
    from computer_use.native.contracts import Binding
    from computer_use.protocol.png import PNG
    from computer_use.protocol.records import NativeDirectory as DirectoryFacts
    from computer_use.types import Observation


def tree_text(
    controller: SessionController,
    target: Binding,
    snapshot: SemanticSnapshot,
    *,
    details: bool = False,
) -> str:
    """Render one semantic snapshot with its optional native-detail supplements.

    Args:
        controller: Session services used to format retained continuation aliases.
        target: Native scope binding for this snapshot.
        snapshot: Captured native facts and semantic tree to render.
        details: Include source-accounted fields, actions, and relationships.
    """
    prepared = prepare_snapshot(snapshot, compact=True, public_api=True)
    return tree_document(controller, target, snapshot, prepared=prepared, details=details).text


def tree_document(
    controller: SessionController,
    target: Binding,
    snapshot: SemanticSnapshot,
    *,
    prepared: PreparedSnapshot,
    details: bool = False,
) -> DisplayDocument:
    """Compose a snapshot's structured presentation and its native continuation hints."""
    document = DisplayDocument.tree(prepared, details=details).scoped(
        (target.session, target.instance, target.root_window, target.surface, target.node)
    )
    if not details:
        root = next((ref for ref, node in snapshot.refs.items() if node in snapshot.facts["roots"]), None)
        if root is not None:
            document = DisplayDocument.join((document, DisplayText("details_hint", (prepared, root))), separator="")
    if {"nativeRelationPages", "nativeNodePages"} & set(controller.transport.capabilities):
        document = DisplayDocument.join(
            (
                document,
                controller.pages.plan_hints(
                    target,
                    snapshot,
                    public_api=True,
                    children_only="nativeRelationPages" not in controller.transport.capabilities,
                ),
            ),
            separator="",
        )
    return document


def publish(
    controller: SessionController,
    owner: UIHandle | None,
    text: str | DisplayDocument,
    screenshot: PNG | None = None,
    *,
    trees: tuple[tuple[Binding, SemanticSnapshot], ...] = (),
    full: bool = False,
    scope: str | None = None,
    view: str = "window",
    directory: DirectoryFacts | None = None,
) -> Observation:
    """Publish the combined interface observation and retain its source data.

    Args:
        controller: Session observation and interface services.
        owner: App or Window handle that owns the observed interface, when applicable.
        text: Formatted observation text delivered to the model.
        screenshot: Captured image delivered with this observation, when requested.
        trees: Native target/snapshot pairs retained for later element operations.
        full: Whether this publication requests complete-tree delivery.
        scope: Observed native scope; defaults to the desktop label.
        view: Scope label attached to the publication event, such as window or desktop.
        directory: Current application window catalogue, when available.
    """
    document = text if isinstance(text, DisplayDocument) else DisplayDocument.join((text,))
    roots = [root for _, snapshot in trees for root in snapshot.roots]
    refs = {ref: node for _, snapshot in trees for ref, node in snapshot.refs.items()}
    windows, instance, identity = {}, None, None
    if owner is not None:
        # The pipeline shares retained bindings with their owning service.
        # pylint: disable-next=protected-access
        state = owner._instance
        if state.anchor is not None:
            permitted = allowed(owner)
            windows = {str(ref): key for ref, key in state.windows.items() if key in permitted}
            alias = {key: ref for ref, key in windows.items()}
            current = getattr(owner, "_context", None)
            identity = dict(current=alias.get(current), windows=[])
            for ref, key in windows.items():
                window_data = dict(
                    ref=ref,
                    title=permitted[key]["title"],
                    **{
                        name: permitted[key][name]
                        for name in ("modal", "document")
                        if permitted[key].get(name) is not None
                    },
                )
                if permitted[key].get("parent") in alias:
                    window_data["parent"] = alias[permitted[key]["parent"]]
                identity["windows"].append(window_data)
            focused = (directory or state.directory).get("focusedWindow")
            if focused in alias:
                identity["focusedWindow"] = alias[focused]
            if current in permitted and permitted[current].get("document") is not None:
                identity["document"] = permitted[current]["document"]
            instance = controller.interfaces.references.publish(
                ApplicationEntry(
                    state.anchor.root.session,
                    state.directory["application"],
                    state.anchor.name,
                    True,
                    state.directory,
                    state.anchor.root.root_window,
                    state.anchor.name,
                )
            )
        else:
            instance = state.selector
    if document is not None and owner is not None and identity is not None:
        from dataclasses import replace

        document = replace(
            document,
            context=(
                state.directory["session"],
                state.directory["application"],
                state.anchor.root.root_window,
                identity.get("current"),
                identity.get("document"),
                tuple(
                    (item["ref"], item.get("parent"), item.get("modal"), item.get("document"))
                    for item in identity["windows"]
                ),
            ),
        )
    result = lazy_observation(document, screenshot)
    retain_observation_data(result, roots, refs, windows, instance, identity)
    observation_completed(
        dict(
            source=controller,
            owner=owner,
            scope=scope or "desktop",
            view=view,
            state=result,
            refs=frozen(refs),
            full=full,
            publicApi=True,
            document=document,
            **(dict(observed=directory["observed"]) if directory else {}),
        )
    )
    return cast("Observation", result)


def directory_text(controller: SessionController, ui: UIHandle, directory: DirectoryFacts) -> list[DisplayText]:
    """Format the current window catalogue and its unresolved relationship notes.

    Args:
        controller: Session service owner; this dependency does not create another execution
            route.
        ui: App or Window handle whose instance and observation bounds constrain the operation.
        directory: Observed application/window catalogue, including incomplete relationships.
    """
    # pylint: disable-next=protected-access
    instance = ui._instance
    selected = cast("WorkingWindow", instance.anchor).root.root_window
    view = (
        "App instance"
        # pylint: disable-next=protected-access
        if ui._anchor is None
        # pylint: disable-next=protected-access
        else template("Window {0}", next(ref for ref, key in instance.windows.items() if key == ui._anchor))
    )
    lines = [template("App: {0}.", quoted(cast("WorkingWindow", instance.anchor).name)), template("View: {0}.", view)]
    if len(instance.members) > 1:
        windows = []
        for ref, key in instance.windows.items():
            windows.append(
                template(
                    "{0} {1}{2}{3}{4}",
                    ref,
                    quoted(instance.members[key]["title"]),
                    " (main)" if key == selected else "",
                    " (modal)" if instance.members[key]["modal"] is True else "",
                    template(
                        " (parent={0})",
                        next(
                            (r for r, w in instance.windows.items() if w == instance.members[key]["parent"]),
                            "unknown",
                        ),
                    )
                    if instance.members[key]["parent"] is not None
                    else "",
                )
            )
        lines.append(
            template(
                "Windows: {0}; current={1}",
                join(windows, "; "),
                next((ref for ref, key in instance.windows.items() if key == getattr(ui, "_context", None)), "unknown"),
            )
        )
    focus = directory.get("focusedWindow")
    if focus is not None:
        ref = next((ref for ref, key in instance.windows.items() if key == focus), None)
        lines.append(
            template(
                "Reported input window: {0}", ref if ref is not None else "outside this instance; not an input target"
            )
        )
    other = [item for item in directory["surfaces"] if item["window"] not in instance.members]
    if other:
        choices = controller.interfaces.discovery.publish_choices(
            cast("WorkingWindow", instance.anchor).name, directory
        )

        def _iter_item(_source_items: Iterator[Any]) -> Iterator[Any]:
            for item in _source_items:
                if controller.interfaces.references.window(item.id, directory["session"]) not in instance.members:
                    yield template("{0} {1!r}; computer.app({0})", item.id, item.display_name)

        lines.append(
            template(
                "Other independent or unconfirmed windows: {0}",
                join(
                    _iter_item(iter(choices)),
                    "; ",
                ),
            )
        )
    lines.extend(template("Directory partial: {0}", reason) for reason in directory["stopReasons"])
    return lines


if TYPE_CHECKING:
    from computer_use.native.scopes import WorkingWindow
