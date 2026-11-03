// Copyright (c) Huawei Technologies Co., Ltd. 2026. All rights reserved.

import AppKit
import ApplicationServices

/// Independent external AppKit recipient. The helper may click only this exact
/// disposable fixture, after verifying the physical AX hit at its button.
@MainActor final class ForegroundContinuityDelegate: NSObject, NSApplicationDelegate {
    let folder = Bundle.main.bundleURL.deletingLastPathComponent()
    var window: NSWindow!
    var clicks = 0

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

    @objc func clicked() { clicks += 1; record() }

    func record() {
        let cursor = CGEvent(source: nil)?.location
        let value: [String: Any] = [
            "pid": getpid(), "clicks": clicks, "active": NSApp.isActive,
            "foregroundPID": NSWorkspace.shared.frontmostApplication?.processIdentifier as Any? ?? NSNull(),
            "cursor": cursor.map { ["x": $0.x, "y": $0.y] } as Any? ?? NSNull(),
            "uptime": ProcessInfo.processInfo.systemUptime,
        ]
        try! JSONSerialization.data(withJSONObject: value).write(
            to: folder.appendingPathComponent("state.json"), options: .atomic)
    }
}

@main enum ForegroundContinuityProbe {
    @MainActor static func main() throws {
        let args = Array(CommandLine.arguments.dropFirst())
        if args.first == "--click" {
            guard args.count == 3, let pid = Int32(args[1]),
                args[2].hasPrefix("local.computer-use.continuity."),
                NSRunningApplication(processIdentifier: pid)?.bundleIdentifier == args[2]
            else { fatalError("Exact owned fixture PID and bundle are required") }
            func attr(_ object: AXUIElement, _ key: String) -> CFTypeRef? {
                var value: CFTypeRef?
                return AXUIElementCopyAttributeValue(object, key as CFString, &value) == .success ? value : nil
            }
            let root = AXUIElementCreateApplication(pid)
            let windows = attr(root, kAXWindowsAttribute) as? [AXUIElement] ?? []
            guard let window = windows.first(where: {
                attr($0, kAXTitleAttribute) as? String == "CU Foreground continuity external"
            }) else { fatalError("Owned window missing") }
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
                CFGetTypeID(size) == AXValueGetTypeID() else { fatalError("Owned button frame missing") }
            var origin = CGPoint.zero, dimensions = CGSize.zero
            guard AXValueGetValue(unsafeDowncast(position, to: AXValue.self), .cgPoint, &origin),
                AXValueGetValue(unsafeDowncast(size, to: AXValue.self), .cgSize, &dimensions)
            else { fatalError("Owned button frame invalid") }
            let point = CGPoint(x: origin.x + dimensions.width / 2, y: origin.y + dimensions.height / 2)
            var hit: AXUIElement?, hitPID: pid_t = 0
            guard AXUIElementCopyElementAtPosition(
                AXUIElementCreateSystemWide(), Float(point.x), Float(point.y), &hit) == .success,
                let hit, AXUIElementGetPid(hit, &hitPID) == .success, hitPID == pid
            else { fatalError("Owned fixture is not the physical hit; no input sent") }
            for type in [CGEventType.mouseMoved, .leftMouseDown, .leftMouseUp] {
                CGEvent(mouseEventSource: nil, mouseType: type, mouseCursorPosition: point, mouseButton: .left)!
                    .post(tap: .cghidEventTap)
                RunLoop.current.run(until: Date(timeIntervalSinceNow: 0.06))
            }
            print("Global click sent to verified owned external fixture \(pid)")
            return
        }
        let app = NSApplication.shared
        app.setActivationPolicy(.regular)
        let delegate = ForegroundContinuityDelegate()
        app.delegate = delegate
        app.run()
    }
}
