// Copyright (c) Huawei Technologies Co., Ltd. 2026. All rights reserved.

import AppKit
import ApplicationServices

/// QUERY: fixed references and dependencies -> borrowed query scope. No input or route choice.
@MainActor enum ResolveNativeInputQueryScopePass {
    static func run(
        p: [String: Any], op: NativeOperation, within: [String], actuator: NativeInspectQuery.Actuator?,
        receiver: NativeInspectQuery.Receiver?, basis: [NativeInspectQuery.Dependency], access: NativeQueryAccess
    ) throws -> NativeInputQueryScope {
        try access.sharing.checkAvailable()
        let windowKeyboardScope: NativeInputScope?
        if op.intent == "key", within.isEmpty, actuator == nil, receiver == nil, basis.isEmpty,
            let target = p["target"] as? [String: Any], target["rootWindow"] is String,
            target["node"] is NSNull, target["capture"] is NSNull
        {
            windowKeyboardScope = try access.scopes.windowKeyboardQueryScope(target)
        } else {
            windowKeyboardScope = nil
        }
        let binding = try windowKeyboardScope.map { (wire: $0.wire, blocked: $0.blocked, node: nil as String?) }
            ?? access.scopes.inputBinding(p["target"])
        guard !within.contains(binding.node ?? "") else {
            throw DriverError("within must contain proper ancestors", code: .invalidRequest)
        }
        guard binding.node != nil || within.isEmpty else {
            throw DriverError("window input cannot carry element ancestors", code: .invalidRequest)
        }
        guard
            actuator == nil
                || (actuator?.relation == "containedEditor"
                    ? NativeKeyboardIntent.Kind(rawValue: op.intent) != nil
                    : actuator?.relation == "controlPart" ? op.intent == "click" : op.intent == "scroll")
        else { throw DriverError("this input intent does not accept the selected actuator", code: .invalidRequest) }
        try access.scopes.checkContainment(binding.wire, within: within, op: op)
        try access.scopes.checkActuator(binding.wire, actuator: actuator, op: op)
        guard (actuator == nil && receiver == nil) || !basis.isEmpty else {
            throw DriverError("a selected actuator requires its semantic selection dependencies", code: .invalidRequest)
        }
        guard
            receiver == nil
                || (binding.node != nil && !(binding.wire["capture"] is String)
                    && NativeKeyboardIntent.Kind(rawValue: op.intent) != nil
                    && (actuator == nil || actuator?.relation == "containedEditor"))
        else {
            throw DriverError("receiver relation requires a keyboard or text receiver intent", code: .invalidRequest)
        }
        if let receiver { _ = try access.units.receiverRelation(binding.wire, actuator: actuator, receiver: receiver) }
        let selection = access.contracts.selectionBasis(op.token)
        let scope = try string(binding.wire["surface"], label: "surface")
        try selection.validate(basis, scope: scope)
        return NativeInputQueryScope(
            binding: binding, selection: selection, scope: scope, windowKeyboardScope: windowKeyboardScope)

    }
}
