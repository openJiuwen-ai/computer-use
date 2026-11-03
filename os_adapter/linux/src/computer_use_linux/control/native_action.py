# coding: utf-8
# Copyright (c) Huawei Technologies Co., Ltd. 2026. All rights reserved.

"""One addressed AT-SPI action; semantic selection belongs to the SDK.

Action indices are transient provider positions, never public element IDs or
persistent action identities. Resolve the exact declared name again at dispatch.
"""

from copy import deepcopy

from computer_use_linux.contracts.protocol import Failure, fields, integer, require, string
from computer_use_linux.control.action.support import action_matches
from computer_use_linux.control.native_target import NativeTarget


def action_intent(value):
    fields(value, ("kind", "parameters"))
    kind = value["kind"]
    if kind == "click":
        p = fields(value["parameters"], (), ("button", "count"))
        if "button" in p:
            require(p["button"] in ("left", "right", "middle"), "Invalid click button.")
        if "count" in p:
            integer(p["count"], 1, 2)
        require(
            p.get("button", "left") == "left" and p.get("count", 1) == 1,
            "This native action unit does not synthesize pointer clicks.",
            "unsupported_operation",
        )
    elif kind == "action":
        p = fields(value["parameters"], ("action",))
        require(len(string(p["action"])) <= 16384, "Native action name or description is too long.")
    elif kind == "openMenu":
        fields(value["parameters"], ())
    else:
        raise Failure("This Linux v5 driver does not implement this input intent.", "unsupported_operation")
    return deepcopy(value)


class NativeAction:
    def __init__(self, backend, store, directory, facts):
        self.ax, self.store, self.facts = backend.ax, store, facts
        self.target = NativeTarget(backend, store, directory)

    def execute(self, operation, command, ctx):
        from .action.pipeline import NativeActionPipeline

        return NativeActionPipeline(self.ax, self.target, self.facts).run(operation, command, ctx)

    def inspect(self, operation, target, within, ctx):
        explicit = operation.intent["kind"] == "action"
        obj, _, _, role, enabled = self.target.read(target, within, ctx, include_role=not explicit)
        operation.bind(target, within, self.store)
        # Named requests need the complete machine-readable name catalogue,
        # not optional localized descriptions of unrelated actions.
        catalogue = self.facts.action_catalogue(obj, ctx, descriptions=not explicit)
        match_by = "name"
        if explicit and catalogue["state"] == "complete":
            requested = operation.intent["parameters"]["action"]
            if not any(action["name"] == requested for action in catalogue["items"]):
                match_by = "description"
                catalogue = self.facts.action_descriptions(obj, catalogue)
        complete = catalogue["state"] == "complete"
        primitives = []
        if complete:
            for action in catalogue["items"]:
                selected = dict(name=action["name"])
                if "description" in action and "descriptionStatus" not in action:
                    selected["description"] = action["description"]
                identity = dict(name=action["name"])
                if match_by == "description" and "description" in selected:
                    identity["description"] = selected["description"]
                # Ordinary click/named action does not depend on descriptive
                # text. Bind it only when the request actually uses that text.
                pointer_only = operation.intent["kind"] == "click" and (
                    operation.intent["parameters"].get("button", "left") != "left"
                    or operation.intent["parameters"].get("count", 1) != 1
                )
                usable = (
                    not pointer_only
                    and (match_by != "description" or "description" in identity)
                    and len(action_matches(catalogue, identity, match_by=match_by)) == 1
                )
                identifier = operation.contract(
                    "native_action",
                    target,
                    within,
                    action=selected,
                    identity=identity,
                    role=role,
                    usable=usable,
                    matchBy=match_by,
                    roleRequired=not explicit,
                )
                # Preserve ambiguous declarations as unsupported facts. Dropping
                # them would falsely tell Python that a primary action is absent.
                primitives.append(
                    dict(
                        id=identifier,
                        unit="native_action",
                        channel="object",
                        globalInput="no",
                        supported="yes" if usable else "no",
                        readiness="yes" if enabled and usable else "no",
                        pending=[] if enabled else ["enabled"],
                        backgroundPreparation="no",
                        action=selected,
                    )
                )
        return self.target.context(target, within, primitives, enabled, role, actions_complete=complete)
