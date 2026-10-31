// Copyright (c) Huawei Technologies Co., Ltd. 2026. All rights reserved.

import AppKit

// The displayed query and the application's actual search result are independent.
@MainActor final class SearchInputDelegate: NSObject, NSApplicationDelegate, NSSearchFieldDelegate {
    let directory = Bundle.main.bundleURL.deletingLastPathComponent()
    var window: NSWindow!
    let search = NSSearchField(string: "")
    let other = NSTextField(string: "unchanged")
    let result = NSTextField(labelWithString: "No search event")
    var changes = 0, actions = 0
    var panel: NSOpenPanel?

    func record() {
        let value: [String: Any] = [
            "pid": getpid(), "value": search.stringValue, "changes": changes, "actions": actions,
            "result": result.stringValue, "other": other.stringValue,
            "mouseX": NSEvent.mouseLocation.x, "mouseY": NSEvent.mouseLocation.y,
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
        result.frame = NSRect(x: 20, y: 95, width: 440, height: 25)
        result.setAccessibilityIdentifier("result")
        for view in [search, other, result] { window.contentView!.addSubview(view) }
        let open = NSButton(title: "Open test panel", target: self, action: #selector(openPanel))
        open.frame = NSRect(x: 20, y: 5, width: 170, height: 30)
        window.contentView!.addSubview(open)
        window.orderBack(nil)
        window.makeKey()
        window.makeMain()
        window.makeFirstResponder(other)
        let menu = NSMenu()
        let edit = NSMenuItem(title: "Edit", action: nil, keyEquivalent: "")
        edit.submenu = NSMenu()
        edit.submenu!.addItem(withTitle: "Select All", action: #selector(NSText.selectAll(_:)), keyEquivalent: "a")
        menu.addItem(edit)
        NSApp.mainMenu = menu
        record()
        Timer.scheduledTimer(withTimeInterval: 0.1, repeats: true) { _ in MainActor.assumeIsolated { self.record() } }
    }

    @objc func openPanel() {
        panel = NSOpenPanel()
        panel!.title = "Hosted input panel"
        panel!.directoryURL = URL(fileURLWithPath: "/Applications")
        panel!.beginSheetModal(for: window) { _ in }
    }

    @objc func searched() {
        actions += 1
        result.stringValue = "Matched: " + search.stringValue
        record()
    }
    func controlTextDidChange(_ notification: Notification) {
        changes += 1
        record()
    }
}

@main enum SearchInputProbe {
    @MainActor static func main() {
        let app = NSApplication.shared
        app.setActivationPolicy(.regular)
        let delegate = SearchInputDelegate()
        app.delegate = delegate
        app.run()
    }
}
