// Copyright (c) Huawei Technologies Co., Ltd. 2026. All rights reserved.

import AppKit

@MainActor final class DesktopProbeApplication: NSApplication {
    var keys = [[String: Any]]()
    override func sendEvent(_ event: NSEvent) {
        if [.keyDown, .keyUp].contains(event.type) {
            keys.append([
                "type": event.type.rawValue, "code": event.keyCode,
                "characters": event.characters ?? "", "window": event.windowNumber,
            ])
        }
        super.sendEvent(event)
    }
}

@MainActor final class DesktopProbeDrag: NSView {
    var downs = 0, moves = 0, ups = 0
    var marker = NSPoint(x: 60, y: 65)
    var record: () -> Void = {}
    override func mouseDown(with event: NSEvent) {
        downs += 1
        marker = convert(event.locationInWindow, from: nil)
        needsDisplay = true
        record()
    }
    override func mouseDragged(with event: NSEvent) {
        moves += 1
        marker = convert(event.locationInWindow, from: nil)
        needsDisplay = true
        record()
    }
    override func mouseUp(with event: NSEvent) {
        ups += 1
        marker = convert(event.locationInWindow, from: nil)
        needsDisplay = true
        record()
    }
    override func draw(_ dirtyRect: NSRect) {
        NSColor.controlBackgroundColor.setFill()
        bounds.fill()
        NSColor.controlAccentColor.setFill()
        NSBezierPath(ovalIn: NSRect(x: marker.x - 12, y: marker.y - 12, width: 24, height: 24)).fill()
        "Drag the blue marker".draw(at: NSPoint(x: 12, y: 12), withAttributes: [.font: NSFont.systemFont(ofSize: 16)])
    }
}

@MainActor final class DesktopProbeDocument: NSView {
    override var isFlipped: Bool { true }
}

@MainActor final class DesktopPublicInputDelegate: NSObject, NSApplicationDelegate {
    let folder = Bundle.main.bundleURL.deletingLastPathComponent()
    let field = NSTextField(string: "")
    let guardField = NSTextField(string: "Unrelated draft")
    let result = NSTextField(labelWithString: "Not submitted")
    let scroll = NSScrollView(frame: NSRect(x: 400, y: 50, width: 330, height: 350))
    let drag = DesktopProbeDrag(frame: NSRect(x: 25, y: 75, width: 330, height: 160))
    var window: NSWindow!
    var submissions = 0

    func quartz(_ point: NSPoint) -> [Double] {
        [point.x, CGDisplayBounds(CGMainDisplayID()).height - point.y]
    }
    func screenPoint(_ view: NSView, _ point: NSPoint) -> [Double] {
        quartz(window.convertPoint(toScreen: view.convert(point, to: nil)))
    }
    func frameQuartz() -> CGRect {
        CGRect(x: window.frame.minX, y: CGDisplayBounds(CGMainDisplayID()).height - window.frame.maxY,
               width: window.frame.width, height: window.frame.height)
    }
    func displayFrames() -> [CGRect] {
        var ids = [CGDirectDisplayID](repeating: 0, count: 32), count: UInt32 = 0
        guard CGGetActiveDisplayList(UInt32(ids.count), &ids, &count) == .success else { return [] }
        return ids.prefix(Int(count)).map(CGDisplayBounds)
    }
    func record() {
        guard let window else { return }
        let editor = field.currentEditor() as? NSTextView
        let range = editor?.selectedRange()
        let frame = frameQuartz()
        let keys = (NSApp as? DesktopProbeApplication)?.keys ?? []
        let codes = Set(keys.compactMap { ($0["code"] as? UInt16) }).union([0, 36, 123])
        let flags = CGEventSource.flagsState(.combinedSessionState)
        let value: [String: Any] = [
            "pid": getpid(), "recordedAt": ProcessInfo.processInfo.systemUptime,
            "window": window.windowNumber, "title": window.title,
            "windowObjectID": String(describing: ObjectIdentifier(window)),
            "fieldObjectID": String(describing: ObjectIdentifier(field)),
            "frameQuartz": [frame.minX, frame.minY, frame.width, frame.height],
            "fullyOnDisplay": displayFrames().contains { $0.contains(frame) },
            "frontmostPID": NSWorkspace.shared.frontmostApplication?.processIdentifier as Any? ?? NSNull(),
            "active": NSApp.isActive, "field": field.stringValue, "guard": guardField.stringValue,
            "fieldFocused": editor != nil && window.firstResponder === editor,
            "selection": range.map { ["location": $0.location, "length": $0.length] } as Any? ?? NSNull(),
            "submissions": submissions, "result": result.stringValue, "keys": keys,
            "keysHeld": codes.filter { CGEventSource.keyState(.combinedSessionState, key: $0) }.sorted(),
            "buttonsHeld": [CGMouseButton.left, .right, .center].filter {
                CGEventSource.buttonState(.combinedSessionState, button: $0)
            }.map(\.rawValue),
            "modifierHeld": !flags.intersection([.maskCommand, .maskAlternate, .maskControl, .maskShift]).isEmpty,
            "fieldPointQuartz": screenPoint(field, NSPoint(x: field.bounds.midX, y: field.bounds.midY)),
            "scrollPointQuartz": screenPoint(scroll, NSPoint(x: 160, y: 175)),
            "dragStartQuartz": screenPoint(drag, NSPoint(x: 60, y: 65)),
            "dragEndQuartz": screenPoint(drag, NSPoint(x: 240, y: 65)),
            "scrollY": scroll.contentView.bounds.minY,
            "dragDowns": drag.downs, "dragMoves": drag.moves, "dragUps": drag.ups,
            "marker": [drag.marker.x, drag.marker.y],
        ]
        try! JSONSerialization.data(withJSONObject: value, options: [.sortedKeys]).write(
            to: folder.appendingPathComponent("state.json"), options: .atomic)
    }
    @objc func submitted() {
        submissions += 1
        result.stringValue = "Submitted: " + field.stringValue
        record()
    }
    func applicationDidFinishLaunching(_ notification: Notification) {
        window = NSWindow(contentRect: NSRect(x: 0, y: 0, width: 760, height: 500),
                          styleMask: [.titled, .closable], backing: .buffered, defer: false)
        window.title = "Desktop public input " + (Bundle.main.bundleIdentifier?.split(separator: ".").last.map(String.init) ?? "owned")
        let screen = NSScreen.screens[0].visibleFrame
        window.setFrameOrigin(NSPoint(x: screen.midX - window.frame.width / 2, y: screen.midY - window.frame.height / 2))
        field.frame = NSRect(x: 25, y: 440, width: 330, height: 28)
        field.setAccessibilityLabel("Desktop input")
        field.target = self
        field.action = #selector(submitted)
        field.cell?.sendsActionOnEndEditing = false
        guardField.frame = NSRect(x: 400, y: 440, width: 330, height: 28)
        guardField.setAccessibilityLabel("Unrelated draft")
        result.frame = NSRect(x: 25, y: 360, width: 340, height: 26)
        let document = DesktopProbeDocument(frame: NSRect(x: 0, y: 0, width: 300, height: 1800))
        for index in 0..<45 {
            let line = NSTextField(labelWithString: "Document line \(index + 1)")
            line.frame = NSRect(x: 12, y: index * 40 + 8, width: 270, height: 25)
            document.addSubview(line)
        }
        scroll.documentView = document
        scroll.hasVerticalScroller = true
        drag.record = { [weak self] in self?.record() }
        for view in [field, guardField, result, scroll, drag] { window.contentView!.addSubview(view) }
        let menu = NSMenu(), application = NSMenuItem(title: "DesktopInput", action: nil, keyEquivalent: "")
        application.submenu = NSMenu()
        application.submenu!.addItem(withTitle: "Quit", action: #selector(NSApplication.terminate(_:)), keyEquivalent: "q")
        menu.addItem(application)
        NSApp.mainMenu = menu
        window.orderBack(nil)
        window.makeKey()
        window.makeMain()
        window.makeFirstResponder(guardField)
        record()
        Timer.scheduledTimer(withTimeInterval: 0.05, repeats: true) { _ in MainActor.assumeIsolated { self.record() } }
    }
}

@main enum DesktopPublicInputProbe {
    @MainActor static func main() {
        let app = DesktopProbeApplication.shared
        app.setActivationPolicy(.regular)
        let delegate = DesktopPublicInputDelegate()
        app.delegate = delegate
        app.run()
    }
}
