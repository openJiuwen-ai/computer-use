import ApplicationServices
import Foundation

/// Local state evidence for one already-returned AXPress; never another action.
enum NativeTogglePress {
    static func value(_ raw: Any?) -> String? {
        if let number = raw as? NSNumber, number.doubleValue == Double(number.intValue),
            (0...2).contains(number.intValue)
        {
            return String(number.intValue)
        }
        if let text = raw as? String, ["0", "1", "2"].contains(text) { return text }
        return nil
    }

    static func canObserve(action: String, role: String?, before: String?) -> Bool {
        action == kAXPressAction && [kAXCheckBoxRole, kAXRadioButtonRole].contains(role ?? "") && before != nil
    }

    static func mayConfirm(_ status: AXError) -> Bool {
        // Only returned action errors can be checked against local toggle state.
        // Transport/timeouts and invalid objects retain their original failure.
        [.failure, .attributeUnsupported, .actionUnsupported].contains(status)
    }

    static func shows(role: String, before: String, after: String) -> Bool {
        ["0", "1", "2"].contains(after) && after != before && (role != kAXRadioButtonRole || after == "1")
    }

    @MainActor final class Unconfirmed: ClassifiedFailure, CustomStringConvertible {
        nonisolated let code = FailureCode.executionUnknown
        nonisolated let description: String
        let role: String
        let before: String
        let status: AXError
        let read: @MainActor () throws -> String?

        init(role: String, before: String, status: AXError, read: @escaping @MainActor () throws -> String?) {
            self.role = role
            self.before = before
            self.status = status
            self.read = read
            description = "native action returned AX error \(status.rawValue); effect unknown, not retried"
        }
    }
}
