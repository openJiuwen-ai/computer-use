// Copyright (c) Huawei Technologies Co., Ltd. 2026. All rights reserved.

import AppKit

@MainActor final class KeyboardCommandApplication: NSApplication {
    override func sendEvent(_ event: NSEvent) {
        (delegate as? KeyboardCommandDelegate)?.receive(event)
        super.sendEvent(event)
    }
}

/// A nil-target menu command must reach this original document responder.
@MainActor final class KeyboardDocumentView: NSView, NSMenuItemValidation {
    var onCommand: (Any?) -> Void = { _ in }
    var validatesCommand: () -> Bool = { true }
    override var acceptsFirstResponder: Bool { true }
    @objc func countDocumentCommand(_ sender: Any?) { onCommand(sender) }
    func validateMenuItem(_ item: NSMenuItem) -> Bool {
        item.action == #selector(countDocumentCommand(_:)) ? validatesCommand() : true
    }
}

/// Ordinary AppKit windows and field editors; no accessibility overrides.
@MainActor final class KeyboardCommandDelegate: NSObject, NSApplicationDelegate, NSMenuDelegate {
    let folder = Bundle.main.bundleURL.deletingLastPathComponent()
    let field = NSTextField(string: "KEYBOARD-SEED")
    let guardField = NSTextField(string: "GUARD")
    var window: NSWindow!
    var delegateActions = 0, documentActions = 0, sequence = 0, eventSequence = 0
    var events = [[String: Any]]()
    let menuProbe = CommandLine.arguments.contains("--menu-probe")
    let rebuildMenu = CommandLine.arguments.contains("--rebuild-menu")
    var menuOpens = 0, menuCloses = 0, menuNeedsUpdates = 0, menuGeneration = 0
    var menuValidated = false, menuTracking = false
    var trackingBegins = 0, trackingEnds = 0, notificationTracking = false
    var menuLifecycle = [[String: Any]]()
    var rebuildArmed = false, lastCommand = ""
    var rebuildUptime: TimeInterval?
    var documentMenuItem: NSMenuItem?
    var documentActionSenders = [String](), callbackMenuGenerations = [Int]()

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
            "lastCommand": lastCommand,
            "selection": (window.firstResponder as? NSTextView).map {
                ["location": $0.selectedRange().location, "length": $0.selectedRange().length]
            } as Any? ?? NSNull(), "delegateActions": delegateActions, "documentActions": documentActions,
            "text": field.stringValue, "guard": guardField.stringValue, "cursor": [pointer.x, pointer.y],
            "events": events, "lastEvent": eventSequence
        ]
        var output = value
        output["menu"] = [
            "probe": menuProbe, "rebuild": rebuildMenu, "opens": menuOpens, "closes": menuCloses,
            "needsUpdates": menuNeedsUpdates,
            "generation": menuGeneration, "validated": menuValidated, "tracking": menuTracking,
            "trackingBegins": trackingBegins, "trackingEnds": trackingEnds,
            "notificationTracking": notificationTracking, "lifecycle": menuLifecycle,
            "rebuildArmed": rebuildArmed, "rebuildUptime": rebuildUptime as Any? ?? NSNull(),
            "item": documentMenuItem.map { String(describing: ObjectIdentifier($0)) } ?? "none",
            "enabled": documentMenuItem?.isEnabled ?? false,
            "actionSenders": documentActionSenders, "callbackGenerations": callbackMenuGenerations
        ]
        guard let data = try? JSONSerialization.data(withJSONObject: output, options: [.sortedKeys]) else { return }
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

    func makeDocumentItem() -> NSMenuItem {
        let item = NSMenuItem(
            title: "Count document command", action: #selector(KeyboardDocumentView.countDocumentCommand(_:)),
            keyEquivalent: "k")
        item.target = nil
        item.keyEquivalentModifierMask = .command
        return item
    }

    func recordMenuLifecycle(_ kind: String, menu: NSMenu) {
        menuLifecycle.append([
            "kind": kind, "uptime": ProcessInfo.processInfo.systemUptime,
            "menuTitle": menu.title, "menuObjectID": String(describing: ObjectIdentifier(menu)),
            "isMainMenu": menu === NSApp.mainMenu, "generation": menuGeneration,
            "notificationTracking": notificationTracking
        ])
        if menuLifecycle.count > 160 { menuLifecycle.removeFirst(menuLifecycle.count - 160) }
    }

    @objc func menuTrackingBegan(_ notification: Notification) {
        guard let menu = notification.object as? NSMenu, menu === NSApp.mainMenu else { return }
        trackingBegins += 1
        notificationTracking = true
        recordMenuLifecycle("didBeginTracking", menu: menu)
        record()
    }

    @objc func menuTrackingEnded(_ notification: Notification) {
        guard let menu = notification.object as? NSMenu, menu === NSApp.mainMenu else { return }
        trackingEnds += 1
        notificationTracking = false
        recordMenuLifecycle("didEndTracking", menu: menu)
        record()
    }

    func menuNeedsUpdate(_ menu: NSMenu) {
        menuNeedsUpdates += 1
        recordMenuLifecycle("menuNeedsUpdate", menu: menu)
        // This is AppKit's documented entry point for structural changes.
        // Do not assume it follows didBeginTracking: if it doesn't, this
        // diagnostic deliberately cannot claim a tracking-time rebuild.
        if rebuildMenu, rebuildArmed, notificationTracking, menuGeneration == 0, let original = documentMenuItem {
            let index = menu.index(of: original)
            let replacement = makeDocumentItem()
            menu.removeItem(original)
            menu.insertItem(replacement, at: index)
            documentMenuItem = replacement
            menuGeneration += 1
            rebuildArmed = false
            rebuildUptime = ProcessInfo.processInfo.systemUptime
            recordMenuLifecycle("rebuild", menu: menu)
        }
        menuValidated = true
        // AppKit validates menu items after this callback; no recursive update.
        record()
    }

    func menuWillOpen(_ menu: NSMenu) {
        menuOpens += 1
        menuTracking = true
        recordMenuLifecycle("menuWillOpen", menu: menu)
        // AXChildren getters also invoke this callback. It is not proof of
        // tracking, and AppKit forbids structural/item mutations here.
        record()
    }

    func menuDidClose(_ menu: NSMenu) {
        menuCloses += 1
        menuTracking = false
        recordMenuLifecycle("menuDidClose", menu: menu)
        record()
    }

    func tick() {
        if let data = try? Data(contentsOf: folder.appendingPathComponent("command.json")),
           let command = try? JSONSerialization.jsonObject(with: data) as? [String: Any],
           let id = command["id"] as? String, id != lastCommand {
            lastCommand = id
            if command["kind"] as? String == "arm-rebuild", rebuildMenu, menuGeneration == 0 {
                rebuildArmed = true
            }
        }
        record()
    }

    func applicationDidFinishLaunching(_ notification: Notification) {
        let menu = NSMenu()
        // AppKit reserves the first submenu as the application menu and gives
        // it the app's name. Keep Commands as a separate, observable heading.
        let applicationItem = NSMenuItem()
        applicationItem.submenu = NSMenu()
        applicationItem.submenu!.addItem(
            withTitle: "Quit", action: #selector(NSApplication.terminate(_:)), keyEquivalent: "q")
        menu.addItem(applicationItem)
        let commandsItem = NSMenuItem(title: "Commands", action: nil, keyEquivalent: "")
        let commands = NSMenu(title: "Commands")
        let direct = NSMenuItem(
            title: "Count delegate command", action: #selector(countDelegateCommand(_:)), keyEquivalent: "l")
        direct.target = self
        direct.keyEquivalentModifierMask = .command
        commands.addItem(direct)
        let indirect = makeDocumentItem()
        documentMenuItem = indirect
        commands.addItem(indirect)
        if menuProbe { commands.delegate = self }
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
        // AppKit documents the main menu as the notification object, not the
        // Commands submenu. Record actual notification/delegate ordering; AX
        // getters are known to invoke the delegate, so do not infer tracking.
        NotificationCenter.default.addObserver(
            self, selector: #selector(menuTrackingBegan(_:)), name: NSMenu.didBeginTrackingNotification, object: menu)
        NotificationCenter.default.addObserver(
            self, selector: #selector(menuTrackingEnded(_:)), name: NSMenu.didEndTrackingNotification, object: menu)

        window = NSWindow(
            contentRect: NSRect(x: 180, y: 240, width: 520, height: 220), styleMask: [.titled, .closable],
            backing: .buffered, defer: false)
        window.title = "Keyboard window command probe"
        window.isReleasedWhenClosed = false
        window.tabbingMode = .disallowed
        let document = KeyboardDocumentView(frame: window.contentView!.bounds)
        document.onCommand = { [weak self] sender in
            self?.documentActions += 1
            if let item = sender as? NSMenuItem {
                self?.documentActionSenders.append(String(describing: ObjectIdentifier(item)))
                self?.callbackMenuGenerations.append(self?.menuGeneration ?? -1)
                if let menu = item.menu { self?.recordMenuLifecycle("documentAction", menu: menu) }
            }
            self?.record()
        }
        document.validatesCommand = { [weak self] in
            guard let self else { return false }
            return !self.menuProbe || self.menuValidated && NSApp.mainWindow === self.window
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
        commands.update()
        record()
        _ = Timer.scheduledTimer(withTimeInterval: 0.05, repeats: true) { _ in
            MainActor.assumeIsolated { self.tick() }
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
