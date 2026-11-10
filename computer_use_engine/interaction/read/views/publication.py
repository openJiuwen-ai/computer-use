# Copyright (c) Huawei Technologies Co., Ltd. 2026. All rights reserved.

"""One bounded public body and optional image, retaining native read evidence."""

from __future__ import annotations

import weakref
from dataclasses import asdict
from typing import TYPE_CHECKING, cast

from computer_use.diagnostics import diagnostic
from computer_use.interaction.query.instances.membership import interaction_contains
from computer_use.interaction.query.instances.scope import allowed
from computer_use.interaction.read.acquisition.components.support import ObservationGap
from computer_use.interaction.read.delivery.coverage import document_coverage, tree_collection_issues
from computer_use.interaction.read.presentation.content import DisplayText, template
from computer_use.interaction.read.presentation.content import json_value as quoted
from computer_use.interaction.read.presentation.details import captured_details_document
from computer_use.interaction.read.presentation.document import DisplayDocument, DisplayTree
from computer_use.interaction.read.presentation.element_details import CapturedElementDetails
from computer_use.interaction.read.presentation.headers import InterfaceHeader, MemberHeading
from computer_use.interaction.read.presentation.preparation import prepare_snapshot
from computer_use.protocol.primitives import frozen
from computer_use.session.events import observation_completed, register_observation_delivery
from computer_use.session.interfaces import ApplicationEntry, LookupContext, WindowIdentity, WindowLifetime
from computer_use.session.observation import lazy_observation
from computer_use.session.observation_data import component_delivered, retain_observation_data
from computer_use.session.surface_content import content_host, content_kind, transient_content

from .related import original_lifecycle

if TYPE_CHECKING:
    from collections.abc import Mapping, Sequence

    from computer_use.interaction.controller import SessionController
    from computer_use.interaction.read.facts.snapshot import SemanticSnapshot
    from computer_use.interaction.read.presentation.preparation import PreparedSnapshot
    from computer_use.interaction.read.views.pipeline import WindowPart
    from computer_use.interaction.read.views.related import RelatedRead
    from computer_use.interaction.records import UIHandle
    from computer_use.native.contracts import Binding
    from computer_use.protocol.png import PNG
    from computer_use.protocol.records import NativeDirectory as DirectoryFacts
    from computer_use.types import Observation


def related_window_summary(
    related: RelatedRead,
    directory: DirectoryFacts,
    aliases: Mapping[int, str],
    *,
    refresh_incomplete: bool = False,
) -> DisplayText:
    """Describe catalogue evidence after input, without interpreting its UI effect.

    A retained popup may disappear and reappear, and a menu can live inside an
    existing window. Compare identities with the pre-action catalogue, never
    treating newly observed membership as creation or absence as menu failure.
    """
    newly_observed = {
        item["window"] for item in directory["surfaces"]
        if WindowIdentity.of(item) not in related.known_windows
    }
    members = {item["window"]: item for item in directory["surfaces"]}
    attached = {
        key for key in newly_observed if transient_content(members[key]) and content_host(members[key]) in members
    }
    hosts = {content_host(members[key]) for key in attached}
    menus = [f"window={ref}" for ref, key in aliases.items() if key in hosts]
    sections = []
    for kinds, label in (
        ({"window"}, "newly observed window entries"),
        ({"menu"}, "newly observed menu sources"),
        ({"popup"}, "newly observed popup sources"),
        ({"unknown", "application"}, "newly observed interface sources"),
    ):
        refs = [f"window={ref}" for ref, key in aliases.items()
                if key in newly_observed - attached and content_kind(members[key]) in kinds]
        if refs:
            sections.append(label + ": " + ", ".join(refs))
    if menus:
        sections.append("newly observed attached content for " + ", ".join(menus))
    discovered = directory.get("rootDiscoveryComplete")
    coverage = (
        "partial" if refresh_incomplete or directory["stopReasons"] or discovered is False
        else "complete" if discovered is True
        else "unconfirmed"
    )
    membership = "; ".join(sections) if sections else (
        "newly observed interface sources have no published references" if newly_observed
        else "no newly observed window entries"
    )
    detail = ""
    if related.window_family_only and not related.content_members(directory):
        lifecycle = original_lifecycle(related.original, directory, related.original_window)
        state = "closed" if lifecycle == "closed" else "absent from this catalogue; lifetime unconfirmed"
        detail = f" Original window: {state}. Other windows are listed without expansion."
    return template("After action: {0}; catalogue {1}.{2}", membership, coverage, detail)


def related_focus_summary(
    target: Binding, prepared: PreparedSnapshot, window_ref: int,
) -> DisplayText | None:
    """Present a scoped AX sample independently of unchanged tree rows."""
    snapshot = prepared.snapshot
    collection = snapshot.collection
    if collection is None or collection.surface != target.surface or snapshot.scope_kind != "window":
        return None
    token = collection.focused_element
    node = snapshot.native.nodes.get(token) if token is not None else None
    if node is None or node.states.focused is False:
        return None
    ref = next((ref for ref in prepared.refs if snapshot.refs.get(ref) == token), None)
    if ref is None:
        return None
    return template("Reported AX focus in window {0} at capture: element {1}.", window_ref, ref)


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
    captured_trees: tuple[tuple[Binding, SemanticSnapshot], ...] = (),
    full: bool = False,
    scope: str | None = None,
    view: str = "window",
    directory: DirectoryFacts | None = None,
    partial: bool = False,
    content_available: bool = True,
    gaps: tuple[ObservationGap, ...] = (),
    preparation: dict | None = None,
    context_owner=None,
    content_owner=None,
    lookup_context: LookupContext | None = None,
    on_observation=None,
    window_coverage=(),
    presentations=(),
    member_trees: Mapping[WindowIdentity, SemanticSnapshot] | None = None,
    transient_window: tuple[WindowIdentity, int] | None = None,
) -> Observation:
    """Publish the combined interface observation and retain its source data.

    Args:
        controller: Session observation and interface services.
        owner: App or request scope that owns the observed interface, when applicable.
        text: Formatted observation text delivered to the model.
        screenshot: Captured image delivered with this observation, when requested.
        trees: Native target/snapshot pairs retained for later element operations.
        full: Whether this publication requests complete-tree delivery.
        scope: Observed native scope; defaults to the desktop label.
        view: Scope label attached to the publication event, such as window or desktop.
        directory: Current application window catalogue, when available.
        lookup_context: Exact source context for explicit subtree publication; ordinary reads capture their staged view.
        on_observation: Internal sink for deferring delivery while one optional refresh is attempted.
        transient_window: Exact newly observed window when it supplies the whole automatic body.
    """
    document = text if isinstance(text, DisplayDocument) else DisplayDocument.join((text,))
    # Only sources actually included as tree components contribute to this
    # publication. A native collection cutoff is independent of whether its
    # one remaining root happens to be present or an image is also available.
    shown = tuple(part.prepared.snapshot.native for part in document.parts if isinstance(part, DisplayTree))
    incomplete = []
    for target, snapshot in trees:
        if any(source is snapshot.native for source in shown):
            issues = tree_collection_issues(snapshot)
            if issues:
                incomplete.append((target, snapshot, issues))
    if incomplete:
        partial = True
        collection_gaps = tuple(
            ObservationGap(
                "tree", "observation_incomplete",
                "Native tree collection incomplete: " + "; ".join(dict.fromkeys(
                    issue.detail or issue.state for issue in issues
                )),
            )
            for _, _, issues in incomplete
        )
        gaps = (*gaps, *collection_gaps)
        document = DisplayDocument.join((
            document,
            "Observation coverage: partial. " + " ".join(dict.fromkeys(gap.detail for gap in collection_gaps)),
        ))
        by_window = {
            identity: snapshot for identity, snapshot in member_trees.items() if tree_collection_issues(snapshot)
        } if member_trees is not None else {
            WindowIdentity(target.root_window, target.surface, target.instance): snapshot
            for target, snapshot, _ in incomplete
        }
        coverage_with_gaps = []
        for record in window_coverage:
            if "tree" in record["included"]:
                identity = WindowIdentity(record["window"], record["surface"], record["instance"])
                snapshot = by_window.get(identity)
                if snapshot is not None:
                    record = {
                        **record,
                        "partial": tuple(sorted(set(record.get("partial", ())) | {"tree"})),
                        "treeCollection": {
                            "session": snapshot.native.session,
                            "observation": snapshot.native.id,
                            "intervalNs": snapshot.native.interval_ns,
                        },
                    }
            coverage_with_gaps.append(record)
        window_coverage = tuple(coverage_with_gaps)
    roots = [root for _, snapshot in trees for root in snapshot.roots]
    refs = {ref: node for _, snapshot in trees for ref, node in snapshot.refs.items()}
    windows, instance, identity = {}, None, None
    if owner is not None and lookup_context is None:
        # pylint: disable-next=protected-access
        item = owner._instance.members.get(getattr(owner, "_context", None))
        lookup_context = LookupContext(
            WindowIdentity.of(item) if item is not None and content_available else None,
            bool(getattr(owner, "_menu_only", False)),
            tuple(getattr(owner, "_lookup_scopes", ())),
            content_owner,
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
            identity = {"current": alias.get(current), "windows": []}
            for window_ref, key in windows.items():
                window_data = dict(
                    ref=window_ref,
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
        window_coverage=window_coverage,
        captured_trees=captured_trees,
        presentations=presentations,
    )
    if window_coverage:
        diagnostic({"kind": "observation_member_coverage", "windows": window_coverage})
    retained_result = weakref.ref(result)
    if screenshot is not None and window_coverage:
        from computer_use.session.images import register_image_delivery

        def pixels_delivered():
            observed = retained_result()
            if observed is not None:
                component_delivered(observed, "image")

        register_image_delivery(screenshot, pixels_delivered)
    if owner is not None:
        # Publication proposes a context. Only actual delivery changes the
        # canonical application's convenience-input receiver, including when
        # this observation was made through an explicit window parameter.
        # pylint: disable-next=protected-access
        instance_state = owner._instance
        captured_lookup = lookup_context
        captured_query_windows = frozenset(member_trees) if member_trees is not None else frozenset(
            WindowIdentity(target.root_window, target.surface, target.instance)
            for target, snapshot in trees
            if target.root_window is not None and any(source is snapshot.native for source in shown)
        )
        captured = captured_lookup.window if content_available else None
        captured_image = getattr(owner, "_image", None) if screenshot is not None else None
        native_session = (directory or instance_state.directory or {}).get("session")
        instance_state.observation_generation += 1
        generation = instance_state.observation_generation
        from computer_use.session.observed_lookup import captured_lookup_scopes, publish_lookup_scopes

        captured_name_sources = captured_lookup_scopes(
            instance_state, trees, shown, generation, controller.transport.capabilities, window_coverage,
        )
        retained_state, retained_controller = weakref.ref(instance_state), weakref.ref(controller)
        fixed_owner = context_owner() if context_owner is not None else owner
        fixed = getattr(fixed_owner, "_window_identity", None)
        candidates = set(captured_query_windows)
        if captured is not None:
            candidates.add(captured)
            # An actually delivered sheet/menu also supplies context for its
            # confirmed parents. This does not depend on retaining the scope
            # object that originally opened it, or on reading the parent again.
            candidates.update(
                WindowIdentity.of(item) for item in instance_state.members.values()
                if interaction_contains(directory or instance_state.directory, captured.window, item["window"])
            )
        if fixed is not None:
            candidates.add(WindowIdentity(fixed.root_window, fixed.surface, fixed.instance))
        contexts = {}
        for root_identity in candidates:
            root = instance_state.members.get(root_identity.window)
            if captured is None or root is None or WindowIdentity.of(root) != root_identity:
                continue
            if not interaction_contains(directory or instance_state.directory, captured.window, root_identity.window):
                continue
            menus = []
            for menu_owner in captured_lookup.menus:
                menu_identity = controller.state(menu_owner).menu_window_identity
                menu_window = instance_state.members.get(menu_identity.window) if menu_identity is not None else None
                if menu_window is None or WindowIdentity.of(menu_window) != menu_identity:
                    continue
                if interaction_contains(
                    directory or instance_state.directory, menu_identity.window, root_identity.window,
                ):
                    menus.append(menu_owner)
            contexts[root_identity] = LookupContext(captured, captured_lookup.menu_only, tuple(menus))

        def delivered() -> None:
            observed = retained_result()
            if observed is not None:
                component_delivered(observed, "tree")
            state, runtime = retained_state(), retained_controller()
            if state is None or runtime is None or runtime.transport.session != native_session:
                return
            publish_lookup_scopes(state, captured_name_sources)
            state.delivered_lookup_windows.update(
                identity for identity in captured_query_windows
                if identity in state.evidence and state.evidence[identity].lifetime is WindowLifetime.RETAINED
            )
            if generation > state.delivered_observation_generation:
                state.input_context = captured
                state.lookup_context = captured_lookup
                state.delivered_observation_generation = generation
            from computer_use.session.interfaces import DeliveredWindowContext

            for identity, lookup in contexts.items():
                evidence = state.evidence.get(identity)
                previous = state.window_contexts.get(identity)
                if evidence is None or evidence.lifetime is not WindowLifetime.RETAINED:
                    continue
                if previous is None or generation > previous.generation:
                    state.window_contexts[identity] = DeliveredWindowContext(captured, lookup, generation)
            if captured_image is not None and not runtime.adapters.image_delivery_required():
                # Direct SDK return includes the PNG. MCP acknowledges image
                # delivery separately, only after the image actually leaves the
                # tool response; text delivery alone grants no coordinates.
                from computer_use.session.images import image_delivered

                image_delivered(captured_image.png)

        register_observation_delivery(result, delivered)
    (on_observation or observation_completed)(
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
            coverage=document_coverage(document),
            **({"observed": directory["observed"]} if directory else {}),
            **({"transientWindow": transient_window} if transient_window is not None else {}),
        )
    )
    if owner is not None:
        # The host accepted this result for its running script. Make only its
        # semantic query scope available locally; actual delivery below/after
        # host buffering remains the sole diff/image/input commitment.
        from computer_use.session.lookup_continuation import retain_lookup_continuation

        retain_lookup_continuation(
            instance_state, captured_lookup, captured_query_windows, generation, native_session,
            sources=captured_name_sources,
        )
    return cast("Observation", result)


def strict_public_directory(directory: DirectoryFacts | None, identity: WindowIdentity) -> DirectoryFacts | None:
    """Project window metadata for delivery while retaining the internal catalogue."""
    if directory is None:
        return None
    surfaces = [
        {**item, "parent": None, "parentState": "unknown"} if item["parent"] is not None else item
        for item in directory["surfaces"] if WindowIdentity.of(item) == identity
    ]
    projected = {**directory, "surfaces": surfaces, "strictWindow": True}
    if not surfaces or projected.get("focusedWindow") != identity.window:
        projected.pop("focusedWindow", None)
    if "endedWindows" in projected:
        projected["endedWindows"] = [item for item in projected["endedWindows"] if WindowIdentity.of(item) == identity]
    return projected


def strict_public_applications(directory: DirectoryFacts | None, application: str, window: str):
    if directory is None:
        return None
    projected = {
        **directory,
        "applications": [
            {**item, "windows": [entry for entry in item.get("windows", ()) if entry["window"] == window]}
            for item in directory["applications"] if item.get("id") == application
        ],
    }
    if "nativeProcessNames" in projected:
        projected["nativeProcessNames"] = [
            row for row in projected["nativeProcessNames"] if row["application"] == application
        ]
    return projected


def capture_header(
    controller: SessionController,
    ui: UIHandle,
    directory: DirectoryFacts,
    *,
    parts: Sequence[tuple[str, WindowPart]],
    delivered_gaps: Sequence[tuple[DirectoryFacts, tuple[ObservationGap, ...]]] = (),
    image_windows: tuple[str, ...] | None = None,
) -> InterfaceHeader:
    """Freeze normal-read scope, member headings and each original directory source.

    Existing instance membership and input-report facts determine the header.
    Per-member metadata retains its collection and recheck directories separately.
    """
    # pylint: disable-next=protected-access
    instance = ui._instance
    anchor = instance.anchor
    strict = getattr(ui, "_strict_window", False)
    # pylint: disable-next=protected-access
    aliases = {key: ref for ref, key in instance.windows.items() if not strict or key == ui._anchor}
    permitted = allowed(ui)
    focus = directory.get("focusedWindow")
    windows = []
    entries = {
        ref: (key, instance.members[key]) for ref, key in instance.windows.items()
        # pylint: disable-next=protected-access
        if not strict or key == ui._anchor
    }
    for key, part in parts:
        if part.source is None:
            continue
        identity = part.identity
        ref = controller.interfaces.windows.reference(part.target.session, directory["application"], identity)
        item = next((
            member for member in part.source.source_directory["surfaces"]
            if WindowIdentity.of(member) == identity
        ), None)
        if ref is not None and item is not None and ref not in entries:
            entries[ref] = (key, item)
    for window_ref, (key, item) in entries.items():
        part = next((part for _, part in parts if part.identity == WindowIdentity.of(item)), None)
        source = part.source if part is not None else None
        source_apps = source.applications if source is not None else None
        source_directory = source.source_directory if source is not None else None
        rechecked_directory = source.directory if source is not None else None
        if strict:
            source_apps = strict_public_applications(source_apps, directory["application"], key)
            source_directory = strict_public_directory(source_directory, ui.identity)
            rechecked_directory = strict_public_directory(rechecked_directory, ui.identity)
        current_member = permitted.get(key)
        current_member = current_member is not None and WindowIdentity.of(current_member) == WindowIdentity.of(item)
        parent_ref = aliases.get(item["parent"])
        if not strict and not current_member and source is not None:
            parent = next((
                member for member in source.source_directory["surfaces"] if member["window"] == item["parent"]
            ), None)
            parent_ref = controller.interfaces.windows.reference(
                source.target.session, directory["application"], WindowIdentity.of(parent),
            ) if parent is not None else None
        windows.append(
            MemberHeading(
                window_ref,
                item["title"],
                key,
                # pylint: disable-next=protected-access
                key == ui._anchor,
                current_member and key == getattr(ui, "_context", None),
                current_member and key == focus,
                parent_ref,
                "unknown" if strict and item["parent"] is not None else item["parentState"],
                item["modal"],
                current_member,
                part is not None and (
                    part.tree is not None
                    or part.image is not None and (image_windows is None or key in image_windows)
                ),
                asdict(part.target) if part is not None else None,
                asdict(source.target) if source is not None else None,
                source_apps,
                source_directory,
                rechecked_directory,
                source.scope_confirmed if source is not None else None,
                content_kind(item),
                aliases.get(content_host(item)),
            )
        )
    covered_reasons = set()
    for original, gaps in delivered_gaps:
        if original is directory:
            covered_reasons.update(
                gap.detail for gap in gaps if gap.component == "directory" and gap.code == "observation_incomplete"
            )
    return InterfaceHeader(
        controller.state(ui).name or (anchor.name if anchor is not None else str(instance.selector)),
        # pylint: disable-next=protected-access
        "application" if ui._anchor is None else "window",
        # pylint: disable-next=protected-access
        aliases.get(ui._anchor),
        aliases.get(getattr(ui, "_context", None)),
        aliases.get(focus),
        "unknown" if focus is None else "member" if focus in permitted else "outside",
        tuple(windows),
        tuple(reason for reason in directory["stopReasons"] if reason not in covered_reasons),
        strict_public_directory(directory, ui.identity) if strict else directory,
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
        ui: App or request scope whose instance and observation bounds constrain the operation.
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
    # pylint: disable-next=protected-access
    instance = ui._instance
    listed = len(instance.members) > 1 and instance.members.get(instance.windows.get(ref), {}).get("title") == title
    return template("Window {0}:", ref) if listed else template("Window {0}: {1}", ref, quoted(title))
