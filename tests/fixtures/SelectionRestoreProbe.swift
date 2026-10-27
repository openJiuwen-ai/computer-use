import AppKit

// A normal text view whose Right command completes on a later run-loop turn.
// Its click handler places the caret at the start, so preparing an already
// focused editor must restore the prior selection before inserting text.
@MainActor final class DelayedSelectionView: NSTextView {
    weak var probe: ProbeDelegate?
    override func mouseDown(with event: NSEvent) {
        super.mouseDown(with: event)
        setSelectedRange(NSRange(location: 0, length: 0))
        probe?.record("clicked")
    }
    override func moveRight(_ sender: Any?) {
        guard selectedRange().length > 0 else {
            super.moveRight(sender)
            return
        }
        probe?.pending = true
        probe?.record("restore-pending")
        Timer.scheduledTimer(withTimeInterval: 0.2, repeats: false) { _ in
            MainActor.assumeIsolated { self.completeRight() }
        }
    }
    private func completeRight() {
        super.moveRight(nil)
        if probe?.changeDocument == true {
            window?.representedURL = URL(fileURLWithPath: "/selection-restore-changed.txt")
        }
        probe?.pending = false
        probe?.record("restore-complete")
    }
    override func insertText(_ value: Any, replacementRange: NSRange) {
        probe?.record("insert", extra: ["inserted": value as? String ?? ""])
        super.insertText(value, replacementRange: replacementRange)
        probe?.record("insert-complete")
    }
}

@MainActor final class ProbeDelegate: NSObject, NSApplicationDelegate {
    let directory = Bundle.main.bundleURL.deletingLastPathComponent()
    let text = DelayedSelectionView()
    let unrelated = NSTextField(string: "untouched")
    var window: NSWindow!
    var pending = false
    var changeDocument: Bool { Bundle.main.object(forInfoDictionaryKey: "ChangeDocument") as? Bool == true }
    func record(_ kind: String, extra: [String: Any] = [:]) {
        guard window != nil else { return }
        var row: [String: Any] = [
            "kind": kind, "text": text.string,
            "selection": [text.selectedRange().location, text.selectedRange().length], "pending": pending,
            "unrelated": unrelated.stringValue, "time": Date().timeIntervalSince1970
        ]
        row.merge(extra) { _, new in new }
        let data = try! JSONSerialization.data(withJSONObject: row, options: [.sortedKeys])
        try! data.write(to: directory.appendingPathComponent("state.json"), options: .atomic)
        let path = directory.appendingPathComponent("events.jsonl")
        if let handle = try? FileHandle(forWritingTo: path) {
            try! handle.seekToEnd()
            try! handle.write(contentsOf: data + Data([10]))
            try! handle.close()
        } else {
            try! (data + Data([10])).write(to: path)
        }
    }
    func applicationDidFinishLaunching(_ notification: Notification) {
        try! String(getpid()).write(
            to: directory.appendingPathComponent("fixture.pid"), atomically: true, encoding: .utf8)
        let menu = NSMenu(), edit = NSMenu(title: "Edit"), item = NSMenuItem()
        edit.addItem(withTitle: "Select All", action: #selector(NSText.selectAll(_:)), keyEquivalent: "a")
        item.submenu = edit
        menu.addItem(item)
        NSApp.mainMenu = menu
        window = NSWindow(
            contentRect: NSRect(x: 200, y: 240, width: 460, height: 240), styleMask: [.titled, .closable],
            backing: .buffered, defer: false)
        window.title = "Delayed selection probe"
        window.isReleasedWhenClosed = false
        window.tabbingMode = .disallowed
        window.representedURL = URL(fileURLWithPath: "/selection-restore-initial.txt")
        text.frame = NSRect(x: 20, y: 70, width: 420, height: 145)
        text.isRichText = false
        text.font = NSFont.systemFont(ofSize: 18)
        text.string = "Original content"
        text.probe = self
        text.setAccessibilityLabel("Delayed editor")
        window.contentView?.addSubview(text)
        unrelated.frame = NSRect(x: 20, y: 20, width: 420, height: 30)
        unrelated.setAccessibilityLabel("Unrelated editor")
        window.contentView?.addSubview(unrelated)
        window.orderBack(nil)
        window.makeKey()
        window.makeFirstResponder(text)
        text.setSelectedRange(NSRange(location: text.string.utf16.count, length: 0))
        record("ready")
        Timer.scheduledTimer(withTimeInterval: 120, repeats: false) { _ in
            MainActor.assumeIsolated { NSApp.terminate(nil) }
        }
    }
}
let app = NSApplication.shared
app.setActivationPolicy(.regular)
let delegate = ProbeDelegate()
app.delegate = delegate
app.run()
