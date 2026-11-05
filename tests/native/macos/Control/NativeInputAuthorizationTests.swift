// Copyright (c) Huawei Technologies Co., Ltd. 2026. All rights reserved.

import Foundation

@main struct NativeInputAuthorizationTests {
    @MainActor static func main() async throws {
        precondition(!NativeInputAuthorization.globalInputRequested)
        for allowed in [false, true] {
            try NativeInputAuthorization.$globalInputAllowed.withValue(allowed) {
                do {
                    try NativeInputAuthorization.requireGlobalInput("system pointer")
                    preconditionFailure("object input acquired global authority")
                } catch let error as DriverError {
                    precondition(error.code == .inputUnavailable && error.effect == FailureEffect.none)
                }
            }
        }
        try await NativeInputAuthorization.$globalInputRequested.withValue(true) {
            do {
                try NativeKeyboardDelivery.desktop.requireAdmission()
                preconditionFailure("desktop input admitted without host permission")
            } catch let error as DriverError {
                precondition(error.code == .permissionRequired)
            }
            try await NativeInputAuthorization.$globalInputAllowed.withValue(true) {
                try NativeInputAuthorization.requireGlobalInput("system pointer")
                try await Task { try NativeInputAuthorization.requireGlobalInput("keyboard") }.value
                try NativeInputAuthorization.$globalInputRequested.withValue(false) {
                    do {
                        try NativeInputAuthorization.requireGlobalInput("keyboard")
                        preconditionFailure("object input inherited explicit global input")
                    } catch let error as DriverError { precondition(error.code == .inputUnavailable) }
                }
            }
        }
        precondition(!NativeInputAuthorization.globalInputAllowed && !NativeInputAuthorization.globalInputRequested)
        // Hello parsing accepts booleans only: 1 and "true" are not grants.
        let granted = try boolean(true, label: "globalInputAllowed")
        let denied = try boolean(false, label: "globalInputAllowed")
        precondition(granted && !denied)
        for invalid in [1, "true", NSNull()] as [Any] {
            do {
                _ = try boolean(invalid, label: "globalInputAllowed")
                preconditionFailure("non-boolean authorization accepted")
            } catch let error as DriverError { precondition(error.code == .invalidRequest) }
        }
        try lateDenialContinuation()
        print("PASS explicit global permission, strict grant parsing, nested scope isolation, pre-input denial")
    }

    @MainActor static func lateDenialContinuation() throws {
        let denied = DriverError("scoped keyboard requires global input", code: .permissionRequired)
        for kind in ["prepare_receiver", "key_sequence"] {
            for state in ["clean", "prepared", "unknown", "partial", "cleanup", "priorInput"] {
                let op = NativeOperation(
                    timeoutMs: 1000, writable: true,
                    intent: try NativeIntent(["kind": "insert", "parameters": ["text": "x"]], mode: "write"),
                    target: [:], release: {}, cancelActive: {})
                defer { _ = op.finish() }
                let receipt = NativeExecutionReceipt(target: [:], onEffect: {})
                if state == "prepared" { receipt.recordPreparation("native element focus") }
                if state == "unknown" { receipt.dispatch = "unknown" }
                if state == "partial" { receipt.recordAccepted() }
                if state == "cleanup" { receipt.recordCleanupUnknown() }
                if state == "priorInput" { op.recordInputDispatch() }
                BuildNativeFailureReceiptPass.run(
                    denied, kind: kind, foreground: false, id: "route", op: op, receipt: receipt)
                precondition(receipt.errorCode == .permissionRequired)
                precondition(receipt.retryReason == (state == "clean" ? "routeUnavailable" : nil), state)
            }
        }
    }
}
