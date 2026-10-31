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
        for unit in [NativeInputUnit.receiverContext(.init(role: "AXTextField")), .pointerFocus] {
            let blocked = unit.declaration(id: "focus", supported: true, readiness: disabled)
            precondition(blocked["supported"] as? String == "no")
        }
        keyboardWindowPreparation()
    }

    @MainActor static func keyboardWindowPreparation() {
        precondition(NativeKeyboardIntent.usesWindowCommand(.paste, key: nil))
        precondition(NativeKeyboardIntent.usesWindowCommand(.key, key: "super+a"))
        precondition(!NativeKeyboardIntent.usesWindowCommand(.key, key: "Left"))
        precondition(!NativeKeyboardIntent.usesWindowCommand(.insert, key: nil))
        typealias Facts = NativeKeyboardWindowEvidence.BackgroundSelection
        let cold = Facts(
            windowScope: true, windowCommand: true, foreground: false, standardWindow: true,
            focused: false, windowFocusWritable: false, appFocusWritable: false, main: true, mainWindowMatches: true,
            focusedWindowMatches: true)
        precondition(
            NativeKeyboardWindowEvidence.prefersForegroundBeforeSynthetic(cold),
            "an Element shortcut needs the same window command preparation as App/Window shortcuts")
        for key in [\Facts.windowScope, \Facts.windowCommand, \Facts.standardWindow] {
            var facts = cold
            facts[keyPath: key] = false
            precondition(!NativeKeyboardWindowEvidence.prefersForegroundBeforeSynthetic(facts))
        }
        for key in [\Facts.focused, \Facts.windowFocusWritable, \Facts.appFocusWritable] {
            for value: Bool? in [true, nil] {
                var facts = cold
                facts[keyPath: key] = value
                precondition(!NativeKeyboardWindowEvidence.prefersForegroundBeforeSynthetic(facts))
            }
        }
        for key in [\Facts.main, \Facts.mainWindowMatches, \Facts.focusedWindowMatches] {
            for value: Bool? in [false, nil] {
                var facts = cold
                facts[keyPath: key] = value
                precondition(!NativeKeyboardWindowEvidence.prefersForegroundBeforeSynthetic(facts))
            }
        }
    }
}
