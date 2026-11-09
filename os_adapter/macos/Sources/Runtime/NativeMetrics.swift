// Copyright (c) Huawei Technologies Co., Ltd. 2026. All rights reserved.

import Foundation

/// Request-scoped diagnostics, not observations or action receipts. Timings are
/// inclusive and may overlap; summing phases does not give total request time.
@MainActor final class NativeMetrics {
    static let spansEnabled = ProcessInfo.processInfo.environment["COMPUTER_USE_DIAGNOSE_NATIVE_WORK"] == "1"

    enum Phase: String {
        case windowPreparation, keyboardPreparation, focusPreparation, editorResolution
        case targetValidation, documentValidation, recipientValidation, focusValidation, pointerValidation
        case preparedInputValidation, pointerFocusValidation, windowMembership, windowValidation, keyboardIntentCapture
        case observationWait, accessibilityRead, windowCapture, overviewCapture, capabilityEvaluation
        case captureEnumeration, captureSharing, capturePixels, capturePNGEncoding, captureIdentityValidation
        case sharingStreamStart, sharingStreamStop
        case appObserverRegistration, appObserverWait
        case clickPlan, clickPreparation, clickTargetValidation, clickViewportSampling, clickDispatch, clickRelease
        case readPublication
        case captureJoin
        case capturePermissionPreflight, captureStartupScope, captureTargetResolution, captureDocumentIdentity
        case captureWindowHint, captureRetryScope
        case readCatalogue, readAttributes, readIdentityFallback, readSubtreePrefetch
        case inputQueryResolution, inputRouteSampling, inputContractRegistration, inputContextEncoding
    }
    enum Counter: String {
        case alreadyForeground, cachedWindowPreparation, nativeWindowSelection, activationNotification
        case observationRetry
        case sharingStreamReuse
        case editorForwardOnlyLink, editorHandoff, collectionEditorHandoff, capabilityQuery
        case readPrefetchedRow, readBatchedSingleRelations
    }
    struct Interval {
        let owner: NativeMetrics
        let phase: Phase
        let started: TimeInterval
        @MainActor func end() { owner.record(phase, since: started) }
    }
    private let clock: () -> TimeInterval
    private let started: TimeInterval
    private let retainsSpans: Bool
    private let spanLimit: Int
    private var closed = false
    private var phases = [Phase: (count: Int, durationMs: Double)]()
    private var counters = [Counter: Int]()
    private var spans = [[String: Any]]()
    private var droppedSpans = 0

    init(
        clock: @escaping () -> TimeInterval = { ProcessInfo.processInfo.systemUptime },
        retainSpans: Bool = spansEnabled, spanLimit: Int = 2048
    ) {
        self.clock = clock
        self.started = clock()
        self.retainsSpans = retainSpans
        self.spanLimit = max(0, spanLimit)
    }
    func start(_ phase: Phase) -> Interval? { closed ? nil : Interval(owner: self, phase: phase, started: clock()) }

    /// Query-only diagnostic phases do not add clocks or aggregate fields when
    /// disabled. The original synchronous body runs exactly once, with its
    /// original return value or error; an ended span does not imply success.
    static func measureDiagnostic<Value>(_ phase: Phase, body: () throws -> Value) rethrows -> Value {
        guard let metrics = RequestDiagnostics.metrics, metrics.retainsSpans else { return try body() }
        let interval = metrics.start(phase)
        defer { interval?.end() }
        return try body()
    }

    func count(_ counter: Counter) { if !closed { counters[counter, default: 0] += 1 } }
    private func record(_ phase: Phase, since start: TimeInterval) {
        guard !closed else { return }
        let ended = clock()
        var value = phases[phase] ?? (0, 0)
        value.count += 1
        value.durationMs += max(0, ended - start) * 1000
        phases[phase] = value
        if retainsSpans {
            if spans.count < spanLimit {
                spans.append(["phase": phase.rawValue, "startedUptime": start, "endedUptime": ended])
            } else { droppedSpans += 1 }
        }
    }
    func finish(method: String, status: String) -> [String: Any]? {
        guard !closed else { return nil }
        closed = true
        var result: [String: Any] = [
            "kind": "native_metrics", "version": 1, "method": method, "status": status,
            "durationMs": max(0, clock() - started) * 1000,
            "phases": Dictionary(
                uniqueKeysWithValues: phases.map {
                    ($0.key.rawValue, ["count": $0.value.count, "durationMs": $0.value.durationMs] as [String: Any])
                }), "counters": Dictionary(uniqueKeysWithValues: counters.map { ($0.key.rawValue, $0.value) })
        ]
        if retainsSpans {
            result["spans"] = spans
            result["spanClock"] = "systemUptime"
            result["spanLimit"] = spanLimit
            result["droppedSpans"] = droppedSpans
        }
        return result
    }
}
