// Copyright (c) Huawei Technologies Co., Ltd. 2026. All rights reserved.

import ApplicationServices
import Foundation

@main struct NativePopupAXFactsTests {
    @MainActor static func main() throws {
        for route in Route.allCases {
            let proxy = Proxy()
            try query(route, proxy)
            require(proxy.allRestored, "\(route): optional query leaked its shortened proxy timeout")
            require(!proxy.during.isEmpty && proxy.during.allSatisfy { $0 > 0 && $0 <= 0.1 }, "bounded query")
            require(proxy.identityAvailable(), "\(route): later identity read must retain the ordinary timeout")
        }
        let parent = Proxy()
        try query(.parent, parent)
        require(parent.touched.contains(parent.key(parent.parent)), "PID-only parent proxy must be scoped too")
        require(parent.allRestored, "returned parent keeps the ordinary timeout")

        // Each check boundary can throw, including immediately after OS reads.
        // Cleanup must neither consult cancellation nor replace the first error.
        for route in Route.allCases {
            let baseline = Proxy()
            try query(route, baseline)
            for stop in 1...baseline.checks {
                let proxy = Proxy()
                proxy.stop = stop
                do {
                    try query(route, proxy)
                    require(false, "expected cancellation at \(route)/\(stop)")
                } catch QueryStop.cancelled {}
                require(proxy.allRestored, "\(route)/\(stop): cancellation leaked timeout")
            }
        }
        let exhausted = Proxy()
        try query(.role, exhausted, remaining: -1)
        require(exhausted.touched.isEmpty, "exhausted optional budget must not configure any proxy")
        for route in Route.allCases {
            let wrongPID = Proxy()
            wrongPID.pidMatches = false
            try query(route, wrongPID)
            require(wrongPID.allRestored, "\(route): PID failure leaked timeout")
        }
        for route in [Route.children, .windowID] {
            let unavailable = Proxy()
            unavailable.unavailable = true
            try query(route, unavailable)
            require(unavailable.allRestored, "\(route): unavailable result leaked timeout")
        }
        let failedRestore = Proxy()
        failedRestore.stop = 2
        failedRestore.restoreFails = true
        do { try query(.role, failedRestore) } catch QueryStop.cancelled {}
        require(failedRestore.restoreAttempts == 1, "cleanup is attempted even when the proxy has become invalid")
        require(failedRestore.checks == 2, "cleanup performs no extra cancellation or AX content reads")
        print(
            "PASS optional popup AX timeout scope restores exact proxies for values/children/window/PID-only parent, cancellation and unavailable results"
        )
    }

    enum Route: CaseIterable { case role, subrole, modal, parent, children, windowID }
    enum QueryStop: Error { case cancelled }

    @MainActor fileprivate static func query(_ route: Route, _ proxy: Proxy, remaining: Double = 0.1) throws {
        let facts = NativePopupAXFacts(
            pid: getpid(), until: ProcessInfo.processInfo.systemUptime + remaining,
            check: {
                proxy.checks += 1
                if proxy.stop == proxy.checks { throw QueryStop.cancelled }
            }, adapter: proxy.adapter()
        ).facts()
        switch route {
        case .role: _ = try facts.role(proxy.element)
        case .subrole: _ = try facts.subrole(proxy.element)
        case .modal: _ = try facts.modal(proxy.element)
        case .parent: _ = try facts.parent(proxy.element)
        case .children: _ = try facts.children(proxy.element)
        case .windowID: _ = try facts.windowID(proxy.element)
        }
    }

    static func require(_ value: Bool, _ reason: String) {
        if !value {
            print("FAIL \(reason)")
            exit(1)
        }
    }
}

@MainActor private final class Proxy {
    let element = AXUIElementCreateApplication(getpid())
    let parent = AXUIElementCreateApplication(getppid())
    private var timeouts = [UInt: Float]()
    var touched = Set<UInt>(), during = [Float](), checks = 0, stop: Int?
    var pidMatches = true, unavailable = false, restoreFails = false, restoreAttempts = 0

    func key(_ element: AXUIElement) -> UInt { UInt(bitPattern: Unmanaged.passUnretained(element).toOpaque()) }
    var allRestored: Bool { touched.allSatisfy { timeouts[$0] == AXMessaging.timeoutSeconds } }
    func identityAvailable() -> Bool {
        // AXIdentity.read does not configure a timeout. Model a normal provider
        // taking longer than the exhausted stamp's last few milliseconds.
        timeouts[key(element), default: AXMessaging.timeoutSeconds] >= 0.5
    }
    private func read(_ element: AXUIElement) {
        during.append(timeouts[key(element), default: AXMessaging.timeoutSeconds])
    }
    func adapter() -> NativePopupAXFacts.Adapter {
        .init(
            timeout: { element, value in
                if value == AXMessaging.timeoutSeconds {
                    self.restoreAttempts += 1
                    if self.restoreFails { return .invalidUIElement }
                }
                self.timeouts[self.key(element)] = value
                self.touched.insert(self.key(element))
                return .success
            },
            pid: { element in
                self.read(element)
                return self.pidMatches ? getpid() : getppid()
            },
            value: { element, attribute in
                self.read(element)
                if attribute == kAXParentAttribute { return self.parent }
                if attribute == kAXModalAttribute { return kCFBooleanFalse }
                return "AXGroup" as CFString
            },
            count: { element in
                self.read(element)
                return self.unavailable ? nil : 1
            },
            children: { element, _ in
                self.read(element)
                return [self.parent]
            },
            windowID: { element in
                self.read(element)
                return self.unavailable ? nil : 207
            })
    }
}
