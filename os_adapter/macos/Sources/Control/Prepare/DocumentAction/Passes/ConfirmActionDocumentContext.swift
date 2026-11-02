// Copyright (c) Huawei Technologies Co., Ltd. 2026. All rights reserved.

/// QUERY: require observed exact document context before the single AXPress.
@MainActor enum ConfirmActionDocumentContextPass {
    static func run(
        _ unit: any NativeActionDocumentInput, check: () throws -> Void, onWait: (NativePreparationWait) -> Void
    ) async throws {
        for attempt in 0..<12 {
            try check()
            if try unit.focus() == .target {
                onWait(.satisfied)
                return
            }
            if attempt < 11 { try await Task.sleep(for: .milliseconds(25)) }
        }
        onWait(.exhausted)
        throw NativePreparationUnavailable(
            description: "the exact document context remains unconfirmed; AXPress was not sent")
    }
}
