// Copyright (c) Huawei Technologies Co., Ltd. 2026. All rights reserved.

import AppKit
import ApplicationServices

/// QUERY: sample geometry and readiness of the selected pointer delivery only.
@MainActor enum SampleNativeClickPreparationPass {
    static func needed(_ offer: NativeClickContract, pointerReady: Bool, foreground: Bool) -> Bool {
        !pointerReady || offer.requiresForegroundGeometry && !foreground
    }

    static func run(_ plan: NativeClickPlan, boundary: NativeUnitBoundary, afterPreparation: Bool = false) throws
        -> NativeClickWindowPreparation?
    {
        let metric = RequestDiagnostics.metrics?.start(.clickTargetValidation)
        defer { metric?.end() }
        try boundary.checkConditions()
        let pointer: NativePointerUnit, point: CGPoint
        var kind: NativePointerIntent.Kind = .click
        var elementGeometry: NativeElementPointerUnit.Geometry?
        switch plan.target {
        case .captured(let unit):
            pointer = try unit.resolvingCaptureTranslation()
            point = try pointer.point(plan.pointer!.start!)
        case .element(let unit):
            let viewport = try afterPreparation ? nil : unit.viewport()
            if let exposure = viewport?.exposure {
                pointer = unit.exposurePointerUnit(exposure)
                point = exposure.point
                // Exposure addresses a passive wheel receiver. A click/key
                // receiver is prepared only after the target becomes visible.
                kind = .scroll
            } else {
                let geometry = try viewport.map { try unit.preparationGeometry(in: $0) } ?? unit.geometry()
                pointer = unit.pointerUnit(mapping: geometry.mapping)
                point = geometry.point
                elementGeometry = geometry
            }
        default: return nil
        }
        try pointer.validateWindowIdentity()
        try BackgroundTarget(pointer.application).validate(input: true)
        if plan.offer.delivery == .windowDirected {
            try BackgroundEvents.requirePointerRoute(window: pointer.window)
        }
        let needed = needed(
            plan.offer, pointerReady: pointer.ready(at: point, kind: kind, delivery: plan.offer.delivery),
            foreground: !plan.offer.requiresForegroundGeometry || pointer.foregroundWindow(at: point))
        // These synchronous queries have no preparation effect or suspension.
        // The selected preparation/event checks its own live boundary next.
        try RequestDiagnostics.checkCancellation()
        return NativeClickWindowPreparation(
            pointer: pointer, point: point, needed: needed, kind: kind, elementGeometry: elementGeometry)
    }
}
