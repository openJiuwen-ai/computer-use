// Copyright (c) Huawei Technologies Co., Ltd. 2026. All rights reserved.

import AppKit
import ApplicationServices

/// QUERY-only qualification of one provider's SelectedText content setter.
/// WebKit's remote text wrapper declares SelectedText writable but forwards it
/// to an empty implementation (WebKit-7624.4.5.11.1 / .12.1). Chromium's Cocoa
/// wrapper similarly exposes a setter whose BrowserAccessibility delegate does
/// not handle kReplaceSelectedText; its native Views textfield does handle it.
/// Each exclusion therefore identifies the actual implementation, not merely
/// a WebArea, writable attribute, focus report, or host application name.
enum NativeSelectedTextProviderQualification {
    static let webContentIdentifier = "com.apple.WebKit.WebContent"

    struct SystemImplementation: Equatable {
        let bundleIdentifier: String
        let bundlePath: String
        let executable: String
        let version: String?
    }

    struct Facts {
        let presenterPID: pid_t
        let actualPID: pid_t?
        let process: NativeProcessIdentity?
        let currentProcess: NativeProcessIdentity?
        let currentActualPID: pid_t?
        let resolvedExecutable: String?
        let runningBundleIdentifier: String?
        let runningBundlePath: String?
        let system: SystemImplementation?
    }

    struct Proof {
        let process: NativeProcessIdentity
        let system: SystemImplementation

        var diagnostic: [String: Any] {
            [
                "kind": "selected_text_candidate_excluded", "attribute": "AXSelectedText",
                "reason": "known_system_webkit_empty_content_setter", "actualPID": process.pid,
                "incarnation": process.incarnation, "executable": system.executable,
                "bundleIdentifier": system.bundleIdentifier, "systemBundle": system.bundlePath,
                "providerVersion": system.version as Any? ?? NSNull()
            ]
        }
    }

    struct ChromiumWebProof {
        let actualPID: pid_t
        let elementNodeID: Int32
        let webAreaNodeID: Int32

        var diagnostic: [String: Any] {
            [
                "kind": "selected_text_candidate_excluded", "attribute": "AXSelectedText",
                "reason": "chromium_web_delegate_unhandled_replace_selected_text", "actualPID": actualPID,
                "elementProviderNodeID": elementNodeID, "webAreaProviderNodeID": webAreaNodeID,
                "reviewedSourceVersion": "150.0.7871.250"
            ]
        }
    }

    /// Chromium 150.0.7871.250: AXPlatformNodeCocoa's SelectedText setter emits
    /// kReplaceSelectedText, but BrowserAccessibility::AccessibilityPerformAction
    /// falls through to false. Views Textfield handles this action, so a Chrome
    /// node ID alone is insufficient: prove this exact node's WebArea ancestry.
    /// Unknown ancestry preserves the candidate; it is not a general Web gate.
    static func chromiumWebImplementation<Node>(
        element: Node, same: (Node, Node) -> Bool, role: (Node) -> String?,
        providerNodeID: (Node) -> String?, actualPID: (Node) -> pid_t?, parent: (Node) -> Node?,
        check: () throws -> Void
    ) rethrows -> ChromiumWebProof? {
        func nodeID(_ node: Node) -> Int32? {
            guard let value = providerNodeID(node), !value.isEmpty, value.utf8.count <= 10,
                value.utf8.allSatisfy({ (48...57).contains($0) }), let number = Int32(value), number > 0
            else { return nil }
            return number
        }
        try check()
        guard let elementID = nodeID(element), let pid = actualPID(element), pid > 0 else { return nil }
        // Match the 40-parent-hop input context observation budget, with fresh
        // provider ancestry for this exact editor at the point of qualification.
        let maximumParentHops = 40
        var current = element, seen = [Node]()
        for depth in 0...maximumParentHops {
            try check()
            guard !seen.contains(where: { same($0, current) }), let currentRole = role(current) else { return nil }
            seen.append(current)
            if currentRole == "AXWebArea" {
                guard !same(current, element), actualPID(current) == pid, let areaID = nodeID(current),
                    areaID != elementID
                else { return nil }
                return ChromiumWebProof(actualPID: pid, elementNodeID: elementID, webAreaNodeID: areaID)
            }
            if ["AXWindow", "AXSheet", "AXApplication"].contains(currentRole) { return nil }
            guard depth < maximumParentHops, let next = parent(current) else { return nil }
            current = next
        }
        return nil
    }

    @MainActor static func sampleChromiumWeb(element: AXUIElement) throws -> ChromiumWebProof? {
        try chromiumWebImplementation(
            element: element, same: { CFEqual($0, $1) },
            role: { AXAccess.read($0, kAXRoleAttribute).value as? String },
            providerNodeID: { AXAccess.read($0, "ChromeAXNodeId").value as? String },
            actualPID: BackgroundEvents.accessibilityProcessID,
            parent: { AXAccess.element($0, kAXParentAttribute) }, check: RequestDiagnostics.checkCancellation)
    }

    /// Paths here have already been canonicalized by the query adapter. A
    /// missing redundant running bundle URL is allowed, but a conflicting one
    /// cannot identify the system implementation. Unknown means no exclusion.
    static func knownEmptySetter(_ facts: Facts) -> Proof? {
        guard facts.presenterPID > 0, let actual = facts.actualPID, actual > 0,
            actual != facts.presenterPID, facts.currentActualPID == actual,
            let process = facts.process, process.pid == actual, facts.currentProcess == process,
            let system = facts.system, system.bundleIdentifier == webContentIdentifier,
            facts.runningBundleIdentifier == system.bundleIdentifier,
            !system.bundlePath.isEmpty, !system.executable.isEmpty,
            facts.resolvedExecutable == system.executable,
            facts.runningBundlePath.map({ $0 == system.bundlePath }) != false
        else { return nil }
        return Proof(process: process, system: system)
    }

    static func requireAvailable(_ proof: Proof?) throws(NativeRouteUnavailable) {
        guard proof != nil else { return }
        throw NativeRouteUnavailable(
            description: "the original provider does not implement the selected AXSelectedText content-edit route")
    }

    private static func canonical(_ url: URL) -> String {
        url.standardizedFileURL.resolvingSymlinksInPath().path
    }

    private static func systemImplementation() -> SystemImplementation? {
        // Resolve the installed system framework rather than hard-coding the
        // current Cryptex mount, inferring a provider from a display name, or
        // discovering arbitrary processes/bundles on disk.
        let location = URL(fileURLWithPath:
            "/System/Library/Frameworks/WebKit.framework/Versions/A/XPCServices/com.apple.WebKit.WebContent.xpc")
        guard let bundle = Bundle(url: location), bundle.bundleIdentifier == webContentIdentifier,
            let executable = bundle.executableURL
        else { return nil }
        return SystemImplementation(
            bundleIdentifier: webContentIdentifier, bundlePath: canonical(bundle.bundleURL),
            executable: canonical(executable), version: bundle.object(forInfoDictionaryKey: "CFBundleVersion") as? String)
    }

    @MainActor static func sample(element: AXUIElement, presenterPID: pid_t) -> Proof? {
        // Ordinary native/same-process objects keep their previous contract and
        // do not acquire a process metadata or ancestry prerequisite.
        guard let actual = BackgroundEvents.accessibilityProcessID(element), actual != presenterPID,
            let process = NativeProcessIdentity.read(actual),
            let running = NSRunningApplication(processIdentifier: actual), !running.isTerminated,
            running.bundleIdentifier == webContentIdentifier,
            let system = systemImplementation()
        else { return nil }
        let executable = canonical(URL(fileURLWithPath: process.executable))
        let identifier = running.bundleIdentifier
        let bundlePath = running.bundleURL.map(canonical)
        // Identity refresh closes this local metadata query. A changed remote
        // object/process cannot inherit the previous implementation proof.
        let currentProcess = NativeProcessIdentity.read(actual)
        let currentActualPID = BackgroundEvents.accessibilityProcessID(element)
        let facts = Facts(
            presenterPID: presenterPID, actualPID: actual, process: process,
            currentProcess: currentProcess, currentActualPID: currentActualPID,
            resolvedExecutable: executable, runningBundleIdentifier: identifier,
            runningBundlePath: bundlePath, system: system)
        return knownEmptySetter(facts)
    }
}
