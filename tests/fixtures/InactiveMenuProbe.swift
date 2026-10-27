import AppKit

@MainActor final class InactiveMenuWindow: NSWindow {
    override func accessibilityTitleUIElement() -> Any? {
        if ProcessInfo.processInfo.arguments.contains("--no-accessible-title") { return nil }
        return super.accessibilityTitleUIElement()
    }
}

// Ordinary menu validation: commands are available only for the active document.
// The optional probe hides only the accessible title; menu and input handling
// remain native AppKit behavior.
@MainActor final class InactiveMenuDelegate: NSObject, NSApplicationDelegate, NSMenuItemValidation, NSMenuDelegate {
    let directory = Bundle.main.bundleURL.deletingLastPathComponent()
    var window: NSWindow!
    var menu: NSMenu!
    var commits = 0
    var forbidden = 0
    var opened = 0
    var child: NSWindow?
    var menuOpens = 0
    var menuTracks = 0
    var activeValidations = 0
    var label: NSTextField!
    var validateOnTracking: Bool { ProcessInfo.processInfo.arguments.contains("--validate-on-tracking") }

    func record() {
        label.stringValue = "Commits: \(commits); Forbidden: \(forbidden)"
        try! JSONSerialization.data(
            withJSONObject: [
                "commits": commits, "forbidden": forbidden, "opened": opened, "menuOpens": menuOpens,
                "menuTracks": menuTracks, "activeValidations": activeValidations, "active": NSApp.isActive,
                "pid": getpid()
            ], options: [.sortedKeys]
        ).write(to: directory.appendingPathComponent("state.json"), options: .atomic)
    }
    func applicationDidFinishLaunching(_ notification: Notification) {
        try! String(getpid()).write(
            to: directory.appendingPathComponent("fixture.pid"), atomically: true, encoding: .utf8)
        window = InactiveMenuWindow(
            contentRect: NSRect(x: 120, y: 120, width: 420, height: 160), styleMask: [.titled, .closable],
            backing: .buffered, defer: false)
        window.title = "Inactive Menu Acceptance"
        window.isReleasedWhenClosed = false
        label = NSTextField(labelWithString: "")
        label.frame = NSRect(x: 20, y: 60, width: 380, height: 30)
        window.contentView!.addSubview(label)
        let launch = NSButton(title: "Open child", target: self, action: #selector(openChild))
        launch.frame = NSRect(x: 20, y: 20, width: 140, height: 30)
        window.contentView!.addSubview(launch)
        let main = NSMenu()
        let applicationItem = NSMenuItem()
        applicationItem.submenu = NSMenu(title: "Inactive Menu Probe")
        main.addItem(applicationItem)
        let item = NSMenuItem(title: "File", action: nil, keyEquivalent: "")
        menu = NSMenu(title: "File")
        menu.delegate = self
        let commit = NSMenuItem(title: "Commit document", action: #selector(commitDocument), keyEquivalent: "")
        commit.target = self
        menu.addItem(commit)
        let blocked = NSMenuItem(title: "Always disabled", action: #selector(forbiddenDocument), keyEquivalent: "")
        blocked.target = self
        menu.addItem(blocked)
        item.submenu = menu
        main.addItem(item)
        NSApp.mainMenu = main
        NotificationCenter.default.addObserver(
            self, selector: #selector(menuTracking), name: NSMenu.didBeginTrackingNotification, object: nil)
        window.orderBack(nil)
        menu.update()
        record()
        Timer.scheduledTimer(withTimeInterval: 120, repeats: false) { _ in
            MainActor.assumeIsolated { NSApp.terminate(nil) }
        }
    }
    func validateMenuItem(_ item: NSMenuItem) -> Bool {
        item.action == #selector(commitDocument) && NSApp.isActive && activeValidations > 0
    }
    func menuWillOpen(_ menu: NSMenu) {
        // AppKit can also call this delegate during background AX inspection.
        // Count active validation separately; neither count alone proves pixels.
        menuOpens += 1
        if NSApp.isActive && !validateOnTracking { activeValidations += 1 }
        menu.update()
        record()
    }
    @objc func menuTracking(_ notification: Notification) {
        menuTracks += 1
        if NSApp.isActive && validateOnTracking { activeValidations += 1 }
        menu.update()
        record()
    }
    // AppKit validates again when tracking the menu; activation alone does not
    // explicitly update it, as in applications with lazily validated menus.
    func applicationDidBecomeActive(_ notification: Notification) { if label != nil { record() } }
    func applicationDidResignActive(_ notification: Notification) { if label != nil { record() } }
    @objc func commitDocument() {
        commits += 1
        record()
        if FileManager.default.fileExists(atPath: directory.appendingPathComponent("open-child-on-commit").path) {
            openChild()
        }
    }
    @objc func forbiddenDocument() {
        forbidden += 1
        record()
    }
    @objc func openChild() {
        opened += 1
        child = NSWindow(
            contentRect: NSRect(x: 180, y: 180, width: 320, height: 160), styleMask: [.titled, .closable],
            backing: .buffered, defer: false)
        child!.title = "Native Action Child"
        child!.isReleasedWhenClosed = false
        child!.makeKeyAndOrderFront(nil)
        let configuration = NSWorkspace.OpenConfiguration()
        configuration.activates = true
        configuration.createsNewApplicationInstance = false
        Task { @MainActor in
            try? await Task.sleep(for: .milliseconds(400))
            NSWorkspace.shared.openApplication(at: Bundle.main.bundleURL, configuration: configuration) { _, _ in }
        }
        record()
    }
}
let app = NSApplication.shared
app.setActivationPolicy(.regular)
let delegate = InactiveMenuDelegate()
app.delegate = delegate
app.run()
