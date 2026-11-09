// Copyright (c) Huawei Technologies Co., Ltd. 2026. All rights reserved.

import AppKit
import ApplicationServices

/// PURE: encode exactly the sampled input evidence and primitive declarations; no system query.
@MainActor enum EncodeNativeInputContextPass {
    static func run(_ value: NativeSampledInput) -> [String: Any] {
        let plan = value.plan, binding = plan.query.binding
        let foreground = value.foreground, enabled = value.enabled
        let nativeRole = value.nativeRole, actionsComplete = value.actionsComplete,
            applicationMenu = value.applicationMenu
        let within = plan.within, actuator = plan.actuator, receiver = plan.receiver, basis = plan.basis
        let primitives = value.declarations.primitives
        var input = value.input
        input["foregroundSurface"] = foreground ? binding.wire["surface"]! : NSNull()
        var response: [String: Any] = [
            "kind": "inputContext", "binding": binding.wire, "observed": UUID().uuidString,
            "targetLabel": binding.node == nil ? "working window" : "native element",
            "identity": "yes", "withinScope": "yes", "enabled": enabled.map { $0 ? "yes" : "no" } ?? "unknown",
            "blockedBy": binding.blocked, "primitives": primitives,
            "nativeRole": nativeRole as Any? ?? NSNull(), "actionsComplete": actionsComplete,
            "applicationMenu": applicationMenu, "within": within, "actuator": actuator.map(\.wire) as Any? ?? NSNull(),
            "input": input
        ]
        if !basis.isEmpty { response["basis"] = basis.map(\.wire) }
        if let receiver { response["receiver"] = receiver.wire }
        // The shared reason describes the sole scoped typing refusal. A different
        // condition or another offer keeps its own diagnosis.
        let originalFocusedTarget = binding.node.map {
            $0 == plan.operation.target["node"] as? String && $0 == input["focusedNode"] as? String
        } ?? true
        if let reason = value.webTypingRefusal, originalFocusedTarget, binding.wire["rootWindow"] is String,
            binding.wire["capture"] is NSNull, binding.blocked.isEmpty, actuator == nil, receiver == nil,
            input["keyboardSurface"] as? String == plan.query.scope,
            primitives.count == 1, let offer = primitives.first, offer["unit"] as? String == "key_sequence",
            offer["supported"] as? String == "no", offer["readiness"] as? String == "unknown",
            offer["pending"] as? [String] == ["receiver"], offer["backgroundPreparation"] as? String == "no"
        {
            response["unavailableReason"] = reason
        }
        return response

    }
}
