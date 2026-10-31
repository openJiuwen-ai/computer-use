// Copyright (c) Huawei Technologies Co., Ltd. 2026. All rights reserved.

import AppKit
import ApplicationServices

@main enum NativeUnifiedPointerPolicyTests {
    @MainActor static func main() throws {
        typealias Readiness = NativePointerReadiness
        // A transport preparation record is not a native content receiver.
        precondition(
            !Readiness.ready(
                kind: .click, foreground: false, windowKind: .keyRequiring, focus: .target, backgroundPrepared: true,
                backgroundWindowFocused: false))
        precondition(
            !Readiness.ready(
                kind: .click, foreground: false, windowKind: .keyRequiring, focus: .target, backgroundPrepared: true))
        precondition(
            !Readiness.ready(
                kind: .click, foreground: false, windowKind: .keyRequiring, focus: .otherWindow,
                backgroundPrepared: true, backgroundWindowFocused: true, backgroundReportedActive: true))
        precondition(
            Readiness.ready(
                kind: .click, foreground: false, windowKind: .keyRequiring, focus: .target, backgroundPrepared: true,
                backgroundWindowFocused: true, backgroundReportedActive: true))
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
        precondition(
            !Readiness.ready(
                kind: .click, foreground: false, windowKind: .keyRequiring, focus: .target, backgroundPrepared: true,
                backgroundWindowFocused: true, backgroundReportedActive: false),
            "a background document can report window focus without an internally active content receiver")
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
}
