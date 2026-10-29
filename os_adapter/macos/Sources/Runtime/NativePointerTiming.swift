// Copyright (c) Huawei Technologies Co., Ltd. 2026. All rights reserved.

import Foundation

/// Passive timings of existing pointer queries and posts. This separate opt-in
/// never reads AX, samples the desktop, changes a timeout or retries input.
@MainActor enum NativePointerTiming {
    static let enabled = ProcessInfo.processInfo.environment["COMPUTER_USE_DIAGNOSE_POINTER_TIMING"] == "1"

    static func record(
        _ phase: String, enabled: Bool = enabled,
        metadata: () -> [String: Any] = { [:] }, emit: ([String: Any]) -> Void = { diagnose($0) }
    ) {
        guard enabled else { return }
        var fields = metadata()
        fields["kind"] = "pointer_timing"
        fields["phase"] = phase
        emit(fields)
    }

    /// Execute the original body exactly once, including its original error.
    /// Disabled diagnostics do not evaluate metadata, clock or output callbacks.
    static func measure<Value>(
        _ phase: String, enabled: Bool = enabled, metadata: () -> [String: Any] = { [:] },
        clock: () -> TimeInterval = { ProcessInfo.processInfo.systemUptime },
        emit: ([String: Any]) -> Void = { diagnose($0) }, body: () throws -> Value
    ) rethrows -> Value {
        guard enabled else { return try body() }
        let began = clock()
        var outcome = "threw"
        defer {
            var fields = metadata()
            fields["startedUptime"] = began
            fields["durationMs"] = max(0, clock() - began) * 1000
            fields["outcome"] = outcome
            record(phase, enabled: true, metadata: { fields }, emit: emit)
        }
        let value = try body()
        outcome = "returned"
        return value
    }
}
