// Copyright (c) Huawei Technologies Co., Ltd. 2026. All rights reserved.

import AppKit
import ApplicationServices

/// Independent external AppKit recipient. The helper may click only this exact
/// disposable fixture, after verifying its exact WindowServer hit and owner.
@MainActor final class ForegroundContinuityDelegate: NSObject, NSApplicationDelegate {
    let folder = Bundle.main.bundleURL.deletingLastPathComponent()
    var window: NSWindow!
    var clicks = 0
    var lastClickEpochSeconds: TimeInterval?
    var lastClickUptime: TimeInterval?
    var lastClickEventType: UInt?

    func applicationDidFinishLaunching(_ notification: Notification) {
        window = NSWindow(
            contentRect: NSRect(x: 800, y: 500, width: 350, height: 180), styleMask: [.titled, .closable],
            backing: .buffered, defer: false)
        window.title = "CU Foreground continuity external"
        window.level = .floating
        let button = NSButton(title: "External click counter", target: self, action: #selector(clicked))
        button.frame = NSRect(x: 20, y: 55, width: 310, height: 70)
        window.contentView!.addSubview(button)
        window.orderFront(nil)
        record()
        Timer.scheduledTimer(withTimeInterval: 0.03, repeats: true) { _ in
            MainActor.assumeIsolated { self.record() }
        }
    }

    @objc func clicked() {
        clicks += 1
        lastClickEpochSeconds = Date().timeIntervalSince1970
        lastClickUptime = ProcessInfo.processInfo.systemUptime
        lastClickEventType = NSApp.currentEvent?.type.rawValue
        record()
    }

    func record() {
        let cursor = CGEvent(source: nil)?.location
        let value: [String: Any] = [
            "pid": getpid(), "clicks": clicks, "active": NSApp.isActive,
            "foregroundPID": NSWorkspace.shared.frontmostApplication?.processIdentifier as Any? ?? NSNull(),
            "cursor": cursor.map { ["x": $0.x, "y": $0.y] } as Any? ?? NSNull(),
            "uptime": ProcessInfo.processInfo.systemUptime,
            "lastClickEpochSeconds": lastClickEpochSeconds as Any? ?? NSNull(),
            "lastClickUptime": lastClickUptime as Any? ?? NSNull(),
            "lastClickEventType": lastClickEventType as Any? ?? NSNull(),
        ]
        try! JSONSerialization.data(withJSONObject: value).write(
            to: folder.appendingPathComponent("state.json"), options: .atomic)
    }
}

@main enum ForegroundContinuityProbe {
    private typealias WindowID = @convention(c) (AXUIElement, UnsafeMutablePointer<CGWindowID>) -> Int32
    private typealias MainConnection = @convention(c) () -> UInt32
    private typealias FindWindow = @convention(c) (
        UInt32, UInt32, Int64, Int64, UnsafePointer<CGPoint>, UnsafeMutablePointer<CGPoint>,
        UnsafeMutablePointer<UInt32>, UnsafeMutablePointer<UInt32>
    ) -> Int32

    private struct Failure: Error {
        let code: String
        let detail: String
        var evidence: [String: Any] = [:]
    }

    @MainActor private struct PreparedClick {
        let info: [String: Any]
        let send: @MainActor (Bool) throws -> [String: Any]
    }

    @MainActor private static func emit(_ value: [String: Any], to file: URL? = nil) throws {
        let data = try JSONSerialization.data(withJSONObject: value) + Data([10])
        if let file { try data.write(to: file, options: .atomic) }
        else { try FileHandle.standardOutput.write(contentsOf: data) }
    }

    @MainActor private static func guardState(pid: pid_t) throws -> [String: Any] {
        let file = Bundle.main.bundleURL.deletingLastPathComponent().appendingPathComponent("state.json")
        guard let state = try JSONSerialization.jsonObject(with: Data(contentsOf: file)) as? [String: Any],
            state["pid"] as? pid_t == pid, state["clicks"] is Int
        else { throw Failure(code: "owned_state_unavailable", detail: "Owned guard counter is unavailable") }
        return state
    }

    /// One prewarmed, one-shot owned-fixture trigger. No SDK hook or wait is
    /// introduced: validity still requires independently observed deactivation
    /// and a later SDK down in the experiment's external event journal.
    @MainActor private final class SignalClick {
        let prepared: PreparedClick
        let pid: pid_t
        let token: String
        let readyFile: URL
        let resultFile: URL
        var source: (any DispatchSourceSignal)?
        var triggered = false

        init(_ args: [String]) throws {
            guard args.count == 6, let pid = pid_t(args[1]), pid > 0, !args[5].isEmpty, args[5].count <= 128
            else { throw Failure(code: "invalid_signal_arguments", detail: "Expected PID bundle ready-file result-file token") }
            let folder = Bundle.main.bundleURL.deletingLastPathComponent().resolvingSymlinksInPath().standardizedFileURL
            readyFile = URL(fileURLWithPath: args[3]).standardizedFileURL
            resultFile = URL(fileURLWithPath: args[4]).standardizedFileURL
            guard readyFile.deletingLastPathComponent().resolvingSymlinksInPath() == folder,
                resultFile.deletingLastPathComponent().resolvingSymlinksInPath() == folder, readyFile != resultFile
            else { throw Failure(code: "invalid_signal_output", detail: "Signal evidence must stay in the owned fixture folder") }
            self.pid = pid
            token = args[5]
            prepared = try prepareClick(["--click", args[1], args[2]])
        }

        func start() throws {
            signal(SIGUSR1, SIG_IGN)
            let signalSource = DispatchSource.makeSignalSource(signal: SIGUSR1, queue: .main)
            signalSource.setEventHandler { [weak self] in
                MainActor.assumeIsolated { self?.fire() }
            }
            source = signalSource
            signalSource.resume()
            var ready = prepared.info
            ready["kind"] = "owned_fixture_click_ready"
            ready["token"] = token
            ready["helperPID"] = getpid()
            ready["guardPID"] = pid
            ready["guardWindowID"] = prepared.info["windowID"]
            ready["readyEpochSeconds"] = Date().timeIntervalSince1970
            ready["readyUptime"] = ProcessInfo.processInfo.systemUptime
            ready["signal"] = SIGUSR1
            try emit(ready, to: readyFile)
            try emit(ready)
            Timer.scheduledTimer(withTimeInterval: 30, repeats: false) { [weak self] _ in
                MainActor.assumeIsolated {
                    guard let self, !self.triggered else { return }
                    self.triggered = true
                    self.source?.cancel()
                    try? emit(["kind": "owned_fixture_click_result", "token": self.token, "helperPID": getpid(),
                               "guardPID": self.pid, "clickConfirmed": false, "code": "signal_timeout"],
                              to: self.resultFile)
                    exit(2)
                }
            }
        }

        func fire() {
            guard !triggered else { return }
            triggered = true
            source?.cancel()
            var result: [String: Any] = ["kind": "owned_fixture_click_result", "token": token,
                "helperPID": getpid(), "guardPID": pid, "guardWindowID": prepared.info["windowID"]!,
                "signalEpochSeconds": Date().timeIntervalSince1970, "signalUptime": ProcessInfo.processInfo.systemUptime]
            do {
                let before = try guardState(pid: pid)
                result["guardBefore"] = before
                let sent = try prepared.send(false)
                for (key, value) in sent { result[key] = value }
                // A returned OS post is not consumption. The independent GUI
                // fixture must report the button action and actual foreground.
                let until = ProcessInfo.processInfo.systemUptime + 0.5
                var after = try guardState(pid: pid), confirmed = false
                repeat {
                    after = try guardState(pid: pid)
                    confirmed = after["clicks"] as? Int == (before["clicks"] as! Int) + 1
                        && after["foregroundPID"] as? pid_t == pid && after["active"] as? Bool == true
                        && (after["lastClickUptime"] as? Double ?? 0) >= (sent["downUptime"] as! Double)
                    if confirmed { break }
                    RunLoop.current.run(until: Date(timeIntervalSinceNow: 0.001))
                } while ProcessInfo.processInfo.systemUptime < until
                result["guardAfter"] = after
                result["clickConfirmed"] = confirmed
                result["kind"] = "owned_fixture_click_result"
                try emit(result, to: resultFile)
                try emit(result)
                exit(confirmed ? 0 : 2)
            } catch {
                let failure = error as? Failure
                result["kind"] = "owned_fixture_click_result"
                result["clickConfirmed"] = false
                result["code"] = failure?.code ?? "signal_click_failed"
                result["detail"] = failure?.detail ?? String(describing: error)
                result["evidence"] = failure?.evidence ?? [:]
                try? emit(result, to: resultFile)
                try? emit(result)
                exit(2)
            }
        }
    }

    /// Same exact native-window hit used by the owned Qt A/B helper. AX's
    /// application-wide hit may be redirected by a popup; it is not authority
    /// for whether this disposable guard window is physically uncovered.
    private static func exactWindowHit(
        _ window: AXUIElement, pid: pid_t, point: CGPoint, original: CGWindowID? = nil
    ) throws -> [String: Any] {
        guard let accessibility = dlopen(
            "/System/Library/Frameworks/ApplicationServices.framework/Frameworks/HIServices.framework/HIServices",
            RTLD_LAZY | RTLD_LOCAL)
        else { throw Failure(code: "native_hit_unavailable", detail: "HIServices unavailable") }
        defer { dlclose(accessibility) }
        guard let skyLight = dlopen(
            "/System/Library/PrivateFrameworks/SkyLight.framework/SkyLight", RTLD_LAZY | RTLD_LOCAL)
        else { throw Failure(code: "native_hit_unavailable", detail: "SkyLight unavailable") }
        defer { dlclose(skyLight) }
        guard let axWindow = dlsym(accessibility, "_AXUIElementGetWindow"),
            let connection = dlsym(skyLight, "CGSMainConnectionID"),
            let nativeHit = dlsym(skyLight, "SLSFindWindowAndOwner")
        else { throw Failure(code: "native_hit_unavailable", detail: "Exact native hit symbols unavailable") }
        var expected: CGWindowID = 0, found: UInt32 = 0, owner: UInt32 = 0
        var screenPoint = point, local = CGPoint.zero
        let axStatus = unsafeBitCast(axWindow, to: WindowID.self)(window, &expected)
        let hitStatus = unsafeBitCast(nativeHit, to: FindWindow.self)(
            unsafeBitCast(connection, to: MainConnection.self)(), 0, 1, 0, &screenPoint, &local, &found, &owner)
        let rows = found == 0 ? nil : CGWindowListCopyWindowInfo(.optionIncludingWindow, found) as? [[String: Any]]
        let row = rows?.first { ($0[kCGWindowNumber as String] as? NSNumber)?.uint32Value == found }
        let ownerPID = (row?[kCGWindowOwnerPID as String] as? NSNumber)?.int32Value
        let evidence: [String: Any] = [
            "hitSource": "WindowServer.SLSFindWindowAndOwner", "expectedWindowID": expected,
            "originalWindowID": original as Any? ?? NSNull(), "hitWindowID": found,
            "ownerConnection": owner, "ownerPID": ownerPID as Any? ?? NSNull(),
            "expectedPID": pid, "axWindowStatus": axStatus, "hitStatus": hitStatus
        ]
        guard axStatus == 0, expected != 0, original == nil || original == expected,
            hitStatus == 0, found == expected, owner != 0, ownerPID == pid
        else {
            throw Failure(code: "owned_window_not_physical_hit",
                detail: "Exact owned window and WindowServer owner must match; no click sent", evidence: evidence)
        }
        return evidence
    }

    @MainActor private static func prepareClick(_ args: [String]) throws -> PreparedClick {
        guard args.count == 3, let pid = Int32(args[1]), pid > 0,
            args[2].hasPrefix("local.computer-use.continuity."),
            let app = NSRunningApplication(processIdentifier: pid), !app.isTerminated,
            app.bundleIdentifier == args[2]
        else { throw Failure(code: "invalid_owned_fixture", detail: "Exact owned fixture PID and bundle are required") }
        let launch = app.launchDate
        func attr(_ object: AXUIElement, _ key: String) -> CFTypeRef? {
            var value: CFTypeRef?
            return AXUIElementCopyAttributeValue(object, key as CFString, &value) == .success ? value : nil
        }
        let root = AXUIElementCreateApplication(pid)
        let windows = attr(root, kAXWindowsAttribute) as? [AXUIElement] ?? []
        let matches = windows.filter { attr($0, kAXTitleAttribute) as? String == "CU Foreground continuity external" }
        guard matches.count == 1, let window = matches.first
        else { throw Failure(code: "owned_window_missing", detail: "A unique owned window is required") }
        let validateOwned: @MainActor () throws -> Void = {
            var owner: pid_t = 0
            guard let current = NSRunningApplication(processIdentifier: pid), current.isEqual(app),
                current.launchDate == launch, !current.isTerminated, current.bundleIdentifier == args[2],
                AXUIElementGetPid(window, &owner) == .success, owner == pid,
                attr(window, kAXTitleAttribute) as? String == "CU Foreground continuity external"
            else { throw Failure(code: "owned_fixture_changed", detail: "The original owned fixture ended or changed") }
        }
        var queue = [window], button: AXUIElement?
        for _ in 0..<80 {
            guard !queue.isEmpty else { break }
            let node = queue.removeFirst()
            if attr(node, kAXRoleAttribute) as? String == kAXButtonRole,
                attr(node, kAXTitleAttribute) as? String == "External click counter" {
                button = node; break
            }
            queue += attr(node, kAXChildrenAttribute) as? [AXUIElement] ?? []
        }
        guard let button, let position = attr(button, kAXPositionAttribute),
            let size = attr(button, kAXSizeAttribute), CFGetTypeID(position) == AXValueGetTypeID(),
            CFGetTypeID(size) == AXValueGetTypeID()
        else { throw Failure(code: "owned_button_missing", detail: "Owned button frame unavailable") }
        var origin = CGPoint.zero, dimensions = CGSize.zero
        guard AXValueGetValue(unsafeDowncast(position, to: AXValue.self), .cgPoint, &origin),
            AXValueGetValue(unsafeDowncast(size, to: AXValue.self), .cgSize, &dimensions)
        else { throw Failure(code: "owned_button_frame_invalid", detail: "Owned button frame invalid") }
        let point = CGPoint(x: origin.x + dimensions.width / 2, y: origin.y + dimensions.height / 2)
        try validateOwned()
        let first = try exactWindowHit(window, pid: pid, point: point)
        let windowID = (first["expectedWindowID"] as! NSNumber).uint32Value
        guard let move = CGEvent(mouseEventSource: nil, mouseType: .mouseMoved, mouseCursorPosition: point, mouseButton: .left),
            let down = CGEvent(mouseEventSource: nil, mouseType: .leftMouseDown, mouseCursorPosition: point, mouseButton: .left),
            let up = CGEvent(mouseEventSource: nil, mouseType: .leftMouseUp, mouseCursorPosition: point, mouseButton: .left)
        else { throw Failure(code: "input_event_unavailable", detail: "No global event was sent") }
        let info: [String: Any] = ["pid": pid, "guardBundle": args[2], "windowID": windowID,
            "point": ["x": point.x, "y": point.y], "hit": first]
        return PreparedClick(info: info, send: { includeMove in
            var posted = [UInt32]()
            if includeMove {
                try validateOwned()
                _ = try exactWindowHit(window, pid: pid, point: point, original: windowID)
                move.timestamp = DispatchTime.now().uptimeNanoseconds
                move.post(tap: .cghidEventTap)
                posted.append(CGEventType.mouseMoved.rawValue)
                RunLoop.current.run(until: Date(timeIntervalSinceNow: 0.06))
            }
            let final: [String: Any]
            do {
                try validateOwned()
                final = try exactWindowHit(window, pid: pid, point: point, original: windowID)
            } catch var failure as Failure {
                failure.evidence["postedEventTypes"] = posted
                throw failure
            }
            let downEpoch = Date().timeIntervalSince1970, downUptime = ProcessInfo.processInfo.systemUptime
            down.timestamp = DispatchTime.now().uptimeNanoseconds
            down.post(tap: .cghidEventTap)
            posted.append(CGEventType.leftMouseDown.rawValue)
            if includeMove { RunLoop.current.run(until: Date(timeIntervalSinceNow: 0.06)) }
            // Pair the admitted down even if the window stack changes later.
            let upEpoch = Date().timeIntervalSince1970, upUptime = ProcessInfo.processInfo.systemUptime
            up.timestamp = DispatchTime.now().uptimeNanoseconds
            up.post(tap: .cghidEventTap)
            posted.append(CGEventType.leftMouseUp.rawValue)
            if includeMove { RunLoop.current.run(until: Date(timeIntervalSinceNow: 0.06)) }
            return ["kind": "owned_fixture_click", "pid": pid, "bundle": args[2],
                "point": ["x": point.x, "y": point.y], "hit": final, "postedEventTypes": posted,
                "downEpochSeconds": downEpoch, "downUptime": downUptime,
                "upEpochSeconds": upEpoch, "upUptime": upUptime]
        })
    }

    @MainActor static func main() {
        let args = Array(CommandLine.arguments.dropFirst())
        if args.first == "--click" || args.first == "--wait-for-click-signal" {
            do {
                if args.first == "--click" { try emit(prepareClick(args).send(true)) }
                else {
                    let run = try SignalClick(args)
                    try run.start()
                    withExtendedLifetime(run) { RunLoop.main.run() }
                }
            }
            catch {
                let failure = error as? Failure
                let result: [String: Any] = ["kind": "owned_fixture_click_error",
                    "code": failure?.code ?? "helper_error", "detail": failure?.detail ?? String(describing: error),
                    "evidence": failure?.evidence ?? [:]]
                if let data = try? JSONSerialization.data(withJSONObject: result) {
                    try? FileHandle.standardError.write(contentsOf: data + Data([10]))
                }
                exit(2)
            }
            return
        }
        let app = NSApplication.shared
        app.setActivationPolicy(.regular)
        let delegate = ForegroundContinuityDelegate()
        app.delegate = delegate
        app.run()
    }
}
