// Copyright (c) Huawei Technologies Co., Ltd. 2026. All rights reserved.

import AppKit

@MainActor final class DocumentProbeApplication: NSApplication {
    override func sendEvent(_ event: NSEvent) {
        if [.leftMouseDown, .leftMouseUp, .rightMouseDown, .rightMouseUp, .keyDown, .keyUp, .appKitDefined]
            .contains(event.type), let recorder = delegate as? DocumentActionDelegate
        {
            recorder.event([
                "type": event.type.rawValue, "window": event.windowNumber,
                "point": [event.locationInWindow.x, event.locationInWindow.y],
                "sourcePID": event.cgEvent?.getIntegerValueField(.eventSourceUnixProcessID) ?? -1,
                "keyWindow": keyWindow?.windowNumber ?? 0, "active": isActive,
                "uptime": ProcessInfo.processInfo.systemUptime,
            ])
        }
        super.sendEvent(event)
    }
}

/// The buttons use ordinary AppKit AXPress. Only the target/action connection
/// differs; every window has an independent responder and callback counters.
@MainActor final class DocumentResponder: NSView {
    var directActions = 0, responderActions = 0, contentDowns = 0
    var record: () -> Void = {}
    override var acceptsFirstResponder: Bool { true }
    override func acceptsFirstMouse(for event: NSEvent?) -> Bool { false }
    @objc func directCommand(_ sender: Any?) {
        directActions += 1
        record()
    }
    @objc func responderCommand(_ sender: Any?) {
        responderActions += 1
        record()
    }
    override func mouseDown(with event: NSEvent) {
        contentDowns += 1
        record()
    }
    override func draw(_ dirtyRect: NSRect) {
        NSColor.textBackgroundColor.setFill()
        bounds.fill()
        "Document responder".draw(
            at: NSPoint(x: 20, y: 40),
            withAttributes: [.foregroundColor: NSColor.labelColor, .font: NSFont.systemFont(ofSize: 18)])
    }
}

@MainActor final class DocumentActionDelegate: NSObject, NSApplicationDelegate, NSWindowDelegate {
    let folder = Bundle.main.bundleURL.deletingLastPathComponent()
    let enableOnMain = CommandLine.arguments.contains("--enable-on-main")
    let keepTargetDisabled = CommandLine.arguments.contains("--keep-target-disabled")
    var windows = [NSWindow](), responders = [DocumentResponder]()
    var buttons = [[NSButton]]()
    var generation = 0, lastCommand = ""

    func event(_ value: [String: Any]) {
        let file = folder.appendingPathComponent("events.jsonl")
        if !FileManager.default.fileExists(atPath: file.path) {
            _ = FileManager.default.createFile(atPath: file.path, contents: nil)
        }
        guard let data = try? JSONSerialization.data(withJSONObject: value),
              let handle = try? FileHandle(forWritingTo: file) else { return }
        _ = try? handle.seekToEnd()
        try? handle.write(contentsOf: data + Data([10]))
        try? handle.close()
    }

    func record() {
        guard windows.count == 2 else { return }
        let top = NSScreen.screens.first?.frame.maxY ?? 0
        let pointer = CGEvent(source: nil)?.location ?? .zero
        let value: [String: Any] = [
            "pid": getpid(), "name": ProcessInfo.processInfo.processName, "active": NSApp.isActive,
            "frontmostPID": NSWorkspace.shared.frontmostApplication?.processIdentifier ?? -1,
            "keyWindow": NSApp.keyWindow?.windowNumber ?? 0, "mainWindow": NSApp.mainWindow?.windowNumber ?? 0,
            "generation": generation, "lastCommand": lastCommand, "mouse": [pointer.x, pointer.y],
            "enableOnMain": enableOnMain, "keepTargetDisabled": keepTargetDisabled,
            "documents": windows.enumerated().map { index, window -> [String: Any] in
                let responder = responders[index]
                return [
                    "document": index == 0 ? "A" : "B", "window": window.windowNumber, "title": window.title,
                    "windowObjectID": String(describing: ObjectIdentifier(window)),
                    "responderObjectID": String(describing: ObjectIdentifier(responder)),
                    "buttonObjectIDs": buttons[index].map { String(describing: ObjectIdentifier($0)) },
                    "buttons": buttons[index].enumerated().map { offset, button -> [String: Any] in
                        let rect = window.convertToScreen(button.convert(button.bounds, to: nil))
                        return [
                            "receiver": offset == 0 ? "direct" : "responder",
                            "identifier": button.accessibilityIdentifier(),
                            "enabled": button.isEnabled,
                            "screenCenterQuartz": [rect.midX, top - rect.midY],
                            "screenRectQuartz": [rect.minX, top - rect.maxY, rect.width, rect.height],
                        ]
                    },
                    "directActions": responder.directActions, "responderActions": responder.responderActions,
                    "contentDowns": responder.contentDowns, "key": window.isKeyWindow, "main": window.isMainWindow,
                    "firstResponder": window.firstResponder === responder,
                    "frame": [window.frame.minX, top - window.frame.maxY, window.frame.width, window.frame.height],
                ]
            },
        ]
        guard let data = try? JSONSerialization.data(withJSONObject: value, options: [.sortedKeys]) else { return }
        try? data.write(to: folder.appendingPathComponent("state.json"), options: .atomic)
    }

    func updateEnabled() {
        guard enableOnMain else { return }
        for (index, window) in windows.enumerated() {
            for button in buttons[index] {
                button.isEnabled = window.isMainWindow && !(keepTargetDisabled && index == 0)
            }
        }
    }

    func windowDidBecomeMain(_ notification: Notification) {
        updateEnabled()
        record()
    }

    func windowDidResignMain(_ notification: Notification) {
        updateEnabled()
        record()
    }

    func select(_ index: Int) {
        windows[index].makeFirstResponder(responders[index])
        windows[index].makeMain()
        windows[index].makeKeyAndOrderFront(nil)
        NSApp.activate(ignoringOtherApps: true)
    }

    func tick() {
        if let data = try? Data(contentsOf: folder.appendingPathComponent("command.json")),
           let command = try? JSONSerialization.jsonObject(with: data) as? [String: Any],
           let id = command["id"] as? String, id != lastCommand
        {
            lastCommand = id
            switch command["kind"] as? String {
            case "front-target": select(0)
            case "front-other": select(1)
            default: break
            }
            generation += 1
        }
        record()
    }

    func applicationDidFinishLaunching(_ notification: Notification) {
        let menu = NSMenu(), app = NSMenuItem()
        app.submenu = NSMenu()
        app.submenu!.addItem(withTitle: "Quit", action: #selector(NSApplication.terminate(_:)), keyEquivalent: "q")
        menu.addItem(app)
        NSApp.mainMenu = menu
        for index in 0..<2 {
            let document = index == 0 ? "A" : "B"
            let window = NSWindow(
                contentRect: NSRect(x: 100 + 540 * index, y: 180, width: 500, height: 320),
                styleMask: [.titled, .closable], backing: .buffered, defer: false)
            window.title = "Document context " + document
            window.tabbingMode = .disallowed
            window.isReleasedWhenClosed = false
            let responder = DocumentResponder(frame: NSRect(x: 20, y: 20, width: 460, height: 150))
            responder.setAccessibilityIdentifier("document-content-" + document)
            let direct = NSButton(title: "Direct document action", target: responder,
                                  action: #selector(DocumentResponder.directCommand(_:)))
            let indirect = NSButton(title: "Responder document action", target: nil,
                                    action: #selector(DocumentResponder.responderCommand(_:)))
            for (offset, button) in [direct, indirect].enumerated() {
                button.frame = NSRect(x: 100, y: 240 - 55 * offset, width: 300, height: 38)
                button.setAccessibilityIdentifier("document-" + (offset == 0 ? "direct-" : "responder-") + document)
                window.contentView!.addSubview(button)
            }
            window.contentView!.addSubview(responder)
            window.makeFirstResponder(responder)
            window.orderBack(nil)
            windows.append(window)
            responders.append(responder)
            buttons.append([direct, indirect])
            window.delegate = self
            responder.record = { [weak self] in self?.record() }
        }
        windows[0].makeMain()
        windows[0].makeKey()
        updateEnabled()
        record()
        _ = Timer.scheduledTimer(withTimeInterval: 0.025, repeats: true) { _ in
            MainActor.assumeIsolated { self.tick() }
        }
    }
}

@main enum DocumentActionProbe {
    @MainActor static func main() {
        guard !CommandLine.arguments.contains("--keep-target-disabled")
            || CommandLine.arguments.contains("--enable-on-main")
        else {
            print("--keep-target-disabled requires --enable-on-main")
            exit(2)
        }
        let app = DocumentProbeApplication.shared
        app.setActivationPolicy(.regular)
        let delegate = DocumentActionDelegate()
        app.delegate = delegate
        app.run()
    }
}
