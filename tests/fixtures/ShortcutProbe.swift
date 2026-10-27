import AppKit
import ApplicationServices

@MainActor final class ShortcutProbe: NSObject, NSApplicationDelegate, NSTextViewDelegate {
    let directory = Bundle.main.bundleURL.deletingLastPathComponent()
    let window = NSWindow(
        contentRect: NSRect(x: 220, y: 300, width: 430, height: 220), styleMask: [.titled, .closable],
        backing: .buffered, defer: false)
    let other = NSWindow(
        contentRect: NSRect(x: 700, y: 300, width: 340, height: 180), styleMask: [.titled, .closable],
        backing: .buffered, defer: false)
    let editor = NSTextView(), guardField = NSTextField(string: "GUARD")
    var events = [[String: Any]](), commands = [String]()
    var page = 1
    var monitor: Any?

    func record() {
        let state: [String: Any] = [
            "text": editor.string, "guard": guardField.stringValue, "page": page, "commands": commands,
            "events": events, "focused": window.firstResponder === editor,
            "foreground": NSWorkspace.shared.frontmostApplication?.processIdentifier == getpid()
        ]
        try! JSONSerialization.data(withJSONObject: state, options: [.sortedKeys]).write(
            to: directory.appendingPathComponent("state.json"), options: .atomic)
    }
    func applicationDidFinishLaunching(_ notification: Notification) {
        window.title = "Shortcut target"
        other.title = "Unrelated shortcut document"
        for item in [window, other] {
            item.tabbingMode = .disallowed
            item.isReleasedWhenClosed = false
        }
        editor.frame = NSRect(x: 20, y: 30, width: 390, height: 150)
        editor.isRichText = false
        editor.isAutomaticQuoteSubstitutionEnabled = false
        editor.isAutomaticDashSubstitutionEnabled = false
        editor.setAccessibilityIdentifier("shortcut-input")
        editor.delegate = self
        window.contentView!.addSubview(editor)
        guardField.frame = NSRect(x: 20, y: 70, width: 280, height: 30)
        other.contentView!.addSubview(guardField)
        let root = NSMenu(), navigation = NSMenuItem(title: "Navigation", action: nil, keyEquivalent: "")
        let menu = NSMenu(title: "Navigation")
        let back = NSMenuItem(title: "Back", action: #selector(goBack), keyEquivalent: "[")
        let forward = NSMenuItem(title: "Forward", action: #selector(goForward), keyEquivalent: "]")
        for item in [back, forward] {
            item.target = self
            item.keyEquivalentModifierMask = .command
            menu.addItem(item)
        }
        navigation.submenu = menu
        root.addItem(navigation)
        NSApp.mainMenu = root
        monitor = NSEvent.addLocalMonitorForEvents(matching: [.keyDown, .keyUp, .flagsChanged]) { event in
            MainActor.assumeIsolated {
                self.events.append([
                    "type": event.type.rawValue, "code": event.keyCode,
                    "flags": event.modifierFlags.intersection(.deviceIndependentFlagsMask).rawValue,
                    "characters": event.characters ?? ""
                ])
                self.record()
            }
            return event
        }
        other.orderBack(nil)
        window.orderBack(nil)
        window.makeKey()
        window.makeFirstResponder(editor)
        record()
        Timer.scheduledTimer(withTimeInterval: 0.05, repeats: true) { _ in MainActor.assumeIsolated { self.record() } }
    }
    @objc func goBack() {
        page -= 1
        commands.append("back")
        record()
    }
    @objc func goForward() {
        page += 1
        commands.append("forward")
        record()
    }
    func textDidChange(_ notification: Notification) { record() }
}

let app = NSApplication.shared
app.setActivationPolicy(.regular)
let delegate = ShortcutProbe()
app.delegate = delegate
app.run()
