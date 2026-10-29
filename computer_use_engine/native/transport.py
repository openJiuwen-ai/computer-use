# coding: utf-8
# Copyright (c) Huawei Technologies Co., Ltd. 2026. All rights reserved.

"""Concrete NativePort over the same supervised JSONL connection as observation.

No UI policy lives here: this module converts typed engine objects and strict
wire DTOs. Native errors keep their effect classification; input is never resent.
"""

from __future__ import annotations

import json
from dataclasses import replace
from typing import TYPE_CHECKING

from computer_use.diagnostics import local_failure, pipeline_diagnostic
from computer_use.errors import ComputerUseError
from computer_use.native.contracts import (
    ApplicationBinding,
    Binding,
    Channel,
    Cleanup,
    ClickContinuation,
    ClickOffer,
    ClickPreparationEvidence,
    ClickReceipt,
    ClickTransportEvidence,
    Confirmation,
    CurrentReceiverEvidence,
    Dispatch,
    Facts,
    HostPolicy,
    InputContext,
    Knowledge,
    OpenedOperation,
    Primitive,
    StepResult,
)
from computer_use.native.lifecycle import CallLifecycle
from computer_use.native.queries import NativeNodeQuery
from computer_use.native.semantics import control_capabilities, input_container
from computer_use.protocol.captures import capture_metadata
from computer_use.protocol.primitives import utf16_length

if TYPE_CHECKING:
    from computer_use.protocol.records import Record


_BINDING = dict(
    session="session", instance="instance", root_window="rootWindow", surface="surface", node="node", capture="capture"
)
_CONTEXT = dict(
    foreground_surface="foregroundSurface",
    keyboard_surface="keyboardSurface",
    focused_node="focusedNode",
    focus_ancestors="focusAncestors",
    editor="editor",
    editor_owner="editorOwner",
    selection_known="selectionKnown",
    geometry_current="geometryCurrent",
    window_route="windowRoute",
    pointer_route="pointerRoute",
)


def intent_key(intent):
    return json.dumps(intent, sort_keys=True, ensure_ascii=False, allow_nan=False, separators=(",", ":"))


def encode_fields(value, mapping):
    result = {}
    for name, wire in mapping.items():
        content = getattr(value, name)
        result[wire] = list(content) if isinstance(content, tuple) else content
    return result


def decode_binding(value):
    if "application" in value:
        return ApplicationBinding(value["session"], value["application"])
    return Binding(**{name: value[wire] for name, wire in _BINDING.items()})


def encode_target(target):
    return (
        dict(session=target.session, application=target.application)
        if isinstance(target, ApplicationBinding)
        else encode_fields(target, _BINDING)
    )


def decode_context(value):
    values = {
        name: value.get(wire, "unknown") if name == "pointer_route" else value[wire] for name, wire in _CONTEXT.items()
    }
    values["focus_ancestors"] = tuple(values["focus_ancestors"])
    values["pointer_hit"] = value.get("pointerHit")
    current = value.get("currentReceiver")
    if current is not None:
        values["current_receiver"] = CurrentReceiverEvidence(
            current["state"], current["node"], tuple(current["ancestors"]), current.get("detail", "")
        )
    for name in ("selection_known", "geometry_current", "window_route", "pointer_route"):
        values[name] = Knowledge(values[name])
    return InputContext(**values)


def decode_click_offer(value):
    return (
        None
        if value is None
        else ClickOffer(
            value["routeFamily"],
            value["interaction"],
            value["delivery"],
            value["admission"],
            value["reason"],
            tuple(value["preparationOptions"]),
            value["preferenceProfile"],
            "readyPhysical" if value.get("preferenceHint") == "readyPhysical" else None,
        )
    )


def decode_click_receipt(value):
    if value is None:
        return None
    transport, continuation = value["transportEvidence"], value["continuation"]
    return ClickReceipt(
        tuple(
            ClickPreparationEvidence(
                p["kind"],
                decode_binding(p["scope"]),
                Dispatch(p["dispatch"]),
                p["effect"],
                p["continuation"],
                tuple(p["observations"]),
                p["wait"],
            )
            for p in value["preparationEvidence"]
        ),
        ClickTransportEvidence(
            transport["kind"],
            transport["callEntered"],
            transport["callReturned"],
            transport["submitted"],
            transport["expected"],
        ),
        ClickContinuation(continuation["kind"], decode_binding(continuation["target"]), continuation["routeFamily"]),
    )


class ConnectionPort:
    def __init__(self, connection, policy: HostPolicy):
        if connection.protocol_version != 6:
            raise ValueError("Native execution requires a protocol v6 connection")
        self.connection, self.policy = connection, policy
        self._operations = {}
        self._continuations = {}
        self.calls = CallLifecycle(self)

    @staticmethod
    def pipeline_diagnostic(event):
        pipeline_diagnostic(event)

    @property
    def platform(self):
        return self.connection.platform

    @property
    def capabilities(self):
        return self.connection.capabilities

    def _check(self, operation):
        expected = self._operations.get(operation)
        if expected is None or self.connection.session_identity != expected[0]:
            raise RuntimeError("The native operation's connection ended; it cannot be resumed on another driver")
        return expected[1]

    async def connect(self, timeout_ms):
        hello = await self.connection.connect(timeout_ms)
        if "nativeDirectory" not in hello["capabilities"]:
            raise RuntimeError("This driver does not provide the native discovery contract")
        return Binding(hello["session"], None, None, None)

    async def directory(self, operation, target, query, timeout_ms):
        self._check(operation)
        result = await self.connection.request(
            "inspect",
            dict(operation=operation, target=encode_fields(target, _BINDING), query=query),
            dict(timeoutMs=timeout_ms),
        )
        invalid_result = (
            True
            if (
                result["kind"] != query["kind"]
                or result["session"] != target.session
                or query["kind"] in ("surfaces", "roots")
                and result["application"] != query["application"]
                or result["platform"] != self.platform
            )
            else False
        )
        if invalid_result:
            raise ValueError("Native discovery changed the requested session or application")
        return result

    async def begin(self, intent, target, timeout_ms):
        return await self._begin(
            dict(kind=intent.kind, parameters=dict(intent.parameters)), "write", target, timeout_ms
        )

    async def begin_read(self, target, timeout_ms):
        return await self._begin(dict(kind="read", parameters={}), "read", target, timeout_ms)

    async def _begin(self, intent, mode, target, timeout_ms):
        self.calls.check_begin(intent["kind"], mode, target)
        original = intent_key(intent)
        version = None
        if mode == "write" and intent["kind"] == "click":
            version = next(
                (
                    v
                    for v, capability in (("v2", "nativeClickPipelineV2"), ("v1", "nativeClickPipelineV1"))
                    if capability in self.capabilities
                ),
                None,
            )
        request = dict(intent=json.loads(original), mode=mode, target=encode_target(target))
        if version is not None:
            request["clickContract"] = version
            if "nativeClickPreferenceHintsV1" in self.capabilities:
                request["clickPreferenceHints"] = "v1"
        value = await self.connection.request("begin", request, dict(timeoutMs=timeout_ms))
        if value.get("clickContract") != version:
            self.calls.retired = True
            raise ValueError("Native begin did not acknowledge the requested click contract")
        self._operations[value["operation"]] = (self.connection.session_identity, original, version)
        while len(self._operations) > 8:
            self._operations.pop(next(iter(self._operations)))
        return OpenedOperation(value["operation"], decode_binding(value["target"]), version)

    async def inspect(
        self,
        operation,
        target,
        timeout_ms,
        *,
        within=(),
        actuator=None,
        receiver=None,
        basis=(),
        current_receiver=False,
    ):
        self._check(operation)
        launch = isinstance(target, ApplicationBinding)
        invalid_launch = (
            True
            if launch and (within or actuator is not None or receiver is not None or basis or current_receiver)
            else False
        )
        if invalid_launch:
            raise ValueError("Application launch cannot select an element or editor")
        query = dict(kind="launch") if launch else dict(kind="inputContext", within=list(within))
        if current_receiver:
            invalid_basis = (
                True
                if (
                    target.node is None
                    or target.capture is not None
                    or actuator is not None
                    or receiver is not None
                    or basis
                )
                else False
            )
            if invalid_basis:
                raise ValueError("Current receiver discovery requires an unselected element scope")
            if "nativeCurrentReceiver" not in self.capabilities:
                raise local_failure(
                    "backend does not provide nativeCurrentReceiver",
                    "update the driver before querying current receiver evidence",
                    "mode_unsupported",
                    "inspect",
                )
            query["currentReceiver"] = True
        if actuator is not None:
            invalid_target = (
                True
                if (
                    target.node is None
                    or target.capture is not None
                    or (actuator.relation in ("containedEditor", "controlPart") and target.node == actuator.node)
                )
                else False
            )
            if invalid_target:
                raise ValueError("The actuator requires a compatible element owner")
            if actuator.relation == "controlPart" and "nativeControlParts" not in self.capabilities:
                raise local_failure(
                    "backend does not provide nativeControlParts",
                    "update the driver before selecting a pointer control member",
                    "mode_unsupported",
                    "inspect",
                )
            if actuator.relation == "scrollPointer":
                if "nativeElementScroll" not in self.capabilities:
                    raise local_failure(
                        "backend does not provide nativeElementScroll",
                        "update the driver before selecting an element wheel route",
                        "mode_unsupported",
                        "inspect",
                    )
                if not basis:
                    raise ValueError("An element wheel route requires semantic selection dependencies")
                if actuator.content_path and "nativeScrollContentPath" not in self.capabilities:
                    raise local_failure(
                        "backend does not provide nativeScrollContentPath",
                        "update the driver before selecting moving scroll content",
                        "mode_unsupported",
                        "inspect",
                    )
            query["actuator"] = actuator.wire()
        if receiver is not None:
            if target.node is None or target.capture is not None or not basis:
                raise ValueError("A receiver relation requires an element and semantic selection dependencies")
            capability = {
                "virtualFocus": "nativeVirtualFocus",
                "containedFocus": "nativeFocusMembers",
                "ancestorFocus": "nativeFocusScopes",
            }[receiver.relation]
            if capability not in self.capabilities:
                raise local_failure(
                    "backend does not provide " + capability,
                    "update the driver before selecting a related receiver",
                    "mode_unsupported",
                    "inspect",
                )
            query["receiver"] = receiver.wire()
        if basis:
            if "nativeSelectionBasis" not in self.capabilities:
                raise local_failure(
                    "backend does not provide nativeSelectionBasis",
                    "update the driver before selecting a composed input target",
                    "mode_unsupported",
                    "inspect",
                )
            query["basis"] = [entry.wire() for entry in basis]
        value = await self.connection.request(
            "inspect", dict(operation=operation, target=encode_target(target), query=query), dict(timeoutMs=timeout_ms)
        )
        if value["kind"] != query["kind"]:
            raise ValueError("Native inspection returned a different query kind")
        if tuple(value.get("within", ())) != tuple(within):
            raise ValueError("Native inspection changed the logical target constraints")
        if value.get("basis") != query.get("basis"):
            raise ValueError("Native inspection changed the semantic selection dependencies")
        if value.get("actuator") != query.get("actuator"):
            raise ValueError("Native inspection changed the selected editor")
        if value.get("receiver") != query.get("receiver"):
            raise ValueError("Native inspection changed the selected receiver relationship")
        if not launch and (value["input"].get("currentReceiver") is not None) != current_receiver:
            raise ValueError("Native inspection changed the requested current receiver observation")
        version = self._operations[operation][2]
        if any(("click" in p) != (version in ("v1", "v2")) for p in value["primitives"]):
            raise ValueError("Native primitive does not match the negotiated click contract")
        if version != "v2" and any(p["unit"] == "native_selection" for p in value["primitives"]):
            raise ValueError("Native selection requires the negotiated v2 click contract")
        primitives = tuple(
            Primitive(
                p["id"],
                p["unit"],
                Channel(p["channel"]),
                Knowledge(p["supported"]),
                readiness=Knowledge(p["readiness"]),
                pending=tuple(p["pending"]),
                background_preparation=Knowledge(p["backgroundPreparation"]),
                action=p.get("action", {}).get("name"),
                action_description=p.get("action", {}).get("description"),
                click=decode_click_offer(p.get("click")),
            )
            for p in value["primitives"]
        )
        if launch:
            return Facts(
                decode_binding(value["binding"]),
                value["observed"],
                value["targetLabel"],
                Knowledge.YES,
                Knowledge.YES,
                Knowledge.YES,
                (),
                primitives,
                platform=self.platform,
            )
        return Facts(
            decode_binding(value["binding"]),
            value["observed"],
            value["targetLabel"],
            Knowledge(value["identity"]),
            Knowledge(value["withinScope"]),
            Knowledge(value["enabled"]),
            tuple(value["blockedBy"]),
            primitives,
            decode_context(value["input"]),
            platform=self.connection.platform,
            native_role=value.get("nativeRole"),
            actions_complete=value.get("actionsComplete", False),
            actuator=actuator,
            receiver=receiver,
            application_menu=value.get("applicationMenu"),
            unavailable_reason=value.get("unavailableReason"),
        )

    # Match NativePort.nodes; the adapter collects these fields into NativeNodeQuery.
    # pylint: disable-next=too-many-arguments
    async def nodes(
        self,
        operation,
        target,
        fields,
        max_nodes,
        max_depth,
        timeout_ms,
        *,
        scope_kind=None,
        children_page=None,
        relation_page=None,
        advertised_fields=None,
        follow_relations=None,
        focused_fields=None,
        primary_fields=None,
        text_content=None,
    ):
        """Collect unified observation facts with their separate query scope."""
        from computer_use.protocol.observation.adapters import decode_driver_reply

        request = NativeNodeQuery(
            fields,
            max_nodes,
            max_depth,
            children_page=children_page,
            relation_page=relation_page,
            advertised_fields=advertised_fields,
            follow_relations=follow_relations,
            focused_fields=focused_fields,
            primary_fields=primary_fields,
            text_content=text_content,
        )
        payload, facts = await self._nodes_payload(operation, target, request, timeout_ms)
        reply = decode_driver_reply(payload)
        hints = {
            node["node"]: control_capabilities(self.platform, node, self.capabilities, scope_kind=scope_kind)
            for node in facts["nodes"]
        }
        return replace(
            reply,
            metadata=replace(
                reply.metadata,
                operation_capabilities=hints,
                input_containers={node["node"]: input_container(self.platform, node) for node in facts["nodes"]},
            ),
        )

    async def query_nodes(self, operation, target, fields, max_nodes, max_depth, timeout_ms, **options):
        """Read exact native selection evidence for one input-resolution operation."""
        request = NativeNodeQuery(fields, max_nodes, max_depth, **options)
        _, facts = await self._nodes_payload(operation, target, request, timeout_ms)
        return facts

    async def _nodes_payload(
        self, operation: str, target: Binding, request: NativeNodeQuery, timeout_ms: int
    ) -> tuple[Record, Record]:
        self._check(operation)
        query = dict(kind="nodes", fields=list(request.fields), maxNodes=request.max_nodes, maxDepth=request.max_depth)
        if request.text_content is not None:
            if self.platform != "macos" or "nativeParameterizedText" not in self.connection.capabilities:
                raise ValueError("This driver cannot capture parameterized native text")
            if type(request.text_content) is not bool:
                raise ValueError("text_content must be a boolean")
            query["textContent"] = request.text_content
        if request.primary_fields is not None:
            if "nativeUiaTextLines" not in self.connection.capabilities:
                raise ValueError("This driver cannot limit line probing to primary text objects")
            query["primaryFields"] = list(request.primary_fields)
        if request.focused_fields is not None:
            if "nativeUiaTextContext" not in self.connection.capabilities:
                raise ValueError("This driver cannot limit caret probing to focused objects")
            query["focusedFields"] = list(request.focused_fields)
        if request.follow_relations is not None:
            if "nativeFollowRelations" not in self.connection.capabilities:
                raise ValueError("This driver cannot collect additional native relationships")
            query["followRelations"] = list(request.follow_relations)
        if request.advertised_fields is not None:
            if "nativeAdvertisedFields" not in self.connection.capabilities:
                raise ValueError("This driver cannot query advertised native fields")
            query["advertisedFields"] = list(request.advertised_fields)
        if request.children_page is not None and request.relation_page is not None:
            raise ValueError("Select one root relation page")
        page = request.relation_page if request.relation_page is not None else request.children_page
        property_name = (
            request.relation_page["property"] if request.relation_page is not None else self._children_property()
        )
        if request.relation_page is not None and "nativeRelationPages" not in self.connection.capabilities:
            raise ValueError("This driver cannot collect native relationship pages")
        expected = None
        if page is not None:
            query["relationPage" if request.relation_page is not None else "childrenPage"] = page
            cursor = page["cursor"]
            expected = self._continuations.get(cursor) if cursor is not None else None
            if cursor is not None and (
                expected is None or expected[:4] != (target.session, target.surface, target.node, property_name)
            ):
                raise ComputerUseError(
                    dict(
                        code="snapshot_expired",
                        effect="none",
                        target="element content",
                        detail="The continuation is no longer retained for this element and relationship.",
                        next="read the element again and use its reported continuation",
                    )
                )
        result = await self.connection.request(
            "inspect",
            dict(operation=operation, target=encode_fields(target, _BINDING), query=query),
            dict(timeoutMs=timeout_ms),
        )
        payload = result
        from computer_use.protocol.observation.adapters import decode_query_facts

        result = decode_query_facts(payload)
        if request.follow_relations is not None and not set(request.follow_relations) <= set(
            result.get("requestedRelations", ())
        ):
            raise ValueError("Native collection omitted requested relationship coverage")
        invalid_result_condition = (
            True
            if (
                result["session"] != target.session
                or result["surface"] != target.surface
                or result["platform"] != self.connection.platform
                or result["roots"] != [target.node]
                or len(result["nodes"]) > request.max_nodes
            )
            else False
        )
        if invalid_result_condition:
            raise ValueError("Native fact batch changed its requested scope or budget")
        if page is not None:
            relation = next(
                (r for r in result["relations"] if r["source"] == target.node and r["property"] == property_name), None
            )
            if relation is None:
                raise ValueError("Native page omitted its requested relation")
            if relation["state"] in ("complete", "partial") and relation["certainty"] == "confirmed":
                invalid_expected = (
                    True
                    if (
                        relation.get("offset", 0) != (expected[4] if expected else 0)
                        or expected is not None
                        and relation.get("total") != expected[5]
                        or len(relation["targets"]) > page["limit"]
                    )
                    else False
                )
                if invalid_expected:
                    raise ValueError("Native page changed its continuation range or budget")
        self._remember_continuations(result, result["relations"])
        return payload, result

    async def wait_for_change(self, operation, target, after, delay_ms, timeout_ms):
        self._check(operation)
        invalid_target_condition = (
            True
            if (
                "nativeReadEvents" not in self.capabilities
                or self.platform != "windows"
                or target.node is None
                or target.capture is not None
            )
            else False
        )
        if invalid_target_condition:
            raise ValueError("Event-assisted waiting requires a compatible observed window node")
        invalid_after = (
            True
            # Exact types preserve scalar validation and identity contracts.
            # pylint: disable-next=unidiomatic-typecheck
            if not isinstance(after, str) or not after or type(delay_ms) is not int or not 1 <= delay_ms <= 400
            else False
        )
        if invalid_after:
            raise ValueError("Invalid event-assisted wait token or delay")
        result = await self.connection.request(
            "inspect",
            dict(
                operation=operation,
                target=encode_fields(target, _BINDING),
                query=dict(kind="readChange", after=after, timeoutMs=delay_ms),
            ),
            dict(timeoutMs=timeout_ms),
        )
        invalid_after_condition = (
            True
            if (
                result["kind"] != "readChange"
                or result["session"] != target.session
                or result["surface"] != target.surface
                or result["after"] != after
            )
            else False
        )
        if invalid_after_condition:
            raise ValueError("Event-assisted wait changed its observed session, window or token")
        return result

    def _children_property(self):
        from computer_use.native.semantics import RELATIONS

        return next(key for key, meaning in RELATIONS[self.platform].items() if meaning == "children")

    def _remember_continuations(self, result, relations):
        for relation in relations:
            if "cursor" in relation:
                self._continuations[relation["cursor"]] = (
                    result["session"],
                    result["surface"],
                    relation["source"],
                    relation["property"],
                    relation.get("offset", 0) + len(relation["targets"]),
                    relation["total"],
                )
        while len(self._continuations) > 512:
            self._continuations.pop(next(iter(self._continuations)))

    async def relation(self, operation, target, relation_property, cursor, limit, timeout_ms):
        """Collect one normalized relation page and its original query selector."""
        from computer_use.protocol.observation.adapters import decode_driver_reply

        payload, _ = await self._relation_payload(operation, target, relation_property, cursor, limit, timeout_ms)
        return decode_driver_reply(payload)

    async def query_relation(self, operation, target, relation_property, cursor, limit, timeout_ms):
        """Read exact native relation evidence for an operation's target lookup."""
        _, result = await self._relation_payload(operation, target, relation_property, cursor, limit, timeout_ms)
        return result

    async def _relation_payload(self, operation, target, relation_property, cursor, limit, timeout_ms):
        self._check(operation)
        expected = self._continuations.get(cursor) if cursor is not None else None
        if cursor is not None and (
            expected is None or expected[:4] != (target.session, target.surface, target.node, relation_property)
        ):
            raise ValueError("Relation cursor was not observed for this source and scope")
        result = await self.connection.request(
            "inspect",
            dict(
                operation=operation,
                target=encode_fields(target, _BINDING),
                query=dict(kind="relation", property=relation_property, cursor=cursor, limit=limit),
            ),
            dict(timeoutMs=timeout_ms),
        )
        payload = result
        from computer_use.protocol.observation.adapters import decode_query_facts

        batch = decode_query_facts(payload)
        if len(batch["relations"]) != 1:
            raise ValueError("Native relation query requires one relationship result")
        result = {k: batch[k] for k in ("session", "platform", "surface", "observed")}
        result.update(kind="relation", relation=batch["relations"][0])
        if (
            result["session"] != target.session
            or result["surface"] != target.surface
            or result["platform"] != self.connection.platform
        ):
            raise ValueError("Native relation page changed its requested scope")
        relation = result["relation"]
        if (
            relation["source"] != target.node
            or relation["property"] != relation_property
            or len(relation["targets"]) > limit
        ):
            raise ValueError("Native relation page changed its source, property or budget")
        if relation["state"] in ("complete", "partial"):
            if (
                relation.get("offset", 0) != (expected[4] if expected else 0)
                or expected is not None
                and relation.get("total") != expected[5]
            ):
                raise ValueError("Native relation continuation changed its observed range")
        self._remember_continuations(result, [relation])
        return payload, result

    async def captured_text(self, operation, target, text_ref, offset, length, timeout_ms):
        self._check(operation)
        value = await self.connection.request(
            "inspect",
            dict(
                operation=operation,
                target=encode_fields(target, _BINDING),
                query=dict(kind="capturedText", textRef=text_ref, offset=offset, length=length),
            ),
            dict(timeoutMs=timeout_ms),
        )
        if value["kind"] != "capturedText" or value["session"] != target.session or value["textRef"] != text_ref:
            raise ValueError("Native text page changed its requested capture")
        page = value["page"]
        if page["offset"] != offset or utf16_length(page["text"]) > length:
            raise ValueError("Native text page changed its requested range or budget")
        return page

    async def capture(self, operation, target, max_dimension, timeout_ms, *, kind="window"):
        self._check(operation)
        value = await self.connection.request(
            "capture",
            dict(operation=operation, target=encode_fields(target, _BINDING), kind=kind, maxDimension=max_dimension),
            dict(timeoutMs=timeout_ms),
        )
        if (
            value["kind"] != ("desktopCapture" if kind == "desktop" else "capture")
            or decode_binding(value["binding"]) != target
        ):
            raise ValueError("Native capture changed its requested kind or scope")
        if kind == "desktop":
            dimensions = value["observation"]["canvas"]
            if value["observation"]["mode"] != self.policy.mode:
                raise ValueError("Native desktop capture changed its negotiated mode")
        else:
            dimensions = capture_metadata(value["image"])
        if max(dimensions["width"], dimensions["height"]) > max_dimension:
            raise ValueError("Native capture exceeded its requested image budget")
        return value

    async def execute(self, operation, step, command, timeout_ms):
        original = self._check(operation)
        version = self._operations[operation][2]
        if command.primitive.unit == "native_selection" and version != "v2":
            raise ValueError("Native selection requires the negotiated v2 click contract")
        if (
            (command.primitive.click is not None) != (version in ("v1", "v2"))
            or version in ("v1", "v2")
            and command.prepare
        ):
            raise ValueError("Click execution must use the negotiated integrated native contract")
        if original != intent_key(dict(kind=command.intent.kind, parameters=dict(command.intent.parameters))):
            raise local_failure(
                "The operation intent is already bound; a step cannot change its parameters",
                "finish this operation before requesting a different action",
                "invalid_request",
                "execute",
            )
        if (
            command.intent.kind == "replace"
            and command.primitive.unit == "key_sequence"
            and "nativeTextReplacement" not in self.capabilities
        ):
            raise local_failure(
                "backend does not provide nativeTextReplacement",
                "update the driver before selecting a contextual replacement unit",
                "mode_unsupported",
                "execute",
            )
        if (
            command.primitive.unit == "pointer_sequence"
            and command.target.node is not None
            and "nativeElementPointer" not in self.capabilities
        ):
            raise local_failure(
                "backend does not provide nativeElementPointer",
                "update the driver before selecting an element pointer unit",
                "mode_unsupported",
                "execute",
            )
        if (
            command.actuator is not None
            and command.actuator.relation == "controlPart"
            and "nativeControlParts" not in self.capabilities
        ):
            raise local_failure(
                "backend does not provide nativeControlParts",
                "update the driver before selecting a pointer control member",
                "mode_unsupported",
                "execute",
            )
        if (
            command.actuator is not None
            and command.actuator.relation == "scrollPointer"
            and "nativeElementScroll" not in self.capabilities
        ):
            raise local_failure(
                "backend does not provide nativeElementScroll",
                "update the driver before selecting an element wheel route",
                "mode_unsupported",
                "execute",
            )
        invalid_command = (
            True
            if (
                command.actuator is not None
                and command.actuator.relation == "scrollPointer"
                and command.actuator.content_path
                and "nativeScrollContentPath" not in self.capabilities
            )
            else False
        )
        if invalid_command:
            raise local_failure(
                "backend does not provide nativeScrollContentPath",
                "update the driver before selecting moving scroll content",
                "mode_unsupported",
                "execute",
            )
        wire = dict(
            kind="prepare_receiver" if command.prepare else command.primitive.unit,
            primitiveId=command.primitive.id,
            target=encode_target(command.target),
            foreground=command.foreground,
        )
        value = await self.connection.request(
            "execute", dict(operation=operation, step=step, command=wire), dict(timeoutMs=timeout_ms)
        )
        if ("click" in value) != (version in ("v1", "v2")):
            self.calls.retired = True
            raise ValueError("Native receipt does not match the negotiated click contract")
        if (
            version in ("v1", "v2")
            and value["click"]["continuation"]["routeFamily"] != command.primitive.click.route_family
        ):
            self.calls.retired = True
            raise ValueError("Native click changed its selected delivery family")
        if version in ("v1", "v2"):
            transport = value["click"]["transportEvidence"]["kind"]
            matches_transport = (
                True
                if (
                    (
                        transport == "nativeSelection"
                        and (version != "v2" or command.primitive.unit != "native_selection")
                    )
                    or command.primitive.unit == "native_selection"
                    and transport not in ("none", "nativeSelection")
                )
                else False
            )
            if matches_transport:
                self.calls.retired = True
                raise ValueError("Native selection receipt changed its negotiated transport")
        return StepResult(
            decode_binding(value["target"]),
            Dispatch(value["dispatch"]),
            value["detail"],
            tuple(value["preparation"]),
            Confirmation(value["confirmation"]),
            Cleanup(value["cleanup"]),
            value["errorCode"],
            value["retryReason"],
            value["context"],
            value.get("application"),
            decode_click_receipt(value.get("click")),
        )

    async def finish(self, operation):
        self._check(operation)
        value = await self.connection.request("finish", dict(operation=operation), dict(timeoutMs=1000))
        return Cleanup(value["cleanup"])
