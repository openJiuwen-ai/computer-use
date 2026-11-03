// Copyright (c) Huawei Technologies Co., Ltd. 2026. All rights reserved.

import AppKit
import CoreGraphics
import CryptoKit

/// Observe real events without replacing NSButton's input or accessibility behavior.
@MainActor final class ToggleActivationApplication: NSApplication {
    var inputEvents = [[String: Any]]()

    override func sendEvent(_ event: NSEvent) {
        if [.leftMouseDown, .leftMouseUp, .rightMouseDown, .rightMouseUp, .otherMouseDown, .otherMouseUp]
            .contains(event.type)
        {
            inputEvents.append([
                "kind": "mouse", "type": event.type.rawValue, "button": event.buttonNumber,
                "clickCount": event.clickCount, "window": event.windowNumber, "timestamp": event.timestamp
            ])
        } else if [.keyDown, .keyUp, .flagsChanged].contains(event.type) {
            inputEvents.append([
                "kind": "keyboard", "type": event.type.rawValue, "keyCode": event.keyCode,
                "flags": event.modifierFlags.rawValue, "window": event.windowNumber
            ])
        }
        super.sendEvent(event)
    }
}

@MainActor final class ToggleActivationForm: NSView {
    let route: String
    var model: [String: Bool]
    var callbacks = [[String: Any]]()
    var changed: () -> Void = {}
    let recording = NSButton(), muteOn = NSButton(), muteOff = NSButton()

    init(route: String, frame: NSRect, saved: [String: Bool]? = nil) {
        self.route = route
        model = saved ?? ["recording": false, "muted": false]
        super.init(frame: frame)
        let label = route == "normal" ? "Normal click" : "Explicit action"
        let heading = NSTextField(labelWithString: label)
        heading.frame = NSRect(x: 0, y: 160, width: 260, height: 24)
        addSubview(heading)
        for (button, suffix, type, y) in [
            (recording, "switch", NSButton.ButtonType.switch, 115.0),
            (muteOn, "radio on", NSButton.ButtonType.radio, 75.0),
            (muteOff, "radio off", NSButton.ButtonType.radio, 35.0)
        ] {
            button.frame = NSRect(x: 0, y: y, width: 270, height: 26)
            button.title = label + " " + suffix
            button.setButtonType(type)
            button.setAccessibilityLabel(button.title)
            button.setAccessibilityIdentifier("appkit-\(route)-\(suffix)")
            button.target = self
            button.action = #selector(activated(_:))
            addSubview(button)
        }
        recording.state = model["recording"] == true ? .on : .off
        muteOn.state = model["muted"] == true ? .on : .off
        muteOff.state = model["muted"] == true ? .off : .on
    }

    required init?(coder: NSCoder) { fatalError("init(coder:) has not been implemented") }

    @objc func activated(_ sender: NSButton) {
        let field = sender === recording ? "recording" : "muted"
        let value = sender === muteOff ? sender.state != .on : sender.state == .on
        model[field] = value
        callbacks.append([
            "field": field, "sender": sender === muteOff ? "radio off" : field,
            "state": sender.state.rawValue, "value": value,
            "active": NSApp.isActive, "uptime": ProcessInfo.processInfo.systemUptime
        ])
        changed()
    }

    func values() -> [String: Any] {
        [
            "checked": ["recording": recording.state == .on, "muted": muteOn.state == .on],
            "radioOff": muteOff.state == .on, "model": model, "callbacks": callbacks,
            "counts": [
                "recording": callbacks.filter { $0["field"] as? String == "recording" }.count,
                "muted": callbacks.filter { $0["field"] as? String == "muted" }.count
            ],
            "classes": [
                "recording": String(describing: type(of: recording)),
                "muteOn": String(describing: type(of: muteOn)), "muteOff": String(describing: type(of: muteOff))
            ]
        ]
    }
}

@MainActor final class ToggleActivationDelegate: NSObject, NSApplicationDelegate {
    let folder = Bundle.main.bundleURL.deletingLastPathComponent()
    var window: NSWindow!
    var forms = [String: ToggleActivationForm]()
    let guardField = NSTextField(string: "unchanged")
    let saveButton = NSButton(title: "Save and reopen", target: nil, action: nil)
    var generation = 0, saveClicks = 0, sampleSequence = 0
    var saved: [String: [String: Bool]]?
    var beforeReopen: [String: Any]?
    var failure: String?
    var desktopSamples = [[String: Any]]()

    func installForms(_ values: [String: [String: Bool]]? = nil) {
        for form in forms.values { form.removeFromSuperview() }
        forms = [:]
        for (index, route) in ["normal", "explicit"].enumerated() {
            let form = ToggleActivationForm(
                route: route, frame: NSRect(x: 20 + index * 295, y: 100, width: 280, height: 195),
                saved: values?[route])
            form.changed = { [weak self] in self?.record() }
            window.contentView!.addSubview(form)
            forms[route] = form
        }
        generation += 1
    }

    func formValues() -> [String: Any] { forms.mapValues { $0.values() } }

    func clipboardDigest() -> String {
        var digest = SHA256()
        for item in NSPasteboard.general.pasteboardItems ?? [] {
            for type in item.types.sorted(by: { $0.rawValue < $1.rawValue }) {
                digest.update(data: Data(type.rawValue.utf8))
                if let data = item.data(forType: type) { digest.update(data: data) }
            }
        }
        return digest.finalize().map { String(format: "%02x", $0) }.joined()
    }

    func sampleDesktop() -> [String: Any] {
        sampleSequence += 1
        return [
            "sequence": sampleSequence, "uptime": ProcessInfo.processInfo.systemUptime,
            "frontmostPID": NSWorkspace.shared.frontmostApplication?.processIdentifier ?? -1,
            "cursor": CGEvent(source: nil).map { [$0.location.x, $0.location.y] } as Any? ?? NSNull(),
            "modifiers": CGEventSource.flagsState(.combinedSessionState).rawValue,
            "heldButtons": [
                "left": CGEventSource.buttonState(.combinedSessionState, button: .left),
                "right": CGEventSource.buttonState(.combinedSessionState, button: .right),
                "middle": CGEventSource.buttonState(.combinedSessionState, button: .center)
            ],
            "clipboardChangeCount": NSPasteboard.general.changeCount
        ]
    }

    func record() {
        guard let window else { return }
        let desktop = sampleDesktop()
        desktopSamples.append(desktop)
        if desktopSamples.count > 2400 { desktopSamples.removeFirst(desktopSamples.count - 2400) }
        let value: [String: Any] = [
            "pid": getpid(), "title": window.title, "window": window.windowNumber,
            "active": NSApp.isActive, "visible": window.isVisible,
            "forms": formValues(), "generation": generation, "saveClicks": saveClicks,
            "saved": saved as Any? ?? NSNull(), "beforeReopen": beforeReopen as Any? ?? NSNull(),
            "guard": guardField.stringValue, "desktop": desktop, "desktopSamples": desktopSamples,
            "clipboardDigest": clipboardDigest(),
            "inputEvents": (NSApp as? ToggleActivationApplication)?.inputEvents ?? [],
            "failure": failure as Any? ?? NSNull()
        ]
        try? JSONSerialization.data(withJSONObject: value, options: [.sortedKeys]).write(
            to: folder.appendingPathComponent("state.json"), options: .atomic)
    }

    @objc func saveAndReopen(_ sender: NSButton) {
        saveClicks += 1
        beforeReopen = formValues()
        do {
            let url = folder.appendingPathComponent("saved-settings.json")
            try JSONSerialization.data(withJSONObject: forms.mapValues { $0.model }, options: [.sortedKeys])
                .write(to: url, options: .atomic)
            guard let loaded = try JSONSerialization.jsonObject(with: Data(contentsOf: url)) as? [String: [String: Bool]]
            else { throw CocoaError(.fileReadCorruptFile) }
            saved = loaded
            installForms(loaded)
        } catch { failure = String(describing: error) }
        record()
    }

    func applicationDidFinishLaunching(_ notification: Notification) {
        window = NSWindow(
            contentRect: NSRect(x: 180, y: 180, width: 610, height: 350),
            styleMask: [.titled, .closable], backing: .buffered, defer: false)
        window.title = "AppKit toggle activation probe"
        window.tabbingMode = .disallowed
        window.isReleasedWhenClosed = false
        guardField.frame = NSRect(x: 20, y: 60, width: 560, height: 26)
        guardField.setAccessibilityLabel("Unrelated guard field")
        window.contentView!.addSubview(guardField)
        saveButton.frame = NSRect(x: 200, y: 15, width: 200, height: 30)
        saveButton.setAccessibilityLabel(saveButton.title)
        saveButton.target = self
        saveButton.action = #selector(saveAndReopen(_:))
        window.contentView!.addSubview(saveButton)
        installForms()
        let menu = NSMenu()
        let item = NSMenuItem(title: "AppKitToggleActivationProbe", action: nil, keyEquivalent: "")
        item.submenu = NSMenu()
        item.submenu!.addItem(withTitle: "Quit", action: #selector(NSApplication.terminate(_:)), keyEquivalent: "q")
        menu.addItem(item)
        NSApp.mainMenu = menu
        window.orderBack(nil)
        window.makeKey()
        window.makeMain()
        window.makeFirstResponder(guardField)
        record()
        _ = Timer.scheduledTimer(withTimeInterval: 0.05, repeats: true) { _ in
            MainActor.assumeIsolated { self.record() }
        }
        _ = Timer.scheduledTimer(withTimeInterval: 180, repeats: false) { _ in
            MainActor.assumeIsolated { NSApp.terminate(nil) }
        }
    }
}

@main enum AppKitToggleActivationProbe {
    @MainActor static func main() {
        let app = ToggleActivationApplication.shared
        app.setActivationPolicy(.regular)
        let delegate = ToggleActivationDelegate()
        app.delegate = delegate
        app.run()
    }
}
