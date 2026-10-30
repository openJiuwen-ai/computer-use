# Local gaze service contract

This adapter consumes an existing service; the gaze estimator, calibration UI and voice launcher are separate components. It adds no camera or model dependency. The first integration reads an independent root window, including its confirmed attached dialogs; selecting an attached dialog directly is not currently supported. No click, text entry, pause/resume guard, or automatic voice-task dispatch is added.

## Connection

The host supplies a JSON file with `url` and `token`. The URL must be `http://127.0.0.1:<port>` without a path, credentials, query or fragment. Requests are POSTs to `/api/target`, authenticated with `X-Session-Token`. Redirects and environment proxies are disabled. Each response is limited to 16 KiB. Service errors are not copied into model output.

Each invocation creates a unique `owner`. Request bodies contain `action`, `owner`, and `selection_id` (null until claimed). `claim` also sends `expected_selection_id`, or null, and `read_only: true`. The service must enforce exclusive ownership, freshness at claim, and native window identity throughout the read. Once claimed, looking away can preserve this read-only target; stopping the camera, invalidating calibration, changing the window, or reaching expiry must still revoke it. Simulated/rehearsal selections must be rejected. The adapter also checks the returned source, IDs and selection correspondence.

| Action | Required response / behavior |
| --- | --- |
| `claim` | Claim one fresh confirmed target with `read_only: true`. Voice selections and any repeated claim of an existing read-only lease require exact `expected_selection_id`. Return the receipt below. A service that does not acknowledge read-only scope is rejected before any engine read. |
| `validate` | Reject expired/changed selections or ownership mismatch; otherwise return the same receipt with the current remaining lifetime. |
| `observed` | For a voice selection only, acknowledge successful observation with `{"observed":true}`. |
| `release` | Release only this owner's selection; return `{"released":true}`. |

Receipt fields consumed by the adapter:

```json
{
  "selection_id": "fresh-unique-selection-id",
  "pid": 123,
  "window_id": 456,
  "source": "camera",
  "confirmation_method": "thumb",
  "read_only": true,
  "expires_in_seconds": 120
}
```

`confirmation_method` is `thumb`, `double_blink` or `voice`; this tool requires `read_only` to be exactly true for all three methods. Voice requests also require their exact original selection ID and the `observed` acknowledgement; gesture reads do not acknowledge speech. IDs must be positive integers. Additional receipt fields may be returned by the service but are not forwarded as model instructions. HTTP 400/401/403 means the request was refused without acquiring a claim; other failures leave the claim outcome uncertain. Update the local gaze service together with this adapter; older services that ignore the read-only claim flag are refused safely.

## Execution and failure behavior

The model supplies only an optional selection ID. Validated numeric IDs are inserted into fixed SDK code: resolve with `app_for_window`, then `await app.read(view="both")`. The normal App reference registry, native process identity and window-scope checks remain authoritative. A missing, ambiguous, closed or unsupported target fails without choosing another window. The service's raw IDs are host-local correlation evidence, not authorization or a replacement for native lifetime checks.

Only a successful engine read followed by validation can acknowledge a voice observation. A cancelled claim waits for its bounded HTTP response to obtain the ID needed for cleanup. If a reply is lost or release cannot be confirmed, the tool refuses another read. The service must expire the lease within 120 seconds; after that, recreate the toolkit and obtain a new confirmation. Capture is never automatically retried.

The existing general Python `computer_use` tool is unchanged and can perform actions. This helper does not impose a read-only policy on the whole agent or pause a running Python batch. A separate action policy is needed before porting the older per-action gesture guard. Use ordinary host permissions and desktop mode for the rest of the agent.

The helper currently runs normal application discovery before resolving the target. Latency depends on the running applications; this change makes no latency-improvement claim. Unit tests use synthetic receipts and a scripted model endpoint. A real camera user test remains necessary for gaze accuracy, calibration and gesture timing.
