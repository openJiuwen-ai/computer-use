// Copyright (c) Huawei Technologies Co., Ltd. 2026. All rights reserved.

import AppKit

/// Layout metadata deliberately changes while the actionable child stays alive.
@MainActor final class StableReferenceLayout: NSView {
    var generation = 0
    weak var logicalButton: StableReferenceButton?
    override nonisolated func accessibilityRole() -> NSAccessibility.Role? { .group }
    override nonisolated func accessibilityIdentifier() -> String {
        MainActor.assumeIsolated { "layout-\(generation)" }
    }
    override nonisolated func accessibilityLabel() -> String? { "Layout wrapper" }
    override nonisolated func accessibilityChildren() -> [Any]? {
        let button = MainActor.assumeIsolated { logicalButton }
        return button.map { [$0] } ?? []
    }
}

@MainActor final class StableReferenceContent: NSView {
    weak var layout: StableReferenceLayout?
    weak var marker: NSTextField?
    override nonisolated func accessibilityChildren() -> [Any]? {
        let (layout, marker) = MainActor.assumeIsolated { (self.layout, self.marker) }
        return [layout, marker].compactMap { $0 }
    }
}

@MainActor final class StableReferenceButton: NSButton {
    var generation = 0
}

@MainActor final class StableReferenceDelegate: NSObject, NSApplicationDelegate {
    let folder = Bundle.main.bundleURL.deletingLastPathComponent()
    var window: NSWindow!
    var wrapper: StableReferenceLayout!
    var button: StableReferenceButton!
    let marker = NSTextField(labelWithString: "Initial generation")
    var sequence = 0, generation = 0, clicks = 0, windowGeneration = 0
    var callbackGenerations = [Int]()
    var oldButtonID = "", oldWrapperID = "", oldWindowID = ""
    var commandError: String?

    func installButton() {
        generation += 1
        let next = StableReferenceButton(title: "Retained button", target: self, action: #selector(clicked))
        next.generation = generation
        next.setAccessibilityIdentifier("stable-button")
        next.frame = NSRect(x: 40, y: 75, width: 200, height: 32)
        button = next
        // The physical view stays directly attached to the window. AX-only
        // layout replacement below must not destroy its AppKit AX identity.
        window.contentView!.addSubview(next)
        wrapper.logicalButton = next
        next.setAccessibilityParent(wrapper)
    }

    func installWrapper() {
        let next = StableReferenceLayout(frame: NSRect(x: 20, y: 50, width: 280, height: 85))
        next.setAccessibilityElement(true)
        wrapper = next
        window.contentView!.addSubview(next)
        (window.contentView as! StableReferenceContent).layout = next
    }

    func installWindow() {
        windowGeneration += 1
        window = NSWindow(contentRect: NSRect(x: 120, y: 160, width: 430, height: 220),
                          styleMask: [.titled, .closable], backing: .buffered, defer: false)
        window.isReleasedWhenClosed = false
        window.title = "Stable reference acceptance"
        window.contentView = StableReferenceContent(frame: NSRect(x: 0, y: 0, width: 430, height: 220))
        installWrapper()
        installButton()
        marker.frame = NSRect(x: 20, y: 155, width: 380, height: 30)
        window.contentView!.addSubview(marker)
        (window.contentView as! StableReferenceContent).marker = marker
        window.orderBack(nil)
    }

    @objc func clicked(_ sender: StableReferenceButton) {
        clicks += 1
        callbackGenerations.append(sender.generation)
        record()
    }

    func record() {
        let value: [String: Any] = [
            "pid": getpid(), "sequence": sequence, "clicks": clicks,
            "generation": generation, "windowGeneration": windowGeneration,
            "callbackGenerations": callbackGenerations, "marker": marker.stringValue,
            "buttonID": String(describing: ObjectIdentifier(button)),
            "wrapperID": String(describing: ObjectIdentifier(wrapper)),
            "windowID": String(describing: ObjectIdentifier(window)),
            "oldButtonID": oldButtonID, "oldWrapperID": oldWrapperID, "oldWindowID": oldWindowID,
            "windowNumber": window.windowNumber, "buttonAttached": button.window === window,
            "frontmostPID": NSWorkspace.shared.frontmostApplication?.processIdentifier ?? -1,
            "commandError": commandError as Any? ?? NSNull()
        ]
        do {
            try JSONSerialization.data(withJSONObject: value, options: [.sortedKeys]).write(
                to: folder.appendingPathComponent("state.json"), options: .atomic)
        } catch { fputs("Fixture state failed: \(error)\n", stderr) }
    }

    func consumeCommand() {
        guard let data = try? Data(contentsOf: folder.appendingPathComponent("command.json")),
              let value = try? JSONSerialization.jsonObject(with: data) as? [String: Any],
              let next = value["sequence"] as? Int, next > sequence else { return }
        sequence = next
        commandError = nil
        switch value["action"] as? String {
        case "sibling":
            marker.stringValue = "Unrelated sibling changed \(sequence)"
            window.title = "Stable reference acceptance \(sequence)"
        case "metadata":
            wrapper.generation += 1
            marker.stringValue = "Layout metadata changed \(sequence)"
        case "rewrap":
            oldWrapperID = String(describing: ObjectIdentifier(wrapper))
            wrapper.removeFromSuperview()
            installWrapper()
            wrapper.logicalButton = button
            button.setAccessibilityParent(wrapper)
            marker.stringValue = "Wrapper replaced \(sequence)"
        case "physical-reattach":
            button.removeFromSuperview()
            window.contentView!.addSubview(button)
            button.setAccessibilityParent(wrapper)
            marker.stringValue = "Physical reattachment \(sequence)"
        case "replace-button":
            oldButtonID = String(describing: ObjectIdentifier(button))
            button.removeFromSuperview()
            installButton()
            marker.stringValue = "Replacement generation \(generation)"
        case "reopen-window":
            oldWindowID = String(describing: ObjectIdentifier(window))
            marker.removeFromSuperview()
            window.close()
            installWindow()
            marker.stringValue = "Reopened generation \(windowGeneration)"
        default: commandError = "unknown command"
        }
        record()
    }

    func applicationDidFinishLaunching(_ notification: Notification) {
        installWindow()
        record()
        Timer.scheduledTimer(withTimeInterval: 0.05, repeats: true) { _ in
            MainActor.assumeIsolated { self.consumeCommand() }
        }
    }
    func applicationShouldTerminateAfterLastWindowClosed(_ sender: NSApplication) -> Bool { false }
}

@main enum StableReferenceProbe {
    @MainActor static func main() {
        let app = NSApplication.shared
        app.setActivationPolicy(.regular)
        let delegate = StableReferenceDelegate()
        app.delegate = delegate
        app.run()
    }
}
