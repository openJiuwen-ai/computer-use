// Copyright (c) Huawei Technologies Co., Ltd. 2026. All rights reserved.

import ApplicationServices

/// Retain the original keyboard owner for one native primitive. Exact focus
/// can be supplemented by a typed cell-editor proof or that owner's explicitly
/// related popup active item. The registry owns cross-call editor association;
/// this primitive's continuity never transfers the semantic owner.
@MainActor struct NativeReceiverContinuity {
    struct Lost: ClassifiedFailure, CustomStringConvertible {
        let description: String
        let code: FailureCode
    }

    private let focus: AXUIElement?
    private let window: AXUIElement

    init?(element: AXUIElement?, window: AXUIElement, state: NativeReceiverSnapshot) {
        guard element == nil || CFEqual(element!, window) else { return nil }
        // Some providers expose only the window as AXFocusedUIElement. That
        // proves no control identity beyond the unit's existing window guard;
        // the first character may legitimately instantiate an editor.
        focus = state.focus.flatMap { CFEqual($0, window) ? nil : $0 }
        self.window = state.window ?? window
    }

    func validate(
        _ state: NativeReceiverSnapshot, ownedEditor: NativeCellEditorEvidence.OwnedEditor<AXUIElement>? = nil
    ) throws {
        // An initially unavailable AX receiver preserves the existing visual
        // window route. Never adopt a new object midway through this primitive.
        guard let focus else { return }
        guard let current = state.focus else {
            throw Lost(
                description: "the previously observed keyboard receiver is unavailable; remaining input was not sent",
                code: .inputUnavailable)
        }
        if CFEqual(current, focus) { return }
        if let ownedEditor, CFEqual(ownedEditor.owner, focus), CFEqual(ownedEditor.editor, current),
            CFEqual(ownedEditor.window, window)
        {
            return
        }
        guard try NativeReceiverRelation.ownsVirtualFocus(editor: focus, focused: current, window: window) else {
            // Keep the original editor even if its explicitly related popup
            // reports an active item. Unrelated siblings and new field editors
            // still cannot take over this input unit.
            throw Lost(
                description: "the keyboard receiver changed during this input; remaining input was not sent",
                code: .staleTarget)
        }
    }
}
