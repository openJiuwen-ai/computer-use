import AppKit

// Compare event-local and globally polled coordinates without an application
// name exception. The editor reports its actual selection and received events.
@MainActor final class Editor: NSTextView {
    var log: ([String: Any]) -> Void = { _ in }
    override func mouseDown(with event: NSEvent) {
        let global = NSEvent.mouseLocation
        let local = window!.convertPoint(fromScreen: global)
        log([
            "stage": "coordinates", "global": [global.x, global.y],
            "cgEvent": event.cgEvent.map { [$0.location.x, $0.location.y] } ?? [],
            "eventLocal": [event.locationInWindow.x, event.locationInWindow.y], "polledLocal": [local.x, local.y]
        ])
        log([
            "stage": "editor-before", "point": [event.locationInWindow.x, event.locationInWindow.y],
            "selected": [selectedRange().location, selectedRange().length], "flags": event.modifierFlags.rawValue,
            "clickCount": event.clickCount
        ])
        if CommandLine.arguments.contains("--global-location") {
            let point = convert(local, from: nil)
            if bounds.contains(point) {
                setSelectedRange(NSRange(location: characterIndexForInsertion(at: point), length: 0))
            }
        } else {
            super.mouseDown(with: event)
        }
        log(["stage": "editor-after", "selected": [selectedRange().location, selectedRange().length]])
    }
    override func mouseUp(with event: NSEvent) {
        log(["stage": "editor-up", "point": [event.locationInWindow.x, event.locationInWindow.y]])
        super.mouseUp(with: event)
    }
}

@MainActor final class ProbeApplication: NSApplication {
    override func sendEvent(_ event: NSEvent) {
        if [.leftMouseDown, .leftMouseUp, .mouseMoved, .mouseEntered, .leftMouseDragged].contains(event.type) {
            (delegate as? Delegate)?.log([
                "stage": "application", "type": event.type.rawValue, "window": event.windowNumber,
                "point": [event.locationInWindow.x, event.locationInWindow.y],
                "cgEvent": event.cgEvent.map { [$0.location.x, $0.location.y] } ?? [],
                "subtype": event.subtype.rawValue, "flags": event.modifierFlags.rawValue,
                "keyWindow": keyWindow?.windowNumber ?? -1, "active": isActive
            ])
        }
        super.sendEvent(event)
    }
}

@MainActor final class NonKeyPanel: NSPanel {
    override var canBecomeKey: Bool { false }
    override var canBecomeMain: Bool { false }
}

@MainActor final class Delegate: NSObject, NSApplicationDelegate {
    let directory = URL(fileURLWithPath: CommandLine.arguments[1])
    var windows = [NSWindow](), editors = [Editor]()
    var cover: NSWindow?
    func log(_ value: [String: Any]) {
        let file = directory.appendingPathComponent("events.jsonl")
        if !FileManager.default.fileExists(atPath: file.path) {
            FileManager.default.createFile(atPath: file.path, contents: nil)
        }
        if let handle = try? FileHandle(forWritingTo: file) {
            _ = try? handle.seekToEnd()
            try? handle.write(contentsOf: JSONSerialization.data(withJSONObject: value) + Data([10]))
            try? handle.close()
        }
    }
    func record() {
        guard editors.count == 2 else { return }
        if cover == nil, FileManager.default.fileExists(atPath: directory.appendingPathComponent("cover").path) {
            let window = NSWindow(
                contentRect: windows[0].frame, styleMask: [.borderless], backing: .buffered, defer: false)
            window.title = "Covering window"
            window.level = .floating
            window.backgroundColor = .orange
            window.isReleasedWhenClosed = false
            window.orderFrontRegardless()
            cover = window
        }
        let value: [String: Any] = [
            "pid": getpid(), "keyWindow": NSApp.keyWindow?.title ?? "none", "active": NSApp.isActive,
            "coverWindow": cover?.windowNumber ?? 0,
            "foreground": NSWorkspace.shared.frontmostApplication?.processIdentifier ?? -1,
            "globalPointer": [NSEvent.mouseLocation.x, NSEvent.mouseLocation.y],
            "editors": editors.map { editor -> [String: Any] in
                let rect = editor.accessibilityFrame(for: NSRange(location: 64, length: 1))
                let point = NSPoint(x: rect.midX, y: rect.midY)
                return [
                    "text": editor.string, "selected": [editor.selectedRange().location, editor.selectedRange().length],
                    "glyph": [point.x, NSScreen.screens[0].frame.maxY - point.y], "window": editor.window!.windowNumber,
                    "responder": editor.window!.firstResponder === editor
                ]
            }
        ]
        try? JSONSerialization.data(withJSONObject: value).write(
            to: directory.appendingPathComponent("state.json"), options: .atomic)
    }
    func applicationDidFinishLaunching(_ notification: Notification) {
        let menu = NSMenu(), item = NSMenuItem()
        item.submenu = NSMenu()
        item.submenu!.addItem(withTitle: "Quit", action: #selector(NSApplication.terminate(_:)), keyEquivalent: "q")
        menu.addItem(item)
        NSApp.mainMenu = menu
        for i in 0..<2 {
            let rect = NSRect(x: 120 + 610 * i, y: 150, width: 600, height: 400)
            let window: NSWindow
            if CommandLine.arguments.contains("--non-key-panel"), i == 0 {
                let panel = NonKeyPanel(
                    contentRect: rect, styleMask: [.borderless, .nonactivatingPanel], backing: .buffered, defer: false)
                panel.hidesOnDeactivate = false
                panel.level = .floating
                window = panel
            } else {
                window = NSWindow(contentRect: rect, styleMask: [.titled, .closable], backing: .buffered, defer: false)
            }
            // Reproduce capture/native coordinate transforms at a nonzero
            // origin while leaving the original fixture layout unchanged.
            if CommandLine.arguments.contains("--offset"), i == 0 {
                window.setFrameOrigin(NSPoint(x: 1279, y: NSScreen.screens[0].frame.maxY - 30 - window.frame.height))
            }
            window.title = "Selection target \(i)"
            window.tabbingMode = .disallowed
            window.isReleasedWhenClosed = false
            if CommandLine.arguments.contains("--dialog"), i == 0 { window.setAccessibilitySubrole(.dialog) }
            let editor = Editor(frame: NSRect(x: 20, y: 40, width: 560, height: 330))
            editor.isRichText = false
            editor.font = NSFont.monospacedSystemFont(ofSize: 18, weight: .regular)
            editor.string = """
                First paragraph,with text.\nSecond paragraph,more words.\nThird paragraph,target \
                text.\nFourth paragraph,untouched.\n
                """
            editor.setAccessibilityIdentifier("editor-\(i)")
            editor.log = { [weak self] in self?.log($0) }
            window.contentView!.addSubview(editor)
            window.orderBack(nil)
            window.makeFirstResponder(editor)
            editor.setSelectedRange(NSRange(location: 0, length: 55))
            windows.append(window)
            editors.append(editor)
        }
        windows[1].makeKey()
        windows[1].makeMain()
        if CommandLine.arguments.contains("--foreground") {
            windows[0].makeKeyAndOrderFront(nil)
            NSApp.activate(ignoringOtherApps: true)
        } else if CommandLine.arguments.contains("--front-other") {
            // Same foreground process, but the target document is not key.
            // The target's default first mouse must still reach its content.
            windows[1].makeKeyAndOrderFront(nil)
            NSApp.activate(ignoringOtherApps: true)
        }
        record()
        _ = Timer.scheduledTimer(withTimeInterval: 0.02, repeats: true) { _ in
            MainActor.assumeIsolated { self.record() }
        }
    }
}

let application = ProbeApplication.shared
application.setActivationPolicy(.regular)
let delegate = Delegate()
application.delegate = delegate
application.run()
