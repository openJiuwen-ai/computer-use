// Copyright (c) Huawei Technologies Co., Ltd. 2026. All rights reserved.

import Foundation

/// A bounded read-only wait for an existing menu's natural presentation end.
/// Still-open and unknown results are observations, never input failures.
@MainActor enum NativeMenuPresentationSettlement {
    struct Result {
        let samples: Int
        let elapsed: TimeInterval
        let stillPresented: Bool
    }

    static func wait(
        initiallyPresented: Bool, deadline: TimeInterval,
        check: () throws -> Void, presented: () throws -> Bool,
        now: () -> TimeInterval = { ProcessInfo.processInfo.systemUptime },
        pause: (TimeInterval) async throws -> Void = { try await Task.sleep(for: .seconds($0)) }
    ) async throws -> Result {
        let started = now(), until = min(deadline, started + 0.5)
        var pending = initiallyPresented, samples = 0
        while pending, now() < until {
            try check()
            try await pause(min(0.05, max(0, until - now())))
            try check()
            pending = try presented()
            samples += 1
        }
        return Result(samples: samples, elapsed: now() - started, stillPresented: pending)
    }
}
