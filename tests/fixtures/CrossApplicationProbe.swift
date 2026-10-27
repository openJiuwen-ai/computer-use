import AppKit

@MainActor final class CrossApplicationDelegate: NSObject, NSApplicationDelegate, NSMenuItemValidation {
    let directory = Bundle.main.bundleURL.deletingLastPathComponent()
    var window: NSWindow!
    var label: NSTextField!
    var count = 0
    var configuration = [String: Any]()
    func record(_ event: String) {
        label.stringValue = "Actions: \(count)"
        let data: [String: Any] = [
            "event": event, "count": count, "pid": getpid(),
            "foreground": NSWorkspace.shared.frontmostApplication?.processIdentifier ?? 0
        ]
        try! JSONSerialization.data(withJSONObject: data).write(
            to: directory.appendingPathComponent(Bundle.main.bundleURL.lastPathComponent + ".state.json"),
            options: .atomic)
    }
    func applicationDidFinishLaunching(_ notification: Notification) {
        configuration =
            (try! JSONSerialization.jsonObject(
                with: Data(contentsOf: directory.appendingPathComponent("configuration.json")))) as! [String: Any]
        window = NSWindow(
            contentRect: NSRect(x: 120, y: 180, width: 460, height: 180), styleMask: [.titled, .closable],
            backing: .buffered, defer: false)
        window.title = Bundle.main.bundleURL.deletingPathExtension().lastPathComponent
        window.isReleasedWhenClosed = false
        label = NSTextField(labelWithString: "Actions: 0")
        label.frame = NSRect(x: 20, y: 130, width: 300, height: 25)
        window.contentView!.addSubview(label)
        let open = NSButton(title: "Open receiver", target: self, action: #selector(openReceiver))
        open.frame = NSRect(x: 20, y: 70, width: 160, height: 32)
        window.contentView!.addSubview(open)
        let restore = NSButton(title: "Restore original desktop", target: self, action: #selector(restoreOriginal))
        restore.frame = NSRect(x: 20, y: 25, width: 220, height: 32)
        window.contentView!.addSubview(restore)
        let main = NSMenu(), appItem = NSMenuItem()
        appItem.submenu = NSMenu(title: "Cross Application Probe")
        main.addItem(appItem)
        let item = NSMenuItem(title: "File", action: nil, keyEquivalent: ""), menu = NSMenu(title: "File")
        let command = NSMenuItem(title: "Open receiver", action: #selector(openReceiver), keyEquivalent: "")
        command.target = self
        menu.addItem(command)
        item.submenu = menu
        main.addItem(item)
        NSApp.mainMenu = main
        window.orderBack(nil)
        window.makeKey()
        window.makeMain()
        menu.update()
        record("ready")
        Timer.scheduledTimer(withTimeInterval: 120, repeats: false) { _ in
            MainActor.assumeIsolated { NSApp.terminate(nil) }
        }
    }
    func validateMenuItem(_ item: NSMenuItem) -> Bool { NSApp.isActive }
    @objc func openReceiver() {
        count += 1
        record("open-requested")
        let config = NSWorkspace.OpenConfiguration()
        config.activates = true
        NSWorkspace.shared.openApplication(
            at: URL(fileURLWithPath: configuration["receiver"] as! String), configuration: config
        ) { _, _ in }
    }
    @objc func restoreOriginal() {
        count += 1
        if let pid = configuration["originalPID"] as? Int32 {
            _ = NSRunningApplication(processIdentifier: pid)?.activate(options: [])
        }
        record("restore-requested")
    }
}
let app = NSApplication.shared
app.setActivationPolicy(.regular)
let delegate = CrossApplicationDelegate()
app.delegate = delegate
app.run()
