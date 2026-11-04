// Copyright (c) Huawei Technologies Co., Ltd. 2026. All rights reserved.

import Foundation
@preconcurrency import ScreenCaptureKit

@main enum NativeWindowCaptureRetryTests {
    @MainActor static func main() async throws {
        for code in [SCStreamError.Code.userDeclined, .missingEntitlements, .userStopped, .invalidParameter,
                     .failedToStart, .internalError] {
            let error = NSError(domain: SCStreamErrorDomain, code: code.rawValue)
            precondition(!WindowCaptureRetry.permits(error, originalWindowKnown: true))
        }
        let pending = WindowCapturePending(description: "same live window; SC catalogue geometry is behind")
        precondition(!WindowCaptureRetry.permits(pending, originalWindowKnown: false))
        for code in [SCStreamError.Code.noWindowList, .noCaptureSource, .failedApplicationConnectionInterrupted] {
            precondition(WindowCaptureRetry.permits(
                NSError(domain: SCStreamErrorDomain, code: code.rawValue), originalWindowKnown: true))
        }
        var attempts = 0, scopeChecks = 0, enumerationTime = 0.0
        let result = try await WindowCaptureRetry.run(
            maximumAttempts: Int.max,
            check: { if enumerationTime >= 0.35 { throw ObservationBudgetExceeded() } },
            validateOriginalScope: { scopeChecks += 1 },
            permits: { WindowCaptureRetry.permits($0, originalWindowKnown: true) }
        ) {
            attempts += 1
            enumerationTime += 0.04
            if attempts < 7 { throw pending }
            return "pixels of original window"
        }
        precondition(result == "pixels of original window" && attempts == 7 && scopeChecks == 6,
                     "actual enumeration progress uses the original budget, without four-attempt or sleep barriers")

        attempts = 0
        do {
            _ = try await WindowCaptureRetry.run(
                maximumAttempts: Int.max, check: {},
                validateOriginalScope: { throw DriverError("original window ended", code: .staleTarget) },
                permits: { WindowCaptureRetry.permits($0, originalWindowKnown: true) }
            ) { attempts += 1; throw pending }
            preconditionFailure("a lost original scope must not start another capture")
        } catch let failure as DriverError { precondition(failure.code == .staleTarget && attempts == 1) }

        for error: Error in [CancellationError(), ObservationBudgetExceeded(),
                             DriverError("permission denied", code: .desktopUnavailable)] {
            attempts = 0
            do {
                _ = try await WindowCaptureRetry.run(
                    maximumAttempts: Int.max, check: {}, validateOriginalScope: {},
                    permits: { WindowCaptureRetry.permits($0, originalWindowKnown: true) }
                ) { attempts += 1; throw error }
                preconditionFailure("permission/budget/cancellation is terminal")
            } catch { precondition(attempts == 1) }
        }
        print("PASS fixed-scope capture recovery uses real enumeration work and stops on terminal causes")
    }
}
