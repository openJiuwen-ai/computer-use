// Copyright (c) Huawei Technologies Co., Ltd. 2026. All rights reserved.

import AppKit
import ApplicationServices

@MainActor enum NativeClickTarget {
    case action(node: String, name: String, description: String?)
    case selection(node: String, contract: NativeRowSelection.Contract)
    case menu(NativeMenuUnit, node: String, name: String, description: String?)
    case menuPointer(NativeMenuUnit)
    case captured(NativePointerUnit)
    case coordinateMenuPress(NativeCoordinateMenuPress)
    case element(NativeElementPointerUnit)
    case popupPointer(any NativePopupPointerInput)
}

@MainActor struct NativeClickPlan {
    let target: NativeClickTarget
    let offer: NativeClickContract
    let pointer: NativePointerIntent?
    let command: NativeUnitCommand
    let operation: NativeOperation
    let within: [String]
    let step: Int64
    var document: (any NativeActionDocumentInput)? = nil
    var documentPreparation = NativeActionDocumentPolicy.Preparation()
    var actionDocument: NativeActionDocumentContract? = nil
    var documentScope: NativeActionDocumentContexts.Scope? = nil
    var actionContext: (any NativeApplicationContextInput)? = nil
    var qtPopupOpener: NativeQtPopupOpener? = nil
}

@MainActor struct NativeClickWindowPreparation {
    let pointer: NativePointerUnit
    let point: CGPoint
    let needed: Bool
    var kind: NativePointerIntent.Kind = .click
    var elementGeometry: NativeElementPointerUnit.Geometry? = nil
}
