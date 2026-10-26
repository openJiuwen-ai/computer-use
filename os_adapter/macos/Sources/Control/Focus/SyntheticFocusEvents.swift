import AppKit
import ApplicationServices

/// Process-addressed notifications observed in SkyComputerUseService 26.1013.1001501.
/// These prepare AppKit's local state without requesting global activation.
enum SyntheticFocusEvents {
    // Native evidence: 0x100735ff4, 0x10073617c and notifyWindowKeyFocusReturned.
    static let processNotification: UInt = 21
    static let keyFocusReturned: Int16 = .min

    static func keyFocus() throws -> CGEvent {
        guard let kind = NSEvent.EventType(rawValue: processNotification),
            let event = NSEvent.otherEvent(
                with: kind, location: .zero, modifierFlags: [], timestamp: ProcessInfo.processInfo.systemUptime,
                windowNumber: 0, context: nil, subtype: keyFocusReturned, data1: 0, data2: 0)?.cgEvent
        else { throw NativePreparationUnavailable(description: "process focus notification is unavailable") }
        return event
    }

}
