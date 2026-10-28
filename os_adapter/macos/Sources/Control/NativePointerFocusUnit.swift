import AppKit
import ApplicationServices

/// A preparation-only click on one confirmed native editor hit. Python chooses
/// this route after AX preparation; this unit never sends requested text/keys.
@MainActor struct NativePointerFocusUnit {
    let application: NSRunningApplication
    let window: AXUIElement
    let element: AXUIElement
    var receiverRelation: NativeReceiverRelation? = nil
    var cellEditor: NativeCellEditorHandoff? = nil

    private var pointer: NativeElementPointerUnit {
        NativeElementPointerUnit(application: application, window: window, element: element)
    }

    func readiness() throws -> NativeReadiness {
        if try NativeReceiverSnapshot.read(application: application, window: window, receiverRelation: receiverRelation)
            .receives(element)
        {
            if let cellEditor,
                try cellEditor.association(window: window).map({ CFEqual($0.editor, element) }) != true {
                return NativeReadiness([("receiver", "unknown")])
            }
            return NativeReadiness()
        }
        let facts = try pointer.conditions()
        return facts.geometry ? NativeReadiness([("receiver", "no")]) : NativeReadiness([("geometry", "unknown")])
    }

    private func selection() -> CFRange? {
        guard let value = AXAccess.read(element, kAXSelectedTextRangeAttribute).value,
            CFGetTypeID(value) == AXValueGetTypeID()
        else { return nil }
        var range = CFRange()
        return AXValueGetValue(unsafeDowncast(value, to: AXValue.self), .cfRange, &range) ? range : nil
    }

    func prepare(
        foreground: Bool, check: @escaping @MainActor () throws -> Void,
        onPreparation: @escaping @MainActor (String) -> Void, onCleanupUnknown: @escaping @MainActor () -> Void
    ) async throws {
        try check()
        if try readiness().ready { return }
        let checkEffect: @MainActor () throws -> Void = {
            try check()
            try cellEditor?.requireAssociation(element: element, window: window)
        }
        let range = selection()
        try await pointer.prepare(
            foreground: foreground, check: check, onPreparation: onPreparation, onCleanupUnknown: onCleanupUnknown)
        try checkEffect()
        let click = try NativePointerIntent(
            unit: "pointer_sequence", kind: "click", parameters: ["button": "left", "count": 1], foreground: false,
            element: true)
        try await pointer.deliver(
            click, check: checkEffect, onDispatch: { onPreparation("verified editor pointer focus") }, onPosted: {},
            onCleanupUnknown: onCleanupUnknown)
        for attempt in 0..<12 {
            try check()
            if try NativeReceiverSnapshot.read(
                application: application, window: window, receiverRelation: receiverRelation
            ).receives(element) {
                if let range, selection().map({ $0.location == range.location && $0.length == range.length }) != true {
                    guard AXAccess.writable(element, kAXSelectedTextRangeAttribute).value == true else {
                        throw NativePreparationUnavailable(
                            description:
                                "pointer focus could not preserve the editor selection; no requested input sent")
                    }
                    var original = range
                    guard let value = AXValueCreate(.cfRange, &original) else {
                        throw DriverError("cannot encode the editor selection", code: .inputUnavailable)
                    }
                    try checkEffect()
                    onPreparation("native editor selection restoration")
                    guard
                        AXMessaging.setAttributeValue(element, kAXSelectedTextRangeAttribute as CFString, value)
                            == .success
                    else {
                        throw NativePreparationUnavailable(
                            description: "editor selection restoration failed; no requested input sent")
                    }
                    for restoration in 0..<12 {
                        try check()
                        if selection().map({ $0.location == range.location && $0.length == range.length }) == true {
                            return
                        }
                        if restoration < 11 { try await Task.sleep(for: .milliseconds(25)) }
                    }
                    throw NativePreparationUnavailable(
                        description: "editor selection restoration is unconfirmed; no requested input sent")
                }
                return
            }
            if attempt < 11 { try await Task.sleep(for: .milliseconds(25)) }
        }
        throw NativePreparationUnavailable(
            description: "pointer preparation did not establish the selected editor; no requested input sent")
    }
}
