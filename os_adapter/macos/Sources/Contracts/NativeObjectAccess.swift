// Copyright (c) Huawei Technologies Co., Ltd. 2026. All rights reserved.

import AppKit
import ApplicationServices

/// Query-only view of the original session registry and its native facts.
@MainActor protocol NativeQueryObjectAccess: AnyObject {
    func nativeFacts(_ ref: String, session: String, fields: [String], maxNodes: Int, maxDepth: Int,
                     childrenPage: NativeInspectQuery.ChildrenPage?, relationPage: NativeInspectQuery.RelationPage?,
                     advertisedFields: [String], followRelations: [String], textContent: Bool) throws -> [String: Any]
    func nativeRelation(_ ref: String, session: String, property: String, cursor: String?, limit: Int) throws -> [String: Any]
    func readText(_ ref: String, offset: Int, length: Int) throws -> [String: Any]
    func registerNativeRoot(_ element: AXUIElement, app: NSRunningApplication, scope: String) throws -> String
    func nativeActionFacts(_ ref: String) throws -> NativeElementControl.ActionFacts
    func nativeSelectionContract(_ ref: String) throws -> NativeRowSelection.Contract?
    func nativeTextValueFacts(_ ref: String) throws -> NativeElementControl.TextValueFacts
}

/// Addressed control capabilities. These never select another route or editor.
@MainActor protocol NativeControlObjectAccess: AnyObject {
    func retainNode(_ ref: String) -> (() -> Void)?
    func nativeActionFacts(_ ref: String) throws -> NativeElementControl.ActionFacts
    func executeNativeAction(
        _ ref: String, action: String, description: String?, onDispatch: @MainActor () throws -> Void,
        onReturned: @MainActor () -> Void) throws
    func executeNativeSelection(
        _ ref: String, contract: NativeRowSelection.Contract, onDispatch: @MainActor () throws -> Void,
        onReturned: @MainActor () -> Void
    ) throws -> NativeElementControl.ActionResult
    func executeNativeTextValue(
        _ ref: String, contract: NativeElementControl.TextValueContract, text: String,
        onDispatch: @MainActor () throws -> Void
    ) throws -> NativeElementControl.ActionResult
}

extension NativeControlObjectAccess {
    func executeNativeAction(
        _ ref: String, action: String, description: String?, onDispatch: @MainActor () throws -> Void
    ) throws {
        try executeNativeAction(ref, action: action, description: description, onDispatch: onDispatch, onReturned: {})
    }
}
