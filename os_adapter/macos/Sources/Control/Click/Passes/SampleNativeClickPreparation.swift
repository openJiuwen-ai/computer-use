// Copyright (c) Huawei Technologies Co., Ltd. 2026. All rights reserved.

import AppKit
import ApplicationServices

/// QUERY: sample geometry and readiness of the selected pointer delivery only.
@MainActor enum SampleNativeClickPreparationPass {
    static func run(_ plan: NativeClickPlan, boundary: NativeUnitBoundary, afterPreparation: Bool = false) throws
        -> NativeClickWindowPreparation?
    {
        let metric = RequestDiagnostics.metrics?.start(.clickTargetValidation)
        defer { metric?.end() }
        try boundary.checkConditions()
        let pointer: NativePointerUnit, point: CGPoint
        switch plan.target {
        case .captured(let unit):
            pointer = unit
            point = try unit.point(plan.pointer!.start!)
        case .element(let unit):
            if !afterPreparation, let exposure = try unit.exposure() {
                pointer = NativePointerUnit(
                    application: unit.application, window: exposure.mapping, nativeWindow: unit.window)
                point = exposure.point
            } else {
                let geometry = try afterPreparation ? unit.geometry() : unit.preparationGeometry()
                pointer = NativePointerUnit(
                    application: unit.application, window: geometry.mapping, nativeWindow: unit.window)
                point = geometry.point
            }
        default: return nil
        }
        try pointer.validateWindowIdentity()
        try BackgroundTarget(pointer.application).validate(input: true)
        try BackgroundTarget.validateReleasedInput()
        if plan.offer.delivery == .windowDirected {
            guard !BackgroundTarget.isExclusive else {
                throw DriverError("window-directed clicks are unavailable in exclusive mode", code: .modeUnsupported)
            }
            try BackgroundEvents.requirePointerRoute(window: pointer.window)
        }
        let needed = !pointer.ready(at: point, delivery: plan.offer.delivery)
        try boundary.checkConditions()
        return NativeClickWindowPreparation(pointer: pointer, point: point, needed: needed)
    }
}
