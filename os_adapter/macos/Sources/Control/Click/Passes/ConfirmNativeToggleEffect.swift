import Foundation

/// QUERY: confirm only the original toggle's stable local value after AX error.
@MainActor enum ConfirmNativeToggleEffectPass {
    static func run(_ observation: NativeTogglePress.Unconfirmed, boundary: NativeUnitBoundary) async throws
        -> NativeElementControl.ActionResult?
    {
        guard NativeTogglePress.mayConfirm(observation.status),
            NativeTogglePress.canObserve(action: "AXPress", role: observation.role, before: observation.before)
        else { return nil }
        let deadline = ProcessInfo.processInfo.systemUptime + 0.5
        while true {
            try boundary.checkConditions()
            if let after = try observation.read(),
                NativeTogglePress.shows(role: observation.role, before: observation.before, after: after)
            {
                try await Task.sleep(for: .milliseconds(250))
                try boundary.checkConditions()
                let stable = try observation.read() == after
                try boundary.checkConditions()
                if stable {
                    return .init(
                        verified: true,
                        detail: """
                            Original \(observation.role) AXValue changed from \(observation.before) to \
                            \(after) and remained stable; AXPress returned AX error \
                            \(observation.status.rawValue). Application behavior is unconfirmed.
                            """)
                }
            }
            if ProcessInfo.processInfo.systemUptime >= deadline { return nil }
            try await Task.sleep(for: .milliseconds(25))
        }
    }
}
