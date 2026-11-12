// Copyright (c) Huawei Technologies Co., Ltd. 2026. All rights reserved.

import AppKit
import CoreGraphics

/// Explicit Computer input is posted to the live desktop, like a human input
/// device. No object, capture, AX receiver, window or foreground contract is
/// consulted. The operation owns only its event sequence and paired releases.
@MainActor enum NativeGlobalInputUnit {
    static func run(
        _ operation: NativeOperation, receipt: NativeExecutionReceipt, onEffect: @escaping () -> Void = {}
    ) async throws {
        let check: @MainActor () throws -> Void = {
            try operation.check(write: true)
            try Input.requireDesktopInput()
        }
        let dispatched: @MainActor () throws -> Void = {
            try check()
            operation.recordInputDispatch()
            receipt.dispatch = "unknown"
            onEffect()
        }
        let posted: @MainActor () -> Void = { receipt.recordAccepted() }
        func cleanupUnknown() { receipt.recordCleanupUnknown() }
        try check()
        switch operation.request.kind {
        case "click", "scroll", "drag":
            let request = try NativePointerIntent(
                unit: "pointer_sequence", kind: operation.request.kind, parameters: operation.request.parameters,
                foreground: false, screen: true)
            guard let from = request.start else {
                throw DriverError("global pointer requires screen coordinates", code: .invalidRequest)
            }
            // Pages are measured in display points. This supplies event distance
            // only; it neither validates nor redirects the supplied coordinates.
            var display = CGMainDisplayID(), count: UInt32 = 0
            _ = CGGetDisplaysWithPoint(from, 1, &display, &count)
            let bounds = CGDisplayBounds(display)
            let viewport = CGSize(width: max(1, bounds.width), height: max(1, bounds.height))
            let sequence = try NativePointerEvents(
                request: request, from: from, to: request.end, viewport: viewport, source: Input.source())
            try await sequence.deliver(
                check: { _, _ in try check() },
                post: { event, point in
                    AgentPointer.current?.move(
                        to: point, window: nil,
                        pulse: request.kind == .click
                            && [.leftMouseDown, .rightMouseDown, .otherMouseDown].contains(event.type),
                        count: request.count, dragging: request.kind == .drag ? event.type != .leftMouseUp : nil)
                    event.location = point
                    let effect = NativeInputActivity.begin(
                        device: .mouse, transport: .globalHID, mechanism: "CGEvent.post.cghidEventTap", event: event)
                    event.post(tap: .cghidEventTap)
                    effect?.returned()
                }, canRelease: { true }, skipped: cleanupUnknown, onDispatch: dispatched, onPosted: posted)
        case "key", "insert", "paste":
            // Empty text has no physical effects and requires no event source.
            if ["insert", "paste"].contains(operation.request.kind),
                operation.request.parameters["text"] as? String == "" {
                _ = try object(operation.request.parameters, keys: ["text"])
                receipt.detail = "Empty text completed; no input events were sent."
                receipt.dispatch = "dispatched"
                receipt.confirmation = "confirmed"
                return
            }
            let kind = operation.request.kind
            let request = try NativeKeyboardIntent(
                unit: kind == "paste" ? "clipboard_paste" : "key_sequence", kind: kind,
                parameters: operation.request.parameters, foreground: false)
            if request.kind == .key {
                try await Input.preparedKey(
                    request.value!, delivery: .desktop, validate: check, onDispatch: dispatched,
                    onPosted: posted, onCleanupUnknown: cleanupUnknown)
            } else if request.kind == .insert {
                try await Input.preparedText(
                    request.value!, delivery: .desktop, applicationPID: nil, validate: check,
                    onDispatch: dispatched, onPosted: posted, onCleanupUnknown: cleanupUnknown)
            } else {
                let transaction = try PasteboardTransaction()
                var sent = false
                do {
                    try check()
                    receipt.recordPreparation("temporary clipboard payload")
                    try transaction.install(request.value!)
                    try await Input.preparedKey(
                        "super+v", delivery: .desktop,
                        validate: { try check(); try transaction.validateOwnership() },
                        onDispatch: dispatched, onPosted: { sent = true; posted() },
                        onCleanupUnknown: cleanupUnknown)
                } catch {
                    receipt.recordClipboardRestore(await transaction.finish(afterInput: sent))
                    throw error
                }
                receipt.recordClipboardRestore(await transaction.finish(afterInput: sent))
            }
        default: throw DriverError("unsupported global input intent", code: .invalidRequest)
        }
        receipt.dispatch = "dispatched"
    }
}
