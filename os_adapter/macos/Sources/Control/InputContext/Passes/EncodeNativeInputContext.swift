import AppKit
import ApplicationServices

/// PURE: encode exactly the sampled input evidence and primitive declarations; no system query.
@MainActor enum EncodeNativeInputContextPass {
    static func run(_ value: NativeSampledInput) -> [String: Any] {
        let plan = value.plan, binding = plan.query.binding, desktop = plan.query.desktop
        let foreground = value.foreground, enabled = value.enabled
        let nativeRole = value.nativeRole, actionsComplete = value.actionsComplete,
            applicationMenu = value.applicationMenu
        let within = plan.within, actuator = plan.actuator, receiver = plan.receiver, basis = plan.basis
        var input = value.input
        input["foregroundSurface"] = foreground ? binding.wire["surface"]! : NSNull()
        var response: [String: Any] = [
            "kind": "inputContext", "binding": binding.wire, "observed": UUID().uuidString,
            "targetLabel": desktop ? "Desktop" : (binding.node == nil ? "working window" : "native element"),
            "identity": "yes", "withinScope": "yes", "enabled": enabled.map { $0 ? "yes" : "no" } ?? "unknown",
            "blockedBy": binding.blocked, "primitives": value.declarations.primitives,
            "nativeRole": nativeRole as Any? ?? NSNull(), "actionsComplete": actionsComplete,
            "applicationMenu": applicationMenu, "within": within, "actuator": actuator.map(\.wire) as Any? ?? NSNull(),
            "input": input
        ]
        if !basis.isEmpty { response["basis"] = basis.map(\.wire) }
        if let receiver { response["receiver"] = receiver.wire }
        return response

    }
}
