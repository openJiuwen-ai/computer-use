import AppKit

// Ordinary AppKit sheet: the document stays main while its sheet becomes key.
// The command follows AppKit's responder chain and records its real recipient.
@MainActor final class SheetMenuWindow: NSWindow {
    weak var probe: SheetMenuProbe?
    @objc func recordReceiver() {
        probe?.counts[title, default: 0] += 1
        probe?.lastCommand = [
            "receiver": title, "main": NSApp.mainWindow?.title ?? "", "key": NSApp.keyWindow?.title ?? ""
        ]
        probe?.record()
    }
}
@MainActor final class SheetMenuProbe: NSObject, NSApplicationDelegate {
    let directory = Bundle.main.bundleURL.deletingLastPathComponent()
    var documents = [NSWindow]()
    var sheet: NSWindow?
    var counts = [String: Int]()
    var lastCommand = [String: String]()
    func record() {
        let state: [String: Any] = [
            "counts": counts, "main": NSApp.mainWindow?.title ?? "", "key": NSApp.keyWindow?.title ?? "",
            "sheet": sheet?.isVisible ?? false, "lastCommand": lastCommand
        ]
        try! JSONSerialization.data(withJSONObject: state).write(
            to: directory.appendingPathComponent("state.json"), options: .atomic)
    }
    func applicationDidFinishLaunching(_ notification: Notification) {
        try! String(getpid()).write(
            to: directory.appendingPathComponent("fixture.pid"), atomically: true, encoding: .utf8)
        let menu = NSMenu(), appItem = NSMenuItem(title: "Probe", action: nil, keyEquivalent: "")
        appItem.submenu = NSMenu(title: "Probe")
        menu.addItem(appItem)
        let heading = NSMenuItem(title: "Document", action: nil, keyEquivalent: "")
        let commands = NSMenu(title: "Document")
        commands.autoenablesItems = false
        commands.addItem(
            withTitle: "Record receiver", action: #selector(SheetMenuWindow.recordReceiver), keyEquivalent: "")
        heading.submenu = commands
        menu.addItem(heading)
        NSApp.mainMenu = menu
        for (index, name) in ["Alpha", "Beta"].enumerated() {
            let window = SheetMenuWindow(
                contentRect: NSRect(x: 120 + index * 470, y: 220, width: 440, height: 280),
                styleMask: [.titled, .closable], backing: .buffered, defer: false)
            window.title = name
            window.isReleasedWhenClosed = false
            window.probe = self
            let button = NSButton(title: "Open attached sheet", target: self, action: #selector(openSheet(_:)))
            button.frame = NSRect(x: 20, y: 100, width: 220, height: 32)
            window.contentView!.addSubview(button)
            window.orderBack(nil)
            documents.append(window)
        }
        documents[0].makeMain()
        documents[0].makeKey()
        record()
        Timer.scheduledTimer(withTimeInterval: 180, repeats: false) { _ in
            MainActor.assumeIsolated { NSApp.terminate(nil) }
        }
    }
    @objc func openSheet(_ sender: NSButton) {
        let panel = SheetMenuWindow(
            contentRect: NSRect(x: 0, y: 0, width: 320, height: 160), styleMask: [.titled], backing: .buffered,
            defer: false)
        panel.title = "Attached editor"
        panel.isReleasedWhenClosed = false
        panel.probe = self
        let field = NSTextField(frame: NSRect(x: 20, y: 90, width: 260, height: 25))
        field.stringValue = "Sheet content"
        panel.contentView!.addSubview(field)
        let close = NSButton(title: "Dismiss attached sheet", target: self, action: #selector(dismissSheet))
        close.frame = NSRect(x: 20, y: 30, width: 240, height: 32)
        panel.contentView!.addSubview(close)
        sheet = panel
        sender.window!.beginSheet(panel) { _ in }
        panel.makeFirstResponder(field)
        record()
    }
    @objc func dismissSheet() {
        if let sheet {
            sheet.sheetParent?.endSheet(sheet)
            sheet.orderOut(nil)
        }
        sheet = nil
        record()
    }
}
let app = NSApplication.shared
app.setActivationPolicy(.regular)
let delegate = SheetMenuProbe()
app.delegate = delegate
app.run()
