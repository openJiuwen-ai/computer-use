// Copyright (c) Huawei Technologies Co., Ltd. 2026. All rights reserved.

import AppKit
import ApplicationServices

@main enum NativeWindowScrollExposureTests {
    @MainActor static func main() async throws {
        precondition(NativeElementViewport.clips(role: kAXTableRole, distance: 1, elementRole: kAXRowRole))
        precondition(!NativeElementViewport.clips(role: kAXTableRole, distance: 2, elementRole: kAXRowRole))
        precondition(!NativeElementViewport.clips(role: kAXTableRole, distance: 1, elementRole: kAXButtonRole))
        precondition(NativeElementViewport.clips(role: kAXScrollAreaRole, distance: 2, elementRole: kAXButtonRole))
        let window = CGRect(x: -1138, y: 189, width: 440, height: 700)
        let bar = CGRect(x: -704, y: 221, width: 6, height: 596)
        let target = CGRect(x: -1068, y: 1032, width: 78, height: 22)
        let candidate = NativeWindowScrollExposure.candidate(
            frame: target, bar: bar, window: window, orientation: kAXVerticalOrientationValue)!
        precondition(candidate.viewport.minY == 221 && candidate.viewport.maxY == 817)
        precondition(bar.contains(candidate.point), "wheel receiver is the real bar, never an editable form field")
        precondition(candidate.direction == "down" && candidate.pages <= 1)
        let noOrientation = NativeWindowScrollExposure.candidate(
            frame: target, bar: bar, window: window, orientation: nil)
        precondition(noOrientation?.direction == "down", "omitted orientation does not erase a nonsquare native bar")
        precondition(NativeWindowScrollExposure.candidate(
            frame: target, bar: bar.offsetBy(dx: 1000, dy: 0), window: window, orientation: nil) == nil,
            "another window's bar cannot supply a receiver")
        precondition(NativeWindowScrollExposure.candidate(
            frame: target.offsetBy(dx: 1000, dy: 0), bar: bar, window: window, orientation: nil) == nil,
            "a vertical bar cannot expose a target in another horizontal extent")
        precondition(NativeWindowScrollExposure.candidate(
            frame: target, bar: bar, window: window, orientation: kAXHorizontalOrientationValue) == nil)
        let visible = target.offsetBy(dx: 0, dy: -400)
        precondition(NativeWindowScrollExposure.candidate(
            frame: visible, bar: bar, window: window, orientation: nil) == nil,
            "already exposed targets do not get unnecessary scroll preparation")

        let none = CGRect.null
        precondition(!NativeElementViewport.progressed(
            frame: target, clipped: none, window: window,
            from: target, originalWindow: window, viewport: candidate.viewport),
            "a disappearing overlay bar with unchanged offscreen target is not progress")
        precondition(!NativeElementViewport.progressed(
            frame: target.offsetBy(dx: 0, dy: 100), clipped: none, window: window,
            from: target, originalWindow: window, viewport: candidate.viewport),
            "scrolling the original object farther away must stop")
        precondition(NativeElementViewport.progressed(
            frame: target.offsetBy(dx: 0, dy: -100), clipped: none, window: window,
            from: target, originalWindow: window, viewport: candidate.viewport))
        precondition(NativeElementViewport.progressed(
            frame: visible, clipped: visible.intersection(candidate.viewport), window: window,
            from: target, originalWindow: window, viewport: candidate.viewport))
        precondition(!NativeElementViewport.progressed(
            frame: target.offsetBy(dx: 50, dy: -100), clipped: none, window: window.offsetBy(dx: 50, dy: -100),
            from: target, originalWindow: window, viewport: candidate.viewport),
            "whole-window translation is not progress of its scrolling content")
        precondition(NativeElementViewport.progressed(
            frame: target.offsetBy(dx: 50, dy: -200), clipped: none, window: window.offsetBy(dx: 50, dy: -100),
            from: target, originalWindow: window, viewport: candidate.viewport),
            "scroll progress remains measurable if the same window also moves")
        precondition(NativeWindowScrollExposure.probePoints(frame: target, viewport: window).count == 9)
        precondition(NativeWindowScrollExposure.probePoints(frame: visible, viewport: window).isEmpty)
        precondition(NativeWindowScrollExposure.probePoints(
            frame: target.offsetBy(dx: 1000, dy: 0), viewport: window).isEmpty)
        for role in [kAXTextFieldRole, kAXTextAreaRole, kAXSliderRole, kAXIncrementorRole, kAXPopUpButtonRole] {
            precondition(!NativeWindowScrollExposure.passiveWheelReceiver(role: role, actions: []),
                         "wheel preparation must not edit another control")
        }
        precondition(NativeWindowScrollExposure.passiveWheelReceiver(role: kAXStaticTextRole, actions: []))
        precondition(!NativeWindowScrollExposure.passiveWheelReceiver(role: kAXStaticTextRole, actions: [kAXPressAction]))
        precondition(!NativeWindowScrollExposure.passiveWheelReceiver(role: kAXStaticTextRole, actions: nil))
        try clippedLeafReveal()
        try await hoverReplacesProbeReceiver()
        print("Window scroll exposure regressions passed")
    }

    @MainActor private static func clippedLeafReveal() throws {
        typealias Clip = NativeElementViewport.VerticalClip
        let viewport = CGRect(x: 22, y: 139, width: 1200, height: 690)
        let sliver = CGRect(x: 988, y: 828, width: 27, height: 1)
        precondition(Clip.edge(frame: sliver, clipped: sliver, viewport: viewport) == .bottom,
                     "provider-reported one-point edge is still clipped even when frame equals intersection")
        let top = CGRect(x: 988, y: 139, width: 27, height: 1)
        precondition(Clip.edge(frame: top, clipped: top, viewport: viewport) == .top)
        let unrelated = sliver.offsetBy(dx: 0, dy: -1)
        precondition(Clip.edge(frame: unrelated, clipped: unrelated, viewport: viewport) == nil)
        let full = CGRect(x: 988, y: 503, width: 27, height: 17)
        precondition(Clip.edge(frame: full, clipped: full, viewport: viewport) == nil)
        precondition(Clip.edge(frame: sliver, clipped: sliver,
                               viewport: CGRect(x: 22, y: 52, width: 1200, height: 780)) == nil,
                     "a window edge is not evidence of a scroll viewport clipping edge")
        precondition(!Clip.isExposed(frame: sliver, clipped: sliver, viewport: viewport))
        precondition(!Clip.isExposed(frame: full, clipped: sliver, viewport: viewport))
        precondition(Clip.isExposed(frame: full, clipped: full, viewport: viewport))

        let proof = NativeClickClippedLeaf(
            region: AXUIElementCreateSystemWide(), viewport: viewport, edge: .bottom, window: 42, pid: 7)
        let offer = proof.offer
        precondition(offer.delivery == .windowDirected && offer.admission == .preparationRequired)
        precondition(offer.preparationOptions == ["background"] && offer.requiresExactElementHit)
        precondition(!offer.permitsElementPointer(hasActuator: true))
        let operation = NativeOperation(
            timeoutMs: 1000, writable: true, intent: try NativeIntent(["kind": "click", "parameters": [:]], mode: "write"),
            target: [:],
            clickContract: "v2", release: {}, cancelActive: {})
        let evidence = NativeClickEvidence(target: [:])
        let receipt = NativeExecutionReceipt(target: [:], onEffect: {})
        var calls = 0
        do {
            try proof.performOnce(operation: operation, receipt: receipt, evidence: evidence) { onCall in
                onCall()
                calls += 1
                return .cannotComplete
            }
            preconditionFailure("unconfirmed reveal must fail")
        } catch let error as DriverError {
            precondition(error.code == .inputUnavailable)
        }
        precondition(operation.didRevealClippedClickLeaf && calls == 1)
        precondition(receipt.dispatch == "notDispatched" && receipt.preparation.count == 1)
        precondition(!evidence.permitsReselection, "no geometry progress means no blind retry")
        do {
            try proof.performOnce(operation: operation, receipt: receipt, evidence: evidence) { _ in
                preconditionFailure("an admitted reveal can run at most once per public operation")
            }
            preconditionFailure("another reveal must be refused")
        } catch let error as DriverError {
            precondition(error.code == .inputUnavailable)
        }
    }

    @MainActor private static func hoverReplacesProbeReceiver() async throws {
        let request = try NativePointerIntent(
            unit: "pointer_sequence", kind: "scroll", parameters: ["direction": "down", "pages": 0.5],
            foreground: false, element: true)
        let sequence = try NativePointerEvents(
            request: request, from: CGPoint(x: 100, y: 100), to: nil, viewport: CGSize(width: 400, height: 600),
            source: Input.source())
        var role = kAXStaticTextRole, posted = [CGEventType](), effect = false
        let receipt = NativeExecutionReceipt(target: [:], onEffect: { effect = true })
        do {
            try await sequence.deliver(
                check: { _, _ in
                    guard NativeWindowScrollExposure.passiveWheelReceiver(role: role, actions: []) else {
                        throw DriverError("probe receiver changed", code: .inputUnavailable)
                    }
                }, post: { event, _ in
                    posted.append(event.type)
                    if event.type == .mouseMoved { role = kAXTextFieldRole }
                }, canRelease: { true }, skipped: {},
                onDispatch: { receipt.recordPreparation("scroll probe") }, onPosted: {})
            preconditionFailure("hovering into an editor must stop the subsequent wheel")
        } catch let error as DriverError {
            precondition(error.code == .inputUnavailable)
        }
        precondition(posted == [.mouseMoved], "the new editor must not receive a wheel")
        precondition(effect && !receipt.preparation.isEmpty, "the preceding preparation effect stays recorded")
    }
}
