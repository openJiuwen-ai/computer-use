// Copyright (c) Huawei Technologies Co., Ltd. 2026. All rights reserved.

import AppKit
import ApplicationServices

@main enum NativeUnifiedPointerPolicyTests {
    @MainActor static func main() async throws {
        typealias Readiness = NativePointerReadiness
        backgroundDocumentReadiness()
        receiverSampling()
        qtComboChoice()
        try await physicalInputDependencies()
        precondition(
            !Readiness.ready(
                kind: .click, foreground: false, windowKind: .visualOnly, focus: .notRequired, backgroundPrepared: true)
        )
        precondition(
            Readiness.ready(
                kind: .scroll, foreground: false, windowKind: .visualOnly, focus: .notRequired, backgroundPrepared: true
            ))
        precondition(
            Readiness.ready(
                kind: .click, foreground: false, windowKind: .otherDeclared, focus: .notRequired,
                backgroundPrepared: true))
        // Activation does not change the captured delivery. A ready ordinary
        // window remains addressed even while it owns the actual foreground.
        precondition(
            Readiness.ready(
                kind: .click, foreground: true, windowKind: .keyRequiring, focus: .target, backgroundPrepared: false))
        precondition(NativePointerDeliveryPolicy.delivery(nil, windowDirectedAvailable: true) == .windowDirected)
        precondition(
            NativePointerDeliveryPolicy.delivery(.windowDirected, windowDirectedAvailable: false) == .windowDirected)
        precondition(
            NativePointerDeliveryPolicy.delivery(.systemPointer, windowDirectedAvailable: true) == .systemPointer)
        precondition(NativePointerDeliveryPolicy.delivery(nil, windowDirectedAvailable: false) == .systemPointer)
        precondition(
            NativePointerDeliveryPolicy.implicitDelivery(
                kind: .click, windowDirectedAvailable: true, receiverSupported: false) == .systemPointer,
            "pointer focus must not bypass the concrete-receiver contract used by explicit clicks")
        precondition(
            NativePointerDeliveryPolicy.implicitDelivery(
                kind: .click, windowDirectedAvailable: true, receiverSupported: true) == .windowDirected)
        for kind in [NativePointerIntent.Kind.scroll, .drag] {
            precondition(
                NativePointerDeliveryPolicy.implicitDelivery(
                    kind: kind, windowDirectedAvailable: true, receiverSupported: false) == .windowDirected)
        }
        for role in [kAXWindowRole, kAXGroupRole, kAXStaticTextRole, kAXImageRole, kAXUnknownRole] {
            precondition(
                !NativeAddressedPointerReceiver(role: role, actions: [kAXRaiseAction, kAXShowMenuAction]).supported)
        }
        precondition(!NativeAddressedPointerReceiver(role: kAXButtonRole, actions: []).supported)
        precondition(!NativeAddressedPointerReceiver(role: kAXButtonRole, actions: nil).supported)
        precondition(NativeAddressedPointerReceiver(role: kAXButtonRole, actions: [kAXPressAction]).supported)
        precondition(NativeAddressedPointerReceiver(role: kAXTextFieldRole, actions: [], focusWritable: true).supported)
        precondition(!NativeAddressedPointerReceiver(role: kAXTextFieldRole, actions: [kAXConfirmAction]).supported)
        try foregroundPointQualification()
        exposureRetainsOriginalTargetRequirement()
        try await foregroundPointRelease(afterDown: false)
        try await foregroundPointRelease(afterDown: true)
        for kind in [NativePointerIntent.Kind.click, .drag, .scroll] {
            let declaration = NativeInputUnit.pointer(kind).declaration(
                id: "addressed", supported: true, readiness: NativeReadiness([("receiver", "unknown")]))
            precondition(declaration["supported"] as? String == "yes")
            precondition(declaration["backgroundPreparation"] as? String == "yes")
        }
        print(
            "PASS addressed delivery survives foreground preparation; synthetic transport alone cannot admit content clicks"
        )
    }

    @MainActor private static func physicalInputDependencies() async throws {
        typealias Physical = NativePhysicalInput
        let held = Physical.State(modifiers: [.maskCommand, .maskShift], buttons: [.left, .right])
        precondition(held.conflicts() == ["modifier.command", "modifier.shift", "mouse.button.0", "mouse.button.1"])
        for delivery: NativeClickDelivery in [.objectAction, .objectSelection, .windowDirected] {
            // A user dragging/typing elsewhere must not enter a session-state
            // probe before this addressed action/selection or window event.
            try Physical.validate(Physical.dependency(for: delivery), sample: {
                preconditionFailure("addressed input cannot inherit a global held-state prerequisite")
            })
            try await Physical.wait(Physical.dependency(for: delivery), check: {
                preconditionFailure("an unrelated physical wait cannot veto addressed input")
            }, sample: { held })
        }
        precondition(Physical.dependency(for: .systemPointer) == .globalHID)
        for dependency: Physical.Dependency in [.globalHID, .menuTracking] {
            do {
                try Physical.validate(dependency, sample: { held })
                preconditionFailure("global input/tracking must retain the actual conflict barrier")
            } catch let error as DriverError {
                precondition(error.code == .inputUnavailable)
                precondition(error.description.contains("modifier.command"))
                precondition(error.description.contains("mouse.button.0"))
            }
        }
        // An already-held synthetic drag can continue on its own button, but
        // a second physical button or modifier is still a real HID conflict.
        try Physical.validate(.globalHID, allowing: .left, sample: { .init(modifiers: [], buttons: [.left]) })
        precondition(held.conflicts(allowing: .left) == ["modifier.command", "modifier.shift", "mouse.button.1"])
        var now: TimeInterval = 0, pauses = 0, checks = 0
        try await Physical.wait(.globalHID, check: { checks += 1 }, sample: {
            pauses == 0 ? held : .init(modifiers: [], buttons: [])
        }, clock: { now }, pause: { duration in
            pauses += 1
            now += duration
        })
        precondition(pauses == 1 && checks >= 2 && now <= 0.08,
                     "one call must absorb a short user gesture instead of requiring model retries")
        pauses = 0
        try await Physical.wait(.globalHID, check: {}, sample: { .init(modifiers: [], buttons: []) }, pause: { _ in
            preconditionFailure("no conflict must mean no physical-input delay")
        })
        now = 0
        do {
            try await Physical.wait(.menuTracking, check: {}, sample: { held }, clock: { now }, pause: { duration in
                now += duration
            })
            preconditionFailure("a lasting tracking conflict cannot consume an unbounded wait")
        } catch let error as DriverError {
            precondition(error.code == .inputUnavailable && now <= 0.080001)
        }
    }

    @MainActor private static func qtComboChoice() {
        typealias Choice = NativeQtComboChoice
        // Document-scope observations retain the exact forward window path,
        // even though the generic nested-surface property is intentionally nil.
        let roles = ["document": kAXWindowRole, "popup": kAXWindowRole, "combo": kAXMenuButtonRole,
            "list": kAXListRole, "row": kAXRowRole, "cell": kAXStaticTextRole]
        let path = ["document", "combo", "list", "row"]
        precondition(Choice.observationAnchor(logicalSurface: nil, ancestors: path, role: { roles[$0] }) == "document")
        precondition(Choice.observationAnchor(logicalSurface: "popup", ancestors: path, role: { roles[$0] }) == "popup")
        precondition(Choice.observationAnchor(logicalSurface: nil, ancestors: ["list", "row"], role: { roles[$0] }) == nil)
        precondition(Choice.observationAnchor(logicalSurface: nil, ancestors: ["unknown"], role: { roles[$0] }) == nil)
        let qt = "QApplication.Probe.hours.QComboBoxListView"
        precondition(
            Choice.kind(role: kAXStaticTextRole, identifier: qt, listIdentifier: qt, rowIdentifier: qt, hasRow: true)
                == .tableCell)
        let custom = "QApplication.QComboBoxPrivateContainer.QListWidget"
        precondition(
            Choice.kind(
                role: kAXStaticTextRole, identifier: custom + ".QWidget.QLabel", listIdentifier: custom,
                rowIdentifier: nil, hasRow: false) == .widget)
        precondition(
            Choice.kind(
                role: kAXStaticTextRole, identifier: "QApplication.QListView", listIdentifier: "QApplication.QListView",
                rowIdentifier: nil, hasRow: false) == nil)
        precondition(
            Choice.kind(
                role: kAXStaticTextRole, identifier: "unrelated", listIdentifier: qt, rowIdentifier: qt, hasRow: true)
                == nil)
        func membership(popup: [String]?, list: [String]?, cells: [String]?) -> Bool {
            Choice.containsChoice(
                element: "choice", list: "list", row: "row", popupChildren: popup, listChildren: list,
                rowChildren: cells, same: ==)
        }
        precondition(membership(popup: ["list"], list: ["row", "other"], cells: ["choice"]))
        precondition(!membership(popup: ["another-popup-list"], list: ["row"], cells: ["choice"]))
        precondition(!membership(popup: ["list"], list: ["another-row"], cells: ["choice"]))
        precondition(!membership(popup: ["list"], list: ["row"], cells: ["another-choice"]))
        precondition(!membership(popup: nil, list: ["row"], cells: ["choice"]))
        // Both presentation entries reach the same real popup; the AX leaf
        // can keep its independent document native ID in either entry.
        for observed in [UInt32(4), UInt32(9)] {
            precondition(Choice.acceptsObservedWindow(popup: 9, owner: 4, observed: observed))
            precondition(Choice.acceptsWindowIDs(popup: 9, owner: 4, members: [4]))
        }
        precondition(!Choice.acceptsObservedWindow(popup: 9, owner: 4, observed: 10))
        precondition(Choice.kind(role: kAXRowRole, identifier: qt, listIdentifier: qt,
            rowIdentifier: qt, hasRow: true) == nil)
        precondition(Choice.acceptsWindowIDs(popup: 9, owner: 4, members: [4, 9, nil]))
        precondition(!Choice.acceptsWindowIDs(popup: 9, owner: 4, members: [10]))
        func parentConflict(_ parent: String?, unknown: Bool = false) -> Bool {
            NativePopupOwnerBoundary.hasKnownParentConflict(
                popup: "popup", owner: "document",
                windows: [.init(ref: "D", element: "document"),
                    .init(ref: "P", element: "popup", parent: parent, parentUnknown: unknown)], same: ==)
        }
        precondition(!parentConflict(nil))
        precondition(!parentConflict("D"))
        precondition(parentConflict("another-document"))
        precondition(!parentConflict("another-document", unknown: true))
        let action = NativeClickContract(
            routeFamily: "macos.axAction.AXPress", delivery: .objectAction, admission: .ready, reason: nil,
            preparationOptions: [])
        precondition(
            Choice.actionOffer(action, action: kAXPressAction, kind: .tableCell).admission == .insufficientEvidence)
        precondition(Choice.actionOffer(action, action: kAXPressAction, kind: .widget) == action)
        precondition(Choice.actionOffer(action, action: kAXPressAction, kind: nil) == action)
        precondition(Choice.actionOffer(action, action: kAXConfirmAction, kind: .tableCell) == action)
        // Contract reuse must not retain a proof after its reverse owner path
        // changes, even when the two physical native IDs have not changed.
        let leaf = AXUIElementCreateApplication(101), list = AXUIElementCreateApplication(102)
        let row = AXUIElementCreateApplication(103), popup = AXUIElementCreateApplication(104)
        let owner = AXUIElementCreateApplication(105), oldCombo = AXUIElementCreateApplication(106)
        let newCombo = AXUIElementCreateApplication(107)
        let process = NativeProcessIdentity(pid: 101, parent: 1, seconds: 1, microseconds: 0, executable: "fixture")
        func choice(combo: AXUIElement) -> Choice {
            let stamp = NativePopupOwnerStamp(popup: popup, list: list, reverse: [list, combo, owner],
                roles: ["AXList", "AXComboBox", "AXWindow"], popupWindowID: 9, ownerWindowID: 4, process: process)!
            return Choice(element: leaf, list: list, row: row, popup: popup, observedWindow: popup,
                ancestors: [popup, list, row], pid: 101, popupID: 9, ownerID: 4, observedWindowID: 9,
                ownerStamp: stamp, kind: .tableCell)
        }
        var original = action, unchanged = action, changed = action
        original.qtComboChoice = choice(combo: oldCombo)
        unchanged.qtComboChoice = choice(combo: oldCombo)
        changed.qtComboChoice = choice(combo: newCombo)
        precondition(original == unchanged)
        precondition(original != changed)
        // A captured popup uses the existing Qt choice mechanism, but owns its
        // original delivered pixel point. A new image or sibling row point
        // cannot borrow the old contract even when the AX objects are equal.
        original.qtComboChoice!.capturedPoint = .init(capture: "delivered", pixels: CGPoint(x: 20, y: 30))
        unchanged.qtComboChoice!.capturedPoint = .init(capture: "delivered", pixels: CGPoint(x: 20, y: 30))
        precondition(original == unchanged)
        unchanged.qtComboChoice!.capturedPoint = .init(capture: "replacement", pixels: CGPoint(x: 20, y: 30))
        precondition(original != unchanged)
        unchanged.qtComboChoice!.capturedPoint = .init(capture: "delivered", pixels: CGPoint(x: 20, y: 31))
        precondition(original != unchanged)
        unchanged.qtComboChoice!.capturedPoint = nil
        precondition(original != unchanged)
    }

    private static func receiverSampling() {
        typealias Receiver = NativeAddressedPointerReceiver
        // A range control must receive the same declaration through a retained
        // Element and a coordinate AX hit. A cached false focus flag is not a
        // substitute for its independent writable Value capability.
        for role in [kAXSliderRole, kAXIncrementorRole] {
            for cachedFocus: Bool? in [nil, false, true] {
                var reads = [String]()
                let receiver = Receiver.sample(
                    role: role, actions: [kAXIncrementAction, kAXDecrementAction], focusWritable: cachedFocus
                ) { key in
                    reads.append(key)
                    return key == kAXValueAttribute
                }
                precondition(receiver.supported)
                precondition(receiver.exposureQualification == .declaredInteraction)
                precondition(receiver.qualification(concreteHit: true, sameWindow: true) == .declaredInteraction)
                precondition(reads == [kAXValueAttribute])
            }
            precondition(!Receiver.sample(role: role, actions: [kAXIncrementAction]) { _ in false }.supported)
        }
        var reads = [String]()
        let selectedText = Receiver.sample(role: kAXTextFieldRole, actions: [], focusWritable: false) { key in
            reads.append(key)
            return key == kAXSelectedTextAttribute
        }
        precondition(selectedText.supported && reads == [kAXSelectedTextAttribute],
                     "retained-element facts must preserve independent selected-text interaction")
        let button = Receiver.sample(role: kAXButtonRole, actions: [kAXPressAction]) { _ in
            preconditionFailure("button pointer admission must not query text or ranged-value attributes")
        }
        precondition(button.supported)
        precondition(!Receiver.sample(role: nil, actions: [kAXPressAction]) { _ in
            preconditionFailure("missing role must not trigger unrelated provider queries")
        }.supported)
    }

    @MainActor private static func exposureRetainsOriginalTargetRequirement() {
        typealias Receiver = NativeAddressedPointerReceiver
        // A list row does not need to advertise AXPress at the scroll viewport
        // center. Admission may prepare it, but dispatch still needs an exact
        // hit on that original row in its physical foreground window.
        let row = Receiver(role: kAXRowRole, actions: [])
        precondition(!row.supported)
        let pending = row.exposureQualification
        precondition(pending == .originalElementNeedsExposure)
        precondition(pending.requirement(captured: true) == nil,
                     "exposure of a retained object cannot authorize unrelated screenshot coordinates")
        let requirement = pending.requirement(captured: false)!
        precondition(requirement == .foregroundWindowPoint)
        for exact in [true, false] {
            precondition(NativeElementPointerUnit.allowsDispatchBasis(
                .exactHit, requirement: requirement, exactOriginalHit: exact, hasMember: false) == exact,
                "preparation must not replace the original hidden row with a visible sibling")
        }
        precondition(!NativeElementPointerUnit.allowsDispatchBasis(
            .exactHit, requirement: requirement, exactOriginalHit: true, hasMember: true))
        precondition(!NativePointerReadiness.ready(
            kind: .click, foreground: false, windowKind: .otherDeclared, focus: .notRequired,
            backgroundPrepared: true, requirement: requirement),
            "an exposure promise cannot waive the actual original foreground window")
        let radio = Receiver(role: kAXRadioButtonRole, actions: [kAXPressAction])
        precondition(radio.exposureQualification.requirement(captured: false) == .contentReceiver,
                     "an ordinary declared control retains its background preparation route")
    }

    private static func backgroundDocumentReadiness() {
        typealias Readiness = NativePointerReadiness
        // AppKit's normal window can report AXFocused=false (not writable)
        // while its text field reports AXFocused=true. Neither element flag
        // belongs in this window-receiver contract: AXFocusedWindow identifies
        // the original key window, and AXFrontmost reports internal activation.
        precondition(Readiness.ready(
            kind: .click, foreground: false, windowKind: .keyRequiring, focus: .target,
            backgroundPrepared: true, backgroundReportedActive: true),
            "a prepared active original key window remains usable when a child holds keyboard focus")
        for focus: Readiness.WindowFocus in [.otherWindow, .absent, .unresolved, .failed, .notRequired] {
            precondition(!Readiness.ready(
                kind: .click, foreground: false, windowKind: .keyRequiring, focus: focus,
                backgroundPrepared: true, backgroundReportedActive: true),
                "a background pointer needs positive original focused-window identity")
        }
        for active: Bool? in [false, nil] {
            precondition(!Readiness.ready(
                kind: .click, foreground: false, windowKind: .keyRequiring, focus: .target,
                backgroundPrepared: true, backgroundReportedActive: active),
                "a key-window report without confirmed internal application activation is insufficient")
        }
        precondition(!Readiness.ready(
            kind: .click, foreground: false, windowKind: .keyRequiring, focus: .target,
            backgroundPrepared: false, backgroundReportedActive: true),
            "an expired or missing local preparation receipt cannot admit background input")
        precondition(!Readiness.ready(
            kind: .click, foreground: false, windowKind: .identityConflict, focus: .target,
            backgroundPrepared: true, backgroundReportedActive: true),
            "activation and window focus cannot repair an original-window identity conflict")
    }

    @MainActor private static func foregroundPointQualification() throws {
        typealias Receiver = NativeAddressedPointerReceiver
        typealias Readiness = NativePointerReadiness
        let group = Receiver(role: kAXGroupRole, actions: [])
        let point = group.qualification(concreteHit: true, sameWindow: true)
        precondition(point == .sameWindowPoint && !group.supported)
        precondition(point.requirement(captured: true) == .foregroundWindowPoint)
        precondition(point.requirement(captured: false) == nil, "element and pointer-focus admission must not broaden")
        let visualPoint = Receiver.Qualification.originalWindowPoint
        precondition(visualPoint.requirement(captured: true) == .foregroundWindowPoint)
        precondition(visualPoint.requirement(captured: false) == nil,
                     "a screenshot window point cannot stand in for a retained element's exact hit")
        precondition(Readiness.ready(
            kind: .click, foreground: true, windowKind: .visualOnly, focus: .notRequired,
            backgroundPrepared: false, requirement: visualPoint.requirement(captured: true)!),
            "an exact foreground WindowServer hit remains a valid directed receiver without AX children")
        precondition(!Readiness.ready(
            kind: .click, foreground: false, windowKind: .visualOnly, focus: .notRequired,
            backgroundPrepared: true, requirement: visualPoint.requirement(captured: true)!),
            "missing AX children do not establish a background receiver")
        let exactOriginal = Receiver.Qualification.originalElementExactHit
        precondition(exactOriginal.requirement(captured: false) == .foregroundWindowPoint)
        precondition(exactOriginal.requirement(captured: true) == nil,
                     "an element identity proof cannot authorize a captured point")
        precondition(!Receiver(role: kAXRowRole, actions: []).supported,
                     "exact-element delivery must not add a role-based semantic receiver exception")
        for facts in [group, Receiver(role: kAXButtonRole, actions: [kAXPressAction])] {
            precondition(facts.qualification(concreteHit: false, sameWindow: true) == .unconfirmed)
            precondition(facts.qualification(concreteHit: true, sameWindow: false) == .unconfirmed)
        }
        precondition(Receiver.Qualification.unconfirmed.requirement(captured: true) == nil)
        let declared = Receiver(role: kAXButtonRole, actions: [kAXPressAction])
            .qualification(concreteHit: true, sameWindow: true)
        precondition(declared.requirement(captured: true) == .contentReceiver)
        precondition(declared.requirement(captured: false) == .contentReceiver)
        // Role/action evidence is sampled once. Later semantic changes do not
        // replace the selected original-window/point requirement.
        let pinned = point.requirement(captured: true)!
        var backgroundPreparations = 0
        do {
            try NativeClickPreparationPipeline.requireBackgroundPreparation(pinned)
            backgroundPreparations += 1
            preconditionFailure("a no-longer-ready foreground-only route cannot enter local preparation")
        } catch is NativePreparationUnavailable { }
        precondition(backgroundPreparations == 0,
                     "the actual preparation branch must refuse before any background preparation effect")
        try NativeClickPreparationPipeline.requireBackgroundPreparation(.contentReceiver)
        backgroundPreparations += 1
        precondition(backgroundPreparations == 1, "ordinary content receiver preparation remains available")
        for foreground in [true, false, true] {
            precondition(Readiness.ready(
                kind: .click, foreground: foreground, windowKind: .keyRequiring, focus: .target,
                backgroundPrepared: true, backgroundReportedActive: true,
                requirement: pinned) == foreground,
                "a foreground loss must stop this route even with a valid background preparation receipt")
        }
        precondition(!Readiness.ready(
            kind: .click, foreground: true, windowKind: .keyRequiring, focus: .otherWindow,
            backgroundPrepared: true, requirement: pinned))
        precondition(!Readiness.ready(
            kind: .click, foreground: true, windowKind: .identityConflict, focus: .target,
            backgroundPrepared: true, requirement: pinned))
        let mapping = WindowGeometry.WindowMapping(
            id: 71, pid: getpid(), frame: CGRect(x: 0, y: 0, width: 100, height: 100),
            title: nil, width: 100, height: 100)
        let unit = NativePointerUnit(
            application: .current, window: mapping, pointerRequirement: pinned)
        let weakAXQualification = try unit.addressedPointerReceiver(at: .zero)
        precondition(weakAXQualification == .originalWindowPoint,
                     "an absent AX window cannot erase the retained WindowServer route")
        // No real AX object is supplied: fixed delivery must not re-query a role
        // or fall through to HID after qualification was already retained.
        let fixedDelivery = try unit.resolvedDelivery(nil, at: .zero, kind: .click)
        precondition(fixedDelivery == .windowDirected)
        precondition(!unit.backgroundPreparationAvailable())
        let retained = try unit.resolvingCaptureTranslation()
        precondition(retained.pointerRequirement == pinned)
        let offer = NativeClickContract(
            routeFamily: "macos.windowDirected.v1", delivery: .windowDirected, admission: .preparationRequired,
            reason: nil, preparationOptions: ["foreground"], pointerRequirement: pinned)
        var other = offer
        other.pointerRequirement = .contentReceiver
        precondition(offer != other, "native primitive reuse must preserve the selected route requirement")
        precondition(offer.permitsElementPointer(hasActuator: false),
                     "a retained window-directed requirement may enter the original-element unit")
        precondition(!offer.permitsElementPointer(hasActuator: true),
                     "the new exact-original-element requirement cannot adopt a member")
        let global = NativeClickContract(
            routeFamily: "macos.hid.v1", delivery: .systemPointer, admission: .preparationRequired,
            reason: nil, preparationOptions: ["foreground"], pointerRequirement: pinned)
        precondition(!global.permitsElementPointer(hasActuator: false),
                     "the new requirement cannot change its selected delivery to global input")
        precondition(other.permitsElementPointer(hasActuator: true),
                     "existing declared-content member routes retain their own admission contract")
    }

    private enum ForegroundLost: Error { case beforeNextInput }

    @MainActor private static func foregroundPointRelease(afterDown: Bool) async throws {
        let request = try NativePointerIntent(
            unit: "pointer_sequence", kind: "click", parameters: ["x": 25, "y": 25, "count": 2], foreground: false)
        let sequence = try NativePointerEvents(
            request: request, from: CGPoint(x: 25, y: 25), to: nil, viewport: CGSize(width: 100, height: 100),
            source: CGEventSource(stateID: .privateState)!)
        var foreground = true
        var posted = [(CGEventType, NativeClickDelivery)]()
        var releases = 0
        let delivery = NativeClickDelivery.windowDirected
        do {
            try await sequence.deliver(
                check: { _, _ in
                    guard NativePointerReadiness.ready(
                        kind: .click, foreground: foreground, windowKind: .keyRequiring, focus: .target,
                        backgroundPrepared: true, backgroundReportedActive: true,
                        requirement: .foregroundWindowPoint) else { throw ForegroundLost.beforeNextInput }
                },
                post: { event, _ in
                    posted.append((event.type, delivery))
                    if event.type == (afterDown ? .leftMouseDown : .mouseMoved) { foreground = false }
                }, canRelease: { releases += 1; return true }, skipped: { preconditionFailure("release skipped") },
                onDispatch: {}, onPosted: {})
            preconditionFailure("focus loss must stop before the next requested down")
        } catch ForegroundLost.beforeNextInput { }
        precondition(posted.map { $0.0 } == (afterDown ? [.mouseMoved, .leftMouseDown, .leftMouseUp] : [.mouseMoved]))
        precondition(posted.allSatisfy { $0.1 == .windowDirected }, "release must retain the original process channel")
        precondition(releases == (afterDown ? 1 : 0), "held input must release without admitting another click")
    }
}
