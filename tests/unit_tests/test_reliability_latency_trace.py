# Copyright (c) Huawei Technologies Co., Ltd. 2026. All rights reserved.

"""Actual-work accounting accepts only native interval evidence, never envelopes."""

import importlib.util
import json
from pathlib import Path

import pytest

_SOURCE = Path(__file__).parents[1] / "manual" / "reliability_latency_trace.py"
_SPEC = importlib.util.spec_from_file_location("reliability_latency_trace", _SOURCE)
_MODULE = importlib.util.module_from_spec(_SPEC)
_SPEC.loader.exec_module(_MODULE)
measure_work = _MODULE.measure_work

BASE = 1_793_750_400_000_000_000  # 2026-11-04T00:00:00Z
MS = 1_000_000


def command(id_, result, *, method="inspect", execution_id=7, status="ok"):
    return {
        "kind": "command_end",
        "executionId": execution_id,
        "id": id_,
        "traceId": f"trace-{id_}",
        "method": method,
        "status": status,
        "result": result,
        "durationMs": 50_000,
    }


def observation(start_ms, end_ms, **read):
    return {"kind": "observation", "read": dict(intervalNs=[BASE + start_ms * MS, BASE + end_ms * MS], **read)}


def activity(id_, *, stage="input", duration_ms=100, timestamp="2026-11-04T00:00:00.850Z", **changes):
    value = {
        "kind": "input_activity",
        "phase": "didReturn",
        "stage": stage,
        "nativeInvoked": True,
        "nativeTimestamp": timestamp,
        "durationMs": duration_ms,
    }
    value.update(changes)
    return {
        "kind": "backend_diagnostic",
        "executionId": 7,
        "requestId": id_,
        "traceId": f"trace-{id_}",
        "timestamp": "2026-11-04T00:00:30Z",
        "value": value,
    }


def write_trace(tmp_path, events, *, prefix=(), suffix=(), execution_error=False):
    path = tmp_path / "journal.jsonl"
    rows = [
        *prefix,
        {"kind": "execution_start", "title": "unique operation", "executionId": 7},
        *events,
        {"kind": "execution_end", "executionId": 7, "isError": execution_error},
        *suffix,
    ]
    path.write_text("".join(json.dumps(row) + "\n" for row in rows), encoding="utf-8")
    return path


def measured(path, **changes):
    options = {
        "title": "unique operation",
        "wall_ns": 1000 * MS,
        "started_epoch_ns": BASE,
        "completed_epoch_ns": BASE + 1000 * MS,
    }
    options.update(changes)
    return measure_work(path, **options)


def test_unions_overlaps_uses_pixels_and_excludes_preparation_cleanup_and_other_execution(tmp_path):
    path = write_trace(
        tmp_path,
        [
            command(1, observation(200, 600)),
            command(
                2,
                {
                    "kind": "capture",
                    "observed": {"startedAt": BASE / MS, "completedAt": BASE / MS + 1000},
                    "image": {"startedAt": BASE / MS + 500, "completedAt": BASE / MS + 800},
                },
                method="capture",
            ),
            command(
                3, {"kind": "applications", "observed": {"startedAt": BASE / MS + 100, "completedAt": BASE / MS + 300}}
            ),
            activity(4),
            activity(4, stage="preparation", duration_ms=800),
            activity(4, stage="cleanup", duration_ms=500),
            activity(4, nativeInvoked=False, duration_ms=850),
            command(4, {}, method="execute"),
            command(5, {"kind": "inputContext", "observed": "identity-not-an-interval"}),
            command(6, observation(0, 1000), execution_id=8),
        ],
    )
    report = measured(path)
    assert report["status"] == "ok"
    assert report["executionId"] == 7
    assert report["actual_work_ns"] == 750 * MS
    assert report["fraction_of_full_wall"] == 0.75
    assert report["majority_passed"]
    assert report["union_intervals_ns"] == [[BASE + 100 * MS, BASE + 850 * MS]]
    assert report["interval_count"] == 4
    assert report["preparation_cleanup"]["preparation"]["union_ns"] == 800 * MS
    assert report["preparation_cleanup"]["cleanup"]["union_ns"] == 500 * MS
    assert [row["result_kind"] for row in report["uncovered_native_commands"]] == ["inputContext"]
    assert report["coverage_status"] == "partial"
    assert set(report["categories"]) == {"native_read", "native_directory", "native_pixels", "native_input"}


@pytest.mark.parametrize(("duration_ms", "passed"), [(500, False), (501, True)])
def test_majority_is_strictly_greater_than_half_full_wall(tmp_path, duration_ms, passed):
    report = measured(write_trace(tmp_path, [command(1, observation(0, duration_ms))]))
    assert report["actual_work_ns"] == duration_ms * MS
    assert report["majority_passed"] is passed


def test_clips_native_intervals_to_outer_mcp_epoch_span(tmp_path):
    report = measured(write_trace(tmp_path, [command(1, observation(-100, 800)), command(2, observation(1200, 1400))]))
    assert report["actual_work_ns"] == 800 * MS
    assert report["intervals"][0]["clamped"]
    assert report["excluded_intervals"][0]["id"] == 2


def test_missing_invalid_intervals_and_command_envelopes_never_fill_gaps(tmp_path):
    report = measured(
        write_trace(
            tmp_path,
            [
                command(1, observation(100, 200)),
                command(2, observation(600, 300)),
                command(
                    3,
                    {"kind": "capture", "observed": {"startedAt": BASE / MS, "completedAt": BASE / MS + 1000}},
                    method="capture",
                ),
                command(4, {}, method="execute"),
                activity(4, duration_ms="900"),
                activity(4, nativeTimestamp=None),
                activity(4, duration_ms=900, phase="end"),
            ],
        )
    )
    assert report["status"] == "ok"
    assert report["actual_work_ns"] == 100 * MS
    assert not report["majority_passed"]
    assert len(report["invalid_intervals"]) == 2
    assert len(report["missing_intervals"]) == 2
    assert len(report["uncovered_native_commands"]) == 3


def test_uses_desktop_screenshot_interval_and_native_input_timestamp_not_receive_timestamp(tmp_path):
    report = measured(
        write_trace(
            tmp_path,
            [
                command(
                    1,
                    {
                        "kind": "desktopCapture",
                        "observed": {"startedAt": BASE / MS, "completedAt": BASE / MS + 1000},
                        "screenshot": {"startedAt": BASE / MS + 100, "completedAt": BASE / MS + 200},
                    },
                    method="capture",
                ),
                activity(2, duration_ms=100.125),
                command(2, {}, method="execute"),
            ],
        )
    )
    assert report["actual_work_ns"] == 200_125_000
    assert report["intervals"][1]["interval_ns"] == [BASE + 749_875_000, BASE + 850 * MS]
    assert any("+/-1 ms" in note for note in report["coverage_limitations"])


def test_resource_initialization_is_separate_including_before_execution_start(tmp_path):
    resource = {
        "kind": "resource_initialization_end",
        "timestamp": "2026-11-04T00:00:00.900Z",
        "resource": "worker",
        "durationMs": 900,
    }
    report = measured(write_trace(tmp_path, [command(1, observation(0, 200))], prefix=[resource]))
    assert report["actual_work_ns"] == 200 * MS
    assert not report["majority_passed"]
    assert report["cold_worker_resource_initialization"] == {
        "present": True,
        "events": [resource],
        "excluded_from_primary": True,
    }


def test_cached_read_is_not_evidence_of_current_native_work(tmp_path):
    report = measured(write_trace(tmp_path, [command(1, observation(0, 900, cached=True))]))
    assert report["status"] == "error"
    assert report["actual_work_ns"] == 0
    assert not report["majority_passed"]
    assert "cached" in report["excluded_intervals"][0]["reason"]


@pytest.mark.parametrize(
    "failure", ["missing_file", "no_title", "duplicate_title", "execution_error", "bad_json", "no_execution_end"]
)
def test_trace_errors_return_majority_false_without_fabricated_fallback(tmp_path, failure):
    path = write_trace(tmp_path, [command(1, observation(0, 900))], execution_error=failure == "execution_error")
    if failure == "missing_file":
        path.unlink()
    elif failure == "no_title":
        path.write_text(path.read_text().replace("unique operation", "other operation"))
    elif failure == "duplicate_title":
        with path.open("a") as journal:
            journal.write(json.dumps({"kind": "execution_start", "executionId": 8, "title": "unique operation"}) + "\n")
    elif failure == "bad_json":
        with path.open("a") as journal:
            journal.write("{broken\n")
    elif failure == "no_execution_end":
        path.write_text("\n".join(path.read_text().splitlines()[:-1]) + "\n")
    report = measured(path)
    assert report["status"] == "error"
    assert not report["majority_passed"]
    assert report["errors"] or report["journal_errors"]


def test_inconsistent_monotonic_and_epoch_spans_do_not_pass(tmp_path):
    report = measured(write_trace(tmp_path, [command(1, observation(0, 900))]), wall_ns=100 * MS)
    assert report["actual_work_ns"] == 900 * MS
    assert report["status"] == "error"
    assert not report["majority_passed"]


def test_missing_journal_path_returns_structured_error():
    report = measured(None)
    assert report["status"] == "error"
    assert report["log_path"] is None
    assert report["global_input_effects"] == []
    assert not report["majority_passed"]
    assert "No MCP journal path" in report["errors"][0]


@pytest.mark.parametrize("failure", [None, "execution_error", "bad_json", "no_execution_end"])
def test_global_input_will_call_evidence_survives_execution_and_journal_errors(tmp_path, failure):
    global_call = activity(
        2,
        phase="willCall",
        transport="globalHID",
        mechanism="CGEventPost",
        activityId="global-activity",
        effectId=3,
        eventType="leftMouseDown",
        targetPID=123,
    )
    other_execution = {**global_call, "executionId": 8}
    path = write_trace(
        tmp_path,
        [
            command(1, observation(0, 600)),
            global_call,
            activity(2, phase="willCall", transport="process"),
            activity(2, transport="globalHID"),
            other_execution,
        ],
        execution_error=failure == "execution_error",
    )
    if failure == "bad_json":
        with path.open("a") as journal:
            journal.write("{broken\n")
    elif failure == "no_execution_end":
        path.write_text("\n".join(path.read_text().splitlines()[:-1]) + "\n")
    report = measured(path)
    assert len(report["global_input_effects"]) == 1
    evidence = report["global_input_effects"][0]
    assert evidence["executionId"] == 7
    assert evidence["requestId"] == 2
    assert evidence["traceId"] == "trace-2"
    assert evidence["activityId"] == "global-activity"
    assert evidence["effectId"] == 3
    assert evidence["mechanism"] == "CGEventPost"
    assert evidence["phase"] == "willCall"
    assert evidence["transport"] == "globalHID"
    assert report["status"] == ("ok" if failure is None else "error")
    if failure is not None:
        assert not report["majority_passed"]


def sdk_event(boundary, operation_id, timestamp_ms, **details):
    event = {
        "kind": f"sdk_operation_{boundary}",
        "executionId": 7,
        "operationId": operation_id,
        "operationType": "ObserveUI",
        "timestamp": f"2026-11-04T00:00:00.{timestamp_ms:03d}Z",
        "budgetMs": 1000,
    }
    if boundary == "end":
        event.update(durationMs=200, status="ok", overrun=False, outcome=None)
    event.update(details)
    return event


def test_sdk_operations_preserve_real_duration_results_and_full_mcp_wall_comparison(tmp_path):
    events = [
        sdk_event("start", "read", 100),
        sdk_event("end", "read", 300, observationComplete=True, observationPartial=False),
        sdk_event("start", "click", 600, operationType="ExecuteAction"),
        sdk_event(
            "end",
            "click",
            900,
            operationType="ExecuteAction",
            durationMs=300,
            observationComplete=False,
            observationStatus="partial",
            observationGapCodes=["window-gap"],
        ),
        sdk_event("end", "foreign", 990, executionId=8),
        command(1, observation(0, 600)),
    ]
    report = measured(write_trace(tmp_path, events))
    assert report["status"] == "ok" and report["majority_passed"]
    assert report["actual_work_ns"] == 600 * MS
    assert report["wall_ns"] == 1000 * MS
    assert report["sdk_operations_sum_ns"] == 500 * MS
    assert report["sdk_operations_sum_count"] == 2
    assert report["sdk_operations_sum_fraction_of_full_wall"] == 0.5
    assert report["wall_minus_sdk_operations_sum_ns"] == 500 * MS
    assert report["sdk_operation_gaps"] == []
    assert report["sdk_operations_coverage"] == "complete"
    assert [row["id"] for row in report["sdk_operations"]] == ["read", "click"]
    read, click = report["sdk_operations"]
    assert read["type"] == "ObserveUI" and read["durationMs"] == 200 and read["complete"]
    assert click["type"] == "ExecuteAction" and click["durationMs"] == 300 and click["complete"]
    assert click["status"] == "ok" and click["overrun"] is False
    assert click["observationGapCodes"] == ["window-gap"] and click["observationComplete"] is False
    assert report["final_delivery_tail_ns"] == 100 * MS
    assert report["final_delivery_tail_anchor"]["id"] == "click"
    assert report["final_delivery_tail_anchor"]["timestamp_resolution_ns"] == MS
    assert any("presentation, transport and parent collection" in note for note in report["coverage_limitations"])


def test_sdk_missing_start_and_end_are_gaps_and_only_recorded_ends_contribute_duration(tmp_path):
    report = measured(
        write_trace(
            tmp_path,
            [
                sdk_event("end", "end-only", 300, durationMs=125.125),
                sdk_event("start", "start-only", 400),
                command(1, observation(0, 600)),
            ],
        )
    )
    assert report["majority_passed"]  # SDK trace coverage does not change the original native-work gate.
    assert report["sdk_operations_coverage"] == "partial"
    assert report["sdk_operations_sum_ns"] == 125_125_000
    assert report["sdk_operations_sum_count"] == 1
    assert {(gap["id"], gap["code"]) for gap in report["sdk_operation_gaps"]} == {
        ("end-only", "missing_start"),
        ("start-only", "missing_end"),
    }
    assert report["sdk_operations"][1]["durationMs"] is None
    assert report["final_delivery_tail_ns"] == 700 * MS


@pytest.mark.parametrize(
    ("change", "gap"),
    [
        ({"durationMs": None}, "invalid_duration"),
        ({"durationMs": -1}, "invalid_duration"),
        ({"durationMs": float("nan")}, "invalid_duration"),
        ({"operationType": "AnotherType"}, "operation_type_changed"),
        ({"status": ["invalid"]}, "invalid_end_status"),
        ({"overrun": None}, "invalid_overrun"),
        ({"timestamp": "not-a-timestamp"}, "invalid_end_timestamp"),
    ],
)
def test_invalid_sdk_diagnostics_are_visible_without_changing_native_work_gate(tmp_path, change, gap):
    report = measured(
        write_trace(
            tmp_path,
            [sdk_event("start", "read", 100), sdk_event("end", "read", 300, **change), command(1, observation(0, 600))],
        )
    )
    assert report["status"] == "ok" and report["majority_passed"]
    assert gap in {item["code"] for item in report["sdk_operation_gaps"]}
    assert not report["sdk_operations"][0]["complete"]
    if "durationMs" in change:
        assert report["sdk_operations_sum_ns"] == 0
    if "timestamp" in change:
        assert report["final_delivery_tail_ns"] is None


def test_sdk_duplicate_end_does_not_double_count_recorded_duration(tmp_path):
    report = measured(
        write_trace(
            tmp_path,
            [
                sdk_event("start", "read", 100),
                sdk_event("end", "read", 300),
                sdk_event("end", "read", 301),
                command(1, observation(0, 600)),
            ],
        )
    )
    assert report["sdk_operations_sum_ns"] == 0
    assert report["sdk_operations_sum_count"] == 0
    assert report["sdk_operations"][0]["durationMs"] is None
    assert report["sdk_operation_gaps"] == [{"id": "read", "code": "duplicate_end", "count": 2}]
    assert report["final_delivery_tail_ns"] == 699 * MS


def test_sdk_end_error_and_overrun_facts_survive_mcp_error(tmp_path):
    report = measured(
        write_trace(
            tmp_path,
            [
                sdk_event("start", "read", 10),
                sdk_event(
                    "end",
                    "read",
                    990,
                    durationMs=1050,
                    status="error",
                    overrun=True,
                    errorType="OperationDeadlineExceeded",
                    effect="possible",
                    observationComplete=False,
                ),
                command(1, observation(0, 600)),
            ],
            execution_error=True,
        )
    )
    assert report["status"] == "error" and not report["majority_passed"]
    row = report["sdk_operations"][0]
    assert row["durationMs"] == 1050 and row["status"] == "error" and row["overrun"] is True
    assert row["effect"] == "possible" and row["observationComplete"] is False
    assert report["sdk_operations_sum_ns"] == 1050 * MS
    assert report["wall_minus_sdk_operations_sum_ns"] == -50 * MS  # Preserve the comparison without clipping.


def test_sdk_trace_absence_does_not_invent_operations_or_delivery_tail(tmp_path):
    report = measured(write_trace(tmp_path, [command(1, observation(0, 600))]))
    assert report["majority_passed"]
    assert report["sdk_operations"] == [] and report["sdk_operations_sum_count"] == 0
    assert report["sdk_operations_coverage"] == "unavailable"
    assert report["sdk_operation_gaps"] == [{"code": "missing_sdk_operation_events"}]
    assert report["final_delivery_tail_ns"] is None and report["final_delivery_tail_anchor"] is None
