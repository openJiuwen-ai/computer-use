# coding: utf-8
# Copyright (c) Huawei Technologies Co., Ltd. 2026. All rights reserved.

"""Protocol 6 operations; never forward to legacy Reader or Control."""

from __future__ import annotations

from functools import partial

from computer_use_linux.capture.native_capture import NativeCapture
from computer_use_linux.capture.pipeline import NativeCapturePipeline
from computer_use_linux.contracts.protocol import Failure, fields, integer, require, string
from computer_use_linux.control.click.pipeline import NativeClickPipeline
from computer_use_linux.control.input_context.model import ContextUnits, InputUnits
from computer_use_linux.control.input_context.pipeline import NativeInputContextPipeline
from computer_use_linux.control.native_action import NativeAction, action_intent
from computer_use_linux.control.native_current_receiver import NativeCurrentReceiver
from computer_use_linux.control.native_desktop_input import NativeDesktopInput
from computer_use_linux.control.native_element_pointer import NativeElementPointer
from computer_use_linux.control.native_keyboard import NativeKeyboard, keyboard_intent
from computer_use_linux.control.native_launch import NativeLaunch
from computer_use_linux.control.native_paste import NativePaste, paste_intent
from computer_use_linux.control.native_pointer import NativePointer, pointer_intent
from computer_use_linux.control.native_selection import NativeSelection, decode_selection_request
from computer_use_linux.control.native_text import NativeText, text_intent
from computer_use_linux.control.native_typed_text import NativeTypedText
from computer_use_linux.control.pipeline import NativeInputPipeline
from computer_use_linux.objects.keyboard_recipients import NativeKeyboardRecipients
from computer_use_linux.objects.native_store import NativeStore
from computer_use_linux.read.native_facts import FOLLOW_RELATIONS, NativeFacts
from computer_use_linux.read.pipeline import NativeQueryPipeline
from computer_use_linux.runtime.resource_scope.pipeline import NativeResourceScope
from computer_use_linux.windowing.native_directory import NativeDirectory

CAPABILITIES = [
    "cancel",
    "requestDedup",
    "nativeOperations",
    "nativeDirectory",
    "nativeObservationV1",
    "nativeRelations",
    "nativeNodePages",
    "nativeNodeDepthBudget",
    "nativeRelationPages",
    "nativeCapturedText",
    "nativeTableFacts",
    "nativeAdvertisedFields",
    "nativeFollowRelations",
    "nativeTableContent",
    "nativeTableViewport",
    "nativeRawNodeMetadata",
    "desktopCoordination",
    "nativeBoundContracts",
    "nativeUnitReadinessV2",
    "nativeActuators",
    "nativeSelectionBasis",
    "nativeCurrentReceiver",
    "nativeTextInput",
    "nativeTextReplacement",
    "nativeWindowScope",
    "nativeActionIntents",
    "nativeLaunch",
    "nativeClickPipelineV1",
]
BINDING_KEYS = ("session", "instance", "rootWindow", "surface", "node", "capture")


def binding(value, session):
    if isinstance(value, dict) and "application" in value:
        fields(value, ("session", "application"))
        string(value["session"])
        string(value["application"])
        require(value["session"] == session, "Target belongs to a different native session.", "stale_target")
        return dict(value)
    fields(value, BINDING_KEYS)
    require(value["session"] == session, "Target belongs to a different native session.", "stale_target")
    for key in BINDING_KEYS:
        if value[key] is not None:
            string(value[key])
    window = value["surface"] is not None
    require(all((value[k] is not None) == window for k in ("instance", "rootWindow")), "Incomplete window binding.")
    require(
        (value["node"] is None or window) and (value["capture"] is None or value["node"] is None),
        "Native bindings require an observed surface, original image or session discovery scope.",
    )
    return dict(value)


def names(value, minimum=1, maximum=128):
    require(isinstance(value, list) and minimum <= len(value) <= maximum, "Invalid native field list.")
    for item in value:
        require(len(string(item)) <= 4096, "Native property name is too long.")
    require(len(set(value)) == len(value), "Duplicate native property.")


def query(value):
    require(isinstance(value, dict), "Invalid inspect query.")
    kind = value.get("kind")
    if kind in ("applications", "launch"):
        fields(value, ("kind",))
    elif kind == "surfaces":
        fields(value, ("kind", "application"))
        string(value["application"])
    elif kind == "nodes":
        fields(
            value,
            ("kind", "fields", "maxNodes", "maxDepth"),
            ("childrenPage", "relationPage", "advertisedFields", "followRelations"),
        )
        names(value["fields"])
        if "advertisedFields" in value:
            names(value["advertisedFields"])
            require(
                set(value["advertisedFields"]) <= set(value["fields"]),
                "Advertised fields must be a subset of requested fields.",
            )
            # Interface properties always require the provider's declaration
            # in NativeFacts.field, including when explicitly requested.
        if "followRelations" in value:
            names(value["followRelations"], maximum=16)
            require(
                set(value["followRelations"]) <= set(FOLLOW_RELATIONS),
                "Unsupported traversal relation; ownership links are read without expanding their target.",
            )
        integer(value["maxNodes"], 1, 2000)
        integer(value["maxDepth"], 0, 2000)
        require(not ("childrenPage" in value and "relationPage" in value), "Select one root relation page.")
        page_key = "relationPage" if "relationPage" in value else "childrenPage"
        if page_key in value:
            page = fields(
                value[page_key], ("property", "cursor", "limit") if page_key == "relationPage" else ("cursor", "limit")
            )
            if page_key == "relationPage":
                string(page["property"])
            if page["cursor"] is not None:
                string(page["cursor"])
            integer(page["limit"], 1, 1999)
            require(
                value["maxDepth"] >= 1 and page["limit"] < value["maxNodes"],
                "A relation page requires depth and space for its root and targets.",
            )
    elif kind == "relation":
        fields(value, ("kind", "property", "cursor", "limit"))
        string(value["property"])
        if value["cursor"] is not None:
            string(value["cursor"])
        integer(value["limit"], 1, 2000)
    elif kind == "capturedText":
        fields(value, ("kind", "textRef", "offset", "length"))
        string(value["textRef"])
        integer(value["offset"])
        integer(value["length"], 2, 16384)
    elif kind == "inputContext":
        fields(value, ("kind", "within"), ("actuator", "basis", "currentReceiver"))
        names(value["within"], minimum=0, maximum=16)
        if "currentReceiver" in value:
            require(
                value["currentReceiver"] is True and not ({"actuator", "basis"} & value.keys()),
                "Current receiver observation cannot carry a selected actuator or structural basis.",
            )
        decode_selection_request(value)
    else:
        raise Failure("This Linux v6 driver does not implement inspect " + str(kind) + ".", "mode_unsupported")
    return value


class NativeExecution:
    capabilities = CAPABILITIES

    def __init__(self, backend, session):
        self.b, self.session = backend, session
        self.store = NativeStore(backend.ax)
        self.directory = NativeDirectory(backend, self.store, session)
        self.facts = NativeFacts(backend.ax, self.store, session)
        self.text = NativeText(backend, self.store, self.directory)
        self.action = NativeAction(backend, self.store, self.directory, self.facts)
        self.captures = NativeCapture(backend, self.directory)
        self.keyboard_recipients = NativeKeyboardRecipients()
        self.element_pointer = NativeElementPointer(backend, self.store, self.directory, self.captures)
        self.keyboard = NativeKeyboard(backend, self.store, self.directory, recipients=self.keyboard_recipients)
        self.typed_text = NativeTypedText(
            backend,
            self.store,
            self.directory,
            recipients=self.keyboard_recipients,
            element_pointer=self.element_pointer,
        )
        self.desktop_input = NativeDesktopInput(backend, self.store, self.captures, recipients=self.keyboard_recipients)
        self.paste = NativePaste(backend, self.store, self.directory, recipients=self.keyboard_recipients)
        self.pointer = NativePointer(backend, self.store, self.directory, self.captures)
        self.launch = NativeLaunch(backend, self.directory)
        self.capabilities = CAPABILITIES + (
            ["nativeCapture", "nativeCaptureKinds"] if getattr(backend.desktop, "can_capture", False) else []
        )
        if getattr(backend.desktop, "can_input", False):
            self.capabilities += [
                "nativeKeyboardIntents",
                "nativeElementPointer",
                "nativeControlParts",
                "nativeElementScroll",
                "nativeScrollContentPath",
            ]
            if getattr(backend.desktop, "can_capture", False):
                self.capabilities += ["nativePointer", "nativeDesktopInput"]
        backend.ax.focus_lost = self.keyboard_recipients.clear
        self.resources = NativeResourceScope(
            store=self.store,
            coordinate=backend.coordinate,
            selection_factory=partial(
                NativeSelection, session=session, ax=backend.ax, store=self.store, facts=self.facts
            ),
        )
        units = ContextUnits(
            self.desktop_input,
            self.pointer,
            self.paste,
            self.keyboard,
            self.text,
            self.typed_text,
            self.element_pointer,
            self.action,
        )
        context = NativeInputContextPipeline(
            units,
            self.capabilities,
            backend.desktop,
            current_receiver=NativeCurrentReceiver(backend, self.store, self.directory),
        )
        self.queries = NativeQueryPipeline(
            session=session,
            current=self.resources.current_view,
            ax=backend.ax,
            directory=self.directory,
            facts=self.facts,
            text_page=self.store.text_page,
            launch_inspect=self.launch.inspect,
            context=context,
            finish=self.resources.release_native_resources,
            decode_binding=binding,
            decode_query=query,
            binding_keys=BINDING_KEYS,
        )
        self.capture_pipeline = NativeCapturePipeline(
            session=session,
            current=self.resources.current_view,
            captures=self.captures,
            finish=self.resources.release_native_resources,
            expire=self.resources.expire,
            decode_binding=binding,
        )
        self.inputs = NativeInputPipeline(
            session=session,
            current=self.resources.current_view,
            finished=self.resources.finished,
            units=InputUnits(**vars(units), launch=self.launch),
            expire=self.resources.expire,
            decode_binding=binding,
            click=NativeClickPipeline(self.action, self.pointer, self.element_pointer),
        )

    @property
    def current(self):
        return self.resources.current

    @property
    def finished(self):
        return self.resources.finished

    def finish(self, operation):
        return self.resources.release_native_resources(operation)

    def expire(self):
        self.resources.expire()

    def perform(self, method, params, ctx):
        # Validate the entire frame before touching a provider or taking a
        # scope. The whole session uses v6, including unsupported operations.
        if method == "finish":
            fields(params, ("operation",))
            string(params["operation"])
            return self.finish(params["operation"])
        if method == "begin":
            fields(params, ("mode", "target", "intent"), ("clickContract",))
            intent = fields(params["intent"], ("kind", "parameters"))
            require(params["mode"] in ("read", "write"), "Invalid operation mode.")
            writable = params["mode"] == "write"
            click_contract = params.get("clickContract")
            if "clickContract" in params:
                require(
                    click_contract == "v1" and writable and intent["kind"] == "click",
                    "Only a write click accepts clickContract v1.",
                    "invalid_request",
                )
            target = binding(params["target"], self.session)
            launch = "application" in target
            require(launch == (intent["kind"] == "launch"), "Launch intent requires only an installation binding.")
            if launch:
                require(
                    writable and intent == dict(kind="launch", parameters={}),
                    "An installation accepts only an explicit launch intent.",
                )
                self.launch.installation(target)
            if writable and not launch and target["capture"] is not None:
                require(
                    intent["kind"] in ("click", "scroll", "drag")
                    or target["surface"] is None
                    and intent["kind"] in ("key", "insert"),
                    "Image input supports pointer intents; captured desktop also supports keys and text insertion.",
                    "mode_unsupported",
                )
            if writable and not launch:
                decoder = (
                    pointer_intent
                    if target["capture"] is not None and intent["kind"] in ("click", "scroll", "drag")
                    else paste_intent
                    if intent["kind"] == "paste"
                    else keyboard_intent
                    if intent["kind"] in ("focus", "key")
                    else text_intent
                    if intent["kind"] in ("insert", "replace")
                    else action_intent
                )
                if target["capture"] is None and intent["kind"] in ("click", "scroll"):
                    intent = pointer_intent(intent, element=True)
                else:
                    intent = decoder(intent)
            elif not writable:
                require(intent == dict(kind="read", parameters={}), "Read operation cannot bind an input intent.")
            require(writable or target.get("capture") is None, "Reading requires a scope, not a retained image.")
            require(
                not writable or launch or target["surface"] is not None or target["capture"] is not None,
                "Native input requires a working window or captured desktop.",
            )
            if not launch and target["capture"] is not None:
                self.captures.input(target)
            self.expire()
            require(self.current is None, "Another native operation is active.")
            if not launch and target["surface"] is not None:
                if target["capture"] is None:
                    self.b.ax.begin(ctx)
                self.directory.validate(target, ctx)
            ctx.check()
            return self.resources.acquire(target, intent, writable, ctx, click_contract)
        if method == "capture":
            return self.capture_pipeline.run(params, ctx)
        if method == "execute":
            return self.inputs.run(params, ctx)
        require(method == "inspect", "This Linux v6 driver has no " + str(method) + " unit.", "mode_unsupported")
        return self.queries.run(params, ctx)

    def close(self):
        self.keyboard_recipients.clear()
        if getattr(self.b.ax, "focus_lost", None) == self.keyboard_recipients.clear:
            self.b.ax.focus_lost = None
        if self.current:
            self.finish(self.current.token)
        self.store.clear()
        self.directory.clear()
        self.captures.clear()
        self.launch.close()
