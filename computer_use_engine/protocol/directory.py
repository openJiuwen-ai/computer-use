# coding: utf-8
# Copyright (c) Huawei Technologies Co., Ltd. 2026. All rights reserved.

"""Discovery facts do not require an accessibility root or an image capture."""

from copy import deepcopy

from .schema import array, boolean, count, enum, identity, number, obj, string, unknown

_stamp = obj(dict(id=identity, startedAt=count, completedAt=count))
app_change = obj({"session": identity, "application": identity, "incarnation": identity, "token": identity})
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
surface_schema = obj(
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
        content=obj({
            "kind": enum("window", "menu", "popup", "application", "unknown"),
            "host": identity.nullable(),
            "evidence": identity,
            "hostNode": identity.nullable().optional(),
        }).optional(),
    )
)
_common = dict(
    session=identity, platform=enum("macos", "windows", "linux"), observed=_stamp, stopReasons=array(identity, max=128)
)
_schemas = {
    "windowCatalogue": obj(
        dict(
            kind=enum("windowCatalogue"),
            appName=identity,
            windows=array(
                obj(dict(selector=identity, window=surface_schema, appIsForeground=boolean().nullable())), max=256
            ),
            **_common,
        )
    ),
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
            path=identity.optional(),
            installation=identity.optional(),
            endedApplications=array(identity, max=4096).optional(),
            nativeProcessNames=array(obj({"application": identity, "name": identity}), max=512).optional(),
            windowTitle=obj({"title": identity, "complete": boolean()}).optional(),
            **_common,
        )
    ),
    "surfaces": obj(
        dict(
            kind=enum("surfaces"),
            application=identity,
            appChange=app_change.nullable().optional(),
            surfaces=array(surface_schema, max=512),
            rootDiscoveryComplete=boolean(),
            endedWindows=array(
                obj({"window": identity, "instance": identity, "surface": identity}), max=4096
            ).optional(),
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
            appChange=app_change.nullable().optional(),
            roots=array(obj(dict(kind=identity, instance=identity, surface=identity, node=identity)), max=64),
            **_common,
        )
    ),
}


def validate_surface(value):
    """Check facts shared by application and desktop window catalogues."""
    if (value["parent"] is not None) != (value["parentState"] == "known"):
        raise ValueError("protocol: parent relationship knowledge is inconsistent")
    if (value["node"] is not None) != value["accessibility"]:
        raise ValueError("protocol: accessibility root availability is inconsistent")


def decode_directory(value):
    parsed = _schemas[value["kind"]].parse(value)
    result = parsed if value["kind"] == "surfaceBatch" else deepcopy(parsed)
    if result["observed"]["completedAt"] < result["observed"]["startedAt"]:
        raise ValueError("protocol: directory collection interval is reversed")
    change = result.get("appChange")
    if change is not None and any(change[key] != result[key] for key in ("session", "application")):
        raise ValueError("protocol: application change evidence changed the directory scope")
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
                scope_changed = (
                    item["kind"] != "surfaces"
                    or item["application"] != entry["application"]
                    or item["session"] != result["session"]
                )
                if scope_changed or item["platform"] != result["platform"]:
                    raise ValueError("protocol: batch surface directory changed its scope")
                entry["result"] = item
            else:
                entry["error"] = decode_failure(entry["error"])
        if len(applications) != len(set(applications)):
            raise ValueError("protocol: duplicate batch application")
    elif result["kind"] == "windowCatalogue":
        windows = result["windows"]
        identities = {item["window"]["window"] for item in windows}
        foreground = {item["selector"] for item in windows if item["appIsForeground"] is True}
        if result["platform"] != "macos" or len(identities) != len(windows) or len(foreground) > 1:
            raise ValueError("protocol: invalid visible window catalogue")
        for item in windows:
            surface = item["window"]
            validate_surface(surface)
            exact_instance = item["selector"].startswith("instance:") and surface["nativeWindow"]
            native_metadata = (
                exact_instance
                and not surface["accessibility"]
                and surface["parentState"] == "unknown"
                and surface["modal"] is None
            )
            if not native_metadata or item["selector"] in foreground and item["appIsForeground"] is not True:
                raise ValueError("protocol: window catalogue requires native metadata for the exact instance")
    elif result["kind"] == "applications":
        if "path" in result or "installation" in result:
            paths = {item["path"] for item in result["applications"]}
            scope_error = "protocol: invalid exact application scope"
            if result["platform"] != "macos" or "scope" in result:
                raise ValueError(scope_error)
            if "path" in result and ("installation" in result or not result["path"].startswith("/")):
                raise ValueError(scope_error)
            if len(paths) > 1 or any(not path or not path.startswith("/") for path in paths):
                raise ValueError(scope_error)
            if "installation" in result and any(not item["isRunning"] for item in result["applications"]):
                raise ValueError(scope_error)
        identifiers = [item["id"] for item in result["applications"]]
        if len(set(identifiers)) != len(identifiers):
            raise ValueError("protocol: duplicate application lifetime")
        if "endedApplications" in result:
            ended = result["endedApplications"]
            lifetime_scope = result["platform"] == "macos" and not any(
                key in result for key in ("path", "installation", "scope")
            )
            valid_instances = lifetime_scope and all(application.startswith("instance:") for application in ended)
            if not valid_instances or len(set(ended)) != len(ended) or set(ended).intersection(identifiers):
                raise ValueError("protocol: conflicting ended application lifetime")
        if "windowTitle" in result:
            running_windows = result["platform"] == "windows" and result.get("scope") == "runningWindows"
            if not running_windows or any(
                not item["isRunning"] or not item.get("windows") for item in result["applications"]
            ):
                raise ValueError("protocol: invalid Windows caption catalogue")
        native_names = result.get("nativeProcessNames")
        if native_names is not None:
            named = {row["application"] for row in native_names}
            native_candidates = {
                item["id"] for item in result["applications"] if item["isRunning"] and item.get("windows")
            }
            running_windows = result["platform"] == "windows" and result.get("scope") == "runningWindows"
            if not running_windows or len(native_names) != len(named) or not named.issubset(native_candidates):
                raise ValueError("protocol: invalid Win32 process name catalogue")
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
            validate_surface(item)
        ended = result.get("endedWindows", ())
        ended_keys = [(item["window"], item["surface"], item["instance"]) for item in ended]
        live_keys = {(item["window"], item["surface"], item["instance"]) for item in entries}
        if len(ended_keys) != len(set(ended_keys)) or live_keys.intersection(ended_keys):
            raise ValueError("protocol: conflicting ended window lifetime evidence")
        # Unknown parents and cycles are provider facts, not an excuse to drop
        # windows or guess another document. Selection handles them explicitly.
    return result
