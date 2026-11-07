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

    @MainActor static func main() throws {
        dispatchBasis()
        try preparedClickUsesItsSelectedPoint()
        try qualifiedCompoundCaptionUsesMouseSemantics()
        ownedControlParts()
        serviceAncestorProbeKeepsIndependentEvidence()
        opaqueRowClickPreservesAlternativeActions()
        let original = geometry()
        let expanded = geometry(frame: original.frame.insetBy(dx: -25, dy: -15))
        precondition(expanded.permitsFrozenDispatch(
            from: original, basis: .exactHit, requiresExactGeometry: false),
                     "hover layout must not reject the same original object at the frozen point")
        precondition(expanded.permitsFrozenDispatch(
            from: original, basis: .ownedControlPart, requiresExactGeometry: false),
                     "a fresh passive part still proves the original object after hover changes layout")
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

    private static func serviceAncestorProbeKeepsIndependentEvidence() {
        for status: AXError? in [nil, .noValue, .cannotComplete, .notImplemented, .success] {
            precondition(NativeElementHitEvidence.permitsServiceAncestorProbe(
                .windowRoot, systemStatus: status, differentSystemHit: false),
                "an empty or failed system-wide read must not erase the original presenter hit")
        }
        precondition(!NativeElementHitEvidence.permitsServiceAncestorProbe(
            .windowRoot, systemStatus: .success, differentSystemHit: true),
            "a successful hit on a different object must not authorize the service fallback")
        precondition(!NativeElementHitEvidence.permitsServiceAncestorProbe(
            .otherWindow, systemStatus: .cannotComplete, differentSystemHit: false),
            "missing system-wide evidence cannot qualify a different window")
        precondition(NativeElementHitEvidence.permitsServiceAncestorProbe(
            .otherControl, systemStatus: nil, differentSystemHit: false),
            "a same-window service ancestor can still be proved by its parent path and endpoint")
    }

    @MainActor private static func opaqueRowClickPreservesAlternativeActions() {
        let roles = ["cell": kAXCellRole, "row": kAXRowRole, "table": kAXTableRole]
        let parents = ["leaf": "cell", "cell": "row", "row": "table"]
        let children = ["cell": ["leaf"], "row": ["cell"]]
        func matches(
            selected: String? = nil, foreign: String? = nil, originalScope: Bool = true, rows: [String] = ["row"]
        ) -> Bool {
            NativeOpaqueRowClick.matches(
                "leaf", same: ==, readRole: { roles[$0] }, readParent: { parents[$0] },
                readChildren: { children[$0] }, readRows: { _ in rows }, readSelected: { $0 == selected },
                sameProcess: { $0 != foreign && $1 != foreign }, inScope: { $0 == "table" && originalScope })
        }
        precondition(matches() && !matches(selected: "cell") && !matches(selected: "row"))
        precondition(!matches(foreign: "table") && !matches(originalScope: false) && !matches(rows: ["other"]))
        let action = NativeClickContract(
            routeFamily: "macos.axAction.AXPress", delivery: .objectAction, admission: .ready,
            reason: nil, preparationOptions: [])
        precondition(NativeOpaqueRowClick.actionOffer(action, preferPointer: false) == action)
        precondition(NativeOpaqueRowClick.actionOffer(action, preferPointer: true).admission == .insufficientEvidence)
        let other = NativeClickContract(
            routeFamily: "macos.axAction.AXShowMenu", delivery: .objectAction, admission: .ready,
            reason: nil, preparationOptions: [])
        precondition(NativeOpaqueRowClick.actionOffer(other, preferPointer: true) == other)
    }

    @MainActor private static func preparedClickUsesItsSelectedPoint() throws {
        // The probe has no window or pointer geometry to rediscover. Delivery
        // must consume preparation's point while its later live guard remains
        // independently responsible for verifying that point.
        let probe = AXUIElementCreateApplication(getpid())
        let unit = NativeElementPointerUnit(application: .current, window: probe, element: probe)
        let original = geometry()
        let edge = Geometry(mapping: original.mapping, frame: original.frame, visible: original.visible,
                            candidate: .init(point: CGPoint(x: 204, y: 230), source: .edge))
        let delivered = try unit.geometry(prepared: edge)
        precondition(delivered.same(as: edge) && delivered.point != original.point)
    }

    private static func qualifiedCompoundCaptionUsesMouseSemantics() throws {
        let intent = try NativeIntent(["kind": "click", "parameters": ["button": "right"]], mode: "write")
        func qualification(memberRole: String, exactHit: Bool, actions: [String]?)
            -> NativeAddressedPointerReceiver.Qualification?
        {
            NativeAddressedPointerReceiver.compoundTextContextQualification(
                intent: intent, ownerRole: kAXStaticTextRole, ownerActions: actions, memberRole: memberRole,
                exactMemberHit: exactHit)
        }
        let right = qualification(memberRole: kAXStaticTextRole, exactHit: true, actions: [kAXPressAction])
        precondition(right?.requirement(captured: false) == .contentReceiver)
        precondition(qualification(memberRole: kAXButtonRole, exactHit: true, actions: [kAXPressAction]) == nil)
        precondition(qualification(memberRole: kAXStaticTextRole, exactHit: false, actions: [kAXPressAction]) == nil)
        precondition(qualification(memberRole: kAXStaticTextRole, exactHit: true, actions: nil) == nil)
    }

    @MainActor private static func ownedControlParts() {
        let frame = CGRect(x: 10, y: 10, width: 50, height: 30)
        let roles = ["button": kAXButtonRole, "label": kAXStaticTextRole, "link": "AXLink",
                     "row": kAXRowRole, "cell": kAXCellRole, "table": kAXTableRole,
                     "layout": kAXGroupRole, "foreign": kAXStaticTextRole]
        let parents = ["label": "button", "link": "button", "foreign": "button",
                       "cell": "row", "layout": "row", "row": "table"]
        func accepts(_ target: String, _ hit: String, passive: Bool = true, layout: CGRect? = nil) -> Bool {
            NativeOwnedElementHit.matches(
                target, hit: hit, frame: frame, same: ==, role: { roles[$0] }, parent: { parents[$0] },
                inWindow: { $0 != "foreign" }, passive: { _ in passive }, hitFrame: { _ in layout })
        }
        precondition(accepts("button", "label"))
        precondition(!accepts("button", "link") && !accepts("button", "label", passive: false))
        precondition(!accepts("button", "foreign") && !accepts("row", "label"))
        precondition(accepts("row", "cell") && accepts("row", "layout", layout: frame))
        precondition(!accepts("row", "layout", layout: frame.insetBy(dx: 2, dy: 2)))
        precondition(!accepts("row", "layout", passive: false, layout: frame))
        let point = NativeElementHitSelection.Candidate(point: CGPoint(x: 14, y: 15), source: .edge)
        let hit = NativeElementHitSelection.select([point], canContinue: { _ in true }) { _ in
            NativeElementHitSelection.Observation<String>(
                evidence: .init(status: .success, relation: .ownedControlPart), hit: "label", foreground: false)
        }
        precondition(hit?.basis == .ownedControlPart && hit?.candidate == point)
        let row = NativeAddressedPointerReceiver(role: kAXRowRole, actions: [])
        precondition(row.retainedElementQualification(basis: .ownedControlPart, preparation: .geometryReady,
            exactOriginalHit: false, popup: false) == .originalElementForegroundGeometry)
        let bounds = CGRect(x: 0, y: 0, width: 100, height: 60)
        let label = CGRect(x: 5, y: 5, width: 20, height: 20)
        let candidates = NativeElementHitSelection.candidates(bounds: bounds, activationPoint: nil, memberBounds: label)
        precondition(candidates.first?.source == .memberCenter && candidates.first?.point == CGPoint(x: 15, y: 15))
    }

    @MainActor private static func dispatchBasis() {
        let bases: [Basis] = [
            .exactHit, .ownedControlPart, .providerStackingGeometry, .coarseAncestorGeometry,
            .serviceAncestorGeometry, .backgroundGeometry, .rejected
        ]
        for basis in bases {
            for exactOriginalHit in [false, true] {
                for hasMember in [false, true] {
                    precondition(NativeElementPointerUnit.allowsDispatchBasis(
                        basis, requirement: .contentReceiver, exactOriginalHit: exactOriginalHit, hasMember: hasMember
                    ) == (hasMember ? basis == .exactHit : basis != .rejected),
                        "a selected control part needs its own exact hit, not the owner's geometry")
                    precondition(NativeElementPointerUnit.allowsDispatchBasis(
                        basis, requirement: .contentReceiver, exactOriginalHit: exactOriginalHit,
                        hasMember: hasMember, kind: .scroll
                    ) == (basis != .rejected), "scroll retains its original viewport and hit policy")
                    precondition(NativeElementPointerUnit.allowsDispatchBasis(
                        basis, requirement: .contentReceiver, exactOriginalHit: exactOriginalHit,
                        hasMember: hasMember, requiresExactElementHit: true
                    ) == (basis == .exactHit && exactOriginalHit && !hasMember),
                        "opaque row content requires its exact hit without imposing foreground")
                    precondition(NativeElementPointerUnit.allowsDispatchBasis(
                        basis, requirement: .foregroundWindowPoint, exactOriginalHit: exactOriginalHit, hasMember: hasMember
                    ) == (!hasMember && (basis == .exactHit && exactOriginalHit || basis == .ownedControlPart)),
                        "foreground preparation needs an exact hit on the original element or its proved part")
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
