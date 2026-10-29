// Copyright (c) Huawei Technologies Co., Ltd. 2026. All rights reserved.

import AppKit
import ApplicationServices

@MainActor enum NativeClickTarget {
    case action(node: String, name: String, description: String?)
    case selection(node: String, contract: NativeRowSelection.Contract)
    case menu(NativeMenuUnit, node: String, name: String, description: String?)
    case captured(NativePointerUnit)
    case element(NativeElementPointerUnit)
    case desktop(NativeDesktopUnit)
}

@MainActor struct NativeClickPlan {
    let target: NativeClickTarget
    let offer: NativeClickContract
    let pointer: NativePointerIntent?
    let command: NativeUnitCommand
    let operation: NativeOperation
    let within: [String]
    let step: Int64
    var document: NativeDocumentContext? = nil
    var documentPreparationRequired = false
}

@MainActor struct NativeClickWindowPreparation {
    let pointer: NativePointerUnit
    let point: CGPoint
    let needed: Bool
}
