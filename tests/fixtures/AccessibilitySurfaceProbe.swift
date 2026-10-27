import AppKit

/// AXWindows is an accessibility catalogue, not a list of CGWindowIDs. This
/// provider declares a non-window entry, like a desktop or application canvas.
@MainActor final class SurfaceApplication: NSApplication {
    let surface = NSAccessibilityElement()
    override func accessibilityWindows() -> [Any]? {
        [surface] + ((delegate as? SurfaceDelegate)?.document.map { [$0] } ?? [])
    }
    override func accessibilityMainWindow() -> Any? { (delegate as? SurfaceDelegate)?.document }
    override func accessibilityFocusedWindow() -> Any? { keyWindow }
}

@MainActor final class SurfaceDelegate: NSObject, NSApplicationDelegate {
    var document: NSWindow?
    var count = 0
    let root = Bundle.main.bundleURL.deletingLastPathComponent()
    @objc func recordAction() {
        count += 1
        try! String(count).write(to: root.appendingPathComponent("actions.txt"), atomically: true, encoding: .utf8)
    }
    func applicationDidFinishLaunching(_ notification: Notification) {
        let app = NSApp as! SurfaceApplication
        app.surface.setAccessibilityRole(.scrollArea)
        app.surface.setAccessibilityLabel("Application canvas")
        app.surface.setAccessibilityParent(app)
        app.surface.setAccessibilityFrame(NSRect(x: 80, y: 100, width: 600, height: 400))
        let label = NSAccessibilityElement()
        label.setAccessibilityRole(.staticText)
        label.setAccessibilityValue("Content without a native window")
        label.setAccessibilityParent(app.surface)
        app.surface.setAccessibilityChildren([label])
        let bar = NSMenu(), menu = NSMenu(title: "Fixture")
        let applicationItem = NSMenuItem(title: "Application", action: nil, keyEquivalent: "")
        applicationItem.submenu = NSMenu(title: "Application")
        bar.addItem(applicationItem)
        let command = menu.addItem(withTitle: "Record action", action: #selector(recordAction), keyEquivalent: "")
        command.target = self
        let item = NSMenuItem(title: "Fixture", action: nil, keyEquivalent: "")
        item.submenu = menu
        bar.addItem(item)
        app.mainMenu = bar
        try! String(getpid()).write(to: root.appendingPathComponent("fixture.pid"), atomically: true, encoding: .utf8)
        Timer.scheduledTimer(withTimeInterval: 0.05, repeats: true) { _ in
            MainActor.assumeIsolated {
                let setup = self.root.appendingPathComponent("create-document")
                if FileManager.default.fileExists(atPath: setup.path), self.document == nil {
                    try? FileManager.default.removeItem(at: setup)
                    let window = NSWindow(
                        contentRect: NSRect(x: 160, y: 260, width: 440, height: 240), styleMask: [.titled, .closable],
                        backing: .buffered, defer: false)
                    window.title = "Independent document"
                    window.isReleasedWhenClosed = false
                    window.contentView?.addSubview(
                        NSTextField(labelWithString: "Do not select this document for the canvas"))
                    window.orderBack(nil)
                    self.document = window
                    try! "created".write(
                        to: self.root.appendingPathComponent("document-ready"), atomically: true, encoding: .utf8)
                }
            }
        }
    }
}
let app = SurfaceApplication.shared
app.setActivationPolicy(.regular)
let delegate = SurfaceDelegate()
app.delegate = delegate
app.run()
