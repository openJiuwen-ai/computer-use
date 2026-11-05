// Copyright (c) Huawei Technologies Co., Ltd. 2026. All rights reserved.

import AppKit
import ApplicationServices

/// Shared session state matters only to mechanisms that consume it. Addressed
/// AX actions/attributes, window events and explicit global input do not wait
/// for a physically idle desktop. Only menu-tracking preparation uses this gate.
@MainActor enum NativePhysicalInput {
    enum Dependency: String { case menuTracking }
    struct State {
        let modifiers: CGEventFlags
        let buttons: [CGMouseButton]

        func conflicts(allowing button: CGMouseButton? = nil) -> [String] {
            let names: [(CGEventFlags, String)] = [
                (.maskCommand, "command"), (.maskControl, "control"), (.maskAlternate, "option"), (.maskShift, "shift")
            ]
            let modifiers = names.compactMap { self.modifiers.contains($0.0) ? "modifier.\($0.1)" : nil }
            return modifiers + buttons.filter { $0 != button }.map { "mouse.button.\($0.rawValue)" }
        }
    }

    static func sample() -> State {
        .init(
            modifiers: CGEventSource.flagsState(.combinedSessionState),
            buttons: [.left, .right, .center].filter { CGEventSource.buttonState(.combinedSessionState, button: $0) })
    }

    private static func unavailable(_ dependency: Dependency, _ conflicts: [String]) -> DriverError {
        diagnose(["kind": "held_input_conflict", "mechanism": dependency.rawValue, "conflicts": conflicts])
        return DriverError(
            "\(dependency.rawValue) conflicts with held input: \(conflicts.joined(separator: ", "))",
            code: .inputUnavailable, next: "continue after the current physical gesture finishes")
    }

    /// Immediate event barrier. A release of our own already-held button is
    /// independent cleanup and never enters this admission check.
    static func validate(
        _ dependency: Dependency?, allowing button: CGMouseButton? = nil,
        sample: @MainActor () -> State = NativePhysicalInput.sample
    ) throws {
        guard let dependency else { return }
        let conflicts = sample().conflicts(allowing: button)
        guard conflicts.isEmpty else { throw unavailable(dependency, conflicts) }
    }

    /// Only an observed conflict waits; no preparation/input is replayed and
    /// cancellation/the original request deadline still bound every sample.
    static func wait(
        _ dependency: Dependency?, check: @MainActor () throws -> Void,
        sample: @MainActor () -> State = NativePhysicalInput.sample,
        clock: @MainActor () -> TimeInterval = { ProcessInfo.processInfo.systemUptime },
        pause: @MainActor (TimeInterval) async throws -> Void = { try await Task.sleep(for: .seconds($0)) }
    ) async throws {
        guard let dependency else { return }
        var conflicts = sample().conflicts()
        guard !conflicts.isEmpty else { return }
        try check()
        let started = clock(), until = started + 0.08
        diagnose(["kind": "held_input_wait", "mechanism": dependency.rawValue, "conflicts": conflicts])
        while !conflicts.isEmpty {
            try RequestDiagnostics.checkCancellation()
            let remaining = until - clock()
            guard remaining > 0 else { throw unavailable(dependency, conflicts) }
            try await pause(min(0.01, remaining))
            try RequestDiagnostics.checkCancellation()
            conflicts = sample().conflicts()
        }
        // The receiver can evolve during a physical gesture. Refresh its
        // boundary once after waiting, without AX queries in every poll.
        try check()
        diagnose([
            "kind": "held_input_released", "mechanism": dependency.rawValue,
            "waitMs": max(0, (clock() - started) * 1000)
        ])
    }
}
