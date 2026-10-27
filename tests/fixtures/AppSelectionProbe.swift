import AppKit

// An ordinary two-document app can expose actionable menus without reporting
// any keyboard window. The command's observable effect is independent of focus.
@MainActor final class SelectionApplication: NSApplication {
    override func accessibilityFocusedWindow() -> Any? { nil }
    override func accessibilityMainWindow() -> Any? { nil }
    override func accessibilityApplicationFocusedUIElement() -> Any? { nil }
}

@MainActor final class SelectionDelegate: NSObject, NSApplicationDelegate {
    var windows = [NSWindow]()
    var panel: NSPanel?
    var log: FileHandle!
    func record(_ kind: String) {
        let state: [String: Any] = [
            "kind": kind, "foreground": NSWorkspace.shared.frontmostApplication?.processIdentifier ?? 0,
            "windows": windows.filter(\.isVisible).map(\.title)
        ]
        try? log.write(contentsOf: JSONSerialization.data(withJSONObject: state) + Data([10]))
    }
    func applicationDidFinishLaunching(_ notification: Notification) {
        let url = Bundle.main.bundleURL.deletingPathExtension().appendingPathExtension("events.jsonl")
        FileManager.default.createFile(atPath: url.path, contents: nil)
        log = try! FileHandle(forWritingTo: url)
        let menu = NSMenu(), file = NSMenuItem(title: "File", action: nil, keyEquivalent: "")
        let application = NSMenuItem()
        application.submenu = NSMenu()
        application.submenu!.addItem(
            withTitle: "Quit", action: #selector(NSApplication.terminate(_:)), keyEquivalent: "q")
        menu.addItem(application)
        file.submenu = NSMenu(title: "File")
        file.submenu!.autoenablesItems = false
        let open = file.submenu!.addItem(withTitle: "Open Panel", action: #selector(openPanel), keyEquivalent: "o")
        open.target = self
        menu.addItem(file)
        NSApp.mainMenu = menu
        for (index, name) in ["Document Alpha", "Document Beta"].enumerated() {
            let window = NSWindow(
                contentRect: NSRect(x: 100 + index * 400, y: 180, width: 350, height: 170),
                styleMask: [.titled, .closable], backing: .buffered, defer: false)
            window.title = name
            window.isReleasedWhenClosed = false
            let label = NSTextField(labelWithString: name)
            label.frame = NSRect(x: 20, y: 100, width: 250, height: 24)
            window.contentView!.addSubview(label)
            window.orderBack(nil)
            windows.append(window)
        }
        record("ready")
    }
    @objc func openPanel() {
        let window = NSPanel(
            contentRect: NSRect(x: 200, y: 420, width: 320, height: 130), styleMask: [.titled], backing: .buffered,
            defer: false)
        window.title = "Opened by native menu"
        window.hidesOnDeactivate = false
        window.setAccessibilitySubrole(.dialog)
        window.setAccessibilityModal(true)
        let close = NSButton(title: "Dismiss", target: self, action: #selector(dismiss))
        close.frame = NSRect(x: 30, y: 30, width: 150, height: 30)
        window.contentView!.addSubview(close)
        panel = window
        window.orderBack(nil)
        record("opened")
    }
    @objc func dismiss() {
        panel?.close()
        panel = nil
        record("dismissed")
    }
}

let app = SelectionApplication.shared
app.setActivationPolicy(.regular)
let delegate = SelectionDelegate()
app.delegate = delegate
app.run()
