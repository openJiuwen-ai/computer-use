// Copyright (c) Huawei Technologies Co., Ltd. 2026. All rights reserved.

import Foundation
@preconcurrency import ScreenCaptureKit

/// A fixed window can outlive ScreenCaptureKit's catalogue catching up with
/// a modal/layout transition. Other errors are not automatically retries.
struct WindowCapturePending: ClassifiedFailure, CustomStringConvertible {
    let description: String
    var code: FailureCode { .observationIncomplete }
}

@MainActor enum WindowCaptureRetry {
    static func permits(_ error: Error, originalWindowKnown: Bool) -> Bool {
        guard originalWindowKnown else { return false }
        if error is WindowCapturePending { return true }
        let native = error as NSError
        guard native.domain == SCStreamErrorDomain else { return false }
        return [
            SCStreamError.Code.noWindowList.rawValue, SCStreamError.Code.noCaptureSource.rawValue,
            SCStreamError.Code.failedApplicationConnectionInterrupted.rawValue
        ].contains(native.code)
    }

    /// Re-enumeration is the real asynchronous wait. The original scope and
    /// deadline remain authoritative; there is no new budget or fixed delay.
    static func run<Value>(
        maximumAttempts: Int, check: () throws -> Void, validateOriginalScope: () throws -> Void,
        permits: (Error) -> Bool, onRetry: (Error, Int) -> Void = { _, _ in },
        attempt: () async throws -> Value
    ) async throws -> Value {
        var attempts = 0
        while true {
            try check()
            attempts += 1
            do { return try await attempt() } catch {
                try check()
                guard attempts < maximumAttempts, permits(error) else { throw error }
                try validateOriginalScope()
                try check()
                onRetry(error, attempts)
            }
        }
    }
}
