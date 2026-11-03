// Copyright (c) Huawei Technologies Co., Ltd. 2026. All rights reserved.

import AppKit
import ApplicationServices

/// QUERY-only qualification of one provider's SelectedText content setter.
/// WebKit's remote text wrapper declares SelectedText writable but forwards it
/// to an empty implementation (WebKit-7624.4.5.11.1 / .12.1). Other providers,
/// including Firefox and Chromium, implement real selected-text editing.
/// No WebArea/marker heuristic or host application name grants this exclusion.
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
