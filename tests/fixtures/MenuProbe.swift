import AppKit

@MainActor final class MenuProbe: NSObject, NSApplicationDelegate {
    var window: NSWindow!
    let label = NSTextField(labelWithString: "Executed commands: 0")
    let lastCommand = NSTextField(labelWithString: "Last command: none")
    let edit = NSMenu(title: "Edit")
    var undo: NSMenuItem!
    var secondTracked: NSMenuItem!
    // Detached objects remain readable, so stale-reference checks must inspect
    // live membership rather than relying on AX reads to fail after removal.
    var retained = [NSMenuItem]()
    var count = 0

    func applicationDidFinishLaunching(_ notification: Notification) {
        NSApp.setActivationPolicy(.accessory)
        let bar = NSMenu()
        let applicationMenu = NSMenuItem(title: "Menu Probe", action: nil, keyEquivalent: "")
        applicationMenu.submenu = NSMenu(title: "Menu Probe")
        bar.addItem(applicationMenu)
        let heading = NSMenuItem(title: "Edit", action: nil, keyEquivalent: "")
        edit.autoenablesItems = false
        undo = command("Undo History", in: edit)
        command("Unavailable", in: edit, enabled: false)
        command("Duplicate", in: edit, identity: "first")
        command("Duplicate", in: edit, identity: "second")
        command("First tracked", in: edit, identity: "tracked first")
        secondTracked = command("Second tracked", in: edit, identity: "tracked second")
        command("Line\nBreak", in: edit)
        let paste = NSMenuItem(title: "Paste As", action: nil, keyEquivalent: "")
        let submenu = NSMenu(title: "Paste As")
        submenu.autoenablesItems = false
        command("New Image", in: submenu)
        paste.submenu = submenu
        edit.addItem(paste)
        heading.submenu = edit
        bar.addItem(heading)
        NSApp.mainMenu = bar
        window = NSWindow(
            contentRect: NSRect(x: 200, y: 200, width: 480, height: 180), styleMask: [.titled, .closable],
            backing: .buffered, defer: false)
        window.title = "Computer Use Menu Probe"
        label.frame = NSRect(x: 20, y: 130, width: 440, height: 30)
        window.contentView!.addSubview(label)
        lastCommand.frame = NSRect(x: 20, y: 95, width: 440, height: 30)
        window.contentView!.addSubview(lastCommand)
        for (title, selector, x) in [
            ("Reorder duplicates", #selector(reorder), 10.0), ("Replace duplicate", #selector(replaceDuplicate), 170.0),
            ("Replace unique", #selector(replaceUnique), 330.0)
        ] {
            let button = NSButton(title: title, target: self, action: selector)
            button.frame = NSRect(x: x, y: 35, width: 150, height: 32)
            window.contentView!.addSubview(button)
        }
        window.orderBack(nil)
    }

    @discardableResult func command(_ title: String, in menu: NSMenu, enabled: Bool = true, identity: String? = nil)
        -> NSMenuItem
    {
        let item = NSMenuItem(title: title, action: #selector(execute(_:)), keyEquivalent: "")
        item.target = self
        item.isEnabled = enabled
        item.representedObject = identity ?? title
        menu.addItem(item)
        return item
    }

    @objc func execute(_ sender: NSMenuItem) {
        count += 1
        label.stringValue = "Executed commands: \(count)"
        lastCommand.stringValue = "Last command: \(sender.representedObject as? String ?? "unknown")"
    }

    @objc func reorder() {
        edit.removeItem(secondTracked)
        edit.insertItem(secondTracked, at: 4)
    }

    @objc func replaceDuplicate() {
        retained.append(secondTracked)
        edit.removeItem(secondTracked)
        secondTracked = command("Replacement tracked", in: edit, identity: "replacement")
    }

    @objc func replaceUnique() {
        retained.append(undo)
        edit.removeItem(undo)
        undo = command("Changed command", in: edit, identity: "replacement unique")
    }
}

let app = NSApplication.shared
let delegate = MenuProbe()
app.delegate = delegate
app.run()
