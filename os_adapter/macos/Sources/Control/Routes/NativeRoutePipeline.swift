// Copyright (c) Huawei Technologies Co., Ltd. 2026. All rights reserved.

import AppKit
import ApplicationServices

@MainActor enum NativeRoutePipeline {
    static func select(
        _ command: NativeUnitCommand, op: NativeOperation, resolved: ResolvedNativeTarget, boundary: NativeUnitBoundary,
        receipt: NativeExecutionReceipt, access: NativeControlAccess
    ) throws -> SelectedNativeRoute {
        let context = NativeRouteContext(
            command: command, operation: op, resolved: resolved, boundary: boundary, receipt: receipt, access: access)
        switch resolved.contract.unit {
        case .launch: throw DriverError("installation contract cannot send control input", code: .invalidRequest)
        case .selection:
            throw DriverError("native selection requires the negotiated click pipeline", code: .invalidRequest)
        case .action(let name, let description):
            return try NativeObjectActionPipeline.select(context, name: name, description: description)
        case .menuAction(let name, let description):
            return try BuildNativeMenuRoutePass.run(context, name: name, description: description)
        case .textValue(let valueContract):
            return try BuildNativeTextValueRoutePass.run(context, valueContract: valueContract)
        case .cellSelection(let selection):
            return try NativeCellSelectionPreparation.route(context, selection: selection)
        case .receiverContext(let receiverContract):
            return try BuildNativeReceiverRoutePass.run(context, receiverContract: receiverContract)
        case .pointerFocus(let target): return try BuildNativePointerFocusRoutePass.run(context, target: target)
        case .selectedText(let editContract):
            return try BuildNativeSelectedTextRoutePass.run(context, editContract: editContract)
        case .scroll: return try BuildNativeScrollRoutePass.run(context)
        case .keyboard(let expected, let replacement, let textPolicy):
            return try BuildNativeKeyboardRoutePass.run(
                context, expected: expected, replacement: replacement, textPolicy: textPolicy)
        case .applicationCommand(let command):
            return try NativeApplicationKeyboardUnit.route(context, command: command)
        case .pointer(let expected): return try BuildNativePointerRoutePass.run(context, expected: expected)
        }

    }
}
