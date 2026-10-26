# macOS native contracts

The source layout follows the existing native execution boundary. `Runtime/Driver.swift`
retains the wire-request ledger and request lifetime; `Runtime/NativeExecution.swift`
retains operation, primitive-contract and selection tables and assembles borrowed
services. `NativeOperation` remains the only owner of its step ledger, input progress,
retained input references and paired resource release.

The production call graph is:

```text
Driver → NativeExecution (assembly) → DriverPipeline
  begin/finish → Runtime/Resources/NativeResourceScope
  inspect      → Read/ReadPipeline
                   → Read/Query/NativeNodeQueryPipeline
                   → Control/InputContext/NativeInputContextPipeline
  capture      → Capture/CapturePipeline
  execute      → Control/ControlPipeline → Write/NativeWritePipeline
                   → Routes/NativeRoutePipeline
                   → Prepare/NativePreparePipeline or Dispatch/NativeDispatchPipeline
                   → Write/Launch/NativeLaunchPipeline (installation branch)
```

Each `Passes/*.swift` contains one leaf stage and states its effect contract.
Pipeline files register order and selected branches. `Contracts` contains immutable
wire data and borrowed views: query-only object access cannot dispatch an addressed
edit; control/launch contract lookup cannot replace primitive declarations.
`NativeScopeResolver` borrows the original registry, selection catalogue and capture
owners; it owns no operation journal or alternative input strategy.

The preserved order is important:

- Resource admission resolves/retains, waits for the original desktop lease,
  rechecks the fixed boundary, then transfers resources to `NativeOperation`.
- Node reads resolve, collect/register facts in their original registry, recheck
  the live scope, then retain selection evidence or update pointer observation.
- Input context resolves dependencies, samples existing routes, publishes only
  that selection's declarations, checks the original operation, then encodes facts.
- Writes decode and validate the envelope, enter the original step ledger, resolve
  the live target, check initial boundaries, select one native branch and run it.
  Every system unit keeps its final guard, dispatch-in-flight callback and native
  send in the same call and actor. Compound inputs keep per-event rechecks.
- Preparation confirms selection before recording readiness. Unknown/partial
  dispatch and cleanup evidence cannot trigger another native route or replay.

Run the source-boundary checks without UI access:

```sh
uv run python os_adapter/macos/Tests/Runtime/test_source_architecture.py
```

Build and run all native mechanism contracts without desktop input:

```sh
swift build --package-path os_adapter/macos
uv run python tests/run_macos.py --native-only
```

`NativePipelineContractTests` runs the actual query, preparation and dispatch
composition, including failure barriers, step replay, narrow object substitutes,
unselected routes and a test-only local fact extension. Other native tests cover
window/identity changes, modal boundaries, deadlines, cancellation, clipboard and
paired input cleanup. These checks do not replace real desktop acceptance.

`NativeElementHitSelectionTests` covers bounded point selection and preserves AX
query errors separately from target/occlusion evidence. `NativeInputWindowHitTests`
covers independent private capabilities and the public AX-hit/window-identity
fallback. `Acceptance/ClickSafetyAcceptance.py --run --accepts-first-mouse=true` uses a dedicated AppKit
fixture to check that window preparation never clicks an unrelated control and
that activation points, partial coverage and scrolling bounds select one target.
Each case starts a fresh fixture. The default `accepts-first-mouse=false` is an
explicit consumption limitation probe: a cold background window may consume the
only requested down, and the runner preserves that failed effect instead of
retrying. `--desktop exclusive` requires the matching explicit host environment
and checks authorized foreground preparation separately.
