// Copyright (c) Huawei Technologies Co., Ltd. 2026. All rights reserved.

import AppKit

/// Standard NSButton target/action; AppKit supplies the native AXPress action.
@MainActor final class CommandIdentityDelegate: NSObject, NSApplicationDelegate {
    let folder = Bundle.main.bundleURL.deletingLastPathComponent()
    var window: NSWindow!
    var button: NSButton!
    var other: NSButton!
    weak var oldButton: NSButton?
    var commandId = ""
    var count = 0, otherClicks = 0
    var labelPolicy = "both"

    func makeButton(title: String, description: String, identifier: String) -> NSButton {
        let value = NSButton(title: title, target: self, action: #selector(pressed(_:)))
        value.frame = NSRect(x: 20, y: 85, width: 420, height: 38)
        value.setButtonType(.momentaryPushIn)
        value.bezelStyle = .rounded
        value.setAccessibilityLabel(description)
        value.setAccessibilityIdentifier(identifier)
        return value
    }

    func updateLabels() {
        button.title = ["title", "both"].contains(labelPolicy) ? "Command count \(count)" : "Command action"
        button.setAccessibilityLabel(
            ["description", "both"].contains(labelPolicy) ? "Command description \(count)" : "Command identity action")
    }

    @objc func pressed(_ sender: NSButton) {
        guard sender === button else { return }
        count += 1
        updateLabels()
        record()
    }

    @objc func otherPressed(_ sender: NSButton) {
        otherClicks += 1
        record()
    }

    func record() {
        guard let button else { return }
        let state: [String: Any] = [
            "pid": getpid(), "commandId": commandId, "count": count, "otherClicks": otherClicks,
            "title": button.title, "description": button.accessibilityLabel() ?? "",
            "buttonObjectID": String(describing: ObjectIdentifier(button)), "labelPolicy": labelPolicy,
            "identifier": button.accessibilityIdentifier(), "oldButtonAlive": oldButton != nil,
        ]
        try! JSONSerialization.data(withJSONObject: state, options: [.sortedKeys]).write(
            to: folder.appendingPathComponent("state.json"), options: .atomic)
    }

    func tick() {
        guard let data = try? Data(contentsOf: folder.appendingPathComponent("command.json")),
              let command = try? JSONSerialization.jsonObject(with: data) as? [String: Any],
              let id = command["id"] as? String, id != commandId else { record(); return }
        commandId = id
        switch command["kind"] as? String ?? "configure" {
        case "configure":
            let policy = command["labelPolicy"] as? String ?? "both"
            labelPolicy = ["title", "description", "both", "fixed"].contains(policy) ? policy : "both"
            count = 0
            otherClicks = 0
            button.setAccessibilityIdentifier("command-control")
            updateLabels()
        case "label":
            if let title = command["title"] as? String { button.title = title }
            if let description = command["description"] as? String { button.setAccessibilityLabel(description) }
        case "identity":
            button.setAccessibilityIdentifier(command["identifier"] as? String ?? "changed-command-control")
        case "replace":
            window.makeFirstResponder(other)
            let previous = button!
            oldButton = previous
            previous.removeFromSuperview()
            button = nil
            // Prevent allocator-address reuse from obscuring native replacement.
            withExtendedLifetime(previous) {
                button = makeButton(title: previous.title, description: previous.accessibilityLabel() ?? "",
                                    identifier: previous.accessibilityIdentifier())
                window.contentView!.addSubview(button)
            }
        default: break
        }
        record()
    }

    func applicationDidFinishLaunching(_ notification: Notification) {
        window = NSWindow(contentRect: NSRect(x: 180, y: 180, width: 460, height: 160),
                          styleMask: [.titled, .closable], backing: .buffered, defer: false)
        window.title = "Command identity probe"
        window.tabbingMode = .disallowed
        button = makeButton(title: "", description: "", identifier: "command-control")
        updateLabels()
        other = NSButton(title: "Unrelated command", target: self, action: #selector(otherPressed(_:)))
        other.frame = NSRect(x: 20, y: 25, width: 420, height: 36)
        other.setButtonType(.momentaryPushIn)
        other.bezelStyle = .rounded
        other.setAccessibilityIdentifier("other-command-control")
        window.contentView!.addSubview(button)
        window.contentView!.addSubview(other)
        window.orderBack(nil)
        window.makeKey()
        window.makeMain()
        window.makeFirstResponder(other)
        record()
        _ = Timer.scheduledTimer(withTimeInterval: 0.05, repeats: true) { _ in
            MainActor.assumeIsolated { self.tick() }
        }
    }
}

@main enum CommandIdentityProbe {
    @MainActor static func main() {
        let application = NSApplication.shared
        application.setActivationPolicy(.regular)
        let delegate = CommandIdentityDelegate()
        application.delegate = delegate
        application.run()
    }
}
