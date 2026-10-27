import AppKit

// Disposable real AppKit application used by the opt-in desktop smoke test.
@MainActor final class ProbeContent: NSView {
    var excluded: NSView?
    override func accessibilityChildren() -> [Any]? { subviews.filter { $0 !== excluded } }
}

@MainActor final class ProbeDelegate: NSObject, NSApplicationDelegate {
    var window: NSWindow!
    let content = ProbeContent()
    let field = NSTextField(string: "")
    let counter = NSTextField(labelWithString: "Clicks: 0")
    let firstDuplicate = NSButton(title: "Duplicate A", target: nil, action: nil)
    let secondDuplicate = NSButton(title: "Duplicate B", target: nil, action: nil)
    var clicks = 0

    func applicationDidFinishLaunching(_ notification: Notification) {
        let log = Bundle.main.bundleURL.deletingLastPathComponent().appendingPathComponent("probe-events.jsonl")
        FileManager.default.createFile(atPath: log.path, contents: nil)
        let handle = try! FileHandle(forWritingTo: log)
        // Passive input log: preserve normal AppKit dispatch and prove the target
        // stayed behind the foreground app while accepting injected keys.
        NSEvent.addLocalMonitorForEvents(matching: [.keyDown, .keyUp, .flagsChanged]) { event in
            let value: [String: Any] = [
                "type": event.type.rawValue, "code": event.keyCode, "flags": event.modifierFlags.rawValue,
                "window": event.windowNumber,
                "chars": event.type == .keyDown || event.type == .keyUp ? event.characters ?? "" : "",
                "active": NSApplication.shared.isActive,
                "frontPID": NSWorkspace.shared.frontmostApplication?.processIdentifier ?? 0,
                "pid": ProcessInfo.processInfo.processIdentifier
            ]
            try? handle.write(contentsOf: JSONSerialization.data(withJSONObject: value) + Data([10]))
            return event
        }
        let menu = NSMenu()
        let item = NSMenuItem()
        let submenu = NSMenu()
        submenu.addItem(
            withTitle: "Quit Desktop Probe", action: #selector(NSApplication.terminate(_:)), keyEquivalent: "q")
        item.submenu = submenu
        menu.addItem(item)
        let edit = NSMenuItem(title: "Edit", action: nil, keyEquivalent: "")
        let editMenu = NSMenu(title: "Edit")
        editMenu.addItem(withTitle: "Select All", action: #selector(NSText.selectAll(_:)), keyEquivalent: "a")
        let countItem = editMenu.addItem(
            withTitle: "Count via shortcut", action: #selector(clicked), keyEquivalent: "d")
        countItem.target = self
        let moveItem = editMenu.addItem(
            withTitle: "Move test window", action: #selector(moveWindow), keyEquivalent: "m")
        moveItem.keyEquivalentModifierMask = [.command, .shift]
        moveItem.target = self
        edit.submenu = editMenu
        menu.addItem(edit)
        NSApplication.shared.mainMenu = menu
        window = NSWindow(
            contentRect: NSRect(x: 0, y: 0, width: 460, height: 180), styleMask: [.titled, .closable],
            backing: .buffered, defer: false)
        window.title = "Computer Use Desktop Probe"
        window.isReleasedWhenClosed = false
        content.setAccessibilityElement(true)
        content.setAccessibilityRole(.group)
        window.contentView = content
        field.frame = NSRect(x: 24, y: 110, width: 410, height: 28)
        field.setAccessibilityLabel("Probe input")
        field.setAccessibilityIdentifier("ProbeInput")
        let button = NSButton(title: "Count click", target: self, action: #selector(clicked))
        button.setAccessibilityIdentifier("CountClick")
        button.frame = NSRect(x: 24, y: 55, width: 140, height: 32)
        counter.frame = NSRect(x: 185, y: 60, width: 230, height: 24)
        window.contentView?.addSubview(field)
        window.contentView?.addSubview(button)
        window.contentView?.addSubview(counter)
        for (index, duplicate) in [firstDuplicate, secondDuplicate].enumerated() {
            duplicate.frame = NSRect(x: 12 + index * 110, y: 10, width: 108, height: 28)
            duplicate.setAccessibilityIdentifier("AllClear")
            duplicate.target = self
            duplicate.action = #selector(clicked)
            window.contentView?.addSubview(duplicate)
        }
        let toggle = NSButton(title: "Toggle A", target: self, action: #selector(toggleDuplicate))
        toggle.setAccessibilityIdentifier("ToggleDuplicate")
        toggle.frame = NSRect(x: 232, y: 10, width: 108, height: 28)
        window.contentView?.addSubview(toggle)
        let reattach = NSButton(title: "Reattach A", target: self, action: #selector(reattachDuplicate))
        reattach.setAccessibilityIdentifier("ReattachDuplicate")
        reattach.frame = NSRect(x: 342, y: 10, width: 108, height: 28)
        window.contentView?.addSubview(reattach)
        window.center()
        window.orderBack(nil)
        window.makeKey()
        window.makeMain()
        window.makeFirstResponder(field)
    }

    @objc func clicked() {
        clicks += 1
        counter.stringValue = "Clicks: \(clicks)"
    }
    @objc func toggleDuplicate() { content.excluded = content.excluded == nil ? firstDuplicate : nil }
    @objc func reattachDuplicate() {
        firstDuplicate.removeFromSuperview()
        content.addSubview(firstDuplicate)
    }
    @objc func moveWindow() { window.setFrameOrigin(NSPoint(x: window.frame.minX + 40, y: window.frame.minY + 24)) }
    func applicationShouldTerminateAfterLastWindowClosed(_ sender: NSApplication) -> Bool { true }
}

let application = NSApplication.shared
application.setActivationPolicy(.regular)
let delegate = ProbeDelegate()
application.delegate = delegate
application.run()
