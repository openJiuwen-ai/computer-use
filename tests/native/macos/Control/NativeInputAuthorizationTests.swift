// Copyright (c) Huawei Technologies Co., Ltd. 2026. All rights reserved.

import Foundation

@main struct NativeInputAuthorizationTests {
    @MainActor static func main() async throws {
        // A legacy environment variable cannot grant global input.
        setenv("COMPUTER_USE_DESKTOP", "exclusive", 1)
        precondition(!NativeInputAuthorization.globalInputAllowed)
        for route in ["desktop keyboard", "system pointer", "scoped foreground keyboard"] {
            do {
                try NativeInputAuthorization.requireGlobalInput(route)
                preconditionFailure("unauthorized \(route)")
            } catch let error as DriverError {
                precondition(error.code == .permissionRequired && error.effect == FailureEffect.none)
                precondition(error.description.contains(route))
            }
        }
        // Desktop dispatch checks authority before OS/foreground checks.
        do {
            try NativeKeyboardDelivery.desktop.requireAdmission()
            preconditionFailure("desktop input admitted without permission")
        } catch let error as DriverError {
            precondition(error.code == .permissionRequired && error.effect == FailureEffect.none)
        }
        try await NativeInputAuthorization.$globalInputAllowed.withValue(true) {
            try NativeInputAuthorization.requireGlobalInput("system pointer")
            try await Task { try NativeInputAuthorization.requireGlobalInput("scoped foreground keyboard") }.value
            try NativeInputAuthorization.$globalInputAllowed.withValue(false) {
                do {
                    try NativeInputAuthorization.requireGlobalInput("desktop keyboard")
                    preconditionFailure("nested denied scope inherited permission")
                } catch let error as DriverError { precondition(error.code == .permissionRequired) }
            }
            precondition(NativeInputAuthorization.globalInputAllowed)
        }
        precondition(!NativeInputAuthorization.globalInputAllowed)
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
