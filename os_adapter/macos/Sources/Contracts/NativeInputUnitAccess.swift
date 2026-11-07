// Copyright (c) Huawei Technologies Co., Ltd. 2026. All rights reserved.

import AppKit
import ApplicationServices

/// Construct only the explicitly requested native unit from the original registry.
/// Fact queries do not obtain a complete execution owner through this view.
@MainActor protocol NativeInputUnitAccess: AnyObject {
    func receiverRelation(
        _ binding: [String: Any], actuator: NativeInspectQuery.Actuator?, receiver: NativeInspectQuery.Receiver?
    ) throws -> NativeReceiverRelation?
    func actionContextUnit(_ binding: [String: Any], action: String) throws -> (any NativeApplicationContextInput)?
    func keyboardUnit(
        _ binding: [String: Any], actuator: NativeInspectQuery.Actuator?, receiver: NativeInspectQuery.Receiver?
    ) throws -> NativeKeyboardUnit
    func applicationKeyboardUnit(_ binding: [String: Any]) throws -> NativeApplicationKeyboardUnit?
    func selectedTextUnit(
        _ binding: [String: Any], actuator: NativeInspectQuery.Actuator?, receiver: NativeInspectQuery.Receiver?
    ) throws -> NativeSelectedTextUnit
    func elementPointerUnit(_ binding: [String: Any], actuator: NativeInspectQuery.Actuator?) throws
        -> NativeElementPointerUnit
    func popupPointerUnit(_ binding: [String: Any], qualification: NativePopupPointerQualification) throws
        -> any NativePopupPointerInput
    func menuUnit(_ binding: [String: Any], within: [String]) throws -> NativeMenuUnit
    func scrollUnit(_ binding: [String: Any], actuator: NativeInspectQuery.Actuator) throws -> NativeScrollUnit
}

extension NativeInputUnitAccess {
    func actionContextUnit(_ binding: [String: Any], action: String) throws -> (any NativeApplicationContextInput)? { nil }
    func applicationKeyboardUnit(_ binding: [String: Any]) throws -> NativeApplicationKeyboardUnit? { nil }
    func popupPointerUnit(_ binding: [String: Any], qualification: NativePopupPointerQualification) throws
        -> any NativePopupPointerInput
    { NativePopupPointerUnit(element: try elementPointerUnit(binding, actuator: nil), qualification: qualification) }
    func elementPointerUnit(_ binding: [String: Any]) throws -> NativeElementPointerUnit {
        try elementPointerUnit(binding, actuator: nil)
    }
    func menuUnit(_ binding: [String: Any]) throws -> NativeMenuUnit { try menuUnit(binding, within: []) }
}
