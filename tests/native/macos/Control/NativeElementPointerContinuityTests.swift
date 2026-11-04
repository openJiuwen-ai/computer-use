// Copyright (c) Huawei Technologies Co., Ltd. 2026. All rights reserved.

import AppKit
import ApplicationServices

@main enum NativeElementPointerContinuityTests {
    typealias Geometry = NativeElementPointerUnit.Geometry
    typealias Basis = NativeElementHitSelection.Basis

    @MainActor static func geometry(
        id: CGWindowID = 71, pid: pid_t = 72,
        window: CGRect = CGRect(x: 100, y: 100, width: 600, height: 400),
        frame: CGRect = CGRect(x: 200, y: 200, width: 100, height: 60),
        visible: CGRect? = nil, point: CGPoint = CGPoint(x: 250, y: 230)
    ) -> Geometry {
        .init(
            mapping: .init(id: id, pid: pid, frame: window, title: nil,
                           width: Int(window.width), height: Int(window.height)),
            frame: frame, visible: visible ?? frame.intersection(window),
            candidate: .init(point: point, source: .center))
    }

    @MainActor static func main() {
        dispatchBasis()
        let original = geometry()
        let expanded = geometry(frame: original.frame.insetBy(dx: -25, dy: -15))
        precondition(expanded.permitsFrozenDispatch(
            from: original, basis: .exactHit, requiresExactGeometry: false),
                     "hover layout must not reject the same original object at the frozen point")
        precondition(!expanded.same(as: original),
                     "retained popup/capture geometry equality must remain strict")
        precondition(!expanded.permitsFrozenDispatch(
            from: original, basis: .exactHit, requiresExactGeometry: true),
                     "a qualified popup cannot adopt changed geometry")

        for basis in [Basis.providerStackingGeometry, .coarseAncestorGeometry, .backgroundGeometry,
                      .unsupportedForegroundGeometry, .rejected] {
            precondition(!expanded.permitsFrozenDispatch(
                from: original, basis: basis, requiresExactGeometry: false),
                         "changed layout requires a fresh exact hit, not a geometry fallback")
        }
        for basis in [Basis.exactHit, .providerStackingGeometry, .coarseAncestorGeometry, .backgroundGeometry,
                      .unsupportedForegroundGeometry] {
            for strict in [false, true] {
                precondition(original.permitsFrozenDispatch(
                    from: original, basis: basis, requiresExactGeometry: strict))
            }
        }
        precondition(!original.permitsFrozenDispatch(
            from: original, basis: .rejected, requiresExactGeometry: false),
                     "an unchanged frame cannot override a conflicting hit")

        let outside = geometry(visible: CGRect(x: 260, y: 200, width: 40, height: 60))
        let movedPoint = geometry(point: CGPoint(x: 251, y: 230))
        let changedWindow = geometry(id: 73)
        let changedProcess = geometry(pid: 74)
        let movedWindow = geometry(window: CGRect(x: 101, y: 100, width: 600, height: 400))
        let resizedWindow = geometry(window: CGRect(x: 100, y: 100, width: 601, height: 400))
        for current in [outside, movedPoint, changedWindow, changedProcess, movedWindow, resizedWindow] {
            precondition(!current.permitsFrozenDispatch(
                from: original, basis: .exactHit, requiresExactGeometry: false),
                         "original coordinate mapping, point and current visible scope remain mandatory")
        }
        print("Element pointer continuity regressions passed")
    }

    @MainActor private static func dispatchBasis() {
        let bases: [Basis] = [.exactHit, .providerStackingGeometry, .coarseAncestorGeometry, .backgroundGeometry, .rejected]
        for basis in bases {
            for exactOriginalHit in [false, true] {
                for hasMember in [false, true] {
                    precondition(NativeElementPointerUnit.allowsDispatchBasis(
                        basis, requirement: .contentReceiver, exactOriginalHit: exactOriginalHit, hasMember: hasMember
                    ) == (basis != .rejected), "existing geometry and member routes keep their qualification")
                    precondition(NativeElementPointerUnit.allowsDispatchBasis(
                        basis, requirement: .foregroundWindowPoint, exactOriginalHit: exactOriginalHit, hasMember: hasMember
                    ) == (basis == .exactHit && exactOriginalHit && !hasMember),
                        "foreground preparation cannot replace the original-element exact hit with a weaker basis")
                }
            }
        }
        let target = AXUIElementCreateApplication(getpid())
        let unit = NativeElementPointerUnit(application: .current, window: target, element: target)
        let selected = unit.requiring(.foregroundWindowPoint)
        precondition(unit.pointerRequirement == .contentReceiver)
        precondition(selected.pointerRequirement == .foregroundWindowPoint)
        precondition(CFEqual(selected.element, unit.element) && CFEqual(selected.window, unit.window))
        // Construction only: no AX query, application activation or input post.
        // Preparation/exposure/dispatch use this same narrow constructor.
        let original = geometry()
        let refreshed = geometry(window: original.mapping.frame.offsetBy(dx: 10, dy: 20))
        for mapping in [original.mapping, refreshed.mapping] {
            let pointer = selected.pointerUnit(mapping: mapping)
            precondition(pointer.pointerRequirement == .foregroundWindowPoint)
            precondition(pointer.window.id == mapping.id && pointer.window.frame == mapping.frame)
            precondition(pointer.nativeWindow.map { CFEqual($0, target) } == true)
            precondition(!pointer.backgroundPreparationAvailable(),
                         "refreshing original geometry cannot drop the fixed foreground requirement")
        }
    }
}
