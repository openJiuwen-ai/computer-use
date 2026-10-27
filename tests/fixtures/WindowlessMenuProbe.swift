import AppKit

@MainActor final class WindowlessMenuDelegate: NSObject, NSApplicationDelegate, NSMenuItemValidation {
    let directory = Bundle.main.bundleURL.deletingLastPathComponent()
    var window: NSWindow?
    var fileMenu: NSMenu!
    var opened = 0, forbidden = 0
    var callbackActive: Bool?
    var needsActive: Bool {
        FileManager.default.fileExists(atPath: directory.appendingPathComponent("needs-active").path)
    }
    func record() {
        try! JSONSerialization.data(withJSONObject: [
            "opened": opened, "forbidden": forbidden, "windows": NSApp.windows.filter { $0.isVisible }.count,
            "active": NSApp.isActive, "callbackActive": callbackActive as Any? ?? NSNull()
        ]).write(to: directory.appendingPathComponent("state.json"), options: .atomic)
    }
    func applicationDidFinishLaunching(_ notification: Notification) {
        try! String(getpid()).write(
            to: directory.appendingPathComponent("fixture.pid"), atomically: true, encoding: .utf8)
        let main = NSMenu(), appItem = NSMenuItem(), file = NSMenuItem(title: "File", action: nil, keyEquivalent: "")
        appItem.submenu = NSMenu(title: "Windowless Menu Probe")
        main.addItem(appItem)
        fileMenu = NSMenu(title: "File")
        let new = NSMenuItem(title: "New document", action: #selector(newDocument), keyEquivalent: "n")
        new.target = self
        if FileManager.default.fileExists(atPath: directory.appendingPathComponent("submenu").path) {
            let choices = NSMenuItem(title: "Document choices", action: nil, keyEquivalent: "")
            let submenu = NSMenu(title: "Document choices")
            submenu.addItem(new)
            choices.submenu = submenu
            fileMenu.addItem(choices)
        } else {
            fileMenu.addItem(new)
        }
        if FileManager.default.fileExists(atPath: directory.appendingPathComponent("duplicate-command").path) {
            let duplicate = NSMenuItem(title: "New document", action: #selector(forbiddenAction), keyEquivalent: "")
            duplicate.target = self
            fileMenu.addItem(duplicate)
        }
        let disabled = NSMenuItem(title: "Always disabled", action: #selector(forbiddenAction), keyEquivalent: "")
        disabled.target = self
        fileMenu.addItem(disabled)
        file.submenu = fileMenu
        main.addItem(file)
        NSApp.mainMenu = main
        if FileManager.default.fileExists(atPath: directory.appendingPathComponent("unknown-surface").path) {
            showWindow("Ordinary popup-level window")
            window!.level = .popUpMenu
        } else if FileManager.default.fileExists(atPath: directory.appendingPathComponent("initial-document").path) {
            showWindow("Existing document")
            window!.makeKeyAndOrderFront(nil)
        }
        fileMenu.update()
        record()
        Timer.scheduledTimer(withTimeInterval: 0.03, repeats: true) { _ in
            MainActor.assumeIsolated {
                if self.window == nil
                    && FileManager.default.fileExists(atPath: self.directory.appendingPathComponent("race-window").path)
                {
                    self.showWindow("Unexpected document")
                }
            }
        }
        Timer.scheduledTimer(withTimeInterval: 120, repeats: false) { _ in
            MainActor.assumeIsolated { NSApp.terminate(nil) }
        }
    }
    func applicationShouldTerminateAfterLastWindowClosed(_ sender: NSApplication) -> Bool { false }
    func applicationDidBecomeActive(_ notification: Notification) {
        guard fileMenu != nil else { return }
        if FileManager.default.fileExists(atPath: directory.appendingPathComponent("activation-window").path)
            && window == nil
        {
            showWindow("Activation dialog")
        }
        fileMenu.update()
        record()
    }
    func applicationDidResignActive(_ notification: Notification) {
        if fileMenu != nil {
            fileMenu.update()
            record()
        }
    }
    func validateMenuItem(_ item: NSMenuItem) -> Bool {
        item.action == #selector(newDocument) && (!needsActive || NSApp.isActive)
    }
    func showWindow(_ title: String) {
        window = NSWindow(
            contentRect: NSRect(x: 140, y: 160, width: 420, height: 180), styleMask: [.titled, .closable],
            backing: .buffered, defer: false)
        window!.isReleasedWhenClosed = false
        window!.title = title
        let label = NSTextField(labelWithString: "Unrelated document content must stay unchanged")
        label.frame = NSRect(x: 15, y: 75, width: 390, height: 25)
        window!.contentView!.addSubview(label)
        window!.orderBack(nil)
        record()
    }
    @objc func newDocument() {
        opened += 1
        callbackActive = NSApp.isActive
        showWindow("New document")
        // A declared/enabled app-level action may defer activation when invoked
        // inactive. A controlled delay exposes the request-lifetime boundary;
        // preparing the receiver avoids entering this callback branch at all.
        if !NSApp.isActive
            && FileManager.default.fileExists(atPath: directory.appendingPathComponent("deferred-activation").path)
        {
            Task { @MainActor in
                try? await Task.sleep(for: .milliseconds(1600))
                let configuration = NSWorkspace.OpenConfiguration()
                configuration.activates = true
                configuration.createsNewApplicationInstance = false
                NSWorkspace.shared.openApplication(at: Bundle.main.bundleURL, configuration: configuration) { _, _ in }
            }
        }
    }
    @objc func forbiddenAction() {
        forbidden += 1
        record()
    }
}
let app = NSApplication.shared
app.setActivationPolicy(.regular)
let delegate = WindowlessMenuDelegate()
app.delegate = delegate
app.run()
