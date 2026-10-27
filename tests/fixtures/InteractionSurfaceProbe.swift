import AppKit

// Real AppKit popover/sheet surfaces: the AX hierarchy and keyboard window
// need not have the same WindowServer identity. No AX focus is fabricated.
@MainActor final class SurfaceProbe: NSObject, NSApplicationDelegate, NSTextFieldDelegate {
    let first = NSWindow(
        contentRect: NSRect(x: 220, y: 280, width: 480, height: 280), styleMask: [.titled, .closable],
        backing: .buffered, defer: false)
    let second = NSWindow(
        contentRect: NSRect(x: 740, y: 280, width: 440, height: 250), styleMask: [.titled, .closable],
        backing: .buffered, defer: false)
    let popover = NSPopover()
    let document = NSTextField(string: "document unchanged")
    let unrelated = NSTextField(string: "unrelated unchanged")
    let editor = NSTextField(string: "before")
    let sheetEditor = NSTextField(string: "sheet before")
    let anchor = NSButton(title: "Open popover", target: nil, action: nil)
    var sheet: NSWindow?
    var commits = 0, clicks = 0, changes = 0
    var handoff = false
    let output = Bundle.main.bundleURL.deletingLastPathComponent()

    func record() {
        let state: [String: Any] = [
            "document": document.stringValue, "unrelated": unrelated.stringValue, "editor": editor.stringValue,
            "sheet": sheetEditor.stringValue, "commits": commits, "changes": changes, "clicks": clicks,
            "popoverShown": popover.isShown, "handoff": handoff, "mainWindow": first.windowNumber,
            "otherWindow": second.windowNumber, "popupWindow": editor.window?.windowNumber ?? -1,
            "foreground": NSWorkspace.shared.frontmostApplication?.processIdentifier == getpid(),
            "keyWindow": NSApp.keyWindow?.windowNumber ?? -1
        ]
        try! JSONSerialization.data(withJSONObject: state, options: [.sortedKeys]).write(
            to: output.appendingPathComponent("state.json"), options: .atomic)
    }

    func field(_ field: NSTextField, id: String, view: NSView, y: Double) {
        field.frame = NSRect(x: 20, y: y, width: 270, height: 28)
        field.setAccessibilityIdentifier(id)
        field.delegate = self
        view.addSubview(field)
    }

    func applicationDidFinishLaunching(_ notification: Notification) {
        let menu = NSMenu(), edit = NSMenuItem(title: "Edit", action: nil, keyEquivalent: "")
        edit.submenu = NSMenu(title: "Edit")
        edit.submenu!.addItem(withTitle: "Paste", action: #selector(NSText.paste(_:)), keyEquivalent: "v")
        menu.addItem(edit)
        NSApp.mainMenu = menu
        try! String(getpid()).write(to: output.appendingPathComponent("fixture.pid"), atomically: true, encoding: .utf8)
        first.title = "Routing document"
        second.title = "Unrelated document"
        for window in [first, second] {
            window.tabbingMode = .disallowed
            window.isReleasedWhenClosed = false
        }
        field(document, id: "document", view: first.contentView!, y: 170)
        field(unrelated, id: "unrelated", view: second.contentView!, y: 150)
        anchor.frame = NSRect(x: 20, y: 90, width: 150, height: 32)
        anchor.target = self
        anchor.action = #selector(openPopover)
        first.contentView!.addSubview(anchor)
        let button = NSButton(title: "Open sheet", target: self, action: #selector(openSheet))
        button.frame = NSRect(x: 190, y: 90, width: 140, height: 32)
        first.contentView!.addSubview(button)
        let controller = NSViewController()
        controller.view = NSView(frame: NSRect(x: 0, y: 0, width: 320, height: 270))
        field(editor, id: "popup-editor", view: controller.view, y: 220)
        editor.target = self
        editor.action = #selector(commit)
        let done = NSButton(title: "Close popover", target: self, action: #selector(closePopover))
        done.frame = NSRect(x: 20, y: 165, width: 150, height: 30)
        controller.view.addSubview(done)
        for (title, action, y) in [
            ("Focus document", #selector(focusDocument), 115.0), ("Focus other document", #selector(focusOther), 70.0),
            ("Move focus after #", #selector(armHandoff), 25.0)
        ] {
            let control = NSButton(title: title, target: self, action: action)
            control.frame = NSRect(x: 20, y: y, width: 240, height: 30)
            controller.view.addSubview(control)
        }
        popover.contentViewController = controller
        popover.behavior = .applicationDefined
        second.orderBack(nil)
        first.orderBack(nil)
        record()
        Timer.scheduledTimer(withTimeInterval: 0.1, repeats: true) { _ in MainActor.assumeIsolated { self.record() } }
        NSEvent.addLocalMonitorForEvents(matching: .leftMouseDown) { [weak self] event in
            MainActor.assumeIsolated {
                self?.clicks += 1
                self?.record()
            }
            return event
        }
    }
    @objc func openPopover() {
        popover.show(relativeTo: anchor.bounds, of: anchor, preferredEdge: .maxX)
        editor.window?.makeFirstResponder(editor)
        record()
    }
    @objc func closePopover() {
        popover.close()
        record()
    }
    @objc func focusDocument() {
        first.makeKey()
        first.makeFirstResponder(document)
        record()
    }
    @objc func focusOther() {
        second.makeKey()
        second.makeFirstResponder(unrelated)
        record()
    }
    @objc func armHandoff() {
        handoff = true
        record()
    }
    @objc func commit() {
        commits += 1
        record()
    }
    @objc func openSheet() {
        let panel = NSWindow(
            contentRect: NSRect(x: 0, y: 0, width: 320, height: 180), styleMask: [.titled], backing: .buffered,
            defer: false)
        panel.title = "Routing sheet"
        panel.isReleasedWhenClosed = false
        field(sheetEditor, id: "sheet-editor", view: panel.contentView!, y: 100)
        let close = NSButton(title: "Close sheet", target: self, action: #selector(closeSheet))
        close.frame = NSRect(x: 20, y: 35, width: 150, height: 30)
        panel.contentView!.addSubview(close)
        sheet = panel
        first.beginSheet(panel)
        panel.makeFirstResponder(sheetEditor)
        record()
    }
    @objc func closeSheet() {
        if let sheet {
            first.endSheet(sheet)
            sheet.orderOut(nil)
        }
        sheet = nil
        record()
    }
    func controlTextDidChange(_ notification: Notification) {
        changes += 1
        if handoff, notification.object as? NSTextField === editor, editor.stringValue.contains("#") {
            handoff = false
            focusDocument()
        }
        record()
    }
}

let app = NSApplication.shared
app.setActivationPolicy(.regular)
let delegate = SurfaceProbe()
app.delegate = delegate
app.run()
