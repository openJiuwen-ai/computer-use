// Copyright (c) Huawei Technologies Co., Ltd. 2026. All rights reserved.

import AppKit
import ApplicationServices

@main enum NativeWriteStageTests {
    @MainActor static func main() throws {
        typealias Hits = NativeElementHitSelection
        typealias Evidence = NativeElementHitEvidence
        let points = Hits.candidates(bounds: CGRect(x: 10, y: 20, width: 100, height: 30), activationPoint: nil)
        func sample(_ relation: Evidence.Relation, foreground: Bool) -> Hits.Selection<Int> {
            Hits.select([points[0]], canContinue: { _ in true }) { _ in
                .init(evidence: .init(status: .success, relation: relation), hit: 1, foreground: foreground)
            }!
        }
        let initial = sample(.windowRoot, foreground: false)
        precondition(
            initial.basis == .rejected && Hits.preparation(initial) == .foreground,
            "a valid window-root point can admit preparation but never dispatch")
        let confirmed = sample(.coarseAncestorWindow, foreground: true)
        precondition(confirmed.basis == .coarseAncestorGeometry && Hits.preparation(confirmed) == .geometryReady)
        try retainedElementPreparation(initial: initial, confirmed: confirmed)
        precondition(sample(.target, foreground: true).basis == .exactHit)
        precondition(
            sample(.windowRoot, foreground: true).basis == .rejected,
            "foreground status alone cannot turn an unconfirmed hit into dispatch evidence")
        for relation in [Evidence.Relation.otherControl, .unknownOwner] {
            let conflicting = sample(relation, foreground: false)
            precondition(conflicting.basis == .rejected && Hits.preparation(conflicting) == .unavailable)
        }
        let laterPoint = Hits.select(points, canContinue: { _ in true }) { candidate in
            Hits.Observation<Int>(
                evidence: .init(status: .success, relation: candidate.source == .center ? .otherControl : .windowRoot),
                hit: 1, foreground: false)
        }!
        precondition(
            laterPoint.candidate.source == .edge && laterPoint.basis == .rejected,
            "a preparable candidate must survive a rejected center without authorizing input")

        for kind in [NativePointerIntent.Kind.click, .scroll, .drag] {
            let waiting = NativeReadiness([("foreground", "unknown")], preparationAllowed: true)
            let declaration = NativeInputUnit.pointer(kind).declaration(
                id: "pointer", supported: true, readiness: waiting)
            precondition(
                declaration["supported"] as? String == "yes"
                    && declaration["readiness"] as? String == "unknown"
                    && declaration["backgroundPreparation"] as? String == "no")
            let blocked = NativeReadiness([("receiver", "no")], preparationAllowed: false)
            let rejected = NativeInputUnit.pointer(kind).declaration(
                id: "pointer", supported: true, readiness: blocked)
            precondition(
                rejected["supported"] as? String == "no"
                    && rejected["backgroundPreparation"] as? String == "no")
        }
        let context = NativeReadiness([("receiver", "unknown")])
        precondition(context.preparationAllowed && !context.ready)
        let pointerReady = NativeReadiness().awaitingReceiver()
        precondition(
            !pointerReady.ready && pointerReady.pending == ["receiver"] && pointerReady.preparationAllowed,
            "pointer readiness must not bypass the requested focus preparation")
        let coveredFocus = NativeReadiness([("foreground", "no")]).awaitingReceiver()
        precondition(coveredFocus.pending == ["foreground", "receiver"] && coveredFocus.preparationAllowed)
        let blockedFocus = NativeReadiness([("geometry", "unknown")], preparationAllowed: false).awaitingReceiver()
        precondition(!blockedFocus.preparationAllowed && blockedFocus.pending == ["geometry", "receiver"])
        let disabled = context.requiringEnabled(false)
        precondition(!disabled.preparationAllowed && disabled.pending.contains("enabled"))
        for unit in [NativeInputUnit.receiverContext(.init(role: "AXTextField")),
                     .pointerFocus(.editor), .pointerFocus(.scalarText)] {
            let blocked = unit.declaration(id: "focus", supported: true, readiness: disabled)
            precondition(blocked["supported"] as? String == "no")
        }
        let scalarFocus = NativeInputUnit.pointerFocus(.scalarText).declaration(
            id: "scalar", supported: true, readiness: NativeReadiness([("receiver", "no")]))
        precondition(scalarFocus["supported"] as? String == "yes"
                     && scalarFocus["globalInput"] as? String == "no",
                     "text-point preparation declares its actual window-directed transport")
        keyboardWindowPreparation()
        viewportPreparationContinuation()
    }

    @MainActor static func viewportPreparationContinuation() {
        func evidence() -> NativeClickEvidence {
            NativeClickEvidence(target: ["surface": "original-window", "node": "original-checkbox"])
        }
        let pending = evidence()
        pending.record(.init(kind: .viewport, phase: .willSend, detail: "wheel", window: 7))
        pending.confirmViewportProgress(window: 7)
        precondition(!pending.permitsReselection, "a pending native wheel cannot be recovered or replayed")

        let noProgress = evidence()
        noProgress.record(.init(kind: .viewport, phase: .willSend, detail: "wheel", window: 7))
        noProgress.record(.init(kind: .viewport, phase: .returned, detail: "wheel", window: 7))
        noProgress.confirmViewportProgress(window: 8)
        precondition(!noProgress.permitsReselection, "another window's geometry is not original-target progress")
        noProgress.confirmViewportProgress(window: 7)
        precondition(noProgress.permitsReselection)
        noProgress.fail(dispatch: "notDispatched", cleanup: "complete", retryReason: "preparationUnavailable")
        let wire = noProgress.wire
        let preparation = (wire["preparationEvidence"] as! [[String: Any]])[0]
        precondition(preparation["effect"] as? String == "contentPossible")
        precondition(preparation["continuation"] as? String == "withinScope")
        precondition((wire["continuation"] as! [String: Any])["kind"] as? String == "reselectWithinScope")
        noProgress.markCall("postToPid")
        precondition(!noProgress.permitsReselection, "entered requested input can never continue as preparation")
    }

    @MainActor static func retainedElementPreparation(
        initial: NativeElementHitSelection.Selection<Int>, confirmed: NativeElementHitSelection.Selection<Int>
    ) throws {
        typealias Receiver = NativeAddressedPointerReceiver
        typealias Hits = NativeElementHitSelection
        for facts in [Receiver(role: kAXTextFieldRole, actions: [], focusWritable: true),
                      Receiver(role: kAXButtonRole, actions: [])] {
            let qualification = facts.retainedElementQualification(
                basis: initial.basis, preparation: Hits.preparation(initial), exactOriginalHit: false, popup: false)!
            precondition(qualification == .originalElementForegroundGeometry)
            precondition(qualification.requirement(captured: true) == nil,
                         "a retained element cannot qualify an unrelated captured coordinate")
            let offer = NativeClickContract(
                routeFamily: "macos.windowDirected.v1", delivery: .windowDirected, admission: .preparationRequired,
                reason: nil, preparationOptions: ["foreground"],
                pointerRequirement: qualification.requirement(captured: false)!,
                requiresForegroundGeometry: qualification.requiresForegroundGeometry)
            precondition(offer.permitsElementPointer(hasActuator: false))
            precondition(!offer.permitsElementPointer(hasActuator: true))
            var weakened = offer
            weakened.requiresForegroundGeometry = false
            precondition(offer != weakened, "native contract identity must preserve the unfinished geometry obligation")
            precondition(SampleNativeClickPreparationPass.needed(offer, pointerReady: true, foreground: false),
                         "a previous background pointer lease cannot skip original-window exposure")
            precondition(!SampleNativeClickPreparationPass.needed(offer, pointerReady: true, foreground: true))
            precondition(SampleNativeClickPreparationPass.needed(offer, pointerReady: false, foreground: true))
            do {
                try NativeClickPreparationPipeline.requireBackgroundPreparation(
                    offer.pointerRequirement, requiresForegroundGeometry: offer.requiresForegroundGeometry)
                preconditionFailure("the original geometry obligation cannot enter local-only preparation")
            } catch is NativePreparationUnavailable { }
            precondition(facts.retainedElementQualification(
                basis: confirmed.basis, preparation: Hits.preparation(confirmed), exactOriginalHit: false,
                popup: false) == qualification)
            precondition(NativeElementPointerUnit.allowsDispatchBasis(
                confirmed.basis, requirement: offer.pointerRequirement, exactOriginalHit: false, hasMember: false),
                "post-preparation coarse ancestor/parent/frame proof must remain valid just as for physical input")
            precondition(!NativeElementPointerUnit.allowsDispatchBasis(
                .rejected, requirement: offer.pointerRequirement, exactOriginalHit: false, hasMember: false))
            for popup in [false, true] {
                precondition(facts.retainedElementQualification(
                    basis: .rejected, preparation: .unavailable, exactOriginalHit: false, popup: popup) == nil,
                    "a different control or unknown owner is not a window preparation route")
            }
            precondition(facts.retainedElementQualification(
                basis: initial.basis, preparation: Hits.preparation(initial), exactOriginalHit: false,
                popup: true) == nil, "popup choices retain their independent proof")

            let element = AXUIElementCreateApplication(getpid())
            let selected = NativeElementPointerUnit(application: .current, window: element, element: element)
                .requiring(offer.pointerRequirement, foregroundGeometry: offer.requiresForegroundGeometry)
            precondition(selected.pointerRequirement == .contentReceiver,
                         "element dispatch must not impose the separate exact-hit-only contract")
            for dx in [0.0, 20.0] {
                let mapping = WindowGeometry.WindowMapping(
                    id: 71, pid: getpid(), frame: CGRect(x: dx, y: 0, width: 100, height: 100),
                    title: nil, width: 100, height: 100)
                let pointer = selected.pointerUnit(mapping: mapping)
                precondition(pointer.pointerRequirement == .foregroundWindowPoint,
                             "fresh mappings must preserve foreground validation through final event delivery")
                precondition(CFEqual(selected.element, element) && CFEqual(selected.window, element))
                precondition(!NativePointerReadiness.ready(
                    kind: .click, foreground: false, windowKind: .keyRequiring, focus: .target,
                    backgroundPrepared: true, backgroundReportedActive: true, requirement: pointer.pointerRequirement),
                    "foreground loss after preparation cannot consume a stale background lease")
                precondition(NativePointerReadiness.ready(
                    kind: .click, foreground: true, windowKind: .keyRequiring, focus: .target,
                    backgroundPrepared: true, requirement: pointer.pointerRequirement))
            }
        }
    }

    @MainActor static func keyboardWindowPreparation() {
        precondition(NativeKeyboardIntent.usesWindowCommand(.paste, key: nil))
        precondition(NativeKeyboardIntent.usesWindowCommand(.key, key: "super+a"))
        precondition(!NativeKeyboardIntent.usesWindowCommand(.key, key: "Left"))
        precondition(!NativeKeyboardIntent.usesWindowCommand(.insert, key: nil))
    }
}
