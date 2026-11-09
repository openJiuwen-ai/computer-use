# coding: utf-8
# Copyright (c) Huawei Technologies Co., Ltd. 2026. All rights reserved.

"""Multi-path routing core: scorer, metrics, registry, verifiers, executors and router decisions."""

import asyncio
import json
import sys
import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

import pytest

from computer_use_agent.routing import (
    GUI_FALLBACK_ID,
    MetricPriors,
    OutcomeRecord,
    PathKind,
    PathMetricsStore,
    PathRegistry,
    RiskClass,
    Router,
    RouterConfig,
    RoutingPolicy,
    ScoreWeights,
    TaskPath,
    Verdict,
    gui_fallback_path,
    render_plan,
    score_path,
)
from computer_use_agent.routing.executors import (
    PathArgumentError,
    execute_api,
    execute_cli,
    substitute,
    validate_arguments,
)
from computer_use_agent.routing.paths import ApiSpec, CliSpec, ExecutionStatus, VerifySpec
from computer_use_agent.routing.verifiers import verify


def make_cli(
    path_id, argv, *, app="test", intent="say hello", risk=RiskClass.READ, verify_spec=None, arguments=None, **kwargs
):
    return TaskPath(
        id=path_id,
        kind=PathKind.CLI,
        app=app,
        intent=intent,
        arguments=arguments or {},
        risk=risk,
        cli=CliSpec(argv=argv, **kwargs),
        verify=verify_spec,
    )


def echo_path(path_id="test.echo", **kwargs):
    return make_cli(
        path_id,
        (sys.executable, "-c", "print('hello-route')"),
        verify_spec=VerifySpec(kind="output_contains", expect="hello-route"),
        **kwargs,
    )


def test_scorer_prefers_reliable_and_fast_path():
    store = PathMetricsStore()
    fast = make_cli("fast", ("true",), intent="quick", time_hint=1.0)
    slow = make_cli("slow", ("true",), intent="slow", time_hint=50.0)
    weights = ScoreWeights(exploration=0.0)
    assert score_path(fast, store, weights).score > score_path(slow, store, weights).score
    for _ in range(6):
        store.record(OutcomeRecord(path_id="slow", task="t", success=False), PathKind.CLI)
    assert score_path(fast, store, weights).score > score_path(slow, store, weights).score


def test_scorer_exploration_bonus_samples_cold_paths():
    store = PathMetricsStore()
    weights = ScoreWeights(exploration=0.5)
    proven = echo_path()
    cold = make_cli("cold", (sys.executable, "-c", "print('x')"))
    for _ in range(10):
        store.record(OutcomeRecord(path_id=proven.id, task="t", success=True), PathKind.CLI)
    assert score_path(cold, store, weights).exploration_bonus > 0.0
    assert score_path(proven, store, weights).exploration_bonus == 0.0


def test_scorer_gui_fallback_latency_scales_with_operation_estimate():
    store = PathMetricsStore()
    fallback = gui_fallback_path()
    single = score_path(fallback, store, ScoreWeights(exploration=0.0, gui_ops_estimate=1))
    scaled = score_path(fallback, store, ScoreWeights(exploration=0.0, gui_ops_estimate=8))
    assert scaled.expected_latency_s == pytest.approx(single.expected_latency_s * 8)
    assert scaled.time_penalty > single.time_penalty


def test_metrics_posterior_ignores_unknown_effects_and_persists(tmp_path):
    store = PathMetricsStore(persistence_path=tmp_path / "metrics.jsonl")
    store.record(OutcomeRecord(path_id="p", task="t", success=True), PathKind.API)
    store.record(OutcomeRecord(path_id="p", task="t", success=None), PathKind.API)
    store.record(OutcomeRecord(path_id="p", task="t", success=False), PathKind.API)
    snapshot = store.snapshot("p", PathKind.API)
    assert snapshot.attempts == 3
    assert snapshot.unknown_effects == 1
    posterior = store.posterior_success("p", PathKind.API)
    alpha, beta = MetricPriors().success_alpha_beta[PathKind.API]
    assert posterior == pytest.approx((1 + alpha) / (2 + alpha + beta))

    reloaded = PathMetricsStore(persistence_path=tmp_path / "metrics.jsonl")
    assert reloaded.snapshot("p", PathKind.API).attempts == 3
    assert reloaded.posterior_success("p", PathKind.API) == pytest.approx(posterior)


def test_metrics_ema_updates():
    store = PathMetricsStore(MetricPriors(ema_alpha=0.5))
    store.record(OutcomeRecord(path_id="p", task="t", success=True, latency_s=10.0, cost=1.0), PathKind.API)
    store.record(OutcomeRecord(path_id="p", task="t", success=True, latency_s=20.0, cost=3.0), PathKind.API)
    snapshot = store.snapshot("p", PathKind.API)
    assert snapshot.ema_latency_s == pytest.approx(15.0)
    assert snapshot.ema_cost == pytest.approx(2.0)
    assert snapshot.total_cost == pytest.approx(4.0)


def test_registry_load_file_and_preconditions(tmp_path):
    payload = {
        "paths": [
            {
                "id": "fs.list",
                "kind": "cli",
                "app": "fs",
                "intent": "list directory",
                "risk": "read",
                "cli": {"argv": ["ls", "{directory}"], "binary": "definitely-missing-binary-xyz"},
                "verify": {"kind": "exit_code", "expect": 0},
            }
        ]
    }
    file_path = tmp_path / "paths.json"
    file_path.write_text(json.dumps(payload), encoding="utf-8")
    registry = PathRegistry()
    assert registry.load_file(file_path) == 1
    ok, reason = registry.check_preconditions(registry.get("fs.list"))
    assert not ok
    assert "not found" in reason


def test_registry_rejects_invalid_kind(tmp_path):
    file_path = tmp_path / "paths.json"
    file_path.write_text(json.dumps([{"id": "x", "kind": "telepathy"}]), encoding="utf-8")
    with pytest.raises(ValueError):
        PathRegistry().load_file(file_path)


def test_registry_import_openapi_creates_multiple_api_paths():
    spec = {
        "servers": [{"url": "https://api.example.com"}],
        "paths": {
            "/items": {
                "get": {"operationId": "list_items", "summary": "List items"},
                "post": {"operationId": "create_item", "summary": "Create item"},
            },
            "/items/{item_id}": {
                "get": {
                    "operationId": "show_item",
                    "summary": "Show item",
                    "parameters": [{"name": "item_id", "required": True, "description": "item id"}],
                },
            },
        },
    }
    registry = PathRegistry()
    imported = registry.import_openapi(spec, app="shop")
    assert len(imported) == 3
    getter = registry.get("shop.api.show_item")
    assert getter.risk is RiskClass.READ
    assert getter.arguments["required"] == ["item_id"]
    assert registry.get("shop.api.create_item").risk is RiskClass.WRITE
    assert registry.get("shop.api.create_item").verify.kind == "http_status"


def test_substitute_quotes_values_and_preserves_python_braces():
    rendered = substitute("print({text!r})\ndata = {'role': 1}", {"text": "hi"})
    assert "print('hi')" in rendered
    assert "{'role': 1}" in rendered
    with pytest.raises(PathArgumentError):
        substitute("echo {missing}", {})


def test_validate_arguments_enforces_schema():
    path = echo_path()
    validate_arguments(path, {})
    strict = make_cli(
        "strict",
        ("echo", "{text}"),
        intent="echo",
        arguments={
            "type": "object",
            "properties": {"text": {"type": "string"}},
            "required": ["text"],
            "additionalProperties": False,
        },
    )
    validate_arguments(strict, {"text": "ok"})
    with pytest.raises(PathArgumentError):
        validate_arguments(strict, {"text": "ok", "unexpected": 1})
    with pytest.raises(PathArgumentError):
        validate_arguments(strict, {"unexpected": 1})


def test_verify_contracts_pass_fail_unknown():
    async def run():
        assert (await verify(VerifySpec(kind="exit_code", expect=0), {"returncode": 0}))[0] is Verdict.PASS
        assert (await verify(VerifySpec(kind="http_status", expect=200), {"status": 500}))[0] is Verdict.FAIL
        assert (await verify(VerifySpec(kind="output_contains", expect="hi"), {"output": "say hi"}))[0] is Verdict.PASS
        assert (await verify(VerifySpec(kind="output_regex", expect=r"h.llo"), {"output": "hallo"}))[0] is Verdict.PASS
        assert (await verify(VerifySpec(kind="json_field", expect="42", field="a.b"), {"output": '{"a": {"b": 42}}'}))[
            0
        ] is Verdict.PASS
        verdict, note = await verify(None, {})
        assert verdict is Verdict.UNKNOWN
        assert "not independently verified" in note

    asyncio.run(run())


def test_cli_executor_success_output_and_exit_code():
    async def run():
        execution = await execute_cli(echo_path(), {})
        assert execution.status is ExecutionStatus.COMPLETED
        assert "hello-route" in execution.output
        failing = make_cli("fail", (sys.executable, "-c", "import sys; sys.exit(3)"))
        execution = await execute_cli(failing, {})
        assert execution.status is ExecutionStatus.COMPLETED
        assert '"returncode": 3' in execution.detail

    asyncio.run(run())


def test_cli_executor_timeout_is_effect_unknown_not_replayable():
    async def run():
        path = make_cli("slow", (sys.executable, "-c", "import time; time.sleep(30)"), timeout_s=0.2)
        execution = await execute_cli(path, {})
        assert execution.status is ExecutionStatus.EFFECT_UNKNOWN
        assert execution.dispatched
        assert "do not replay" in execution.detail

    asyncio.run(run())


class _Handler(BaseHTTPRequestHandler):
    def log_message(self, *args):
        return

    def do_GET(self):
        if self.path.startswith("/fail"):
            self.send_response(404)
            self.end_headers()
            self.wfile.write(b"not here")
            return
        self.send_response(200)
        self.send_header("Content-Type", "application/json")
        self.end_headers()
        self.wfile.write(json.dumps({"usage": {"total_tokens": 100}, "a": {"b": "needle"}}).encode())


def test_api_executor_success_cost_and_http_error():
    server = ThreadingHTTPServer(("127.0.0.1", 0), _Handler)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    base = f"http://127.0.0.1:{server.server_port}"

    async def run():
        ok = TaskPath(
            id="api.ok",
            kind=PathKind.API,
            app="svc",
            intent="fetch",
            api=ApiSpec(method="GET", url=base + "/data"),
            verify=VerifySpec(kind="http_status", expect=200),
        )
        execution = await execute_api(ok, {})
        assert execution.status is ExecutionStatus.COMPLETED
        assert execution.cost == pytest.approx(100 * 0.000002)
        bad = TaskPath(
            id="api.bad",
            kind=PathKind.API,
            app="svc",
            intent="fetch missing",
            api=ApiSpec(method="GET", url=base + "/fail"),
        )
        execution = await execute_api(bad, {})
        assert execution.status is ExecutionStatus.FAILED
        assert '"status": 404' in execution.detail

    try:
        asyncio.run(run())
    finally:
        server.shutdown()
        server.server_close()
        thread.join(timeout=3)


def test_api_executor_unreachable_host_is_effect_unknown():
    path = TaskPath(
        id="api.down",
        kind=PathKind.API,
        app="svc",
        intent="fetch",
        api=ApiSpec(method="GET", url="http://127.0.0.1:1/unreachable", timeout_s=2),
    )
    execution = asyncio.run(execute_api(path, {}))
    assert execution.status is ExecutionStatus.EFFECT_UNKNOWN
    assert not execution.dispatched


def test_router_picks_best_route_and_records_outcome():
    registry = PathRegistry()
    registry.register(echo_path())
    router = Router(registry, RouterConfig(policy=RoutingPolicy(min_confidence=0.4)))

    result = asyncio.run(router.route("say hello"))
    assert result.plan.decision == "execute"
    assert result.plan.chosen.path.id == "test.echo"
    assert result.success is True
    assert "hello-route" in result.executions[0].output
    snapshot = router.store.snapshot("test.echo", PathKind.CLI)
    assert snapshot.attempts == 1
    assert snapshot.successes == 1


def test_router_fallback_to_gui_when_confidence_unreachable():
    router = Router(PathRegistry(), RouterConfig(policy=RoutingPolicy(min_confidence=0.99)))

    result = asyncio.run(router.route("do something odd"))
    assert result.plan.decision == "execute"
    assert result.plan.chosen.path.id == GUI_FALLBACK_ID
    assert result.success is None
    assert "gui fallback" in result.summary
    assert result.executions == ()


def test_router_refuses_when_even_gui_fallback_is_proven_bad():
    policy = RoutingPolicy(min_confidence=0.99, refuse_floor=0.4, refuse_after_attempts=2)
    router = Router(PathRegistry(), RouterConfig(policy=policy))
    for _ in range(2):
        router.store.record(OutcomeRecord(path_id=GUI_FALLBACK_ID, task="t", success=False), PathKind.GUI)

    result = asyncio.run(router.route("do something odd"))
    assert result.plan.decision == "refuse"
    assert result.success is False
    assert "GUI fallback success rate" in result.plan.reason
    assert "do not force GUI operations" in render_plan(result.plan)


def test_router_decompose_runs_subtasks_and_stops_at_first_failure():
    registry = PathRegistry()
    registry.register(
        make_cli(
            "good.echo",
            (sys.executable, "-c", "print('good')"),
            app="good",
            intent="say good",
            verify_spec=VerifySpec(kind="output_contains", expect="good"),
        )
    )
    registry.register(
        make_cli(
            "bad.echo",
            (sys.executable, "-c", "import sys; print('bad'); sys.exit(1)"),
            app="bad",
            intent="say bad",
            verify_spec=VerifySpec(kind="exit_code", expect=0),
        )
    )
    router = Router(registry, RouterConfig(policy=RoutingPolicy(min_confidence=0.4)))

    ok = asyncio.run(router.route("combined", subtasks=["run good step", "run good step"]))
    assert ok.plan.decision == "decompose"
    assert ok.success is True
    assert len(ok.executions) == 2

    bad = asyncio.run(router.route("combined", subtasks=["run good step", "run bad step", "run good step"]))
    assert bad.success is False
    assert len(bad.executions) == 2  # third subtask never started
    assert "subtask 1" in bad.summary


def test_router_effect_unknown_keeps_posterior_and_reports_no_replay():
    registry = PathRegistry()
    router = Router(registry, RouterConfig(policy=RoutingPolicy(min_confidence=0.4)))
    prior = router.store.posterior_success("down.net", PathKind.API)
    registry.register(
        TaskPath(
            id="down.net",
            kind=PathKind.API,
            app="flaky",
            intent="network call",
            api=ApiSpec(method="GET", url="http://127.0.0.1:1/x", timeout_s=2),
            verify=VerifySpec(kind="http_status", expect=200),
        )
    )

    async def run():
        plan = await router.plan("network call")
        assert plan.decision == "execute"
        assert plan.chosen.path.id == "down.net"
        return await router.execute_plan(plan, {})

    result = asyncio.run(run())
    assert result.success is None
    execution = result.executions[0]
    assert execution.status is ExecutionStatus.EFFECT_UNKNOWN
    snapshot = router.store.snapshot("down.net", PathKind.API)
    assert snapshot.attempts == 1
    assert snapshot.unknown_effects == 1
    assert snapshot.successes == 0
    assert router.store.posterior_success("down.net", PathKind.API) == pytest.approx(prior)


def test_router_validation_error_does_not_degrade_posterior():
    registry = PathRegistry()
    # No declared schema: eligibility passes without arguments, but the argv template needs {text}.
    registry.register(make_cli("needs.arg", ("echo", "{text}"), intent="echo text"))
    router = Router(registry, RouterConfig(policy=RoutingPolicy(min_confidence=0.4)))
    prior = router.store.posterior_success("needs.arg", PathKind.CLI)

    async def run():
        plan = await router.plan("echo text")
        assert plan.chosen.path.id == "needs.arg"
        return await router.execute_plan(plan, {})  # {text} missing at dispatch time

    result = asyncio.run(run())
    assert result.success is None
    snapshot = router.store.snapshot("needs.arg", PathKind.CLI)
    assert snapshot.attempts == 1
    assert snapshot.unknown_effects == 1
    assert router.store.posterior_success("needs.arg", PathKind.CLI) == pytest.approx(prior)


def test_router_strict_schema_marks_path_ineligible_without_arguments():
    registry = PathRegistry()
    registry.register(
        make_cli(
            "strict.arg",
            ("echo", "{text}"),
            intent="echo text",
            arguments={
                "type": "object",
                "properties": {"text": {"type": "string"}},
                "required": ["text"],
                "additionalProperties": False,
            },
        )
    )
    router = Router(registry, RouterConfig(policy=RoutingPolicy(min_confidence=0.4)))

    result = asyncio.run(router.route("echo text"))
    assert result.plan.chosen.path.id == GUI_FALLBACK_ID
    strict = next(c for c in result.plan.candidates if c.path.id == "strict.arg")
    assert not strict.eligible
    assert "missing required arguments" in strict.reasons[0]


def test_router_destructive_path_requires_confirmation():
    registry = PathRegistry()
    registry.register(
        make_cli(
            "rm.stuff",
            ("echo", "removed"),
            risk=RiskClass.DESTRUCTIVE,
            intent="remove things",
            verify_spec=VerifySpec(kind="output_contains", expect="removed"),
        )
    )
    router = Router(registry, RouterConfig(policy=RoutingPolicy(min_confidence=0.4)))

    denied = asyncio.run(router.route("remove things"))
    assert denied.plan.chosen.path.id == GUI_FALLBACK_ID  # destructive excluded, GUI remains as fallback

    async def confirm(path, arguments):
        return True

    confirmed_router = Router(
        registry, RouterConfig(policy=RoutingPolicy(min_confidence=0.4)), confirm=confirm, store=router.store
    )
    allowed = asyncio.run(confirmed_router.route("remove things"))
    assert allowed.plan.chosen.path.id == "rm.stuff"
    assert allowed.success is True
