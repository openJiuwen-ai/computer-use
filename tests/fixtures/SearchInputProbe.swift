import AppKit

// A broken decorative child must not prevent the field itself from receiving
// normal text input. The optional override changes only accessibility metadata;
// AppKit still owns focus, selection, typing and the search callbacks.
@MainActor final class SearchProbeCell: NSSearchFieldCell {
    // Set once before this cell is exposed; the legacy AX getter is nonisolated.
    nonisolated(unsafe) var accessory: NSAccessibilityElement?
    override func accessibilityAttributeValue(_ attribute: NSAccessibility.Attribute) -> Any? {
        if attribute == .children, let accessory { return [accessory] }
        return super.accessibilityAttributeValue(attribute)
    }
}

// The displayed query and the application's actual search result are independent.
@MainActor final class SearchInputDelegate: NSObject, NSApplicationDelegate, NSSearchFieldDelegate {
    let directory = Bundle.main.bundleURL.deletingLastPathComponent()
    var window: NSWindow!
    let search = NSSearchField(string: "")
    let other = NSTextField(string: "unchanged")
    let result = NSTextField(labelWithString: "No search event")
    var changes = 0, actions = 0

    func record() {
        let value: [String: Any] = [
            "pid": getpid(), "value": search.stringValue, "changes": changes, "actions": actions,
            "result": result.stringValue, "other": other.stringValue
        ]
        try! JSONSerialization.data(withJSONObject: value).write(
            to: directory.appendingPathComponent("state.json"), options: .atomic)
    }

    func applicationDidFinishLaunching(_ notification: Notification) {
        window = NSWindow(
            contentRect: NSRect(x: 170, y: 160, width: 480, height: 210), styleMask: [.titled, .closable],
            backing: .buffered, defer: false)
        window.title = "Native search probe"
        search.frame = NSRect(x: 20, y: 145, width: 440, height: 28)
        search.setAccessibilityIdentifier("live-search")
        search.sendsSearchStringImmediately = true
        search.sendsWholeSearchString = false
        search.target = self
        search.action = #selector(searched)
        search.delegate = self
        other.frame = NSRect(x: 20, y: 40, width: 440, height: 28)
        other.setAccessibilityIdentifier("other")
        if Bundle.main.object(forInfoDictionaryKey: "ProbeIncompleteChildren") as? Bool == true {
            // This provider contradicts its forward containment. The decorative
            // button is not an editor and cannot receive text or keyboard focus.
            let accessory = NSAccessibilityElement()
            accessory.setAccessibilityRole(.button)
            accessory.setAccessibilityLabel("Unresolvable accessory")
            accessory.setAccessibilityParent(other)
            let cell = SearchProbeCell(textCell: "")
            cell.accessory = accessory
            search.cell = cell
            search.isEditable = true
            search.isSelectable = true
            search.sendsSearchStringImmediately = true
            search.sendsWholeSearchString = false
            search.target = self
            search.action = #selector(searched)
        }
        result.frame = NSRect(x: 20, y: 95, width: 440, height: 25)
        result.setAccessibilityIdentifier("result")
        for view in [search, other, result] { window.contentView!.addSubview(view) }
        window.orderBack(nil)
        window.makeKey()
        window.makeMain()
        window.makeFirstResponder(other)
        let menu = NSMenu(), edit = NSMenuItem(title: "Edit", action: nil, keyEquivalent: "")
        edit.submenu = NSMenu()
        edit.submenu!.addItem(withTitle: "Select All", action: #selector(NSText.selectAll(_:)), keyEquivalent: "a")
        menu.addItem(edit)
        NSApp.mainMenu = menu
        record()
        Timer.scheduledTimer(withTimeInterval: 0.1, repeats: true) { _ in MainActor.assumeIsolated { self.record() } }
    }

    @objc func searched() {
        actions += 1
        result.stringValue = "Matched: " + search.stringValue
        record()
    }
    func controlTextDidChange(_ notification: Notification) {
        changes += 1
        if search.stringValue == "X",
            FileManager.default.fileExists(atPath: directory.appendingPathComponent("handoff").path)
        {
            window.makeFirstResponder(other)
        }
        record()
    }
}

let app = NSApplication.shared
app.setActivationPolicy(.regular)
let delegate = SearchInputDelegate()
app.delegate = delegate
app.run()
