# Copyright (c) Huawei Technologies Co., Ltd. 2026. All rights reserved.

"""Account for evidenced native work in a real MCP journal, without desktop I/O.

This is an interval-union lower bound, not a profiler or an estimate of missing
work. Command envelopes are deliberately never a fallback for native intervals.
"""

import json
import math
from collections import Counter
from datetime import UTC, datetime
from decimal import Decimal, InvalidOperation
from pathlib import Path

_EPOCH = datetime(1970, 1, 1, tzinfo=UTC)
_LIMITATIONS = [
    (
        "The numerator is the union of evidenced native read, pixel capture and invoked input-call intervals only; "
        "uncovered commands and uninstrumented native work make it a lower bound, not complete attribution."
    ),
    (
        "Native read/capture intervals include elapsed time inside their instrumented native operation, "
        "including waits; "
        "they are not CPU time or a count of individual OS calls."
    ),
    (
        "Input nativeTimestamp anchors have millisecond resolution (approximately +/-1 ms); durationMs measures the "
        "native call, not eventual application effect or confirmation. Directory millisecond intervals also have "
        "millisecond boundary resolution."
    ),
    (
        "The union is clipped to the caller's epoch-clock MCP span and divided by its full monotonic wall_ns. "
        "Clock adjustments and timestamp resolution can affect boundary attribution."
    ),
    (
        "Input preparation/cleanup and resource initialization are reported separately and excluded from primary work. "
        "Command durationMs, input activity begin/end envelopes, SDK processing, transport and output delivery "
        "are excluded."
    ),
    (
        "SDK durations are recorded monotonic elapsed times, summed only for uniquely ended operations with valid "
        "durationMs. Missing starts/ends are explicit gaps; this sum does not replace full MCP wall time "
        "or native work. "
        "SDK epoch timestamps have millisecond resolution (approximately +/-1 ms). The final delivery tail runs from "
        "the last SDK end to caller completion, including encoding, presentation, transport and parent collection."
    ),
]


def _scaled_ns(value, scale):
    if not isinstance(value, (int, float)) or isinstance(value, bool):
        raise TypeError("timestamp/duration must be numeric")
    if isinstance(value, float) and not math.isfinite(value):
        raise ValueError("timestamp/duration must be a finite number")
    try:
        return int((Decimal(str(value)) * scale).to_integral_value())
    except (InvalidOperation, OverflowError) as error:
        raise ValueError("timestamp/duration cannot be converted to nanoseconds") from error


def _timestamp_ns(value):
    if not isinstance(value, str):
        raise TypeError("nativeTimestamp must be an ISO epoch timestamp with a timezone")
    try:
        instant = datetime.fromisoformat(value)
        if instant.tzinfo is None:
            raise ValueError("timestamp has no timezone")
        delta = instant - _EPOCH
        return ((delta.days * 86400 + delta.seconds) * 1_000_000 + delta.microseconds) * 1000
    except (ValueError, OverflowError) as error:
        raise ValueError("invalid ISO epoch timestamp") from error


def _union(intervals):
    merged = []
    for start, end in sorted(intervals):
        if end <= start:
            continue
        if merged and start <= merged[-1][1]:
            merged[-1][1] = max(merged[-1][1], end)
        else:
            merged.append([start, end])
    return merged


def _total(intervals):
    return sum(end - start for start, end in _union(intervals))


def _command(event):
    result = event.get("result")
    return {
        "id": event.get("id"),
        "traceId": event.get("traceId"),
        "method": event.get("method"),
        "result_kind": result.get("kind") if isinstance(result, dict) else None,
        "status": event.get("status"),
    }


def _same_command(command, diagnostic):
    trace = diagnostic.get("traceId")
    request = diagnostic.get("requestId")
    return (trace is not None and trace == command.get("traceId")) or (
        request is not None and request == command.get("id")
    )


def _resource_event(event):
    kind = str(event.get("kind", "")).lower()
    return "resource" in kind and "init" in kind


def _account_sdk_operations(report, events, completed_epoch_ns, wall_ns):
    """Preserve real SDK completion facts without changing native-work gates."""
    groups = {}
    gaps = report["sdk_operation_gaps"]
    sdk_events = [event for event in events if event.get("kind") in {"sdk_operation_start", "sdk_operation_end"}]
    for event in sdk_events:
        operation_id = event.get("operationId")
        if not isinstance(operation_id, str) or not operation_id.strip():
            gaps.append({"code": "invalid_operation_id", "kind": event["kind"], "timestamp": event.get("timestamp")})
            continue
        group = groups.setdefault(operation_id, {"start": [], "end": []})
        group["start" if event["kind"] == "sdk_operation_start" else "end"].append(event)
    for operation_id, group in groups.items():
        row = {
            "id": operation_id,
            "type": None,
            "durationMs": None,
            "duration_ns": None,
            "status": None,
            "overrun": None,
            "start_count": len(group["start"]),
            "end_count": len(group["end"]),
        }
        row_gaps = []
        for boundary in ("start", "end"):
            records = group[boundary]
            if len(records) != 1:
                row_gaps.append(
                    {"code": f"missing_{boundary}" if not records else f"duplicate_{boundary}", "count": len(records)}
                )
                continue
            record = records[0]
            row[f"{boundary}_timestamp"] = record.get("timestamp")
            try:
                row[f"{boundary}_epoch_ns"] = _timestamp_ns(record.get("timestamp"))
            except (TypeError, ValueError) as error:
                row_gaps.append({"code": f"invalid_{boundary}_timestamp", "detail": str(error)})
        types = [event.get("operationType") for records in group.values() for event in records]
        if any(not isinstance(value, str) or not value.strip() for value in types):
            row_gaps.append({"code": "invalid_operation_type"})
        elif len(set(types)) != 1:
            row_gaps.append({"code": "operation_type_changed", "types": types})
        else:
            row["type"] = types[0]
        if len(group["end"]) == 1:
            end = group["end"][0]
            row.update(
                {
                    key: end[key]
                    for key in (
                        "durationMs",
                        "budgetMs",
                        "targetMs",
                        "targetExceeded",
                        "status",
                        "overrun",
                        "outcome",
                        "errorType",
                        "effect",
                        "observationStatus",
                        "observationErrorCode",
                        "observationPartial",
                        "contentAvailable",
                        "observationGapCodes",
                        "observationComplete",
                        "observationDiagnosticError",
                    )
                    if key in end
                }
            )
            try:
                duration = _scaled_ns(end.get("durationMs"), 1_000_000)
                if duration < 0:
                    raise ValueError("negative SDK duration")
                row["duration_ns"] = duration
                report["sdk_operations_sum_ns"] += duration
                report["sdk_operations_sum_count"] += 1
            except (TypeError, ValueError) as error:
                row_gaps.append({"code": "invalid_duration", "detail": str(error)})
            if end.get("status") not in ("ok", "error"):
                row_gaps.append({"code": "invalid_end_status"})
            if not isinstance(end.get("overrun"), bool):
                row_gaps.append({"code": "invalid_overrun"})
        if "start_epoch_ns" in row and "end_epoch_ns" in row and row["end_epoch_ns"] < row["start_epoch_ns"]:
            row_gaps.append({"code": "reversed_epoch_timestamps"})
        row["complete"] = not row_gaps
        row["gaps"] = row_gaps
        report["sdk_operations"].append(row)
        gaps.extend({"id": operation_id, **gap} for gap in row_gaps)
    if not sdk_events:
        gaps.append({"code": "missing_sdk_operation_events"})
    ends = [event for event in sdk_events if event["kind"] == "sdk_operation_end"]
    if ends:
        last_end = ends[-1]
        report["final_delivery_tail_anchor"] = {
            "id": last_end.get("operationId"),
            "timestamp": last_end.get("timestamp"),
            "timestamp_resolution_ns": 1_000_000,
        }
        try:
            end_ns = _timestamp_ns(last_end.get("timestamp"))
            report["final_delivery_tail_ns"] = completed_epoch_ns - end_ns
            if end_ns > completed_epoch_ns:
                gaps.append({"code": "last_sdk_end_after_outer_completion", "id": last_end.get("operationId")})
        except (TypeError, ValueError) as error:
            gaps.append({"code": "invalid_final_delivery_tail_anchor", "detail": str(error)})
    report["sdk_operations_sum_fraction_of_full_wall"] = report["sdk_operations_sum_ns"] / wall_ns
    report["wall_minus_sdk_operations_sum_ns"] = wall_ns - report["sdk_operations_sum_ns"]
    report["sdk_operations_coverage"] = "unavailable" if not sdk_events else "partial" if gaps else "complete"


def measure_work(log_path: Path, *, title: str, wall_ns: int, started_epoch_ns: int, completed_epoch_ns: int) -> dict:
    """Measure one exact, uniquely titled MCP execution in a static JSONL journal.

    ``majority_passed`` requires strictly more than half of the *full* MCP wall
    time to be supported by primary native intervals and no journal/execution
    error. Missing/invalid interval coverage remains visible; it is never filled
    from command durations or other executions.
    """
    report = {
        "status": "error",
        "title": title,
        "log_path": str(log_path) if log_path is not None else None,
        "executionId": None,
        "wall_ns": wall_ns,
        "outer_epoch_interval_ns": [started_epoch_ns, completed_epoch_ns],
        "actual_work_ns": 0,
        "fraction_of_full_wall": 0.0,
        "majority_passed": False,
        "global_input_effects": [],
        "sdk_operations": [],
        "sdk_operation_gaps": [],
        "sdk_operations_sum_ns": 0,
        "sdk_operations_sum_count": 0,
        "sdk_operations_sum_fraction_of_full_wall": 0.0,
        "sdk_operations_coverage": "unavailable",
        "wall_minus_sdk_operations_sum_ns": wall_ns,
        "final_delivery_tail_ns": None,
        "final_delivery_tail_anchor": None,
        "interval_count": 0,
        "union_intervals_ns": [],
        "intervals": [],
        "sources": [],
        "categories": {},
        "preparation_cleanup": {},
        "uncovered_native_commands": [],
        "missing_intervals": [],
        "invalid_intervals": [],
        "excluded_intervals": [],
        "journal_errors": [],
        "errors": [],
        "cold_worker_resource_initialization": {"present": False, "events": [], "excluded_from_primary": True},
        "coverage_limitations": list(_LIMITATIONS),
    }
    if not (
        isinstance(title, str)
        and title
        and all(
            isinstance(value, int) and not isinstance(value, bool) and value > 0
            for value in (wall_ns, started_epoch_ns, completed_epoch_ns)
        )
        and completed_epoch_ns > started_epoch_ns
    ):
        report["errors"].append(
            "A nonempty exact title, positive wall_ns and increasing positive epoch bounds are required."
        )
        return report
    if log_path is None:
        report["errors"].append("No MCP journal path was provided.")
        return report
    events = []
    try:
        with Path(log_path).open(encoding="utf-8") as journal:
            for line_number, line in enumerate(journal, 1):
                if not line.strip():
                    continue
                try:
                    event = json.loads(line)
                    if not isinstance(event, dict):
                        raise TypeError("journal entry is not an object")
                    events.append(event)
                except (TypeError, ValueError) as error:
                    report["journal_errors"].append({"line": line_number, "error": str(error)})
    except (OSError, UnicodeError, TypeError, ValueError) as error:
        report["errors"].append(f"Cannot read MCP journal: {type(error).__name__}: {error}")
        return report
    starts = [
        (index, event)
        for index, event in enumerate(events)
        if event.get("kind") == "execution_start" and event.get("title") == title
    ]
    if len(starts) != 1:
        report["errors"].append(f"Expected one exact execution_start title match; found {len(starts)}.")
        return report
    start_index, start = starts[0]
    execution_id = start.get("executionId")
    report["executionId"] = execution_id
    if execution_id is None:
        report["errors"].append("Matching execution_start has no executionId.")
        return report
    matching = [event for event in events[start_index + 1 :] if event.get("executionId") == execution_id]
    _account_sdk_operations(report, matching, completed_epoch_ns, wall_ns)
    ends = [event for event in matching if event.get("kind") == "execution_end"]
    if len(ends) != 1:
        report["errors"].append(f"Expected one execution_end for executionId; found {len(ends)}.")
    elif ends[0].get("isError"):
        report["errors"].append("The matching MCP execution ended with isError=true.")
    commands = [event for event in matching if event.get("kind") == "command_end"]
    diagnostics = [event for event in matching if event.get("kind") == "backend_diagnostic"]
    report["matched_command_ends"] = len(commands)
    report["matched_backend_diagnostics"] = len(diagnostics)
    primary = report["intervals"]
    ancillary = []
    covered = set()

    def add_interval(event, source, category, start_ns, end_ns, *, command_index=None, accounting="primary"):
        detail = {"source": source, "category": category, "accounting": accounting}
        if event.get("kind") == "command_end":
            detail.update(_command(event))
        else:
            detail.update(requestId=event.get("requestId"), traceId=event.get("traceId"))
        if start_ns < 0 or end_ns < start_ns:
            report["invalid_intervals"].append({**detail, "reason": "negative or reversed native interval"})
            return
        clipped = [max(started_epoch_ns, start_ns), min(completed_epoch_ns, end_ns)]
        if clipped[1] < clipped[0]:
            report["excluded_intervals"].append(
                {**detail, "reason": "native interval outside the outer MCP epoch span"}
            )
            return
        detail.update(interval_ns=clipped, native_interval_ns=[start_ns, end_ns], clamped=clipped != [start_ns, end_ns])
        (primary if accounting == "primary" else ancillary).append(detail)
        if accounting == "primary" and command_index is not None:
            covered.add(command_index)

    for index, event in enumerate(commands):
        if event.get("status") != "ok":
            continue
        result = event.get("result")
        result = result if isinstance(result, dict) else {}
        kind, method = result.get("kind"), event.get("method")
        source = category = None
        interval = None
        try:
            if kind == "observation":
                source, category = "result.read.intervalNs", "native_read"
                read = result.get("read")
                read = read if isinstance(read, dict) else {}
                if read.get("cached") is True:
                    report["excluded_intervals"].append(
                        {**_command(event), "source": source, "reason": "cached observation is not a fresh native read"}
                    )
                    continue
                interval = read.get("intervalNs")
                if interval is not None:
                    if not isinstance(interval, (list, tuple)) or len(interval) != 2:
                        raise ValueError("read.intervalNs must contain exactly two epoch-nanosecond values")
                    interval = [_scaled_ns(value, 1) for value in interval]
            elif kind in {"applications", "surfaces", "roots"}:
                source, category = "result.observed", "native_directory"
                observed = result.get("observed")
                if isinstance(observed, dict) and "startedAt" in observed and "completedAt" in observed:
                    interval = [_scaled_ns(observed[key], 1_000_000) for key in ("startedAt", "completedAt")]
            elif method == "capture":
                pixel_key = "screenshot" if kind == "desktopCapture" else "image"
                source, category = f"result.{pixel_key}", "native_pixels"
                pixels = result.get(pixel_key)
                if isinstance(pixels, dict) and "startedAt" in pixels and "completedAt" in pixels:
                    interval = [_scaled_ns(pixels[key], 1_000_000) for key in ("startedAt", "completedAt")]
            if source is not None:
                if interval is None:
                    report["missing_intervals"].append({**_command(event), "source": source})
                else:
                    add_interval(event, source, category, *interval, command_index=index)
        except (TypeError, ValueError) as error:
            report["invalid_intervals"].append({**_command(event), "source": source, "reason": str(error)})

    for event in diagnostics:
        value = event.get("value")
        if (
            isinstance(value, dict)
            and value.get("kind") == "input_activity"
            and value.get("phase") == "willCall"
            and value.get("transport") == "globalHID"
        ):
            # Preserve the recorded call boundary even when execution or timing
            # fails. A willCall is evidence of an attempted route, not acceptance.
            report["global_input_effects"].append(
                {
                    "executionId": execution_id,
                    "requestId": event.get("requestId"),
                    "traceId": event.get("traceId"),
                    **{
                        key: value[key]
                        for key in (
                            "nativeTimestamp",
                            "activityId",
                            "effectId",
                            "operation",
                            "sequence",
                            "command",
                            "intent",
                            "phase",
                            "stage",
                            "mechanism",
                            "device",
                            "eventType",
                            "transport",
                            "targetPID",
                            "recipientPID",
                            "presenterPID",
                            "window",
                            "globalInputAllowed",
                        )
                        if key in value
                    },
                }
            )
        if not (
            isinstance(value, dict)
            and value.get("kind") == "input_activity"
            and value.get("phase") == "didReturn"
            and value.get("nativeInvoked") is True
        ):
            continue
        stage = value.get("stage")
        if stage not in {"input", "preparation", "cleanup"}:
            report["missing_intervals"].append(
                {
                    "source": "input_activity.didReturn",
                    "stage": stage,
                    "reason": "unknown stage; cannot classify native-call work",
                }
            )
            continue
        source = "input_activity.didReturn.nativeTimestamp-durationMs"
        if value.get("nativeTimestamp") is None or value.get("durationMs") is None:
            report["missing_intervals"].append({"source": source, "stage": stage, "requestId": event.get("requestId")})
            continue
        try:
            end_ns = _timestamp_ns(value["nativeTimestamp"])
            duration_ns = _scaled_ns(value["durationMs"], 1_000_000)
            if duration_ns < 0:
                raise ValueError("negative native-call duration")
            command_index = next(
                (index for index, command in enumerate(commands) if _same_command(command, event)), None
            )
            add_interval(
                event,
                source,
                "native_input" if stage == "input" else stage,
                end_ns - duration_ns,
                end_ns,
                command_index=command_index,
                accounting="primary" if stage == "input" else stage,
            )
        except (TypeError, ValueError) as error:
            report["invalid_intervals"].append(
                {"source": source, "stage": stage, "requestId": event.get("requestId"), "reason": str(error)}
            )

    for index, event in enumerate(commands):
        if index not in covered:
            report["uncovered_native_commands"].append(
                {**_command(event), "reason": "no eligible primary native interval in this MCP span"}
            )
    for event in events:
        if not _resource_event(event):
            continue
        try:
            inside_span = started_epoch_ns <= _timestamp_ns(event.get("timestamp")) <= completed_epoch_ns
        except (TypeError, ValueError):
            inside_span = False
        if inside_span or event.get("executionId") == execution_id:
            report["cold_worker_resource_initialization"]["events"].append(event)
    report["cold_worker_resource_initialization"]["present"] = bool(
        report["cold_worker_resource_initialization"]["events"]
    )
    merged = _union(item["interval_ns"] for item in primary)
    report.update(
        union_intervals_ns=merged, interval_count=len(primary), actual_work_ns=sum(end - start for start, end in merged)
    )
    report["fraction_of_full_wall"] = report["actual_work_ns"] / wall_ns
    for category in sorted({item["category"] for item in primary}):
        rows = [item["interval_ns"] for item in primary if item["category"] == category]
        report["categories"][category] = {"interval_count": len(rows), "union_ns": _total(rows)}
    report["sources"] = [
        {"source": source, "interval_count": count}
        for source, count in sorted(Counter(item["source"] for item in primary).items())
    ]
    for stage in ("preparation", "cleanup"):
        rows = [item for item in ancillary if item["accounting"] == stage]
        report["preparation_cleanup"][stage] = {
            "interval_count": len(rows),
            "union_ns": _total(item["interval_ns"] for item in rows),
            "intervals": rows,
        }
    report["coverage_status"] = (
        "partial"
        if any(
            report[key]
            for key in ("uncovered_native_commands", "missing_intervals", "invalid_intervals", "excluded_intervals")
        )
        else "instrumented_intervals_only"
    )
    if not primary:
        report["errors"].append("No eligible primary native intervals were found; no duration fallback was used.")
    if report["actual_work_ns"] > wall_ns:
        report["errors"].append(
            "Native interval union exceeds full monotonic wall time; clock/boundary attribution is inconsistent."
        )
    if not report["errors"] and not report["journal_errors"]:
        report["status"] = "ok"
        report["majority_passed"] = report["actual_work_ns"] * 2 > wall_ns
    return report
