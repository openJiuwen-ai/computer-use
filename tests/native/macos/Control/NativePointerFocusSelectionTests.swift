// Copyright (c) Huawei Technologies Co., Ltd. 2026. All rights reserved.

import AppKit
import ApplicationServices

@main enum NativePointerFocusSelectionTests {
    @MainActor final class Editor {
        var range = CFRange(location: 5, length: 2)
        var clickedRange = CFRange(location: 0, length: 0)
        var writable = false
        var selectionAvailable = true
        var selectionAvailableAfterClick = true
        var receives = false
        var alreadyReady = false
        var clickEstablishesReceiver = true
        var boundaryValid = true
        var invalidateBoundaryAfterClick = false
        var restoreStatus = AXError.success
        var readyReads = 0
        var receiverReads = 0
        var selectionReads = 0
        var selectionWritableReads = 0
        var restores = 0
        var waits = 0
        var boundaryChecks = 0
        var completedPreparations = 0
        var actions = [String]()

        func access() -> NativePointerFocusUnit.PreparationAccess {
            .init(
                ready: {
                    self.readyReads += 1
                    return self.alreadyReady
                },
                selection: {
                    self.selectionReads += 1
                    return self.selectionAvailable ? self.range : nil
                },
                preparePointer: { self.actions.append("prepare") },
                clickPointer: {
                    self.actions.append("click")
                    self.range = self.clickedRange
                    self.selectionAvailable = self.selectionAvailableAfterClick
                    self.receives = self.clickEstablishesReceiver
                    if self.invalidateBoundaryAfterClick { self.boundaryValid = false }
                },
                receives: {
                    self.receiverReads += 1
                    return self.receives
                },
                selectionWritable: {
                    self.selectionWritableReads += 1
                    return self.writable
                },
                restoreSelection: { range in
                    precondition(self.boundaryValid, "restore must never cross a failed target boundary")
                    self.restores += 1
                    if self.restoreStatus == .success { self.range = range }
                    return self.restoreStatus
                })
        }

        func prepare(_ intent: NativeKeyboardIntent.Kind) async throws {
            try await NativePointerFocusUnit.prepare(
                intent: intent, access: access(),
                check: {
                    self.boundaryChecks += 1
                    guard self.boundaryValid else {
                        throw DriverError("original editor changed", code: .staleTarget)
                    }
                }, wait: { self.waits += 1 })
            completedPreparations += 1
        }
    }

    @MainActor static func checkFocusUsesReceiverOnly() async throws {
        let selectionCases = [
            (name: "read-only", readable: true, writable: false),
            (name: "unknown", readable: false, writable: false),
            (name: "writable", readable: true, writable: true),
        ]
        for selection in selectionCases {
            let focused = Editor()
            focused.selectionAvailable = selection.readable
            focused.selectionAvailableAfterClick = selection.readable
            focused.writable = selection.writable
            let original = focused.range
            try await focused.prepare(.focus)
            precondition(focused.completedPreparations == 1 && focused.receives,
                         "focus must succeed when its pointer preparation establishes the original receiver: \(selection.name)")
            precondition(focused.readyReads == 1 && focused.receiverReads == 1 && focused.waits == 0
                         && focused.actions == ["prepare", "click"],
                         "focus must exercise the actual cold receiver sequence, not bypass preparation: \(selection.name)")
            precondition(focused.range.location != original.location || focused.range.length != original.length,
                         "the preparation click must really move the old caret in this regression")
            precondition(focused.selectionReads == 0 && focused.selectionWritableReads == 0 && focused.restores == 0,
                         "focus establishes a receiver without reading, requiring or restoring the old selection: \(selection.name)")

            let wrongReceiver = Editor()
            wrongReceiver.selectionAvailable = selection.readable
            wrongReceiver.selectionAvailableAfterClick = selection.readable
            wrongReceiver.writable = selection.writable
            wrongReceiver.clickEstablishesReceiver = false
            do {
                try await wrongReceiver.prepare(.focus)
                preconditionFailure("a pointer click cannot verify focus on another receiver: \(selection.name)")
            } catch is NativePreparationUnavailable {}
            precondition(wrongReceiver.completedPreparations == 0 && !wrongReceiver.receives
                         && wrongReceiver.receiverReads == 12 && wrongReceiver.waits == 11
                         && wrongReceiver.actions == ["prepare", "click"],
                         "focus must exhaust only its original receiver observation without replaying the click")
            precondition(wrongReceiver.selectionReads == 0 && wrongReceiver.selectionWritableReads == 0
                         && wrongReceiver.restores == 0,
                         "a missing focus receiver must not borrow old-caret evidence or authorize a restoration")
        }

        let ready = Editor()
        ready.alreadyReady = true
        ready.receives = true
        try await ready.prepare(.focus)
        precondition(ready.completedPreparations == 1 && ready.readyReads == 1 && ready.actions.isEmpty
                     && ready.receiverReads == 0 && ready.waits == 0)
        precondition(ready.selectionReads == 0 && ready.selectionWritableReads == 0 && ready.restores == 0,
                     "an already confirmed focus receiver requires no caret query or preparation click")

        let changed = Editor()
        changed.invalidateBoundaryAfterClick = true
        do {
            try await changed.prepare(.focus)
            preconditionFailure("a stale original target cannot inherit focus from a preparation click")
        } catch let error as DriverError { precondition(error.code == .staleTarget) }
        precondition(changed.completedPreparations == 0 && changed.actions == ["prepare", "click"]
                     && changed.boundaryChecks >= 2 && changed.receiverReads == 0 && changed.waits == 0,
                     "the post-click target boundary must reject before receiver confirmation")
        precondition(changed.selectionReads == 0 && changed.selectionWritableReads == 0 && changed.restores == 0,
                     "focus must not inspect or restore selection across a changed target boundary")
    }

    @MainActor static func main() async throws {
        // A pointer callback established this exact receiver, but changed a
        // readable, read-only caret. Whole replacement owns its later Select All.
        let replacement = Editor()
        try await replacement.prepare(.replace)
        precondition(replacement.receives && replacement.actions == ["prepare", "click"])
        precondition(replacement.restores == 0 && replacement.selectionReads == 0,
                     "replacement must not require or mutate the old caret during pointer focus")

        // Keeping selection writes available must not make them necessary for
        // replacement either; selection belongs to its later content recipe.
        let writableReplacement = Editor()
        writableReplacement.writable = true
        try await writableReplacement.prepare(.replace)
        precondition(writableReplacement.restores == 0 && writableReplacement.range.location == 0)

        try await checkFocusUsesReceiverOnly()

        for intent in [NativeKeyboardIntent.Kind.insert, .paste] {
            let readOnly = Editor()
            do {
                try await readOnly.prepare(intent)
                preconditionFailure("a caret-sensitive operation must not accept a moved read-only selection")
            } catch let error as DriverError {
                precondition(error.code == .inputUnavailable,
                             "lost selection after a click must stop this operation, not authorize another input route")
            }
            precondition(readOnly.actions == ["prepare", "click"] && readOnly.restores == 0)

            let unchanged = Editor()
            unchanged.clickedRange = unchanged.range
            try await unchanged.prepare(intent)
            precondition(unchanged.restores == 0, "an unchanged read-only selection needs no write")

            let writable = Editor()
            let original = writable.range
            writable.writable = true
            try await writable.prepare(intent)
            precondition(writable.restores == 1 && writable.range.location == original.location
                         && writable.range.length == original.length)

            let failedRestore = Editor()
            failedRestore.writable = true
            failedRestore.restoreStatus = .cannotComplete
            do {
                try await failedRestore.prepare(intent)
                preconditionFailure("an uncertain selection restoration must not authorize input")
            } catch let error as DriverError {
                precondition(error.code == .inputUnavailable,
                             "failed selection restoration must be terminal after pointer preparation")
            }
            precondition(failedRestore.restores == 1 && failedRestore.actions == ["prepare", "click"])
        }

        let unfocused = Editor()
        unfocused.clickEstablishesReceiver = false
        do {
            try await unfocused.prepare(.replace)
            preconditionFailure("replacement still requires the exact selected receiver")
        } catch is NativePreparationUnavailable {}
        precondition(unfocused.waits == 11 && unfocused.restores == 0)

        for intent in [NativeKeyboardIntent.Kind.insert, .paste] {
            for unknown in [false, true] {
                let changedWithoutReceiver = Editor()
                changedWithoutReceiver.clickEstablishesReceiver = false
                changedWithoutReceiver.selectionAvailableAfterClick = !unknown
                do {
                    try await changedWithoutReceiver.prepare(intent)
                    preconditionFailure("a missing receiver cannot authorize another route after losing the old caret")
                } catch let error as DriverError { precondition(error.code == .inputUnavailable) }
                precondition(changedWithoutReceiver.restores == 0,
                             "never restore selection without the selected receiver")
            }

            let preservedWithoutReceiver = Editor()
            preservedWithoutReceiver.clickEstablishesReceiver = false
            preservedWithoutReceiver.clickedRange = preservedWithoutReceiver.range
            do {
                try await preservedWithoutReceiver.prepare(intent)
                preconditionFailure("an unchanged caret still does not prove the receiver")
            } catch is NativePreparationUnavailable {}
            precondition(preservedWithoutReceiver.restores == 0)

            let initiallyUnknown = Editor()
            initiallyUnknown.clickEstablishesReceiver = false
            initiallyUnknown.selectionAvailable = false
            do {
                try await initiallyUnknown.prepare(intent)
                preconditionFailure("an unavailable old caret does not authorize safe replanning after a click")
            } catch let error as DriverError { precondition(error.code == .inputUnavailable) }
            precondition(initiallyUnknown.restores == 0)
        }

        let ready = Editor()
        ready.alreadyReady = true
        try await ready.prepare(.replace)
        precondition(ready.actions.isEmpty && ready.selectionReads == 0)

        let changed = Editor()
        var access = changed.access()
        access = .init(
            ready: access.ready, selection: access.selection, preparePointer: access.preparePointer,
            clickPointer: {
                changed.actions.append("click")
                changed.receives = true
                changed.boundaryValid = false
            }, receives: access.receives, selectionWritable: access.selectionWritable,
            restoreSelection: access.restoreSelection)
        do {
            try await NativePointerFocusUnit.prepare(
                intent: .replace, access: access,
                check: {
                    guard changed.boundaryValid else {
                        throw DriverError("original editor changed", code: .staleTarget)
                    }
                }, wait: {})
            preconditionFailure("a replaced target cannot inherit pointer preparation")
        } catch let error as DriverError { precondition(error.code == .staleTarget) }
        precondition(changed.restores == 0)
        print("Pointer focus selection intent regressions passed")
    }
}
