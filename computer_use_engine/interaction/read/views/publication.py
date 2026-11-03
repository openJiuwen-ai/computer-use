# coding: utf-8
# Copyright (c) Huawei Technologies Co., Ltd. 2026. All rights reserved.

"""One bounded public body and optional image, retaining native read evidence."""

from __future__ import annotations

from dataclasses import asdict
from typing import TYPE_CHECKING, cast
import weakref

from computer_use.interaction.query.instances.scope import allowed
from computer_use.interaction.query.instances.membership import ancestor
from computer_use.interaction.read.presentation.content import DisplayText, template
from computer_use.interaction.read.presentation.content import json_value as quoted
from computer_use.interaction.read.presentation.details import captured_details_document
from computer_use.interaction.read.presentation.document import DisplayDocument
from computer_use.interaction.read.presentation.element_details import CapturedElementDetails
from computer_use.interaction.read.presentation.headers import InterfaceHeader, MemberHeading
from computer_use.interaction.read.presentation.preparation import prepare_snapshot
from computer_use.protocol.primitives import frozen
from computer_use.session.events import observation_completed, register_observation_delivery
from computer_use.session.interfaces import ApplicationEntry, LookupContext, WindowIdentity
from computer_use.session.observation import lazy_observation
from computer_use.session.observation_data import retain_observation_data

if TYPE_CHECKING:
    from collections.abc import Sequence

    from computer_use.interaction.controller import SessionController
    from computer_use.interaction.read.acquisition.components.support import ObservationGap
    from computer_use.interaction.read.facts.snapshot import SemanticSnapshot
    from computer_use.interaction.read.presentation.preparation import PreparedSnapshot
    from computer_use.interaction.read.views.pipeline import WindowPart
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
        concise = (
            not details
            and prepared.request.compact
            and prepared.request.public_api
            and prepared.request.share_descriptions
        )
        document = DisplayDocument.join(
            (
                document,
                controller.pages.plan_hints(
                    target,
                    snapshot,
                    public_api=True,
                    compact=concise,
                    concise=concise,
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
    partial: bool = False,
    content_available: bool = True,
    gaps: tuple[ObservationGap, ...] = (),
    preparation: dict | None = None,
    context_owner=None,
    lookup_context: LookupContext | None = None,
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
        lookup_context: Exact source context for explicit subtree publication; ordinary UI reads capture their staged view.
    """
    document = text if isinstance(text, DisplayDocument) else DisplayDocument.join((text,))
    roots = [root for _, snapshot in trees for root in snapshot.roots]
    refs = {ref: node for _, snapshot in trees for ref, node in snapshot.refs.items()}
    windows, instance, identity = {}, None, None
    if owner is not None and lookup_context is None:
        item = owner._instance.members.get(getattr(owner, "_context", None))
        lookup_context = LookupContext(
            WindowIdentity.of(item) if item is not None and content_available else None,
            bool(getattr(owner, "_menu_only", False)),
            tuple(getattr(owner, "_lookup_scopes", ())),
        )
    if owner is not None:
        # The pipeline shares retained bindings with their owning service.
        # pylint: disable-next=protected-access
        state = owner._instance
        if state.directory is not None:
            permitted = allowed(owner)
            windows = {str(ref): key for ref, key in state.windows.items() if key in permitted}
            alias = {key: ref for ref, key in windows.items()}
            current = lookup_context.window.window if lookup_context.window is not None else None
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
            native_state = controller.state(owner)
            name = native_state.name or str(state.selector)
            instance = controller.interfaces.references.publish(
                ApplicationEntry(state.directory["session"], state.directory["application"], name, True)
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
                getattr(owner, "_anchor", None),
                identity.get("current"),
                identity.get("document"),
                tuple(
                    (item["ref"], item.get("parent"), item.get("modal"), item.get("document"))
                    for item in identity["windows"]
                ),
            ),
        )
    if preparation is not None:
        note = (
            "Observation preparation: activation="
            + preparation["activation"]
            + "; window raise="
            + preparation["raiseStatus"]
            + "; foreground="
            + str(preparation["foreground"]).lower()
            + ". Focus/stacking may have changed; no global input was sent. Redraw freshness remains unknown."
        )
        from dataclasses import replace

        document = replace(DisplayDocument.join((document, note)), context=document.context)
    result = lazy_observation(
        document,
        screenshot,
        details_document=captured_details_document(document),
        element_details=CapturedElementDetails.from_document(document),
        preparation=frozen(preparation) if preparation is not None else None,
    )
    retain_observation_data(
        result,
        roots,
        refs,
        windows,
        instance,
        identity,
        partial=partial,
        content_available=content_available,
        gaps=gaps,
        directory=directory,
    )
    if owner is not None:
        # Publication proposes a context. Only actual delivery changes the
        # canonical application's convenience-input receiver, including when
        # this observation was made through an explicit Window handle.
        instance_state = owner._instance
        captured_lookup = lookup_context
        captured = captured_lookup.window if content_available else None
        captured_image = getattr(owner, "_image", None) if screenshot is not None else None
        native_session = (directory or instance_state.directory or {}).get("session")
        instance_state.observation_generation += 1
        generation = instance_state.observation_generation
        retained_state, retained_controller = weakref.ref(instance_state), weakref.ref(controller)
        fixed_owner = context_owner() if context_owner is not None else owner
        fixed = getattr(fixed_owner, "_window_identity", None)
        root = instance_state.members.get(fixed.root_window) if fixed is not None else None
        confirmed_fixed_context = (
            captured is not None
            and root is not None
            and WindowIdentity.of(root) == WindowIdentity(fixed.root_window, fixed.surface, fixed.instance)
            and ancestor(directory or instance_state.directory, captured.window, fixed.root_window)
        )
        retained_fixed = weakref.ref(fixed_owner) if confirmed_fixed_context else None
        fixed_lookup = None
        if confirmed_fixed_context:
            fixed_menus = []
            for menu_owner in captured_lookup.menus:
                menu_identity = controller.state(menu_owner).menu_window_identity
                menu_window = instance_state.members.get(menu_identity.window) if menu_identity is not None else None
                if (
                    menu_window is not None
                    and WindowIdentity.of(menu_window) == menu_identity
                    and ancestor(directory or instance_state.directory, menu_identity.window, fixed.root_window)
                ):
                    fixed_menus.append(menu_owner)
            fixed_lookup = LookupContext(captured, captured_lookup.menu_only, tuple(fixed_menus))

        def delivered() -> None:
            state, runtime = retained_state(), retained_controller()
            if state is None or runtime is None or runtime.transport.session != native_session:
                return
            if generation > state.delivered_observation_generation:
                state.input_context = captured
                state.lookup_context = captured_lookup
                state.delivered_observation_generation = generation
            fixed_target = retained_fixed() if retained_fixed is not None else None
            if fixed_target is not None and generation > fixed_target._delivered_generation:
                fixed_target._input_context = captured
                fixed_target._lookup_context = fixed_lookup
                fixed_target._delivered_generation = generation
            if captured_image is not None and not runtime.adapters.image_delivery_required():
                # Direct SDK return includes the PNG. MCP acknowledges image
                # delivery separately, only after the image actually leaves the
                # tool response; text delivery alone grants no coordinates.
                from computer_use.session.images import image_delivered

                image_delivered(captured_image.png)

        register_observation_delivery(result, delivered)
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


def capture_header(
    controller: SessionController,
    ui: UIHandle,
    directory: DirectoryFacts,
    *,
    parts: Sequence[tuple[str, WindowPart]],
    delivered_gaps: Sequence[tuple[DirectoryFacts, tuple[ObservationGap, ...]]] = (),
) -> InterfaceHeader:
    """Freeze normal-read scope, member headings and each original directory source.

    Existing instance membership and input-report facts determine the header.
    Per-member metadata retains its collection and recheck directories separately.
    """
    instance = ui._instance
    anchor = instance.anchor
    aliases = {key: ref for ref, key in instance.windows.items()}
    permitted = allowed(ui)
    displayed = dict(parts)
    focus = directory.get("focusedWindow")
    windows = []
    for ref, key in instance.windows.items():
        item = instance.members[key]
        part = displayed.get(key)
        source = part.source if part is not None else None
        windows.append(
            MemberHeading(
                ref,
                item["title"],
                key,
                key == ui._anchor,
                key == getattr(ui, "_context", None),
                key == focus,
                aliases.get(item["parent"]),
                item["parentState"],
                item["modal"],
                key in permitted,
                part is not None,
                asdict(part.target) if part is not None else None,
                asdict(source.target) if source is not None else None,
                source.applications if source is not None else None,
                source.source_directory if source is not None else None,
                source.directory if source is not None else None,
                source.scope_confirmed if source is not None else None,
            )
        )
    covered_reasons = {
        gap.detail
        for original, gaps in delivered_gaps
        if original is directory
        for gap in gaps
        if gap.component == "directory"
        and gap.code == "observation_incomplete"
        and gap.next == "inspect the directory again if the required window is missing"
    }
    return InterfaceHeader(
        controller.state(ui).name or (anchor.name if anchor is not None else str(instance.selector)),
        "application" if ui._anchor is None else "window",
        aliases.get(ui._anchor),
        aliases.get(getattr(ui, "_context", None)),
        aliases.get(focus),
        "unknown" if focus is None else "member" if focus in permitted else "outside",
        tuple(windows),
        tuple(reason for reason in directory["stopReasons"] if reason not in covered_reasons),
        directory,
    )


def directory_text(
    controller: SessionController,
    ui: UIHandle,
    directory: DirectoryFacts,
    *,
    delivered_gaps: Sequence[tuple[DirectoryFacts, tuple[ObservationGap, ...]]] = (),
) -> list[DisplayText]:
    """Format the current window catalogue and its unresolved relationship notes.

    Args:
        controller: Session service owner; this dependency does not create another execution
            route.
        ui: App or Window handle whose instance and observation bounds constrain the operation.
        directory: Observed application/window catalogue, including incomplete relationships.
        delivered_gaps: Complete gap records from the window parts actually included below
            this header, paired with their original directory objects. Only identical source
            objects can cover a repeated directory reason.
    """
    header = capture_header(controller, ui, directory, parts=(), delivered_gaps=delivered_gaps)
    return [DisplayText("interface_header", (header,))]


def window_heading(ui: UIHandle, ref: int, title: str) -> DisplayText:
    """Name a window unless this observation's catalogue already names the same window.

    Args:
        ui: Interface whose member catalogue precedes the window sections.
        ref: Published window number, distinct from an element number.
        title: Title retained for the window section being rendered.
    """
    instance = ui._instance
    listed = len(instance.members) > 1 and instance.members.get(instance.windows.get(ref), {}).get("title") == title
    return template("Window {0}:", ref) if listed else template("Window {0}: {1}", ref, quoted(title))
