import AppKit

// Deliberately violate AX completeness, independently of any business app.
@MainActor final class CatalogApplication: NSApplication {
    var exposesAX = false
    override func accessibilityRole() -> NSAccessibility.Role? { exposesAX ? super.accessibilityRole() : nil }
    override func accessibilityChildren() -> [Any]? { exposesAX ? super.accessibilityChildren() : [] }
    override func accessibilityWindows() -> [Any]? {
        if ProcessInfo.processInfo.environment["COMPUTER_USE_TEST_LATE_WINDOWS"] == "1" {
            (delegate as? CatalogDelegate)?.createDocument()
        }
        return exposesAX ? super.accessibilityWindows() : []
    }
    override func accessibilityFocusedWindow() -> Any? { exposesAX ? super.accessibilityFocusedWindow() : nil }
    override func accessibilityMainWindow() -> Any? { exposesAX ? super.accessibilityMainWindow() : nil }
}

@MainActor final class CatalogWindow: NSWindow {
    override var canBecomeKey: Bool { true }
    override func accessibilityRole() -> NSAccessibility.Role? {
        (NSApp as! CatalogApplication).exposesAX ? super.accessibilityRole() : nil
    }
    override func accessibilityChildren() -> [Any]? {
        (NSApp as! CatalogApplication).exposesAX ? super.accessibilityChildren() : []
    }
}

@MainActor final class CatalogDelegate: NSObject, NSApplicationDelegate {
    let directory = Bundle.main.bundleURL.deletingLastPathComponent()
    var document: NSWindow!
    var twin: NSWindow?
    var popup: NSWindow?
    var openButton: NSButton!
    func applicationDidFinishLaunching(_ notification: Notification) {
        NSWindow.allowsAutomaticWindowTabbing = false
        try! Data(String(ProcessInfo.processInfo.processIdentifier).utf8).write(
            to: directory.appendingPathComponent("fixture.pid"))
        if ProcessInfo.processInfo.environment["COMPUTER_USE_TEST_LATE_WINDOWS"] != "1" { createDocument() }
        Timer.scheduledTimer(withTimeInterval: 300, repeats: false) { _ in
            MainActor.assumeIsolated { NSApp.terminate(nil) }
        }
    }
    func createDocument() {
        guard document == nil else { return }
        document = CatalogWindow(
            contentRect: NSRect(x: 60, y: 260, width: 360, height: 200), styleMask: [.titled, .closable],
            backing: .buffered, defer: false)
        document.title = "Same document"
        document.isReleasedWhenClosed = false
        openButton = button("Open untitled popup", #selector(openPopup), in: document)
        document.orderBack(nil)
        document.makeKey()
        document.makeMain()
    }
    func button(_ title: String, _ action: Selector, in window: NSWindow) -> NSButton {
        let value = NSButton(title: title, target: self, action: action)
        value.frame = NSRect(x: 40, y: 50, width: window.contentView!.bounds.width - 80, height: 44)
        window.contentView!.addSubview(value)
        return value
    }
    @objc func openPopup() {
        guard popup == nil else { return }
        // Identical native geometry/title must still produce separate targets.
        var twinFrame = NSRect(x: 60, y: 260, width: 360, height: 200)
        if ProcessInfo.processInfo.environment["COMPUTER_USE_TEST_OVERLAP"] == "1" {
            twinFrame.origin = NSPoint(x: 244, y: 311)
        }
        if ProcessInfo.processInfo.environment["COMPUTER_USE_TEST_SECOND_DISPLAY"] == "1", NSScreen.screens.count > 1 {
            let display = NSScreen.screens[1].visibleFrame
            twinFrame.origin = NSPoint(x: display.minX + 60, y: display.minY + 200)
        }
        twin = CatalogWindow(contentRect: twinFrame, styleMask: [.titled, .closable], backing: .buffered, defer: false)
        twin!.title = document.title
        twin!.isReleasedWhenClosed = false
        _ = button("Wrong duplicate window", #selector(wrongTarget), in: twin!)
        twin!.orderBack(nil)
        if ProcessInfo.processInfo.environment["COMPUTER_USE_TEST_SECOND_DISPLAY"] == "1"
            || ProcessInfo.processInfo.environment["COMPUTER_USE_TEST_OVERLAP"] == "1"
        {
            // NSWindow's initial placement may be constrained to its default
            // screen. Explicit placement is part of this fixture, not backend input.
            twin!.setFrameOrigin(twinFrame.origin)
        }
        popup = CatalogWindow(
            contentRect: NSRect(x: 460, y: 260, width: 300, height: 160), styleMask: [.borderless], backing: .buffered,
            defer: false)
        popup!.title = ""
        popup!.isReleasedWhenClosed = false
        popup!.level = .floating
        _ = button("Restore accessibility", #selector(restoreAX), in: popup!)
        popup!.orderBack(nil)
        if ProcessInfo.processInfo.environment["COMPUTER_USE_TEST_CHILD_WINDOW"] == "1" {
            document.addChildWindow(popup!, ordered: .above)
        }
        let layout: [String: Any] = [
            "secondDisplay": twin?.screen != nil && twin?.screen != document.screen,
            "document": NSStringFromRect(document.frame), "twin": NSStringFromRect(twin!.frame),
            "popup": NSStringFromRect(popup!.frame), "childWindow": popup?.parent === document,
            "overlap": twin!.frame.intersects(popup!.frame)
        ]
        try! JSONSerialization.data(withJSONObject: layout).write(to: directory.appendingPathComponent("layout.json"))
    }
    @objc func restoreAX() {
        try! Data("untitled popup clicked".utf8).write(to: directory.appendingPathComponent("popup-result"))
        (NSApp as! CatalogApplication).exposesAX = true
        openButton.title = "Commit via accessibility"
        openButton.action = #selector(commit)
        popup?.close()
        popup = nil
    }
    @objc func commit() {
        try! Data("correct original document".utf8).write(to: directory.appendingPathComponent("commit-result"))
        openButton.title = "Committed"
        openButton.isEnabled = false
    }
    @objc func wrongTarget() {
        try! Data("wrong target".utf8).write(to: directory.appendingPathComponent("wrong-result"))
    }
}

let application = CatalogApplication.shared
application.setActivationPolicy(.regular)
let delegate = CatalogDelegate()
application.delegate = delegate
application.run()
