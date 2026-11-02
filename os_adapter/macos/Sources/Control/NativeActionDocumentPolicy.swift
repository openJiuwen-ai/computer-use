// Copyright (c) Huawei Technologies Co., Ltd. 2026. All rights reserved.

import Foundation

/// Keep exact document selection independent from system foreground and from
/// optional process-local activity. Web membership is not an activation requirement.
enum NativeActionDocumentPolicy {
    struct Preparation: Equatable {
        var windowSelectionRequired = false
        var required: Bool { windowSelectionRequired }
    }

    static func preparation(action: String, exactStandardWindow: Bool, otherDocument: Bool) -> Preparation {
        guard action == "AXPress", exactStandardWindow else { return Preparation() }
        return Preparation(windowSelectionRequired: otherDocument)
    }

    @MainActor static func checkDispatch(
        _ document: (any NativeActionDocumentInput)?, required: Bool, retained: NativeActionDocumentContract? = nil
    ) throws {
        guard let document else { return }
        let focus = try retained?.observe() ?? document.focus()
        guard NativeDocumentContext.permitsDispatch(focus, prepared: required || retained?.selectionRequired == true)
        else {
            // A changed receiver is not evidence that background selection
            // was attempted and unavailable. It cannot authorize foreground.
            throw NativeSelectionBasis.Changed()
        }
    }

}
