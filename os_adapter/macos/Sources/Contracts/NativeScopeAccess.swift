// Copyright (c) Huawei Technologies Co., Ltd. 2026. All rights reserved.

import AppKit
import ApplicationServices

/// A fresh AX proxy for the original physical input scope, not a new target.
@MainActor struct NativeFixedInputWindow {
    let application: NSRunningApplication
    let window: AXUIElement

    /// Optional mechanism evidence must not reject unrelated candidates. An
    /// unavailable query is unknown; cancellation and identity conflicts keep
    /// their existing meaning and are never converted to missing evidence.
    static func available<Value>(_ query: () throws -> Value?) throws -> Value? {
        do { return try query() }
        catch let error as DriverError where [.inputUnavailable, .observationIncomplete].contains(error.code) {
            return nil
        }
    }
}

/// Live scope facts and last-moment checks; no operation journal or input unit.
@MainActor protocol NativeScopeAccess: AnyObject {
    func retainedForegroundSource(_ binding: [String: Any]) -> (application: NSRunningApplication, element: AXUIElement?)?
    func fixedInputWindow(_ binding: [String: Any]) throws -> NativeFixedInputWindow?
    func inputBinding(_ raw: Any?, input: Bool, attachedMenuAction: Bool) throws -> (
        wire: [String: Any], blocked: [String], node: String?
    )
    func windowKeyboardQueryScope(_ raw: Any?) throws -> NativeInputScope?
    func popupPointerBoundary(_ raw: Any?, qualification: NativePopupPointerQualification) throws -> [String]
    func qtComboChoiceBoundary(_ raw: Any?, qualification: NativeQtComboChoice) throws -> [String]
    func savePanelPathBoundary(_ raw: Any?, proof: NativeSavePanelPathContinuation.Proof) throws -> [String]
    func keyboardBoundaryAnchor(_ binding: [String: Any], application: NSRunningApplication, window: AXUIElement) throws
        -> NativeKeyboardBoundaryAnchor?
    func keyboardBoundary(_ anchor: NativeKeyboardBoundaryAnchor) throws -> NativeKeyboardBoundaryDecision
    func legacyKeyboardBoundary(_ anchor: NativeKeyboardBoundaryAnchor) throws -> [String]
    func checkContainment(_ binding: [String: Any], within: [String], op: NativeOperation) throws
    func checkActuator(_ binding: [String: Any], actuator: NativeInspectQuery.Actuator?, op: NativeOperation) throws
    func directoryTarget(_ raw: Any?) throws -> [String: Any]?
    func observationTarget(_ raw: Any?) throws -> (wire: [String: Any], node: String)
    func observePointer(_ binding: [String: Any])
    func aimPointer(_ binding: [String: Any], intent: NativeIntent, receiver: String?)
}

extension NativeScopeAccess {
    func retainedForegroundSource(_ binding: [String: Any]) -> (application: NSRunningApplication, element: AXUIElement?)? { nil }
    func fixedInputWindow(_ binding: [String: Any]) throws -> NativeFixedInputWindow? { nil }
    func windowKeyboardQueryScope(_ raw: Any?) throws -> NativeInputScope? { nil }
    func qtComboChoiceBoundary(_ raw: Any?, qualification: NativeQtComboChoice) throws -> [String] {
        throw DriverError("qualified Qt choice boundary unavailable", code: .inputUnavailable)
    }
    func popupPointerBoundary(_ raw: Any?, qualification: NativePopupPointerQualification) throws -> [String] {
        throw DriverError("qualified popup pointer boundary unavailable", code: .inputUnavailable)
    }
    func savePanelPathBoundary(_ raw: Any?, proof: NativeSavePanelPathContinuation.Proof) throws -> [String] {
        throw DriverError("the Go To sheet's original SavePanel boundary is unavailable", code: .inputUnavailable)
    }
    // Existing adapters do not opt into this new strategy implicitly.
    func keyboardBoundaryAnchor(_ binding: [String: Any], application: NSRunningApplication, window: AXUIElement) throws
        -> NativeKeyboardBoundaryAnchor?
    { nil }
    func keyboardBoundary(_ anchor: NativeKeyboardBoundaryAnchor) throws -> NativeKeyboardBoundaryDecision {
        .needsLegacy("keyboard boundary projection unavailable")
    }
    func legacyKeyboardBoundary(_ anchor: NativeKeyboardBoundaryAnchor) throws -> [String] {
        throw DriverError("fixed keyboard boundary adapter unavailable", code: .inputUnavailable)
    }
    func inputBinding(_ raw: Any?) throws -> (wire: [String: Any], blocked: [String], node: String?) {
        try inputBinding(raw, input: false, attachedMenuAction: false)
    }
}
