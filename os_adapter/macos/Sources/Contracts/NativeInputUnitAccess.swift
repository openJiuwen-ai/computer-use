import AppKit
import ApplicationServices

/// Construct only the explicitly requested native unit from the original registry.
/// Fact queries do not obtain a complete execution owner through this view.
@MainActor protocol NativeInputUnitAccess: AnyObject {
    func receiverRelation(
        _ binding: [String: Any], actuator: NativeInspectQuery.Actuator?, receiver: NativeInspectQuery.Receiver?
    ) throws -> NativeReceiverRelation?
    func desktopUnit(_ binding: [String: Any]) throws -> NativeDesktopUnit
    func keyboardUnit(
        _ binding: [String: Any], actuator: NativeInspectQuery.Actuator?, receiver: NativeInspectQuery.Receiver?
    ) throws -> NativeKeyboardUnit
    func selectedTextUnit(
        _ binding: [String: Any], actuator: NativeInspectQuery.Actuator?, receiver: NativeInspectQuery.Receiver?
    ) throws -> NativeSelectedTextUnit
    func elementPointerUnit(_ binding: [String: Any], actuator: NativeInspectQuery.Actuator?) throws
        -> NativeElementPointerUnit
    func menuUnit(_ binding: [String: Any], within: [String]) throws -> NativeMenuUnit
    func scrollUnit(_ binding: [String: Any], actuator: NativeInspectQuery.Actuator) throws -> NativeScrollUnit
}

extension NativeInputUnitAccess {
    func elementPointerUnit(_ binding: [String: Any]) throws -> NativeElementPointerUnit {
        try elementPointerUnit(binding, actuator: nil)
    }
    func menuUnit(_ binding: [String: Any]) throws -> NativeMenuUnit { try menuUnit(binding, within: []) }
}
