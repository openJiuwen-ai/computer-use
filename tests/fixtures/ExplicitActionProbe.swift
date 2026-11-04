// Copyright (c) Huawei Technologies Co., Ltd. 2026. All rights reserved.

import AppKit

/// A real AX action provider with controlled names and localized descriptions.
/// The legacy provider hooks allow this fixture to publish arbitrary raw names.
@MainActor final class ExplicitActionTarget: NSView {
    var mode = "changing"
    var disappeared = false, hideDescription = false
    var aliasTransferred = false, duplicateAtDispatch = false
    var nameReads = 0
    var descriptionReads = [String: Int]()
    var performed = [String]()
    var callbackFinished = true
    var record: () -> Void = {}

    var names: [String] {
        if disappeared { return [] }
        if duplicateAtDispatch { return ["AXCustomExact", "AXCustomExact"] }
        if mode == "standard_large" {
            return ["AXShowAlternateUI"] + (0..<129).map { "AXUnrelated\($0)" }
        }
        if mode.hasPrefix("standard") { return ["AXShowAlternateUI"] }
        switch mode {
        case "press_duplicate": return ["AXPress", "AXPress"]
        case "large": return ["AXCustomExact"] + (0..<129).map { "AXUnrelated\($0)" }
        case "unique_alias", "duplicate_alias", "unknown_alias": return ["AXCustomExact", "AXOther"]
        case "duplicate_name": return ["AXCustomExact", "AXCustomExact"]
        default: return ["AXCustomExact"]
        }
    }

    override nonisolated func accessibilityRole() -> NSAccessibility.Role? { .button }
    override nonisolated func accessibilityActionNames() -> [NSAccessibility.Action] {
        MainActor.assumeIsolated {
            nameReads += 1
            let result = names.map { NSAccessibility.Action(rawValue: $0) }
            record()
            return result
        }
    }
    override nonisolated func accessibilityActionDescription(_ action: NSAccessibility.Action) -> String? {
        MainActor.assumeIsolated {
            let name = action.rawValue
            descriptionReads[name, default: 0] += 1
            let count = descriptionReads[name]!
            record()
            if hideDescription || mode == "missing" || mode == "standard_missing" { return nil }
            if mode == "changing" || mode == "standard" { return "Localized action revision \(count)" }
            if mode == "duplicate_alias" { return "Run probe" }
            if mode == "unknown_alias", name == "AXOther" { return nil }
            if mode == "unique_alias", aliasTransferred {
                return name == "AXOther" ? "Run probe" : "Execute probe"
            }
            if ["unique_alias", "unknown_alias"].contains(mode), name == "AXCustomExact" { return "Run probe" }
            return name == "AXCustomExact" ? "Execute probe" : "Unrelated action \(name)"
        }
    }
    override nonisolated func accessibilityPerformAction(_ action: NSAccessibility.Action) {
        MainActor.assumeIsolated {
            guard names.contains(action.rawValue) else { return }
            performed.append(action.rawValue)
            callbackFinished = false
            record()
            if mode == "slow" { Thread.sleep(forTimeInterval: 3.0) }
            callbackFinished = true
            record()
        }
    }
    override func draw(_ dirtyRect: NSRect) {
        NSColor.controlBackgroundColor.setFill()
        bounds.fill()
        "AX action target".draw(at: NSPoint(x: 8, y: 10), withAttributes: [.font: NSFont.systemFont(ofSize: 16)])
    }
    func configure(_ value: String) {
        mode = value
        disappeared = false
        hideDescription = false
        aliasTransferred = false
        duplicateAtDispatch = false
        nameReads = 0
        descriptionReads = [:]
        performed = []
        callbackFinished = true
    }
}

@MainActor final class ExplicitActionDelegate: NSObject, NSApplicationDelegate {
    let folder = Bundle.main.bundleURL.deletingLastPathComponent()
    let target = ExplicitActionTarget(frame: NSRect(x: 25, y: 85, width: 300, height: 45))
    let other = NSTextField(string: "unchanged")
    var window: NSWindow!
    var foreign: NSWindow?
    var sheet: NSPanel?
    var lastCommand = "", mutation = ""

    func record() {
        guard let window else { return }
        let value: [String: Any] = [
            "pid": getpid(), "active": NSApp.isActive, "lastCommand": lastCommand,
            "mode": target.mode, "mutation": mutation, "names": target.names,
            "aliasTransferred": target.aliasTransferred, "duplicateAtDispatch": target.duplicateAtDispatch,
            "nameReads": target.nameReads, "descriptionReads": target.descriptionReads,
            "performed": target.performed, "callbackFinished": target.callbackFinished,
            "targetIdentity": String(describing: ObjectIdentifier(target)),
            "inOriginalWindow": target.window === window, "sheetAttached": window.attachedSheet != nil,
            "other": other.stringValue,
        ]
        try! JSONSerialization.data(withJSONObject: value).write(
            to: folder.appendingPathComponent("state.json"), options: .atomic)
    }
    func tick() {
        guard let data = try? Data(contentsOf: folder.appendingPathComponent("command.json")),
            let command = try? JSONSerialization.jsonObject(with: data) as? [String: Any],
            let id = command["id"] as? String, id != lastCommand
        else { record(); return }
        lastCommand = id
        let kind = command["kind"] as? String ?? "configure"
        if kind == "configure" {
            if let sheet = window.attachedSheet { window.endSheet(sheet); sheet.orderOut(nil) }
            target.removeFromSuperview()
            window.contentView!.addSubview(target)
            foreign?.orderOut(nil)
            target.configure(command["mode"] as? String ?? "changing")
            mutation = ""
        } else {
            mutation = kind
            switch kind {
            case "hide_description": target.hideDescription = true
            case "disappear": target.disappeared = true
            case "alias_transfer": target.aliasTransferred = true
            case "duplicate_at_dispatch": target.duplicateAtDispatch = true
            case "move":
                if foreign == nil {
                    foreign = NSWindow(contentRect: NSRect(x: 700, y: 180, width: 350, height: 180),
                                       styleMask: [.titled, .closable], backing: .buffered, defer: false)
                    foreign!.title = "Other action document"
                }
                target.removeFromSuperview()
                foreign!.contentView!.addSubview(target)
                foreign!.orderBack(nil)
            case "modal":
                let panel = NSPanel(contentRect: NSRect(x: 0, y: 0, width: 230, height: 90),
                                    styleMask: [.titled], backing: .buffered, defer: false)
                panel.title = "Blocking action sheet"
                sheet = panel
                window.beginSheet(panel)
            default: break
            }
        }
        record()
    }
    func applicationDidFinishLaunching(_ notification: Notification) {
        window = NSWindow(contentRect: NSRect(x: 180, y: 180, width: 410, height: 170),
                          styleMask: [.titled, .closable], backing: .buffered, defer: false)
        window.title = "Explicit action probe"
        target.setAccessibilityElement(true)
        target.setAccessibilityLabel("Explicit action target")
        target.setAccessibilityIdentifier("explicit-action-original")
        target.record = { [weak self] in self?.record() }
        other.frame = NSRect(x: 25, y: 25, width: 350, height: 25)
        other.setAccessibilityLabel("Unrelated input")
        window.contentView!.addSubview(target)
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

@main enum ExplicitActionProbe {
    @MainActor static func main() {
        let app = NSApplication.shared
        app.setActivationPolicy(.regular)
        let delegate = ExplicitActionDelegate()
        app.delegate = delegate
        app.run()
    }
}
