# coding: utf-8
# Copyright (c) Huawei Technologies Co., Ltd. 2026. All rights reserved.

"""Discovery facts do not require an accessibility root or an image capture."""

from copy import deepcopy

from .schema import array, boolean, count, enum, identity, number, obj, string, unknown

_stamp = obj(dict(id=identity, startedAt=count, completedAt=count))
_application = obj(
    dict(
        id=identity,
        displayName=identity,
        isRunning=boolean(),
        path=string().nullable(),
        identifier=string().nullable(),
        aliases=array(identity, max=16).optional(),
        matchKeys=array(identity, max=64).optional(),
        windows=array(obj(dict(selector=identity, window=identity)), max=512).optional(),
    )
)
_surface = obj(
    dict(
        window=identity,
        instance=identity,
        surface=identity,
        node=identity.nullable(),
        title=string(),
        kind=identity,
        parent=identity.nullable(),
        parentState=enum("known", "none", "unknown"),
        modal=boolean().nullable(),
        nativeWindow=boolean(),
        accessibility=boolean(),
    )
)
_common = dict(
    session=identity, platform=enum("macos", "windows", "linux"), observed=_stamp, stopReasons=array(identity, max=128)
)
_schemas = {
    "surfaceBatch": obj(
        dict(
            kind=enum("surfaceBatch"),
            entries=array(
                obj(dict(application=identity, result=unknown().optional(), error=unknown().optional())), max=64
            ),
            **_common,
        )
    ),
    "applications": obj(
        dict(
            kind=enum("applications"),
            applications=array(_application, max=4096),
            scope=enum("runningWindows").optional(),
            **_common,
        )
    ),
    "surfaces": obj(
        dict(
            kind=enum("surfaces"),
            application=identity,
            surfaces=array(_surface, max=512),
            focusedWindow=identity.nullable().optional(),
            provider=obj(
                dict(status=identity, attribute=identity.nullable(), writeStatus=number(integer=True).nullable())
            ),
            **_common,
        )
    ),
    "roots": obj(
        dict(
            kind=enum("roots"),
            application=identity,
            roots=array(obj(dict(kind=identity, instance=identity, surface=identity, node=identity)), max=64),
            **_common,
        )
    ),
}


def decode_directory(value):
    parsed = _schemas[value["kind"]].parse(value)
    result = parsed if value["kind"] == "surfaceBatch" else deepcopy(parsed)
    if result["observed"]["completedAt"] < result["observed"]["startedAt"]:
        raise ValueError("protocol: directory collection interval is reversed")
    if result["kind"] == "surfaceBatch":
        from .session import decode_failure

        applications = []
        for entry in result["entries"]:
            applications.append(entry["application"])
            if ("result" in entry) == ("error" in entry):
                raise ValueError("protocol: a batch entry needs one result or failure")
            if "result" in entry:
                if not isinstance(entry["result"], dict) or entry["result"].get("kind") != "surfaces":
                    raise ValueError("protocol: batch entry must contain a surface directory")
                item = decode_directory(entry["result"])
                if (
                    item["kind"] != "surfaces"
                    or item["application"] != entry["application"]
                    or item["session"] != result["session"]
                    or item["platform"] != result["platform"]
                ):
                    raise ValueError("protocol: batch surface directory changed its scope")
                entry["result"] = item
            else:
                entry["error"] = decode_failure(entry["error"])
        if len(applications) != len(set(applications)):
            raise ValueError("protocol: duplicate batch application")
    elif result["kind"] == "applications":
        identifiers = [item["id"] for item in result["applications"]]
        if len(set(identifiers)) != len(identifiers):
            raise ValueError("protocol: duplicate application lifetime")
        for item in result["applications"]:
            keys = item.get("matchKeys", [])
            if len(set(keys)) != len(keys):
                raise ValueError("protocol: duplicate application match key")
            if item.get("windows") and not item["isRunning"]:
                raise ValueError("protocol: a nonrunning application cannot own observed windows")
        selectors = [w["selector"] for app in result["applications"] for w in app.get("windows", [])]
        windows = [w["window"] for app in result["applications"] for w in app.get("windows", [])]
        if (
            len(set(selectors)) != len(selectors)
            or len(set(windows)) != len(windows)
            or set(selectors).intersection(identifiers)
        ):
            raise ValueError("protocol: conflicting application/window selector identity")
    elif result["kind"] == "roots":
        for key in ("node", "surface"):
            values = [item[key] for item in result["roots"]]
            if len(set(values)) != len(values):
                raise ValueError("protocol: duplicate application root identity")
    else:
        entries = result["surfaces"]
        for key in ("window", "instance", "surface"):
            values = [item[key] for item in entries]
            if len(set(values)) != len(values):
                raise ValueError("protocol: duplicate surface identity")
        for item in entries:
            if (item["parent"] is not None) != (item["parentState"] == "known"):
                raise ValueError("protocol: parent relationship knowledge is inconsistent")
            if (item["node"] is not None) != item["accessibility"]:
                raise ValueError("protocol: accessibility root availability is inconsistent")
        # Unknown parents and cycles are provider facts, not an excuse to drop
        # windows or guess another document. Selection handles them explicitly.
    return result
