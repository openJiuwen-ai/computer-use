// Copyright (c) Huawei Technologies Co., Ltd. 2026. All rights reserved.

import AppKit

/// An independent foreground application records accidental input.
@MainActor final class KeyboardSentinelDelegate: NSObject, NSApplicationDelegate {
    let folder = Bundle.main.bundleURL.deletingLastPathComponent()
    let other = NSTextField(string: "unchanged")
    var window: NSWindow!
    var monitor: Any?
    var keys = 0, keyUps = 0, modifiers = 0, mouseEvents = 0, sequence = 0

    func record() {
        sequence += 1
        let value: [String: Any] = [
            "pid": getpid(), "sequence": sequence, "uptime": ProcessInfo.processInfo.systemUptime,
            "active": NSApp.isActive, "frontmostPID": NSWorkspace.shared.frontmostApplication?.processIdentifier ?? -1,
            "other": other.stringValue, "keys": keys, "keyUps": keyUps, "modifiers": modifiers,
            "mouseEvents": mouseEvents
        ]
        guard let data = try? JSONSerialization.data(withJSONObject: value) else { return }
        try? data.write(to: folder.appendingPathComponent("state.json"), options: .atomic)
    }

    func applicationDidFinishLaunching(_ notification: Notification) {
        window = NSWindow(
            contentRect: NSRect(x: 760, y: 240, width: 330, height: 130), styleMask: [.titled, .closable],
            backing: .buffered, defer: false)
        window.title = "Keyboard command foreground sentinel"
        window.tabbingMode = .disallowed
        other.frame = NSRect(x: 20, y: 50, width: 290, height: 28)
        other.setAccessibilityLabel("Unrelated sentinel input")
        window.contentView!.addSubview(other)
        monitor = NSEvent.addLocalMonitorForEvents(matching: [
            .keyDown, .keyUp, .flagsChanged, .leftMouseDown, .rightMouseDown, .otherMouseDown
        ]) { event in
            MainActor.assumeIsolated {
                if event.type == .keyDown {
                    self.keys += 1
                } else if event.type == .keyUp {
                    self.keyUps += 1
                } else if event.type == .flagsChanged {
                    self.modifiers += 1
                } else {
                    self.mouseEvents += 1
                }
                self.record()
            }
            return event
        }
        window.makeKeyAndOrderFront(nil)
        window.makeFirstResponder(other)
        if #available(macOS 14.0, *) { NSApp.activate() } else { NSApp.activate(ignoringOtherApps: true) }
        record()
        _ = Timer.scheduledTimer(withTimeInterval: 0.05, repeats: true) { _ in
            MainActor.assumeIsolated { self.record() }
        }
    }
}

@main enum KeyboardCommandSentinel {
    @MainActor static func main() {
        let app = NSApplication.shared
        app.setActivationPolicy(.regular)
        let delegate = KeyboardSentinelDelegate()
        app.delegate = delegate
        app.run()
    }
}
