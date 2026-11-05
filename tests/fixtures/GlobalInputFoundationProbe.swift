// Copyright (c) Huawei Technologies Co., Ltd. 2026. All rights reserved.

import AppKit
import ApplicationServices

/// Independent application-side evidence for raw and addressed input. The
/// command file is test setup only; it never sends mouse or keyboard events.
@MainActor final class FoundationApplication: NSApplication {
    var events = [[String: Any]]()
    override func sendEvent(_ event: NSEvent) {
        if [.keyDown, .keyUp, .flagsChanged, .leftMouseDown, .leftMouseUp,
            .leftMouseDragged, .rightMouseDown, .rightMouseUp, .scrollWheel].contains(event.type)
        {
            var value: [String: Any] = [
                "uptime": ProcessInfo.processInfo.systemUptime, "epoch": Date().timeIntervalSince1970,
                "type": event.type.rawValue, "window": event.windowNumber,
                "modifiers": event.modifierFlags.rawValue,
                "screenPoint": event.cgEvent.map { [$0.location.x, $0.location.y] } ?? [],
            ]
            if [.keyDown, .keyUp, .flagsChanged].contains(event.type) {
                value["keyCode"] = event.keyCode
                if event.type != .flagsChanged { value["characters"] = event.characters ?? "" }
            }
            events.append(value)
        }
        super.sendEvent(event)
    }
}

@MainActor final class FoundationWindow: NSWindow {
    var coarseAX = false
    override func accessibilityHitTest(_ point: NSPoint) -> Any? {
        if MainActor.assumeIsolated({ coarseAX }) { return self }
        return super.accessibilityHitTest(point)
    }
    override func accessibilityChildren() -> [Any]? {
        if coarseAX { return [] }
        return super.accessibilityChildren()
    }
}

/// No accessible editor or writable AX value is published. Actual event
/// callbacks, first responder and displayed text are the acceptance oracle.
@MainActor final class FoundationCanvas: NSView {
    var value = "", clicks = 0, inputs = 0, scrolls = 0
    var record: () -> Void = {}
    override var acceptsFirstResponder: Bool { true }
    override func acceptsFirstMouse(for event: NSEvent?) -> Bool { true }
    override func mouseDown(with event: NSEvent) {
        clicks += 1
        window?.makeFirstResponder(self)
        needsDisplay = true
        record()
    }
    override func keyDown(with event: NSEvent) {
        if event.keyCode == 51 {
            if !value.isEmpty { value.removeLast() }
        } else if let text = event.characters,
            !event.modifierFlags.contains(.command), !event.modifierFlags.contains(.control)
        {
            value += text
        }
        inputs += 1
        needsDisplay = true
        record()
    }
    override func scrollWheel(with event: NSEvent) { scrolls += 1; record() }
    override func draw(_ dirtyRect: NSRect) {
        NSColor.textBackgroundColor.setFill()
        bounds.fill()
        NSColor.systemOrange.setStroke()
        NSBezierPath(rect: bounds.insetBy(dx: 1, dy: 1)).stroke()
        let text = value.isEmpty ? "Opaque input — click, then type" : value
        text.draw(at: NSPoint(x: 10, y: 12), withAttributes: [.font: NSFont.systemFont(ofSize: 17)])
    }
}

@MainActor final class FoundationDrag: NSView {
    var downs = 0, moves = 0, ups = 0
    var lastScreenPoint = [Double]()
    var record: () -> Void = {}
    override func acceptsFirstMouse(for event: NSEvent?) -> Bool { true }
    func sample(_ event: NSEvent) {
        lastScreenPoint = event.cgEvent.map { [$0.location.x, $0.location.y] } ?? []
        record()
    }
    override func mouseDown(with event: NSEvent) { downs += 1; sample(event) }
    override func mouseDragged(with event: NSEvent) { moves += 1; sample(event) }
    override func mouseUp(with event: NSEvent) { ups += 1; sample(event) }
    override func draw(_ dirtyRect: NSRect) {
        NSColor.systemBlue.withAlphaComponent(0.16).setFill()
        bounds.fill()
        "Drag starts here; release may be outside this window".draw(
            at: NSPoint(x: 12, y: 20), withAttributes: [.font: NSFont.systemFont(ofSize: 13)])
    }
}

@MainActor final class GlobalInputFoundationDelegate: NSObject, NSApplicationDelegate, NSSearchFieldDelegate {
    let folder = Bundle.main.bundleURL.deletingLastPathComponent()
    var window: FoundationWindow!
    let search = NSSearchField(string: "")
    let guardField = NSTextField(string: "Unrelated draft")
    let result = NSTextField(labelWithString: "No search event")
    let canvas = FoundationCanvas(frame: NSRect(x: 20, y: 125, width: 500, height: 55))
    let drag = FoundationDrag(frame: NSRect(x: 20, y: 45, width: 500, height: 60))
    var panel: NSOpenPanel?
    var open: NSButton!
    var changes = 0, searches = 0, lastCommand = ""
    var setup = [[String: Any]]()

    func quartz(_ point: NSPoint) -> [Double] {
        [point.x, CGDisplayBounds(CGMainDisplayID()).height - point.y]
    }
    func frame(_ window: NSWindow) -> [Double] {
        [window.frame.minX, CGDisplayBounds(CGMainDisplayID()).height - window.frame.maxY,
         window.frame.width, window.frame.height]
    }
    func point(_ view: NSView) -> [Double] {
        quartz(window.convertPoint(toScreen: view.convert(NSPoint(x: view.bounds.midX, y: view.bounds.midY), to: nil)))
    }
    func imagePoint(_ view: NSView) -> [Double] {
        let global = point(view), box = frame(window)
        return [global[0] - box[0], global[1] - box[1]]
    }
    func record() {
        guard let window, let open else { return }
        let editor = search.currentEditor() as? NSTextView
        let events = (NSApp as? FoundationApplication)?.events ?? []
        let keys = Set(events.compactMap { $0["keyCode"] as? UInt16 }).union([53, 0, 56, 55])
        let flags = CGEventSource.flagsState(.combinedSessionState)
        var value: [String: Any] = [
            "epoch": Date().timeIntervalSince1970, "uptime": ProcessInfo.processInfo.systemUptime,
            "pid": getpid(), "bundle": Bundle.main.bundleIdentifier ?? "", "title": window.title,
            "window": window.windowNumber, "frameQuartz": frame(window), "active": NSApp.isActive,
            "keyWindow": NSApp.keyWindow?.windowNumber as Any? ?? NSNull(),
            "foregroundPID": NSWorkspace.shared.frontmostApplication?.processIdentifier as Any? ?? NSNull(),
            "cursor": CGEvent(source: nil).map { [$0.location.x, $0.location.y] } ?? [],
            "coarseAX": window.coarseAX, "value": search.stringValue, "changes": changes, "searches": searches,
            "result": result.stringValue, "guard": guardField.stringValue,
            "searchFocused": editor != nil && window.firstResponder === editor,
            "selection": editor.map { [$0.selectedRange().location, $0.selectedRange().length] } ?? [],
            "opaqueValue": canvas.value, "opaqueClicks": canvas.clicks, "opaqueInputs": canvas.inputs,
            "opaqueFocused": window.firstResponder === canvas, "scrolls": canvas.scrolls,
            "dragDowns": drag.downs, "dragMoves": drag.moves, "dragUps": drag.ups,
            "dragLastScreenPoint": drag.lastScreenPoint,
            "keysHeld": keys.filter { CGEventSource.keyState(.combinedSessionState, key: $0) }.sorted(),
            "buttonsHeld": [CGMouseButton.left, .right, .center].filter {
                CGEventSource.buttonState(.combinedSessionState, button: $0)
            }.map(\.rawValue),
            "modifierHeld": !flags.intersection([.maskCommand, .maskAlternate, .maskControl, .maskShift]).isEmpty,
            "searchPoint": point(search), "searchImagePoint": imagePoint(search),
            "opaquePoint": point(canvas), "opaqueImagePoint": imagePoint(canvas),
            "dragPoint": point(drag), "dragImagePoint": imagePoint(drag),
            "openPoint": point(open), "openImagePoint": imagePoint(open),
            "events": events, "setup": setup, "lastCommand": lastCommand,
        ]
        if let panel {
            value["panel"] = ["visible": panel.isVisible, "window": panel.windowNumber,
                              "title": panel.title ?? "", "frameQuartz": frame(panel)]
        }
        try? JSONSerialization.data(withJSONObject: value, options: [.sortedKeys]).write(
            to: folder.appendingPathComponent("state.json"), options: .atomic)
    }
    @objc func searched() { searches += 1; result.stringValue = "Matched: " + search.stringValue; record() }
    func controlTextDidChange(_ notification: Notification) { changes += 1; record() }
    @objc func openPanel() {
        panel = NSOpenPanel()
        panel!.title = "Foundation hosted file panel"
        panel!.directoryURL = URL(fileURLWithPath: "/Applications")
        panel!.beginSheetModal(for: window) { _ in }
        record()
    }
    func tick() {
        if let data = try? Data(contentsOf: folder.appendingPathComponent("command.json")),
            let command = try? JSONSerialization.jsonObject(with: data) as? [String: Any],
            let id = command["id"] as? String, id != lastCommand
        {
            lastCommand = id
            let kind = command["kind"] as? String ?? ""
            setup.append(["id": id, "kind": kind, "epoch": Date().timeIntervalSince1970,
                          "uptime": ProcessInfo.processInfo.systemUptime])
            switch kind {
            case "front":
                window.makeKeyAndOrderFront(nil)
                NSApp.activate(ignoringOtherApps: true)
            case "back": window.orderBack(nil)
            case "reset":
                panel?.cancel(nil)
                panel = nil
                window.coarseAX = command["coarseAX"] as? Bool ?? false
                search.stringValue = ""
                guardField.stringValue = "Unrelated draft"
                result.stringValue = "No search event"
                changes = 0; searches = 0
                canvas.value = ""; canvas.clicks = 0; canvas.inputs = 0; canvas.scrolls = 0
                canvas.needsDisplay = true
                drag.downs = 0; drag.moves = 0; drag.ups = 0; drag.lastScreenPoint = []
                window.makeFirstResponder(guardField)
            case "move":
                if let x = command["x"] as? Double, let y = command["y"] as? Double {
                    window.setFrameOrigin(NSPoint(x: x,
                        y: CGDisplayBounds(CGMainDisplayID()).height - y - window.frame.height))
                }
            case "closePanel": panel?.cancel(nil); panel = nil
            case "quit": NSApp.terminate(nil)
            default: break
            }
        }
        record()
    }
    func applicationDidFinishLaunching(_ notification: Notification) {
        window = FoundationWindow(contentRect: NSRect(x: 100, y: 150, width: 540, height: 330),
                                  styleMask: [.titled, .closable], backing: .buffered, defer: false)
        window.title = "Input foundation " + (Bundle.main.bundleIdentifier?.split(separator: ".").last.map(String.init) ?? "owned")
        window.isReleasedWhenClosed = false
        window.tabbingMode = .disallowed
        let visible = NSScreen.screens[0].visibleFrame
        window.setFrameOrigin(NSPoint(x: visible.minX + 30, y: visible.maxY - window.frame.height - 30))
        search.frame = NSRect(x: 20, y: 275, width: 500, height: 28)
        search.setAccessibilityIdentifier("foundation-search")
        search.sendsSearchStringImmediately = true
        search.sendsWholeSearchString = false
        search.target = self; search.action = #selector(searched); search.delegate = self
        guardField.frame = NSRect(x: 20, y: 215, width: 500, height: 28)
        guardField.setAccessibilityIdentifier("foundation-guard")
        result.frame = NSRect(x: 20, y: 245, width: 500, height: 22)
        canvas.setAccessibilityElement(false)
        drag.setAccessibilityElement(false)
        canvas.record = { [weak self] in self?.record() }
        drag.record = { [weak self] in self?.record() }
        open = NSButton(title: "Open real file panel", target: self, action: #selector(openPanel))
        open.frame = NSRect(x: 20, y: 5, width: 220, height: 30)
        for view in [search, guardField, result, canvas, drag, open!] { window.contentView!.addSubview(view) }
        let menu = NSMenu(), application = NSMenuItem(title: "Foundation", action: nil, keyEquivalent: "")
        application.submenu = NSMenu()
        application.submenu!.addItem(withTitle: "Quit", action: #selector(NSApplication.terminate(_:)), keyEquivalent: "q")
        menu.addItem(application)
        let edit = NSMenuItem(title: "Edit", action: nil, keyEquivalent: "")
        edit.submenu = NSMenu()
        edit.submenu!.addItem(withTitle: "Select All", action: #selector(NSText.selectAll(_:)), keyEquivalent: "a")
        edit.submenu!.addItem(withTitle: "Paste", action: #selector(NSText.paste(_:)), keyEquivalent: "v")
        menu.addItem(edit)
        NSApp.mainMenu = menu
        window.orderBack(nil); window.makeKey(); window.makeMain(); window.makeFirstResponder(guardField)
        record()
        Timer.scheduledTimer(withTimeInterval: 0.03, repeats: true) { _ in
            MainActor.assumeIsolated { self.tick() }
        }
    }
}

@main enum GlobalInputFoundationProbe {
    @MainActor static func main() {
        let app = FoundationApplication.shared
        app.setActivationPolicy(.regular)
        let delegate = GlobalInputFoundationDelegate()
        app.delegate = delegate
        app.run()
    }
}
