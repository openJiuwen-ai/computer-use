# coding: utf-8
# Copyright (c) Huawei Technologies Co., Ltd. 2026. All rights reserved.

"""Summarize macOS input activity from native/MCP diagnostic JSONL journals."""

import argparse
import json
from collections import Counter
from pathlib import Path

from computer_use_native_tools.console import report


def summarize(records):
    """Use effect-boundary evidence; never infer global input from desktop mode."""
    scopes = {}
    commands = {}
    malformed = []
    for line, raw in enumerate(records, 1):
        if not isinstance(raw, dict):
            malformed.append(line)
            continue
        record = raw.get("diagnostic", raw)
        if not isinstance(record, dict):
            continue
        if record.get("kind") == "command_start" and record.get("method") == "execute":
            params = record.get("params")
            params = params if isinstance(params, dict) else {}
            commands[record.get("traceId")] = {
                "traceId": record.get("traceId"),
                "executionId": record.get("executionId"),
                "operation": params.get("operation"),
                "step": params.get("step"),
                "line": line,
            }
        event = record.get("value") if record.get("kind") == "backend_diagnostic" else record
        if not isinstance(event, dict) or event.get("kind") != "input_activity":
            continue
        if not _valid_event(event):
            malformed.append(line)
            continue
        scope = scopes.setdefault(
            event["activityId"], {"events": [], "traceIds": set(), "executionIds": set(), "driverPIDs": set()}
        )
        scope["events"].append((line, event))
        if record.get("traceId") is not None:
            scope["traceIds"].add(record["traceId"])
        if record.get("executionId") is not None:
            scope["executionIds"].add(record["executionId"])
        if record.get("pid") is not None:
            scope["driverPIDs"].add(record["pid"])

    activities = [_activity(key, scope) for key, scope in scopes.items()]
    audited = {(a["operation"], a["step"]) for a in activities}
    return {
        "version": 1,
        "activities": activities,
        "malformedLines": malformed,
        "executionsWithoutAudit": [c for c in commands.values() if (c["operation"], c["step"]) not in audited],
    }


def _valid_event(event):
    if not (
        type(event.get("version")) is int
        and event["version"] == 1
        and isinstance(event.get("activityId"), str)
        and isinstance(event.get("operation"), str)
        and isinstance(event.get("nativeTimestamp"), str)
        and type(event.get("step")) is int
        and type(event.get("sequence")) is int
        and event["sequence"] > 0
        and isinstance(event.get("phase"), str)
        and event["phase"] in {"begin", "willCall", "didReturn", "end", "keyboardDestination"}
    ):
        return False
    if event["phase"] in {"willCall", "didReturn"}:
        resources = event.get("sharedResources")
        return (
            type(event.get("effectId")) is int
            and event["effectId"] > 0
            and isinstance(event.get("device"), str)
            and isinstance(event.get("transport"), str)
            and event.get("device") in {"mouse", "keyboard", "focus", "element"}
            and event.get("transport") in {"globalHID", "process", "accessibility", "activation", "windowServer"}
            and isinstance(resources, list)
            and all(r in ("mouse", "keyboard", "focus") for r in resources)
            and (event["phase"] != "didReturn" or "nativeInvoked" in event)
            and (event.get("nativeInvoked") is None or type(event["nativeInvoked"]) is bool)
        )
    return True


def _activity(key, scope):
    events = scope["events"]
    metadata = events[0][1]
    starts = [e for _, e in events if e.get("phase") == "begin"]
    ends = [e for _, e in events if e.get("phase") == "end"]
    calls = {e.get("effectId"): e for _, e in events if e.get("phase") == "willCall"}
    returns = {e.get("effectId"): e for _, e in events if e.get("phase") == "didReturn"}
    sequence = [e.get("sequence") for _, e in events]
    issues = []
    if sequence != list(range(1, len(events) + 1)):
        issues.append("sequenceGapOrDuplicate")
    if len(starts) != 1:
        issues.append("missingOrDuplicateBegin")
    if len(ends) != 1:
        issues.append("missingOrDuplicateEnd")
    end = ends[-1] if ends else {}
    if end and end.get("effectCount") != len(calls):
        issues.append("effectCountMismatch")
    if calls.keys() != returns.keys():
        issues.append("unpairedEffectBoundary")
    if sum(e.get("phase") == "willCall" for _, e in events) != len(calls) or sum(
        e.get("phase") == "didReturn" for _, e in events
    ) != len(returns):
        issues.append("duplicateEffectBoundary")

    effects = []
    submitted = Counter()
    possible = Counter()
    transports = Counter()
    shared_times = []
    foreground_receiver = False
    foreground_changed = any(e.get("foregroundChanged") is True for _, e in events)
    for effect_id in sorted(calls.keys() | returns.keys()):
        call = calls.get(effect_id, {})
        returned = returns.get(effect_id, {})
        fields = call or returned
        invoked = returned.get("nativeInvoked")
        evidence = "returned" if invoked is True else "notInvoked" if invoked is False else "unknown"
        resources = fields.get("sharedResources", []) if invoked is not False else []
        counts = submitted if invoked is True else possible
        counts.update(resources)
        if invoked is not False:
            transports[(fields.get("device"), fields.get("transport"))] += 1
            receiver = fields.get("foregroundReceiver") is True
            foreground_receiver |= receiver
            if resources or receiver:
                shared_times.append(fields.get("nativeTimestamp"))
        effects.append(
            {
                "effectId": effect_id,
                "stage": fields.get("stage"),
                "device": fields.get("device"),
                "transport": fields.get("transport"),
                "mechanism": fields.get("mechanism"),
                "eventRole": fields.get("eventRole"),
                "eventType": fields.get("eventType"),
                "recipientPID": fields.get("recipientPID"),
                "presenterPID": fields.get("presenterPID"),
                "targetPID": fields.get("targetPID"),
                "window": fields.get("window"),
                "point": fields.get("point"),
                "evidence": evidence,
                "nativeStatus": returned.get("nativeStatus"),
                "nativeAccepted": returned.get("nativeAccepted"),
                "sharedResources": resources,
                "foregroundReceiver": fields.get("foregroundReceiver"),
                "startedAt": call.get("nativeTimestamp"),
                "returnedAt": returned.get("nativeTimestamp"),
                "startedUptime": call.get("uptime"),
                "returnedUptime": returned.get("uptime"),
                "durationMs": returned.get("durationMs"),
                "before": call.get("before"),
                "after": returned.get("after"),
            }
        )
    return {
        "activityId": key,
        "operation": metadata.get("operation"),
        "step": metadata.get("step"),
        "command": metadata.get("command"),
        "intent": metadata.get("intent"),
        "target": metadata.get("target"),
        "desktopMode": metadata.get("desktopMode"),
        "traceIds": sorted(scope["traceIds"]),
        "executionIds": sorted(scope["executionIds"]),
        "driverPIDs": sorted(scope["driverPIDs"]),
        "startedAt": starts[0].get("nativeTimestamp") if starts else None,
        "endedAt": end.get("nativeTimestamp"),
        "startedUptime": starts[0].get("uptime") if starts else None,
        "endedUptime": end.get("uptime"),
        "durationMs": end.get("durationMs"),
        "firstSharedCall": shared_times[0] if shared_times else None,
        "lastSharedCall": shared_times[-1] if shared_times else None,
        "sharedRequests": dict(submitted),
        "possibleSharedRequests": dict(possible),
        "foregroundReceiver": foreground_receiver,
        "foregroundChanged": foreground_changed,
        "keyboardDestinations": [
            {k: e.get(k) for k in ("expectedPID", "expectedWindow", "expectedFocusedWindow")}
            for _, e in events
            if e.get("phase") == "keyboardDestination"
        ],
        "transports": [{"device": d, "transport": t, "calls": n} for (d, t), n in transports.items()],
        "dispatch": end.get("dispatch"),
        "cleanup": end.get("cleanup"),
        "complete": not issues,
        "auditIssues": issues,
        "effects": effects,
    }


def read_log(path):
    def records():
        with path.open(encoding="utf-8") as stream:
            for line in stream:
                try:
                    yield json.loads(line)
                except ValueError:
                    yield None

    return dict(log=str(path.resolve()), **summarize(records()))


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("log", type=Path)
    parser.add_argument("--json", action="store_true", help="Include individual effect boundaries and targets")
    parser.add_argument("--all", action="store_true", help="Also show operations without recorded shared input")
    args = parser.parse_args()
    summary = read_log(args.log)
    if args.json:
        report(json.dumps(summary, ensure_ascii=False, indent=2))
        return
    report("Native input activity (UTC); call intervals do not reserve the desktop or restore focus.")
    if not summary["activities"]:
        report("UNKNOWN: no input activity records found; this log cannot establish shared-input usage.")
    for activity in summary["activities"]:
        if not (
            args.all
            or activity["sharedRequests"]
            or activity["possibleSharedRequests"]
            or activity["foregroundReceiver"]
            or activity["foregroundChanged"]
            or not activity["complete"]
        ):
            continue
        usage = (
            ", ".join(f"{k}={v}" for k, v in activity["sharedRequests"].items())
            or "no recorded global input/focus request"
        )
        if activity["possibleSharedRequests"]:
            usage += f"; uncertain={activity['possibleSharedRequests']}"
        if activity["foregroundReceiver"]:
            usage += "; foreground keyboard receiver"
        if activity["foregroundChanged"]:
            usage += "; foreground change observed"
        if not activity["complete"]:
            usage += "; INCOMPLETE=" + ",".join(activity["auditIssues"])
        report(
            f"{activity['startedAt']} .. {activity['endedAt']}  "
            f"execution={activity['executionIds']} {activity['intent']}/{activity['command']}  {usage}"
        )
        report(
            f"  operation={activity['operation']}/{activity['step']} "
            f"activity={activity['activityId']} trace={','.join(activity['traceIds'])} "
            f"dispatch={activity['dispatch']} cleanup={activity['cleanup']} "
            f"sharedCalls={activity['firstSharedCall']}..{activity['lastSharedCall']}"
        )
    if summary["executionsWithoutAudit"]:
        report(f"UNKNOWN: {len(summary['executionsWithoutAudit'])} execute requests lack activity evidence.")
    if summary["malformedLines"]:
        report(f"INCOMPLETE: unreadable/unsupported records at lines {summary['malformedLines']}.")


if __name__ == "__main__":
    main()
