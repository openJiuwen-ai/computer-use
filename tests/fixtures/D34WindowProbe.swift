// Copyright (c) Huawei Technologies Co., Ltd. 2026. All rights reserved.

import AppKit
import ApplicationServices

@MainActor final class D34HiddenContent: NSView {
    override nonisolated func accessibilityChildren() -> [Any]? { [] }
}

@MainActor final class D34Application: NSApplication {
    var observedEvents = [[String: Any]]()
    override func sendEvent(_ event: NSEvent) {
        if [.leftMouseDown, .leftMouseUp, .keyDown, .keyUp].contains(event.type) {
            observedEvents.append([
                "type": event.type.rawValue, "window": event.windowNumber,
                "keyCode": [.keyDown, .keyUp].contains(event.type) ? Int(event.keyCode) : -1,
                "time": event.timestamp
            ])
        }
        super.sendEvent(event)
    }
}

@MainActor final class D34Document {
    let name: String
    let window: NSWindow
    let field = NSTextField(string: "")
    let guardField = NSTextField(string: "Guard unchanged")
    let countButton = NSButton(title: "Count", target: nil, action: nil)
    let marker = NSView()
    var markerRGB = [Int]()
    var count = 0
    var submits = 0
    var closed = false
    var closeMode = "normal"
    var closeRequests = 0
    var changes = [String]()
    init(name: String, window: NSWindow) { self.name = name; self.window = window }
}

@MainActor final class D34Delegate: NSObject, NSApplicationDelegate, NSTextFieldDelegate, NSWindowDelegate {
    let folder = Bundle.main.bundleURL.deletingLastPathComponent()
    var documents = [String: D34Document]()
    var commandSequence = 0
    var commandError: String?
    var opens = 0
    var sheetOwner: String?
    var pointerHit = [String: Any]()

    func document(_ sender: NSView) -> D34Document? {
        documents.values.first { $0.window === sender.window }
    }
    @objc func count(_ sender: NSButton) { document(sender)?.count += 1; record() }
    @objc func submit(_ sender: NSTextField) { document(sender)?.submits += 1; record() }
    @objc func openIndependent(_ sender: NSButton) {
        guard documents["Child"] == nil else { return }
        opens += 1
        let child = create("Child", title: "", offset: 70)
        show(child)
        record()
    }
    @objc func closeSelf(_ sender: NSButton) { document(sender)?.window.close(); record() }
    @objc func openSheet(_ sender: NSButton) {
        guard let owner = document(sender), sheetOwner == nil else { return }
        let sheet = create("Sheet", title: "D34 sheet", offset: 0, orderInitially: false)
        sheetOwner = owner.name
        owner.window.beginSheet(sheet.window) { _ in }
        sheet.window.makeFirstResponder(sheet.field)
        record()
    }
    @objc func dismissSheet(_ sender: NSButton) {
        guard let owner = sheetOwner, let sheet = documents["Sheet"] else { return }
        documents[owner]?.window.endSheet(sheet.window)
        sheet.window.orderOut(nil)
        sheet.closed = true
        sheetOwner = nil
        record()
    }
    func controlTextDidChange(_ notification: Notification) {
        guard let field = notification.object as? NSTextField, let doc = document(field) else { return }
        doc.changes.append(field.stringValue)
        record()
    }
    func windowWillClose(_ notification: Notification) {
        guard let window = notification.object as? NSWindow,
            let doc = documents.values.first(where: { $0.window === window }) else { return }
        doc.closed = true
        record()
    }

    func windowShouldClose(_ sender: NSWindow) -> Bool {
        guard let doc = documents.values.first(where: { $0.window === sender }) else { return true }
        doc.closeRequests += 1
        record()
        if doc.closeMode == "deny" { return false }
        if doc.closeMode == "confirm" {
            let alert = NSAlert()
            alert.messageText = "Confirm close \(doc.name)"
            alert.informativeText = "The SDK must leave this choice to its caller."
            alert.addButton(withTitle: "Cancel close")
            alert.addButton(withTitle: "Close document")
            alert.beginSheetModal(for: sender) { response in
                if response == .alertSecondButtonReturn { sender.close() }
                self.record()
            }
            record()
            return false
        }
        return true
    }
    func show(_ doc: D34Document) {
        NSApp.activate(ignoringOtherApps: true)
        doc.window.makeKeyAndOrderFront(nil)
        doc.window.makeMain()
        doc.window.makeFirstResponder(doc.field)
    }
    @discardableResult func create(_ name: String, title: String, offset: CGFloat,
                                   weakAX: Bool = false, orderInitially: Bool = true) -> D34Document {
        let window = NSWindow(contentRect: NSRect(x: 0, y: 0, width: 500, height: 300),
                              styleMask: [.titled, .closable], backing: .buffered, defer: false)
        window.title = title
        window.isReleasedWhenClosed = false
        window.delegate = self
        let screen = NSScreen.screens[0].visibleFrame
        window.setFrameOrigin(NSPoint(x: min(screen.maxX - 510, screen.minX + 80 + offset),
                                      y: min(screen.maxY - 350, screen.minY + 90 + offset)))
        if weakAX { window.contentView = D34HiddenContent(frame: window.contentView!.bounds) }
        let doc = D34Document(name: name, window: window)
        documents[name] = doc
        let heading = NSTextField(labelWithString: "Document " + name)
        heading.frame = NSRect(x: 20, y: 255, width: 440, height: 25)
        doc.field.frame = NSRect(x: 20, y: 210, width: 220, height: 28)
        doc.field.setAccessibilityLabel(name + " input")
        doc.field.setAccessibilityIdentifier("d34-input-" + name)
        doc.field.delegate = self
        doc.field.target = self
        doc.field.action = #selector(submit)
        doc.field.cell?.sendsActionOnEndEditing = false
        doc.guardField.frame = NSRect(x: 260, y: 210, width: 220, height: 28)
        doc.guardField.setAccessibilityLabel(name + " guard")
        doc.countButton.frame = NSRect(x: 20, y: 155, width: 130, height: 32)
        doc.countButton.target = self
        doc.countButton.action = #selector(count)
        doc.markerRGB = ["A": [220, 30, 40], "B": [20, 70, 220], "Child": [20, 180, 70],
                         "Sheet": [230, 140, 20], "Weak": [160, 40, 200]][name]!
        doc.marker.frame = NSRect(x: 420, y: 40, width: 40, height: 40)
        doc.marker.wantsLayer = true
        doc.marker.layer?.backgroundColor = NSColor(srgbRed: CGFloat(doc.markerRGB[0]) / 255,
            green: CGFloat(doc.markerRGB[1]) / 255, blue: CGFloat(doc.markerRGB[2]) / 255, alpha: 1).cgColor
        let controls: [(String, Selector, CGFloat)] = name == "Sheet"
            ? [("Dismiss sheet", #selector(dismissSheet), 100)]
            : [("Open independent window", #selector(openIndependent), 100),
               ("Open sheet", #selector(openSheet), 60), ("Close this window", #selector(closeSelf), 20)]
        for (title, action, y) in controls {
            let button = NSButton(title: title, target: self, action: action)
            button.frame = NSRect(x: 20, y: y, width: 230, height: 32)
            window.contentView!.addSubview(button)
        }
        for view in [heading, doc.field, doc.guardField, doc.countButton, doc.marker] { window.contentView!.addSubview(view) }
        if orderInitially { window.orderBack(nil) }
        window.makeFirstResponder(doc.field)
        return doc
    }
    func point(_ view: NSView) -> [CGFloat] {
        let p = view.window!.convertPoint(toScreen: view.convert(NSPoint(x: view.bounds.midX, y: view.bounds.midY), to: nil))
        return [p.x - view.window!.frame.minX, view.window!.frame.maxY - p.y]
    }
    func record() {
        let states = documents.mapValues { doc -> [String: Any] in
            let frame = doc.window.frame
            return [
                "title": doc.window.title, "window": doc.window.windowNumber,
                "object": String(describing: ObjectIdentifier(doc.window)),
                "closed": doc.closed, "visible": doc.window.isVisible,
                "closeMode": doc.closeMode, "closeRequests": doc.closeRequests,
                "key": doc.window.isKeyWindow, "field": doc.field.stringValue,
                "isSheet": doc.window.isSheet,
                "sheetParent": doc.window.sheetParent?.windowNumber ?? -1,
                "attachedSheet": doc.window.attachedSheet?.windowNumber ?? -1,
                "inputEditing": doc.field.currentEditor() != nil,
                "firstResponder": String(describing: doc.window.firstResponder),
                "guard": doc.guardField.stringValue, "count": doc.count,
                "submits": doc.submits, "changes": doc.changes,
                "frame": [frame.minX, frame.minY, frame.width, frame.height],
                "countPoint": point(doc.countButton), "fieldPoint": point(doc.field),
                "markerPoint": point(doc.marker), "markerRGB": doc.markerRGB
            ]
        }
        let flags = CGEventSource.flagsState(.combinedSessionState)
        let value: [String: Any] = [
            "pid": getpid(), "sequence": commandSequence, "commandError": commandError as Any? ?? NSNull(),
            "pointerHit": pointerHit,
            "opens": opens, "sheetOwner": sheetOwner as Any? ?? NSNull(), "documents": states,
            "keyWindow": NSApp.keyWindow?.windowNumber ?? -1,
            "frontmostPID": NSWorkspace.shared.frontmostApplication?.processIdentifier ?? -1,
            "mouse": [NSEvent.mouseLocation.x, NSEvent.mouseLocation.y],
            "keysHeld": (0..<128).filter { CGEventSource.keyState(.combinedSessionState, key: CGKeyCode($0)) },
            "buttonsHeld": (0..<3).filter { CGEventSource.buttonState(.combinedSessionState, button: CGMouseButton(rawValue: UInt32($0))!) },
            "modifierHeld": !flags.intersection([.maskCommand, .maskAlternate, .maskControl, .maskShift]).isEmpty,
            "events": (NSApp as! D34Application).observedEvents
        ]
        do {
            try JSONSerialization.data(withJSONObject: value, options: [.sortedKeys]).write(
                to: folder.appendingPathComponent("state.json"), options: .atomic)
        } catch { fputs("Could not record fixture state: \(error)\n", stderr) }
    }
    func consumeCommand() {
        guard let data = try? Data(contentsOf: folder.appendingPathComponent("command.json")),
            let command = try? JSONSerialization.jsonObject(with: data) as? [String: Any],
            let sequence = command["sequence"] as? Int, sequence > commandSequence else { return }
        commandSequence = sequence
        commandError = nil
        let name = command["name"] as? String ?? "A"
        switch command["action"] as? String {
        case "pointer-check":
            if let doc = documents[name], !doc.closed {
                let point = doc.window.convertPoint(toScreen: doc.countButton.convert(
                    NSPoint(x: doc.countButton.bounds.midX, y: doc.countButton.bounds.midY), to: nil))
                var element: AXUIElement?
                let status = AXUIElementCopyElementAtPosition(AXUIElementCreateSystemWide(), Float(point.x),
                    Float(NSScreen.screens[0].frame.maxY - point.y), &element)
                var pid: pid_t = -1
                if let element { AXUIElementGetPid(element, &pid) }
                pointerHit = ["window": NSWindow.windowNumber(at: point, belowWindowWithWindowNumber: 0),
                    "axPID": pid, "axStatus": status.rawValue, "point": [point.x, point.y]]
            } else { commandError = "no live pointer target" }
        case "focus":
            if let doc = documents[name], !doc.closed { show(doc) } else { commandError = "no live document" }
        case "close": documents[name]?.window.close()
        case "deny-close": documents[name]?.closeMode = "deny"
        case "confirm-close": documents[name]?.closeMode = "confirm"
        case "weak": show(create("Weak", title: "", offset: 110, weakAX: true))
        case "show-sheet":
            if let doc = documents[name], !doc.closed { openSheet(doc.countButton) }
            else { commandError = "no live sheet owner" }
        case "dismiss-sheet":
            if let sheet = documents["Sheet"], sheetOwner != nil { dismissSheet(sheet.countButton) }
            else { commandError = "no attached sheet" }
        default: commandError = "unknown command"
        }
        record()
    }
    func applicationDidFinishLaunching(_ notification: Notification) {
        _ = create("B", title: "D34 document", offset: 35)
        let a = create("A", title: "D34 document", offset: 0)
        a.window.makeKey(); a.window.makeMain()
        record()
        Timer.scheduledTimer(withTimeInterval: 0.05, repeats: true) { _ in
            MainActor.assumeIsolated { self.consumeCommand(); self.record() }
        }
    }
    func applicationShouldTerminateAfterLastWindowClosed(_ sender: NSApplication) -> Bool { false }
}

@main enum D34WindowProbe {
    @MainActor static func main() {
        let app = D34Application.shared
        app.setActivationPolicy(.regular)
        let delegate = D34Delegate()
        app.delegate = delegate
        app.run()
    }
}
