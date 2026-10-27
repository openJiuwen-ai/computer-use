import AppKit

@MainActor final class PointerPanel: NSPanel {
    override var canBecomeKey: Bool { false }
    override var canBecomeMain: Bool { false }
}

@MainActor final class PointerChoice: NSView {
    var clicked: (() -> Void)?
    override var acceptsFirstResponder: Bool { false }
    override func acceptsFirstMouse(for event: NSEvent?) -> Bool { true }
    override func mouseDown(with event: NSEvent) { clicked?() }
    override func draw(_ dirtyRect: NSRect) {
        NSColor.controlBackgroundColor.setFill()
        bounds.fill()
        ("Choose once" as NSString).draw(
            at: NSPoint(x: 15, y: 25), withAttributes: [.foregroundColor: NSColor.labelColor])
    }
}

@MainActor final class PointerPanelDelegate: NSObject, NSApplicationDelegate {
    let directory = Bundle.main.bundleURL.deletingLastPathComponent()
    var main: NSWindow!
    var palette: NSPanel!
    var count = 0
    var lastClickKey = ""
    let initialForeground = NSWorkspace.shared.frontmostApplication
    let editor = NSTextField(string: "untouched")
    func record() {
        let data: [String: Any] = [
            "count": count, "key": NSApp.keyWindow?.title ?? "", "active": NSApp.isActive,
            "paletteVisible": palette?.isVisible ?? false, "documentText": editor.stringValue,
            "lastClickKey": lastClickKey,
            "foregroundPID": NSWorkspace.shared.frontmostApplication?.processIdentifier ?? 0,
            "initialForegroundPID": initialForeground?.processIdentifier ?? 0,
            "initialForegroundPath": initialForeground?.bundleURL?.path ?? ""
        ]
        try! JSONSerialization.data(withJSONObject: data).write(to: directory.appendingPathComponent("state.json"))
    }
    func applicationDidFinishLaunching(_ notification: Notification) {
        try! String(getpid()).write(
            to: directory.appendingPathComponent("fixture.pid"), atomically: true, encoding: .utf8)
        main = NSWindow(
            contentRect: NSRect(x: 80, y: 100, width: 430, height: 250), styleMask: [.titled, .closable],
            backing: .buffered, defer: false)
        main.title = "Pointer document"
        main.isReleasedWhenClosed = false
        main.tabbingMode = .disallowed
        let button = NSButton(title: "Show non-key palette", target: self, action: #selector(showPalette))
        button.frame = NSRect(x: 20, y: 170, width: 230, height: 32)
        main.contentView?.addSubview(button)
        editor.frame = NSRect(x: 20, y: 110, width: 350, height: 28)
        main.contentView?.addSubview(editor)
        main.orderBack(nil)
        main.makeKey()
        main.makeMain()
        main.makeFirstResponder(editor)
        record()
        Timer.scheduledTimer(withTimeInterval: 0.05, repeats: true) { _ in MainActor.assumeIsolated { self.record() } }
    }
    @objc func showPalette() {
        // A palette/menu does not need to become the keyboard's key window.
        // This is an ordinary AppKit nonactivating panel, with no app-name rule.
        NSApp.activate(ignoringOtherApps: true)
        main.makeKeyAndOrderFront(nil)
        if palette == nil {
            palette = PointerPanel(
                contentRect: NSRect(x: 130, y: 150, width: 240, height: 90),
                styleMask: [.borderless, .nonactivatingPanel], backing: .buffered, defer: false)
            palette.title = "Pointer palette"
            palette.isReleasedWhenClosed = false
            palette.hidesOnDeactivate = false
            palette.level =
                FileManager.default.fileExists(atPath: directory.appendingPathComponent("normal-level").path)
                ? .normal : .popUpMenu
            let choice = PointerChoice(frame: NSRect(x: 0, y: 0, width: 240, height: 90))
            choice.clicked = { [weak self] in
                self?.count += 1
                self?.lastClickKey = NSApp.keyWindow?.title ?? ""
                self?.record()
            }
            palette.contentView = choice
        }
        palette.orderFrontRegardless()
        record()
    }
}

let application = NSApplication.shared
application.setActivationPolicy(.regular)
let delegate = PointerPanelDelegate()
application.delegate = delegate
application.run()
