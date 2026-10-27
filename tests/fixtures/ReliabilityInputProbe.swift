import AppKit

// Native oracle for text delivery. API receipts never determine this state:
// actual AppKit values, delegate callbacks and first responders do.

@MainActor final class ActionSurface: NSView {
    var ax = 0, mouse = 0
    var changed: (() -> Void)?
    override var acceptsFirstResponder: Bool { true }
    override func acceptsFirstMouse(for event: NSEvent?) -> Bool { true }
    override func accessibilityRole() -> NSAccessibility.Role? { .button }
    override func accessibilityLabel() -> String? { "Action surface" }
    override func accessibilityPerformPress() -> Bool {
        ax += 1
        changed?()
        needsDisplay = true
        return true
    }
    override func mouseDown(with event: NSEvent) {
        mouse += 1
        changed?()
        needsDisplay = true
    }
    override func draw(_ rect: NSRect) {
        NSColor.controlBackgroundColor.setFill()
        bounds.fill()
        ("AX: \(ax), Mouse: \(mouse)" as NSString).draw(
            at: NSPoint(x: 10, y: 10), withAttributes: [.foregroundColor: NSColor.labelColor])
    }
}

@MainActor final class ProbeApplication: NSApplication {
    override func sendEvent(_ event: NSEvent) {
        if [.keyDown, .keyUp, .flagsChanged].contains(event.type), let probe = delegate as? Probe {
            probe.record(
                "key-event",
                extra: [
                    "key": event.keyCode, "flags": event.modifierFlags.rawValue, "eventType": event.type.rawValue,
                    "eventWindow": event.windowNumber
                ])
        }
        super.sendEvent(event)
    }
}

@MainActor final class Probe: NSObject, NSApplicationDelegate, NSTextFieldDelegate {
    let directory = Bundle.main.bundleURL.deletingLastPathComponent()
    let window = NSWindow(
        contentRect: NSRect(x: 360, y: 240, width: 520, height: 240), styleMask: [.titled, .closable],
        backing: .buffered, defer: false)
    let target = NSTextField(string: "abcdef"), other = NSTextField(string: "GUARD")
    let sibling = NSTextField(string: "SIBLING"), surface = ActionSurface()
    var second: NSWindow?, timer: Timer?, changes = 0, submits = 0
    var redirected = false
    var options = [String: Any]()
    func applicationDidFinishLaunching(_ notification: Notification) {
        if let data = try? Data(contentsOf: directory.appendingPathComponent("options.json")),
            let value = try? JSONSerialization.jsonObject(with: data) as? [String: Any]
        {
            options = value
        }
        try! String(getpid()).write(
            to: directory.appendingPathComponent("fixture.pid"), atomically: true, encoding: .utf8)
        window.title = "Reliability Target"
        window.isReleasedWhenClosed = false
        let menu = NSMenu(), item = NSMenuItem(), edit = NSMenu(title: "Edit")
        for (title, action, key) in [
            ("Select All", #selector(NSText.selectAll(_:)), "a"), ("Copy", #selector(NSText.copy(_:)), "c"),
            ("Paste", #selector(NSText.paste(_:)), "v"), ("Undo", Selector(("undo:")), "z")
        ] { edit.addItem(withTitle: title, action: action, keyEquivalent: key) }
        item.submenu = edit
        menu.addItem(item)
        NSApp.mainMenu = menu
        target.stringValue = options["initial"] as? String ?? "abcdef"
        for (field, id, label, y) in [(target, "target", "Target field", 170.0), (other, "guard", "Guard field", 115.0)]
        {
            field.frame = NSRect(x: 120, y: y, width: 350, height: 28)
            field.identifier = NSUserInterfaceItemIdentifier(id)
            field.setAccessibilityIdentifier(id)
            field.setAccessibilityLabel(label)
            field.delegate = self
            window.contentView!.addSubview(field)
            let text = NSTextField(labelWithString: label)
            text.frame = NSRect(x: 15, y: y, width: 100, height: 28)
            window.contentView!.addSubview(text)
        }
        surface.frame = NSRect(x: 120, y: 45, width: 250, height: 40)
        surface.setAccessibilityElement(true)
        surface.setAccessibilityIdentifier("surface")
        surface.changed = { [weak self] in self?.record("action") }
        window.contentView!.addSubview(surface)
        window.orderBack(nil)
        window.makeKey()
        window.makeMain()
        window.makeFirstResponder(target)
        if options["multiple"] as? Bool == true {
            let w = NSWindow(
                contentRect: NSRect(x: 920, y: 260, width: 330, height: 150), styleMask: [.titled, .closable],
                backing: .buffered, defer: false)
            w.title = "Reliability Sibling"
            w.isReleasedWhenClosed = false
            sibling.frame = NSRect(x: 20, y: 60, width: 285, height: 28)
            sibling.delegate = self
            sibling.setAccessibilityLabel("Sibling field")
            w.contentView!.addSubview(sibling)
            w.orderBack(nil)
            w.makeFirstResponder(sibling)
            second = w
        }
        if options["caretEnd"] as? Bool == true {
            (target.currentEditor() as? NSTextView)?.setSelectedRange(
                NSRange(location: (target.stringValue as NSString).length, length: 0))
        }
        if let range = options["selection"] as? [Int], range.count == 2 {
            (target.currentEditor() as? NSTextView)?.setSelectedRange(NSRange(location: range[0], length: range[1]))
        }
        if let text = options["markedText"] as? String, let editor = target.currentEditor() as? NSTextView {
            editor.setMarkedText(
                text, selectedRange: NSRange(location: (text as NSString).length, length: 0),
                replacementRange: NSRange(location: NSNotFound, length: 0))
        }
        timer = Timer.scheduledTimer(withTimeInterval: 0.03, repeats: true) { [weak self] _ in
            MainActor.assumeIsolated { self?.record("sample") }
        }
        record("ready")
    }
    func control(_ control: NSControl, textView: NSTextView, doCommandBy commandSelector: Selector) -> Bool {
        if commandSelector == #selector(NSResponder.insertNewline(_:)) {
            submits += 1
            record("submit")
            return true
        }
        return false
    }
    func controlTextDidChange(_ notification: Notification) {
        changes += 1
        record("changed")
        if options["uppercase"] as? Bool == true, notification.object as? NSTextField === target {
            target.stringValue = target.stringValue.uppercased()
            record("formatted")
        }
        if options["redirect"] as? Bool == true, !redirected, notification.object as? NSTextField === target,
            target.stringValue.contains("#")
        {
            redirected = true
            let redirect = { [self] in
                if options["redirectTo"] as? String == "close" {
                    window.close()
                } else if options["redirectTo"] as? String == "sibling", let second {
                    second.makeMain()
                    second.makeKey()
                    second.makeFirstResponder(sibling)
                } else {
                    window.makeFirstResponder(other)
                }
                record("redirect")
            }
            if let delay = options["redirectDelayMs"] as? Double, delay > 0 {
                DispatchQueue.main.asyncAfter(deadline: .now() + delay / 1000, execute: redirect)
            } else {
                redirect()
            }
        }
    }
    func record(_ kind: String, extra: [String: Any] = [:]) {
        let editor = window.firstResponder as? NSTextView
        let field = editor?.delegate as? NSTextField
        let point = window.convertPoint(toScreen: NSPoint(x: surface.frame.midX, y: surface.frame.midY))
        let displayHeight = NSScreen.screens.first!.frame.maxY
        var value: [String: Any] = [
            "kind": kind, "time": ProcessInfo.processInfo.systemUptime, "target": target.stringValue,
            "guard": other.stringValue, "sibling": sibling.stringValue, "focus": field?.identifier?.rawValue ?? "none",
            "selection": [editor?.selectedRange().location ?? -1, editor?.selectedRange().length ?? -1],
            "changes": changes, "submits": submits, "marked": editor?.hasMarkedText() ?? false,
            "markedRange": [editor?.markedRange().location ?? -1, editor?.markedRange().length ?? -1],
            "windowVisible": window.isVisible, "allowsUndo": editor?.allowsUndo ?? false,
            "canUndo": editor?.undoManager?.canUndo ?? false, "windowCanUndo": window.undoManager?.canUndo ?? false,
            "keyWindow": NSApp.keyWindow?.title ?? "none", "mainWindow": NSApp.mainWindow?.title ?? "none",
            "appActive": NSApp.isActive, "axPress": surface.ax, "mouseDown": surface.mouse,
            "foreground": NSWorkspace.shared.frontmostApplication?.processIdentifier ?? 0,
            "windowID": window.windowNumber, "surface": ["x": point.x, "y": displayHeight - point.y]
        ]
        value.merge(extra) { _, new in new }
        let data = try! JSONSerialization.data(withJSONObject: value, options: [.sortedKeys])
        try? data.write(to: directory.appendingPathComponent("state.json"), options: .atomic)
        if kind != "sample" {
            let file = directory.appendingPathComponent("events.jsonl")
            if !FileManager.default.fileExists(atPath: file.path) {
                FileManager.default.createFile(atPath: file.path, contents: nil)
            }
            if let handle = try? FileHandle(forWritingTo: file) {
                try? handle.seekToEnd()
                try? handle.write(contentsOf: data + Data([10]))
                try? handle.close()
            }
        }
    }
}
let app = ProbeApplication.shared, probe = Probe()
app.setActivationPolicy(.regular)
app.delegate = probe
app.run()
