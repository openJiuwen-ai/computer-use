// Copyright (c) Huawei Technologies Co., Ltd. 2026. All rights reserved.

import AppKit

@MainActor final class KeyboardCommandApplication: NSApplication {
    override func sendEvent(_ event: NSEvent) {
        (delegate as? KeyboardCommandDelegate)?.receive(event)
        super.sendEvent(event)
    }
}

/// A nil-target menu command must reach this original document responder.
@MainActor final class KeyboardDocumentView: NSView {
    var onCommand: () -> Void = {}
    override var acceptsFirstResponder: Bool { true }
    @objc func countDocumentCommand(_ sender: Any?) { onCommand() }
}

/// Ordinary AppKit windows and field editors; no accessibility overrides.
@MainActor final class KeyboardCommandDelegate: NSObject, NSApplicationDelegate {
    let folder = Bundle.main.bundleURL.deletingLastPathComponent()
    let field = NSTextField(string: "KEYBOARD-SEED")
    let guardField = NSTextField(string: "GUARD")
    var window: NSWindow!
    var delegateActions = 0, documentActions = 0, sequence = 0, eventSequence = 0
    var events = [[String: Any]]()

    func record() {
        guard let window else { return }
        sequence += 1
        let pointer = CGEvent(source: nil)?.location ?? .zero
        let value: [String: Any] = [
            "pid": getpid(), "sequence": sequence, "uptime": ProcessInfo.processInfo.systemUptime,
            "window": window.windowNumber, "windowObjectID": String(describing: ObjectIdentifier(window)),
            "fieldObjectID": String(describing: ObjectIdentifier(field)), "active": NSApp.isActive,
            "key": window.isKeyWindow, "main": window.isMainWindow,
            "frontmostPID": NSWorkspace.shared.frontmostApplication?.processIdentifier ?? 0,
            "keyWindow": NSApp.keyWindow?.windowNumber ?? 0, "mainWindow": NSApp.mainWindow?.windowNumber ?? 0,
            "firstResponder": window.firstResponder.map { String(describing: type(of: $0)) } ?? "none",
            "firstResponderObjectID": window.firstResponder.map { String(describing: ObjectIdentifier($0)) } ?? "none",
            "selection": (window.firstResponder as? NSTextView).map {
                ["location": $0.selectedRange().location, "length": $0.selectedRange().length]
            } as Any? ?? NSNull(), "delegateActions": delegateActions, "documentActions": documentActions,
            "text": field.stringValue, "guard": guardField.stringValue, "cursor": [pointer.x, pointer.y],
            "events": events, "lastEvent": eventSequence
        ]
        guard let data = try? JSONSerialization.data(withJSONObject: value, options: [.sortedKeys]) else { return }
        try? data.write(to: folder.appendingPathComponent("state.json"), options: .atomic)
    }

    func receive(_ event: NSEvent) {
        let keyboard = [.keyDown, .keyUp, .flagsChanged].contains(event.type)
        let mouse = [
            .mouseMoved, .leftMouseDown, .leftMouseUp, .leftMouseDragged, .rightMouseDown, .rightMouseUp,
            .otherMouseDown, .otherMouseUp
        ].contains(event.type)
        guard keyboard || mouse || event.type == .appKitDefined || event.type.rawValue == 21 else { return }
        eventSequence += 1
        var value: [String: Any] = [
            "index": eventSequence, "type": event.type.rawValue, "timestamp": event.timestamp,
            "flags": event.modifierFlags.rawValue,
            "window": event.windowNumber, "active": NSApp.isActive, "key": window?.isKeyWindow ?? false,
            "sourcePID": event.cgEvent?.getIntegerValueField(.eventSourceUnixProcessID) ?? -1
        ]
        if keyboard { value["keyCode"] = event.keyCode }
        if keyboard && event.type != .flagsChanged {
            value["charactersIgnoringModifiers"] = event.charactersIgnoringModifiers ?? "<nil>"
        }
        if mouse { value["point"] = [event.locationInWindow.x, event.locationInWindow.y] }
        if event.type == .appKitDefined { value["subtype"] = event.subtype.rawValue }
        events.append(value)
        if events.count > 160 { events.removeFirst(events.count - 160) }
        record()
    }

    @objc func countDelegateCommand(_ sender: Any?) {
        delegateActions += 1
        record()
    }

    func applicationDidFinishLaunching(_ notification: Notification) {
        let menu = NSMenu(), commandsItem = NSMenuItem()
        let commands = NSMenu(title: "Commands")
        let direct = NSMenuItem(
            title: "Count delegate command", action: #selector(countDelegateCommand(_:)), keyEquivalent: "l")
        direct.target = self
        direct.keyEquivalentModifierMask = .command
        commands.addItem(direct)
        let indirect = NSMenuItem(
            title: "Count document command", action: #selector(KeyboardDocumentView.countDocumentCommand(_:)),
            keyEquivalent: "k")
        indirect.target = nil
        indirect.keyEquivalentModifierMask = .command
        commands.addItem(indirect)
        commandsItem.submenu = commands
        menu.addItem(commandsItem)
        let edit = NSMenuItem(title: "Edit", action: nil, keyEquivalent: "")
        edit.submenu = NSMenu(title: "Edit")
        let selectAll = NSMenuItem(title: "Select All", action: #selector(NSText.selectAll(_:)), keyEquivalent: "a")
        selectAll.target = nil
        selectAll.keyEquivalentModifierMask = .command
        edit.submenu!.addItem(selectAll)
        menu.addItem(edit)
        NSApp.mainMenu = menu

        window = NSWindow(
            contentRect: NSRect(x: 180, y: 240, width: 520, height: 220), styleMask: [.titled, .closable],
            backing: .buffered, defer: false)
        window.title = "Keyboard window command probe"
        window.isReleasedWhenClosed = false
        window.tabbingMode = .disallowed
        let document = KeyboardDocumentView(frame: window.contentView!.bounds)
        document.onCommand = { [weak self] in
            self?.documentActions += 1
            self?.record()
        }
        window.contentView = document
        field.frame = NSRect(x: 30, y: 120, width: 450, height: 28)
        field.setAccessibilityLabel("Shortcut input")
        guardField.frame = NSRect(x: 30, y: 65, width: 450, height: 28)
        guardField.isEditable = false
        guardField.setAccessibilityLabel("Unrelated guard")
        document.addSubview(field)
        document.addSubview(guardField)
        window.makeKeyAndOrderFront(nil)
        window.makeFirstResponder(field)
        (window.firstResponder as? NSTextView)?.setSelectedRange(
            NSRange(location: field.stringValue.utf16.count, length: 0))
        if #available(macOS 14.0, *) { NSApp.activate() } else { NSApp.activate(ignoringOtherApps: true) }
        record()
        _ = Timer.scheduledTimer(withTimeInterval: 0.05, repeats: true) { _ in
            MainActor.assumeIsolated { self.record() }
        }
    }
}

@main enum KeyboardWindowCommandProbe {
    @MainActor static func main() {
        let app = KeyboardCommandApplication.shared
        app.setActivationPolicy(.regular)
        let delegate = KeyboardCommandDelegate()
        app.delegate = delegate
        app.run()
    }
}
