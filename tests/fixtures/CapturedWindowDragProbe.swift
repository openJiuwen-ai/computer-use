// Copyright (c) Huawei Technologies Co., Ltd. 2026. All rights reserved.

import AppKit
import CoreGraphics

@MainActor enum CapturedWindowDragCoordinates {
    static func quartz(_ point: NSPoint) -> CGPoint {
        CGPoint(x: point.x, y: CGDisplayBounds(CGMainDisplayID()).height - point.y)
    }

    static func point(_ event: NSEvent, window: NSWindow) -> (point: CGPoint, source: String) {
        if let point = event.cgEvent?.location, point.x.isFinite, point.y.isFinite {
            return (point, "event.cgEvent.location")
        }
        return (quartz(window.convertPoint(toScreen: event.locationInWindow)), "convertedCurrentWindowLocation")
    }
}

/// A separate fixture mechanism: real NSView drag callbacks move the same
/// window. This is deliberately not a WindowServer title-bar capture.
@MainActor final class CapturedWindowDragHandle: NSView {
    var observe: (String, NSEvent) -> Void = { _, _ in }
    var anchorPoint: CGPoint?
    var anchorOrigin: NSPoint?
    var anchorSource: String?

    func reset() { anchorPoint = nil; anchorOrigin = nil; anchorSource = nil }
    override func acceptsFirstMouse(for event: NSEvent?) -> Bool { true }
    override func mouseDown(with event: NSEvent) {
        if let window {
            let sample = CapturedWindowDragCoordinates.point(event, window: window)
            anchorPoint = sample.point
            anchorSource = sample.source
            anchorOrigin = window.frame.origin
        }
        observe("handleDown", event)
    }
    override func mouseDragged(with event: NSEvent) {
        if let window, let anchorPoint, let anchorOrigin {
            let current = CapturedWindowDragCoordinates.point(event, window: window).point
            window.setFrameOrigin(NSPoint(x: anchorOrigin.x + current.x - anchorPoint.x,
                                          y: anchorOrigin.y - (current.y - anchorPoint.y)))
        }
        observe("handleDragged", event)
    }
    override func mouseUp(with event: NSEvent) {
        observe("handleUp", event)
        reset()
    }
    override func draw(_ dirtyRect: NSRect) {
        NSColor.controlAccentColor.withAlphaComponent(0.18).setFill()
        bounds.fill()
        "Custom drag handle — moves this window from NSView callbacks".draw(
            at: NSPoint(x: 8, y: 18), withAttributes: [.font: NSFont.systemFont(ofSize: 13)])
    }
}

/// Content input is observed separately from the standard window title bar.
@MainActor final class CapturedWindowDragContent: NSView {
    var observe: (String, NSEvent) -> Void = { _, _ in }
    var prompt = "Drag the standard title bar; content must remain untouched."
    override func mouseDown(with event: NSEvent) { observe("contentDown", event) }
    override func mouseDragged(with event: NSEvent) { observe("contentDragged", event) }
    override func mouseUp(with event: NSEvent) { observe("contentUp", event) }
    override func draw(_ dirtyRect: NSRect) {
        NSColor.windowBackgroundColor.setFill()
        bounds.fill()
        prompt.draw(
            at: NSPoint(x: 20, y: bounds.height - 35),
            withAttributes: [.font: NSFont.systemFont(ofSize: 14)])
    }
}

@MainActor final class CapturedWindowDragDelegate: NSObject, NSApplicationDelegate, NSWindowDelegate {
    let folder = Bundle.main.bundleURL.deletingLastPathComponent()
    let customHandle: Bool
    var window: NSWindow!
    var handle: CapturedWindowDragHandle?
    var originalFrame = NSRect.zero
    var monitor: Any?
    var commandId = ""
    var willMove = 0, didMove = 0, otherClicks = 0
    var resetting = false
    var eventcounts = [String: Int]()
    var timestampLog = [[String: Any]]()

    init(customHandle: Bool) {
        self.customHandle = customHandle
        super.init()
    }

    func resetCounts() {
        eventcounts = Dictionary(uniqueKeysWithValues: [
            "contentDown", "contentDragged", "contentUp", "localDown", "localDragged", "localUp", "localMoved",
            "handleDown", "handleDragged", "handleUp"
        ].map { ($0, 0) })
    }

    func rectangle(_ frame: CGRect) -> [Double] {
        [frame.minX, frame.minY, frame.width, frame.height]
    }

    func quartzFrame() -> (frame: CGRect, source: String) {
        if let rows = CGWindowListCopyWindowInfo(.optionIncludingWindow, CGWindowID(max(0, window.windowNumber)))
            as? [[String: Any]],
            let row = rows.first,
            let bounds = row[kCGWindowBounds as String] as? [String: Any],
            let frame = CGRect(dictionaryRepresentation: bounds as CFDictionary)
        {
            return (frame, "windowServer")
        }
        let frame = window.frame
        return (CGRect(x: frame.minX, y: CGDisplayBounds(CGMainDisplayID()).height - frame.maxY,
                       width: frame.width, height: frame.height), "appKitConversion")
    }

    func append(_ kind: String, event: NSEvent? = nil) {
        guard window != nil else { return }
        let quartz = quartzFrame()
        var item: [String: Any] = [
            "kind": kind, "uptime": ProcessInfo.processInfo.systemUptime,
            "frameAppKit": rectangle(window.frame), "frameQuartz": rectangle(quartz.frame),
            "frameQuartzSource": quartz.source,
        ]
        if let event {
            item["eventType"] = event.type.rawValue
            item["windowNumber"] = event.windowNumber
            item["locationInWindow"] = [event.locationInWindow.x, event.locationInWindow.y]
            item["eventTimestamp"] = event.timestamp
            let converted = window.convertPoint(toScreen: event.locationInWindow)
            let convertedQuartz = CapturedWindowDragCoordinates.quartz(converted)
            let sample = CapturedWindowDragCoordinates.point(event, window: window)
            item["cgEventGlobal"] = event.cgEvent.map { [$0.location.x, $0.location.y] } as Any? ?? NSNull()
            item["convertedCurrentWindowScreenPoint"] = [convertedQuartz.x, convertedQuartz.y]
            item["convertedCurrentWindowAppKitPoint"] = [converted.x, converted.y]
            item["dragGlobalPoint"] = [sample.point.x, sample.point.y]
            item["dragGlobalPointSource"] = sample.source
        }
        timestampLog.append(item)
        if timestampLog.count > 128 { timestampLog.removeFirst(timestampLog.count - 128) }
    }

    func observe(_ kind: String, event: NSEvent) {
        eventcounts[kind, default: 0] += 1
        append(kind, event: event)
        record()
    }

    func windowWillMove(_ notification: Notification) {
        guard !resetting else { return }
        willMove += 1
        append("windowWillMove")
        record()
    }

    func windowDidMove(_ notification: Notification) {
        guard !resetting else { return }
        didMove += 1
        append("windowDidMove")
        record()
    }

    @objc func otherPressed(_ sender: NSButton) {
        otherClicks += 1
        append("otherButtonAction")
        record()
    }

    func record() {
        guard let window else { return }
        let quartz = quartzFrame()
        let cursor = CGEvent(source: nil)?.location
        let dragPoint: [Double]
        if let handle {
            let point = handle.convert(NSPoint(x: handle.bounds.midX, y: handle.bounds.midY), to: nil)
            dragPoint = [point.x, window.frame.height - point.y]
        } else {
            dragPoint = [quartz.frame.width / 2, 11.0]
        }
        let state: [String: Any] = [
            "pid": getpid(), "commandId": commandId, "windowNumber": window.windowNumber,
            "windowObjectID": String(describing: ObjectIdentifier(window)),
            "frameAppKit": rectangle(window.frame), "frameQuartz": rectangle(quartz.frame),
            "frameQuartzSource": quartz.source, "titlebarPoint": [quartz.frame.width / 2, 11.0],
            "customHandle": customHandle,
            "dragHandleKind": customHandle ? "customViewSetFrameOrigin" : "standardTitlebar",
            "dragHandlePoint": dragPoint,
            "willMove": willMove, "didMove": didMove, "eventcounts": eventcounts,
            "contentDown": eventcounts["contentDown", default: 0],
            "contentDragged": eventcounts["contentDragged", default: 0],
            "contentUp": eventcounts["contentUp", default: 0],
            "handleDown": eventcounts["handleDown", default: 0],
            "handleDragged": eventcounts["handleDragged", default: 0],
            "handleUp": eventcounts["handleUp", default: 0],
            "handleAnchorSource": handle?.anchorSource as Any? ?? NSNull(),
            "currentCursor": cursor.map { [$0.x, $0.y] } as Any? ?? NSNull(),
            "buttonHeld": CGEventSource.buttonState(.combinedSessionState, button: .left),
            "ordered": window.isVisible, "active": NSApp.isActive,
            "frontmostPID": NSWorkspace.shared.frontmostApplication?.processIdentifier as Any? ?? NSNull(),
            "otherClicks": otherClicks, "timestampLog": timestampLog,
        ]
        try! JSONSerialization.data(withJSONObject: state, options: [.sortedKeys]).write(
            to: folder.appendingPathComponent("state.json"), options: .atomic)
    }

    func tick() {
        guard let data = try? Data(contentsOf: folder.appendingPathComponent("command.json")),
              let command = try? JSONSerialization.jsonObject(with: data) as? [String: Any],
              let id = command["id"] as? String, id != commandId else { record(); return }
        commandId = id
        switch command["kind"] as? String ?? "reset" {
        case "foreground":
            window.makeKeyAndOrderFront(nil)
            NSApp.activate(ignoringOtherApps: true)
            append("foregroundRequested")
        case "reset":
            resetting = true
            window.setFrame(originalFrame, display: true, animate: false)
            resetting = false
            willMove = 0
            didMove = 0
            otherClicks = 0
            resetCounts()
            handle?.reset()
            timestampLog = []
            append("reset")
        case "close": window.close(); append("closed")
        default: break
        }
        record()
    }

    func applicationDidFinishLaunching(_ notification: Notification) {
        window = NSWindow(contentRect: NSRect(x: 200, y: 210, width: 520, height: 240),
                          styleMask: [.titled, .closable], backing: .buffered, defer: false)
        window.title = "Captured window drag probe"
        window.delegate = self
        window.isReleasedWhenClosed = false
        window.isMovable = true
        window.isMovableByWindowBackground = false
        window.tabbingMode = .disallowed
        let content = CapturedWindowDragContent(frame: window.contentView!.bounds)
        content.observe = { [weak self] kind, event in self?.observe(kind, event: event) }
        window.contentView = content
        if customHandle {
            content.prompt = "Custom NSView handle test; the standard title bar remains independent."
            let value = CapturedWindowDragHandle(frame: NSRect(x: 20, y: content.bounds.height - 95,
                                                              width: content.bounds.width - 40, height: 48))
            value.observe = { [weak self] kind, event in self?.observe(kind, event: event) }
            handle = value
            content.addSubview(value)
        }
        let other = NSButton(title: "Unrelated button", target: self, action: #selector(otherPressed(_:)))
        other.frame = NSRect(x: 20, y: 25, width: 220, height: 34)
        other.bezelStyle = .rounded
        content.addSubview(other)
        originalFrame = window.frame
        resetCounts()
        monitor = NSEvent.addLocalMonitorForEvents(
            matching: [.leftMouseDown, .leftMouseDragged, .leftMouseUp, .mouseMoved]
        ) { event in
            MainActor.assumeIsolated {
                let names: [NSEvent.EventType: String] = [
                    .leftMouseDown: "localDown", .leftMouseDragged: "localDragged",
                    .leftMouseUp: "localUp", .mouseMoved: "localMoved"
                ]
                if event.windowNumber == self.window.windowNumber, let name = names[event.type] {
                    self.observe(name, event: event)
                }
            }
            return event
        }
        window.orderBack(nil)
        window.makeKey()
        window.makeMain()
        record()
        _ = Timer.scheduledTimer(withTimeInterval: 0.03, repeats: true) { _ in
            MainActor.assumeIsolated { self.tick() }
        }
    }

    func applicationShouldTerminateAfterLastWindowClosed(_ sender: NSApplication) -> Bool { false }
}

@main enum CapturedWindowDragProbe {
    @MainActor static func main() {
        let application = NSApplication.shared
        application.setActivationPolicy(.regular)
        let delegate = CapturedWindowDragDelegate(customHandle: CommandLine.arguments.contains("--custom-handle"))
        application.delegate = delegate
        application.run()
    }
}
