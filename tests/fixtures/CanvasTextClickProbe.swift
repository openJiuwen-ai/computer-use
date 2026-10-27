import AppKit

// Canvas text may be selectable on the first click and editable only after
// an explicit double click. AX exposes the text before the editor is active.
@MainActor final class CanvasText: NSTextView {
    var editing = false
    var clicks = 0
    var glyphHits = 0
    var changed: (() -> Void)?
    override var acceptsFirstResponder: Bool { editing }
    override func acceptsFirstMouse(for event: NSEvent?) -> Bool { true }
    override func setAccessibilityFocused(_ value: Bool) { if editing { super.setAccessibilityFocused(value) } }
    override func mouseDown(with event: NSEvent) {
        clicks += 1
        let glyph = accessibilityFrame(for: NSRange(location: 0, length: 1))
        let hitsText = window.map { glyph.contains($0.convertPoint(toScreen: event.locationInWindow)) } ?? false
        if hitsText { glyphHits += 1 }
        if event.clickCount >= 2 && hitsText {
            editing = true
            window?.makeFirstResponder(self)
            super.mouseDown(with: event)
        }
        changed?()
    }
}

@MainActor final class CanvasDelegate: NSObject, NSApplicationDelegate {
    var window: NSWindow!
    let editor = CanvasText(frame: NSRect(x: 20, y: 80, width: 430, height: 150))
    let other = NSTextField(string: "untouched")
    let directory = Bundle.main.bundleURL.deletingLastPathComponent()
    func record() {
        let data = try! JSONSerialization.data(
            withJSONObject: [
                "text": editor.string, "other": other.stringValue, "clicks": editor.clicks,
                "glyphHits": editor.glyphHits, "editing": editor.editing,
                "editorIsResponder": window.firstResponder === editor
            ], options: [.sortedKeys])
        try! data.write(to: directory.appendingPathComponent("values.json"), options: .atomic)
    }
    func applicationDidFinishLaunching(_ notification: Notification) {
        try! String(getpid()).write(
            to: directory.appendingPathComponent("fixture.pid"), atomically: true, encoding: .utf8)
        window = NSWindow(
            contentRect: NSRect(x: 150, y: 160, width: 470, height: 260), styleMask: [.titled, .closable],
            backing: .buffered, defer: false)
        window.title = "Canvas text click"
        window.tabbingMode = .disallowed
        editor.string = "Canvas title"
        editor.isRichText = false
        editor.setAccessibilityIdentifier("canvas-text")
        editor.changed = { [weak self] in self?.record() }
        other.frame = NSRect(x: 20, y: 20, width: 430, height: 28)
        other.setAccessibilityIdentifier("other-input")
        window.contentView?.addSubview(editor)
        window.contentView?.addSubview(other)
        let menu = NSMenu(), edit = NSMenuItem(title: "Edit", action: nil, keyEquivalent: "")
        edit.submenu = NSMenu(title: "Edit")
        edit.submenu!.addItem(withTitle: "Select All", action: #selector(NSText.selectAll(_:)), keyEquivalent: "a")
        menu.addItem(edit)
        NSApp.mainMenu = menu
        window.orderBack(nil)
        window.makeKey()
        window.makeMain()
        window.makeFirstResponder(other)
        record()
        Timer.scheduledTimer(withTimeInterval: 0.04, repeats: true) { _ in MainActor.assumeIsolated { self.record() } }
    }
}
let app = NSApplication.shared
app.setActivationPolicy(.regular)
let delegate = CanvasDelegate()
app.delegate = delegate
app.run()
