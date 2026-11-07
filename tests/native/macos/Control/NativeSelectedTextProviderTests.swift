// Copyright (c) Huawei Technologies Co., Ltd. 2026. All rights reserved.

import AppKit
import ApplicationServices

@main enum NativeSelectedTextProviderTests {
    typealias Qualification = NativeSelectedTextProviderQualification

    static let installedSystem = Qualification.SystemImplementation(
        bundleIdentifier: "com.apple.WebKit.WebContent",
        bundlePath: "/System/Volumes/Preboot/Cryptexes/OS/System/Library/Frameworks/WebKit.framework/Versions/A/XPCServices/com.apple.WebKit.WebContent.xpc",
        executable: "/System/Volumes/Preboot/Cryptexes/OS/System/Library/Frameworks/WebKit.framework/Versions/A/XPCServices/com.apple.WebKit.WebContent.xpc/Contents/MacOS/com.apple.WebKit.WebContent",
        version: "21624.4.5.11.5")

    static func process(
        pid: pid_t = 202, seconds: UInt64 = 100, microseconds: UInt64 = 17,
        executable: String = installedSystem.executable
    ) -> NativeProcessIdentity {
        NativeProcessIdentity(pid: pid, parent: 101, seconds: seconds, microseconds: microseconds, executable: executable)
    }

    /// Facts represent one fresh, read-only sample. The system implementation
    /// is the query adapter's fixed system-framework resolution, not a bundle
    /// selected by an app name or the caller.
    struct Sample {
        var presenterPID: pid_t = 101
        var actualPID: pid_t? = 202
        var process: NativeProcessIdentity? = NativeSelectedTextProviderTests.process()
        var currentProcess: NativeProcessIdentity? = NativeSelectedTextProviderTests.process()
        var currentActualPID: pid_t? = 202
        var resolvedExecutable: String? = installedSystem.executable
        var runningBundleIdentifier: String? = installedSystem.bundleIdentifier
        var runningBundlePath: String? = installedSystem.bundlePath
        var system: Qualification.SystemImplementation? = installedSystem

        var facts: Qualification.Facts {
            .init(
                presenterPID: presenterPID, actualPID: actualPID, process: process,
                currentProcess: currentProcess, currentActualPID: currentActualPID,
                resolvedExecutable: resolvedExecutable, runningBundleIdentifier: runningBundleIdentifier,
                runningBundlePath: runningBundlePath, system: system)
        }

        var proof: Qualification.Proof? { Qualification.knownEmptySetter(facts) }
    }

    @MainActor static func main() throws {
        knownRemoteSystemImplementationIsExcluded()
        unrelatedAndUnknownProvidersKeepTheirCandidates()
        providerChangesCannotInheritAnExclusion()
        redundantBundleMetadataDoesNotReplaceExecutableEvidence()
        try chromiumWebDelegateIsDistinctFromViews()
        geckoAtomicReplacementWithdrawsOnlyTheBrokenSetter()
        relatedEditorRemainsAKeyboardReceiver()
        try providerWindowUsesFixedIdentityWithoutWeakeningTextEvidence()
        try qualificationRefreshPrecedesPreparation()
        try onlyPreInputRouteRefusalsPermitContinuation()
        print("PASS selected-text implementation qualification preserves native Views and related keyboard receivers")
    }

    @MainActor static func geckoAtomicReplacementWithdrawsOnlyTheBrokenSetter() {
        typealias Provider = NativeGeckoTextProvider
        let marker = "AXMozDebugDescriptionForTextMarker"
        let range = "AXMozDebugDescriptionForTextMarkerRange"
        precondition(Provider.exposesContract([marker, range]))
        precondition(!Provider.exposesContract([marker, "AXReplaceRangeWithText"]))
        precondition(!Provider.exposesContract(nil))

        let field = AXUIElementCreateApplication(811)
        let document = AXUIElementCreateApplication(812)
        let window = AXUIElementCreateApplication(813)
        let proof = Provider.Proof(element: field, document: document, window: window, pid: 811)
        let same = Provider.Proof(element: AXUIElementCreateApplication(811), document: document, window: window, pid: 811)
        let changed = Provider.Proof(element: field, document: document, window: field, pid: 811)
        precondition(proof == same && proof != changed)
        let contract = NativeElementControl.TextValueContract(
            role: kAXTextFieldRole, subrole: kAXSearchFieldSubrole, gecko: proof)
        precondition(contract.independent && contract.unit == "native_text_edit")
        precondition(Provider.needsAtomicReplacement(text: "网络", value: "代理"))
        precondition(!Provider.permitsSelectedText(
            replace: true, text: "网络", value: "代理", selection: nil, pinnedSelection: nil),
            "a known split setter must not be dispatched when replacing a populated field")
        precondition(Provider.needsAtomicReplacement(text: "网络", value: nil))
        precondition(!Provider.permitsSelectedText(
            replace: true, text: "网络", value: nil, selection: nil, pinnedSelection: nil),
            "an unreadable value must not revive the known split setter")
        precondition(Provider.permitsSelectedText(
            replace: true, text: "网络", value: "", selection: nil, pinnedSelection: nil))
        precondition(Provider.permitsSelectedText(
            replace: true, text: "", value: "代理", selection: nil, pinnedSelection: nil))

        let start = NativeTextSelection(count: 2, location: 0, length: 0)
        let end = NativeTextSelection(count: 2, location: 2, length: 0)
        precondition(Provider.permitsSelectedText(
            replace: false, text: "网", value: "代理", selection: start, pinnedSelection: nil))
        precondition(!Provider.permitsSelectedText(
            replace: false, text: "网", value: "代理", selection: end, pinnedSelection: nil))
        precondition(!Provider.permitsSelectedText(
            replace: false, text: "网", value: "", selection: end, pinnedSelection: nil),
            "a fresh nonzero caret is stronger than an older empty value")
        precondition(Provider.permitsSelectedText(
            replace: false, text: "网", value: "", selection: nil, pinnedSelection: nil))
        precondition(!Provider.permitsSelectedText(
            replace: false, text: "网", value: "代理", selection: nil, pinnedSelection: nil))
        precondition(!Provider.permitsSelectedText(
            replace: false, text: "网", value: "代理", selection: start, pinnedSelection: end),
            "a live caret at zero cannot authorize an insertion pinned at offset two")
    }

    @MainActor static func providerWindowUsesFixedIdentityWithoutWeakeningTextEvidence() throws {
        typealias Evidence = NativeProviderTextValueEvidence
        precondition(Evidence.reachesFixedWindow(
            sameProxy: false, role: "AXWindow", owner: 10, presenter: 10, currentID: 70, expectedID: 70))
        for (role, owner, id): (String, Int32, UInt32) in [
            ("AXWindow", 10, 71), ("AXWindow", 11, 70), ("AXSheet", 10, 70),
            ("AXPopover", 10, 70), ("AXGroup", 10, 70)
        ] {
            precondition(!Evidence.reachesFixedWindow(
                sameProxy: false, role: role, owner: owner, presenter: 10, currentID: id, expectedID: 70))
        }
        let element = AXUIElementCreateApplication(1_000_011)
        var queries = 0
        let missing = try NativeProviderTextValueQuery.proof(
            element: element, originalWindow: { queries += 1; return nil }, presenterPID: 10,
            role: kAXTextFieldRole, subrole: .absent, valueWritable: true)
        precondition(missing == nil && queries == 0, "ordinary/unknown providers must not acquire window prerequisites")
        var facts = Evidence.Facts(
            role: "AXTextField", subrole: .absent, valueWritable: true, enabled: true,
            editable: .absent, valueUTF16Length: 4, rangeLocation: 0, rangeLength: 1,
            bounds: .rectangle(CGRect(x: 0, y: 0, width: 8, height: 16)),
            scope: .init(
                presenterPID: 10, elementPublicPID: 10, elementActualPID: 10, windowPublicPID: 10,
                webAreaPublicPID: 10, webAreaActualPID: 10, originalWindowID: 70,
                elementWindowID: 70, webAreaWindowID: 70, reachedOriginalWindow: true,
                exactlyOneWebArea: true, elementProviderNodeID: "402", webAreaProviderNodeID: "12"))
        precondition(Evidence.prove(facts) != nil)
        facts.valueUTF16Length = 0
        precondition(Evidence.prove(facts) == nil, "window proxy refresh must not broaden empty-field text semantics")
        facts.valueUTF16Length = 4
        facts.bounds = .rectangle(.zero)
        precondition(Evidence.prove(facts) == nil, "native Views' zero bounds are not callback evidence")
        facts.bounds = .rectangle(CGRect(x: 0, y: 0, width: 8, height: 16))
        facts.scope.elementWindowID = 71
        precondition(Evidence.prove(facts) == nil, "another physical window cannot inherit provider editing evidence")
    }

    static func chromiumWebDelegateIsDistinctFromViews() throws {
        struct Node {
            var role: String?
            var providerID: String?
            var actualPID: pid_t? = 100
            var parent: Int?
        }
        let baseline = [
            1: Node(role: "AXTextField", providerID: "402", parent: 2),
            2: Node(role: "AXGroup", providerID: "396", parent: 3),
            3: Node(role: "AXWebArea", providerID: "12", parent: 4),
            4: Node(role: "AXWindow", providerID: nil, parent: nil)
        ]
        func prove(_ nodes: [Int: Node]) -> Qualification.ChromiumWebProof? {
            Qualification.chromiumWebImplementation(
                element: 1, same: ==, role: { nodes[$0]?.role },
                providerNodeID: { nodes[$0]?.providerID }, actualPID: { nodes[$0]?.actualPID },
                parent: { nodes[$0]?.parent }, check: {})
        }
        let proof = prove(baseline)
        precondition(proof?.elementNodeID == 402 && proof?.webAreaNodeID == 12 && proof?.actualPID == 100)
        // Native Views controls also expose ChromeAXNodeId, but their delegate
        // implements ReplaceSelectedText. Web ancestry is part of this proof.
        var nativeViews = baseline
        nativeViews[3]?.role = "AXGroup"
        precondition(prove(nativeViews) == nil)
        var unknown = baseline
        unknown[1]?.providerID = nil
        precondition(prove(unknown) == nil, "WebKit/Firefox/native Web ancestry alone cannot exclude their setter")
        unknown = baseline; unknown[3]?.providerID = nil
        precondition(prove(unknown) == nil)
        unknown = baseline; unknown[3]?.actualPID = 101
        precondition(prove(unknown) == nil, "a different process's WebArea cannot qualify this element")
        unknown = baseline; unknown[2]?.parent = 1
        precondition(prove(unknown) == nil, "cyclic ancestry is unknown")
        unknown = baseline; unknown[2]?.parent = nil
        precondition(prove(unknown) == nil)
        unknown = baseline; unknown[2]?.role = "AXWindow"
        precondition(prove(unknown) == nil, "do not cross a native window boundary to borrow a Web provider")
        for value in ["", "-1", "0", "402x", "2147483648"] {
            unknown = baseline; unknown[1]?.providerID = value
            precondition(prove(unknown) == nil)
        }
        struct Cancelled: Error {}
        do {
            _ = try Qualification.chromiumWebImplementation(
                element: 1, same: ==, role: { baseline[$0]?.role },
                providerNodeID: { baseline[$0]?.providerID }, actualPID: { baseline[$0]?.actualPID },
                parent: { baseline[$0]?.parent }, check: { throw Cancelled() })
            preconditionFailure("provider qualification swallowed cancellation")
        } catch is Cancelled { }
    }

    @MainActor static func relatedEditorRemainsAKeyboardReceiver() {
        let editor = AXUIElementCreateApplication(1_000_001)
        let activeDescendant = AXUIElementCreateApplication(1_000_002)
        let unrelated = AXUIElementCreateApplication(1_000_003)
        let current = NativeReceiverSnapshot(
            windowRoute: "yes", focus: activeDescendant, ancestors: [], foreground: false, relatedEditor: editor)
        precondition(current.receiver(editor) == "yes", "provider qualification must not remove ARIA text ownership")
        precondition(current.receiver(unrelated) == "no")
        precondition(!NativeKeyboardInterpreterEvidence.needsTextTransport(kind: "paste", text: "path"))
        precondition(!NativeKeyboardInterpreterEvidence.needsTextTransport(kind: "key", text: "Return"))
        precondition(NativeKeyboardInterpreterEvidence.needsTextTransport(kind: "replace", text: "path"))
    }

    @MainActor static func qualificationRefreshPrecedesPreparation() throws {
        var unknown = Sample()
        unknown.actualPID = nil
        try Qualification.requireAvailable(unknown.proof)
        // A later metadata query can identify the original provider. The real
        // focus boundary must call validation before any preparation or setter.
        var preparation = 0, setters = 0
        let element = AXUIElementCreateApplication(202)
        do {
            try NativeReceiverPreparation.setFocus(
                element, detail: "must not focus this disqualified route",
                check: { try Qualification.requireAvailable(Sample().proof) },
                onPreparation: { _ in preparation += 1 },
                set: { _, _, _ in setters += 1; return .success })
            preconditionFailure("known empty setter reached preparation")
        } catch is NativeRouteUnavailable { }
        precondition(preparation == 0 && setters == 0)
        // Unknown remains ordinary qualification, not a new refusal.
        try NativeReceiverPreparation.setFocus(
            element, detail: "ordinary route",
            check: { try Qualification.requireAvailable(unknown.proof) },
            onPreparation: { _ in preparation += 1 },
            set: { _, _, _ in setters += 1; return .success })
        precondition(preparation == 1 && setters == 1)
    }

    @MainActor static func onlyPreInputRouteRefusalsPermitContinuation() throws {
        let unavailable = NativeRouteUnavailable(description: "positive implementation qualification")
        for kind in ["prepare_receiver", "native_text_edit"] {
            for state in ["clean", "prepared", "unknown", "partial", "cleanup", "priorInput", "priorCleanup"] {
                let op = NativeOperation(
                    timeoutMs: 1000, writable: true,
                    intent: try NativeIntent(["kind": "insert", "parameters": ["text": "x"]], mode: "write"),
                    target: [:], release: {}, cancelActive: {})
                defer { _ = op.finish() }
                let receipt = NativeExecutionReceipt(target: [:], onEffect: {})
                if state == "prepared" { receipt.recordPreparation("native element focus") }
                if state == "unknown" { receipt.dispatch = "unknown" }
                if state == "partial" { receipt.recordAccepted() }
                if state == "cleanup" { receipt.recordCleanupUnknown() }
                if state == "priorInput" { op.recordInputDispatch() }
                if state == "priorCleanup" { op.recordUnconfirmedCleanup() }
                BuildNativeFailureReceiptPass.run(
                    unavailable, kind: kind, foreground: false, id: "route", op: op, receipt: receipt)
                precondition(receipt.errorCode == .inputUnavailable)
                precondition(
                    receipt.retryReason == (["clean", "prepared"].contains(state) ? "routeUnavailable" : nil), state)
                do {
                    try op.requireForegroundPreparation("route")
                    preconditionFailure("route refusal must not authorize foreground preparation")
                } catch { }
            }
        }
        for code in [FailureCode.inputUnavailable, .staleTarget, .blockedByModal] {
            let op = NativeOperation(
                timeoutMs: 1000, writable: true,
                intent: try NativeIntent(["kind": "insert", "parameters": ["text": "x"]], mode: "write"),
                target: [:], release: {}, cancelActive: {})
            defer { _ = op.finish() }
            let receipt = NativeExecutionReceipt(target: [:], onEffect: {})
            BuildNativeFailureReceiptPass.run(
                DriverError("ordinary boundary or contract change", code: code),
                kind: "prepare_receiver", foreground: false, id: "route", op: op, receipt: receipt)
            precondition(receipt.errorCode == code && receipt.retryReason == nil)
        }
    }

    static func knownRemoteSystemImplementationIsExcluded() {
        let sample = Sample()
        guard let proof = sample.proof else {
            preconditionFailure("the independently resolved system WebContent implementation must qualify")
        }
        precondition(proof.process == sample.process)
        precondition(proof.system == installedSystem)
        precondition(proof.diagnostic["actualPID"] as? pid_t == 202)
        precondition(proof.diagnostic["incarnation"] as? String == sample.process?.incarnation)
        precondition(proof.diagnostic["executable"] as? String == installedSystem.executable)
        precondition(proof.diagnostic["providerVersion"] as? String == installedSystem.version)
    }

    static func unrelatedAndUnknownProvidersKeepTheirCandidates() {
        var local = Sample()
        local.presenterPID = 202
        precondition(local.proof == nil, "same-process objects do not acquire the remote provider restriction")

        for (identifier, executable) in [
            ("org.mozilla.firefox", "/Applications/Firefox.app/Contents/MacOS/firefox"),
            ("org.chromium.Chromium.helper", "/Applications/Chromium.app/Contents/Frameworks/Chromium Framework.framework/Versions/1.0/Helpers/Chromium Helper.app/Contents/MacOS/Chromium Helper"),
            ("example.AppKitEditor", "/Applications/Example.app/Contents/MacOS/Example")
        ] {
            var other = Sample()
            other.runningBundleIdentifier = identifier
            other.process = process(executable: executable)
            other.currentProcess = other.process
            other.resolvedExecutable = executable
            other.runningBundlePath = nil
            precondition(other.proof == nil, "a Web ancestry or text role cannot exclude another provider's real setter")
        }

        var missing: [Sample] = []
        var unknown = Sample(); unknown.actualPID = nil; missing.append(unknown)
        unknown = Sample(); unknown.process = nil; missing.append(unknown)
        unknown = Sample(); unknown.currentProcess = nil; missing.append(unknown)
        unknown = Sample(); unknown.currentActualPID = nil; missing.append(unknown)
        unknown = Sample(); unknown.resolvedExecutable = nil; missing.append(unknown)
        unknown = Sample(); unknown.runningBundleIdentifier = nil; missing.append(unknown)
        unknown = Sample(); unknown.system = nil; missing.append(unknown)
        unknown = Sample(); unknown.presenterPID = 0; missing.append(unknown)
        unknown = Sample(); unknown.actualPID = 0; missing.append(unknown)
        for sample in missing {
            precondition(sample.proof == nil, "unknown provider facts must preserve the previous route candidates")
        }
    }

    static func providerChangesCannotInheritAnExclusion() {
        var changed = Sample()
        changed.currentProcess = process(seconds: 101)
        precondition(changed.proof == nil, "PID reuse cannot inherit a prior incarnation's qualification")
        changed.currentProcess = process(microseconds: 18)
        precondition(changed.proof == nil)

        changed = Sample()
        changed.currentActualPID = 203
        precondition(changed.proof == nil, "the original object must still name the sampled provider")
        changed = Sample()
        changed.currentProcess = process(executable: "/tmp/replaced-provider")
        precondition(changed.proof == nil, "an executable change invalidates the sampled process identity")

        changed.process = changed.currentProcess
        changed.resolvedExecutable = "/tmp/replaced-provider"
        precondition(changed.proof == nil, "a fresh non-system executable does not inherit an old system match")
        changed = Sample()
        changed.process = process(pid: 203)
        changed.currentProcess = changed.process
        precondition(changed.proof == nil, "a different process cannot supply evidence for the original AX PID")

        let fresh = Sample()
        precondition(fresh.proof != nil, "qualification has no sticky failure or process-wide deny state")
    }

    static func redundantBundleMetadataDoesNotReplaceExecutableEvidence() {
        var sample = Sample()
        sample.runningBundlePath = nil
        precondition(sample.proof != nil, "remote services can lack a redundant NSRunningApplication bundle URL")
        sample.runningBundlePath = "/Applications/Unrelated.app"
        precondition(sample.proof == nil, "conflicting bundle metadata cannot identify the system implementation")

        let copiedBundle = "/Applications/com.apple.WebKit.WebContent.xpc"
        let copiedExecutable = copiedBundle + "/Contents/MacOS/com.apple.WebKit.WebContent"
        sample = Sample()
        sample.process = process(executable: copiedExecutable)
        sample.currentProcess = sample.process
        sample.resolvedExecutable = copiedExecutable
        sample.runningBundlePath = copiedBundle
        precondition(sample.proof == nil, "a matching bundle identifier or basename is not a system implementation")
        sample.runningBundlePath = nil
        precondition(sample.proof == nil, "missing bundle URL must not weaken exact executable evidence")
        sample.runningBundlePath = installedSystem.bundlePath
        precondition(sample.proof == nil, "reported system bundle metadata cannot override a different executable")
    }
}
