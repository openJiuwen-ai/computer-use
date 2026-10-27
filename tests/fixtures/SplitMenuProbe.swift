import AppKit

// One visible control, two distinct AX actions. The revealed choices and counts
// let an external observer distinguish opening the menu from applying a choice.
@MainActor final class SplitControl: NSView {
    var presses = 0
    var opens = 0
    var choices: NSTextField!
    override func accessibilityPerformPress() -> Bool {
        presses += 1
        update()
        return true
    }
    override func accessibilityPerformShowMenu() -> Bool {
        opens += 1
        choices.isHidden = false
        update()
        return true
    }
    func update() {
        setAccessibilityValue("primary=\(presses), menu=\(opens)")
        needsDisplay = true
    }
    override func draw(_ rect: NSRect) {
        ("Apply color ▾  primary=\(presses), menu=\(opens)" as NSString).draw(
            at: NSPoint(x: 4, y: 8), withAttributes: [.foregroundColor: NSColor.labelColor])
    }
}

@MainActor final class Delegate: NSObject, NSApplicationDelegate {
    var window: NSWindow!
    func applicationDidFinishLaunching(_ notification: Notification) {
        window = NSWindow(
            contentRect: NSRect(x: 100, y: 100, width: 450, height: 220), styleMask: [.titled, .closable],
            backing: .buffered, defer: false)
        window.title = "Split Menu Probe"
        let choices = NSTextField(labelWithString: "Choices: Red, Blue")
        choices.frame = NSRect(x: 20, y: 90, width: 400, height: 28)
        choices.isHidden = true
        choices.setAccessibilityIdentifier("menu-choices")
        let split = SplitControl(frame: NSRect(x: 20, y: 140, width: 400, height: 40))
        split.choices = choices
        split.setAccessibilityElement(true)
        split.setAccessibilityRole(.menuButton)
        split.setAccessibilityIdentifier("split-color")
        split.setAccessibilityLabel("Apply color")
        split.update()
        let plain = NSButton(title: "Plain button", target: nil, action: nil)
        plain.frame = NSRect(x: 20, y: 35, width: 180, height: 32)
        plain.setAccessibilityIdentifier("plain-button")
        let disabled = SplitControl(frame: NSRect(x: 225, y: 35, width: 200, height: 32))
        disabled.choices = choices
        disabled.setAccessibilityElement(true)
        disabled.setAccessibilityRole(.menuButton)
        disabled.setAccessibilityIdentifier("disabled-menu")
        disabled.setAccessibilityEnabled(false)
        disabled.update()
        for view in [split, choices, plain, disabled] { window.contentView?.addSubview(view) }
        window.orderBack(nil)
        let menu = NSMenu(), item = NSMenuItem(), commands = NSMenu()
        commands.addItem(withTitle: "Quit Probe", action: #selector(NSApplication.terminate(_:)), keyEquivalent: "q")
        item.submenu = commands
        menu.addItem(item)
        NSApplication.shared.mainMenu = menu
        Timer.scheduledTimer(withTimeInterval: 90, repeats: false) { _ in
            MainActor.assumeIsolated { NSApplication.shared.terminate(nil) }
        }
    }
}
let application = NSApplication.shared
let delegate = Delegate()
application.delegate = delegate
application.setActivationPolicy(.regular)
application.run()
