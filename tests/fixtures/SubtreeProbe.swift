import AppKit

/// Simulates a provider whose visible-child list is wrong while AXChildren works.
/// This is an accessibility fault fixture, not an application-specific adapter.
@MainActor final class IncompleteList: NSView {
    var entries = [Any]()
    override func accessibilityRole() -> NSAccessibility.Role? { .list }
    override func isAccessibilityElement() -> Bool { true }
    override func accessibilityLabel() -> String? { "Incomplete records" }
    override func accessibilityChildren() -> [Any]? { entries }
    override func accessibilityVisibleChildren() -> [Any]? { [] }
}

/// Forward children stay in this window, while a broken bridge points back to
/// another one. Reading that child path must not authorize input there.
@MainActor final class MislinkedGroup: NSView {
    weak var reportedWindow: NSWindow?
    override func accessibilityParent() -> Any? { reportedWindow }
    override func accessibilityWindow() -> Any? { reportedWindow }
    override func accessibilityTopLevelUIElement() -> Any? { reportedWindow }
}

@MainActor final class SubtreeDelegate: NSObject, NSApplicationDelegate {
    var windows = [NSWindow]()
    let status = NSTextField(labelWithString: "Waiting")
    let mislinked = MislinkedGroup(frame: NSRect(x: 20, y: 0, width: 300, height: 26))
    let directory = Bundle.main.bundleURL.deletingLastPathComponent()
    func applicationDidFinishLaunching(_ notification: Notification) {
        try! String(getpid()).write(
            to: directory.appendingPathComponent("fixture.pid"), atomically: true, encoding: .utf8)
        let first = NSWindow(
            contentRect: NSRect(x: 60, y: 200, width: 500, height: 300), styleMask: [.titled, .closable],
            backing: .buffered, defer: false)
        first.title = "Large records"
        let list = IncompleteList(frame: NSRect(x: 20, y: 70, width: 460, height: 200))
        for index in 0..<300 {
            let item = NSAccessibilityElement()
            item.setAccessibilityRole(.staticText)
            item.setAccessibilityLabel("Record \(index)")
            item.setAccessibilityValue("Value \(index)")
            item.setAccessibilityParent(list)
            list.entries.append(item)
        }
        first.contentView!.addSubview(list)
        let deep = NSAccessibilityElement()
        deep.setAccessibilityRole(.group)
        deep.setAccessibilityLabel("Deep region 0")
        deep.setAccessibilityParent(list)
        var parent = deep
        for level in 1...55 {
            let child = NSAccessibilityElement()
            child.setAccessibilityRole(.group)
            child.setAccessibilityLabel("Deep region \(level)")
            child.setAccessibilityParent(parent)
            parent.setAccessibilityChildren([child])
            parent = child
        }
        let secret = NSAccessibilityElement()
        secret.setAccessibilityRole(.staticText)
        secret.setAccessibilityValue("Deep verification reached")
        secret.setAccessibilityParent(parent)
        parent.setAccessibilityChildren([secret])
        list.entries.append(deep)
        status.frame = NSRect(x: 20, y: 30, width: 300, height: 24)
        first.contentView!.addSubview(status)
        let group = NSView(frame: NSRect(x: 340, y: 20, width: 140, height: 35))
        group.setAccessibilityElement(true)
        group.setAccessibilityRole(.group)
        group.setAccessibilityLabel("Actions")
        let button = NSButton(title: "Commit", target: self, action: #selector(commit))
        button.frame = group.bounds
        group.addSubview(button)
        first.contentView!.addSubview(group)
        let second = NSWindow(
            contentRect: NSRect(x: 600, y: 200, width: 400, height: 250), styleMask: [.titled, .closable],
            backing: .buffered, defer: false)
        second.title = "Other document"
        let label = NSTextField(labelWithString: "Unrelated content")
        label.frame = NSRect(x: 20, y: 50, width: 300, height: 25)
        second.contentView!.addSubview(label)
        mislinked.reportedWindow = second
        mislinked.setAccessibilityElement(true)
        mislinked.setAccessibilityRole(.group)
        mislinked.setAccessibilityLabel("Conflicting window links")
        let field = NSTextField(string: "untouched")
        field.frame = mislinked.bounds
        field.setAccessibilityLabel("Mislinked input")
        mislinked.addSubview(field)
        first.contentView!.addSubview(mislinked)
        // A poor bridge can include another native window inside a container.
        // Its controls must remain scoped to that window, not this list's image.
        list.entries.append(second)
        for window in [first, second] {
            window.isReleasedWhenClosed = false
            window.tabbingMode = .disallowed
            window.orderBack(nil)
            windows.append(window)
        }
        first.makeKey()
        first.makeMain()
        Timer.scheduledTimer(withTimeInterval: 150, repeats: false) { _ in
            MainActor.assumeIsolated { NSApp.terminate(nil) }
        }
    }
    @objc func commit() {
        status.stringValue = "Committed"
        mislinked.removeFromSuperview()
        windows[1].contentView!.addSubview(mislinked)
        try! "committed once".write(
            to: directory.appendingPathComponent("commit.txt"), atomically: true, encoding: .utf8)
    }
}
let application = NSApplication.shared
application.setActivationPolicy(.regular)
let delegate = SubtreeDelegate()
application.delegate = delegate
application.run()
