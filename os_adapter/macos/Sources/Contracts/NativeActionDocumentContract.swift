// Copyright (c) Huawei Technologies Co., Ltd. 2026. All rights reserved.

/// Operation-owned original document evidence. Focus can change during native
/// preparation, but a known competing document cannot become an absent guard.
@MainActor final class NativeActionDocumentContract {
    let unit: any NativeActionDocumentInput
    private(set) var selectionRequired = false

    init(_ unit: any NativeActionDocumentInput) { self.unit = unit }

    @discardableResult func observe() throws -> NativeDocumentContext.Focus {
        let focus = try unit.focus()
        if focus == .other { selectionRequired = true }
        return focus
    }
}
