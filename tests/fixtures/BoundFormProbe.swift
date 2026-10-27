import AppKit

// Ordinary Cocoa value bindings update the model through the editing lifecycle.
// Reading a field's displayed string alone cannot verify what Save persists.
@MainActor final class FormModel: NSObject {
    @objc dynamic var first = "old first"
    @objc dynamic var second = "old second"
}

@MainActor final class BoundFormDelegate: NSObject, NSApplicationDelegate {
    let model = FormModel()
    let directory = Bundle.main.bundleURL.deletingLastPathComponent()
    var window: NSWindow!
    var fields = [NSTextField]()
    var saves = 0

    func applicationDidFinishLaunching(_ notification: Notification) {
        try! String(getpid()).write(
            to: directory.appendingPathComponent("fixture.pid"), atomically: true, encoding: .utf8)
        window = NSWindow(
            contentRect: NSRect(x: 160, y: 180, width: 460, height: 200), styleMask: [.titled, .closable],
            backing: .buffered, defer: false)
        window.title = "Bound form probe"
        for (index, key) in ["first", "second"].enumerated() {
            let frame = NSRect(x: 20, y: 140 - index * 50, width: 420, height: 28)
            #if SECURE_INPUT_PROBE
                let field: NSTextField = index == 0 ? NSSecureTextField(frame: frame) : NSTextField(frame: frame)
            #else
                let field = NSTextField(frame: frame)
            #endif
            field.setAccessibilityIdentifier(key)
            field.bind(.value, to: model, withKeyPath: key, options: [.continuouslyUpdatesValue: true])
            window.contentView!.addSubview(field)
            fields.append(field)
        }
        let save = NSButton(title: "Save model", target: self, action: #selector(saveModel))
        save.frame = NSRect(x: 20, y: 20, width: 150, height: 28)
        window.contentView!.addSubview(save)
        let menu = NSMenu(), edit = NSMenuItem(title: "Edit", action: nil, keyEquivalent: "")
        edit.submenu = NSMenu()
        edit.submenu!.addItem(withTitle: "Select All", action: #selector(NSText.selectAll(_:)), keyEquivalent: "a")
        menu.addItem(edit)
        NSApp.mainMenu = menu
        window.orderBack(nil)
        window.makeKey()
        window.makeMain()
        window.makeFirstResponder(fields[0])
        record()
        Timer.scheduledTimer(withTimeInterval: 180, repeats: false) { _ in
            MainActor.assumeIsolated { NSApp.terminate(nil) }
        }
    }

    func record() {
        try! JSONSerialization.data(withJSONObject: [
            "first": model.first, "second": model.second, "displayed": fields.map(\.stringValue), "saves": saves
        ]).write(to: directory.appendingPathComponent("state.json"), options: .atomic)
    }
    @objc func saveModel() {
        window.makeFirstResponder(nil)
        saves += 1
        record()
    }
}

let app = NSApplication.shared
app.setActivationPolicy(.regular)
let delegate = BoundFormDelegate()
app.delegate = delegate
app.run()
