import AppKit

@MainActor final class VisualOnlyWindow: NSWindow {
    override func accessibilityRole() -> NSAccessibility.Role? { nil }
    override func accessibilityChildren() -> [Any]? { [] }
}

/// Real AppKit attachments, including the system's independently created
/// sharing control. The state file is an oracle, never a driver input route.
@MainActor final class Delegate: NSObject, NSApplicationDelegate {
    let folder = Bundle.main.bundleURL.deletingLastPathComponent()
    var main: NSWindow!
    var child: NSWindow!
    var peer: NSWindow?
    var sheet: NSWindow?
    var visual: NSWindow!
    var timer: Timer?
    var clicks = 0
    var lastCommand = ""
    func window(_ title: String, _ label: String, x: CGFloat) -> NSWindow {
        let window = NSWindow(
            contentRect: NSRect(x: x, y: 150, width: 340, height: 160), styleMask: [.titled, .closable],
            backing: .buffered, defer: false)
        window.title = title
        window.isReleasedWhenClosed = false
        let text = NSTextField(labelWithString: label)
        text.frame = NSRect(x: 20, y: 105, width: 300, height: 24)
        window.contentView!.addSubview(text)
        return window
    }
    @objc func countClick() {
        clicks += 1
        record()
    }
    @objc func dismissSheet() {
        main.endSheet(sheet!)
        sheet!.orderOut(nil)
        record()
    }
    func record() {
        let value: [String: Any] = [
            "pid": ProcessInfo.processInfo.processIdentifier, "clicks": clicks, "lastCommand": lastCommand,
            "main": main.windowNumber, "child": child.windowNumber,
            "windows": NSApp.windows.map { w in
                [
                    "id": w.windowNumber, "title": w.title, "visible": w.isVisible,
                    "parent": w.parent?.windowNumber ?? 0, "sheetParent": w.sheetParent?.windowNumber ?? 0,
                    "class": String(describing: type(of: w))
                ] as [String: Any]
            }
        ]
        try! JSONSerialization.data(withJSONObject: value, options: [.sortedKeys]).write(
            to: folder.appendingPathComponent("state.json"), options: .atomic)
    }
    func applicationDidFinishLaunching(_ notification: Notification) {
        main = window("Document", "Main document content", x: 90)
        let mainButton = NSButton(title: "Count document click", target: self, action: #selector(countClick))
        mainButton.frame = NSRect(x: 20, y: 45, width: 200, height: 30)
        main.contentView!.addSubview(mainButton)
        child = window("Attached panel", "Attached panel content", x: 460)
        let button = NSButton(title: "Count click", target: self, action: #selector(countClick))
        button.frame = NSRect(x: 20, y: 45, width: 130, height: 30)
        child.contentView!.addSubview(button)
        main.orderFront(nil)
        main.addChildWindow(child, ordered: .above)
        visual = VisualOnlyWindow(
            contentRect: NSRect(x: 460, y: 370, width: 220, height: 80), styleMask: [.titled, .closable],
            backing: .buffered, defer: false)
        visual.title = "Visual panel"
        visual.isReleasedWhenClosed = false
        let label = NSTextField(labelWithString: "Native-only attached content")
        label.frame = NSRect(x: 15, y: 30, width: 200, height: 24)
        visual.contentView!.addSubview(label)
        main.addChildWindow(visual, ordered: .above)
        timer = Timer.scheduledTimer(withTimeInterval: 0.1, repeats: true) { [weak self] _ in
            MainActor.assumeIsolated {
                guard let self else { return }
                if FileManager.default.fileExists(atPath: self.folder.appendingPathComponent("quit").path) {
                    NSApp.terminate(nil)
                    return
                }
                if let command = try? String(
                    contentsOf: self.folder.appendingPathComponent("command"), encoding: .utf8),
                    command != self.lastCommand
                {
                    switch command {
                    case "detach": self.main.removeChildWindow(self.child)
                    case "attach": self.main.addChildWindow(self.child, ordered: .above)
                    case "peer":
                        self.peer = self.window("Document", "Independent peer content", x: 830)
                        self.peer!.orderFront(nil)
                        self.main.removeChildWindow(self.child)
                        self.peer!.addChildWindow(self.child, ordered: .above)
                    case "sheet":
                        self.sheet = self.window("Modal sheet", "Modal sheet content", x: 90)
                        let dismiss = NSButton(
                            title: "Dismiss sheet", target: self, action: #selector(self.dismissSheet))
                        dismiss.frame = NSRect(x: 20, y: 45, width: 200, height: 30)
                        self.sheet!.contentView!.addSubview(dismiss)
                        self.main.beginSheet(self.sheet!)
                    case "end-sheet":
                        self.main.endSheet(self.sheet!)
                        self.sheet!.orderOut(nil)
                    case "close-main": self.main.close()
                    default: break
                    }
                    self.lastCommand = command
                }
                self.record()
            }
        }
        record()
    }
}
let app = NSApplication.shared
let delegate = Delegate()
app.delegate = delegate
app.setActivationPolicy(.regular)
app.run()
