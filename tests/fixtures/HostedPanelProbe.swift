import AppKit

// Plain NSOpenPanel allows AppKit to use its real view service. Subclassing it
// can force an in-process implementation and miss the presenter/service split.
@MainActor final class HostedPanelDelegate: NSObject, NSApplicationDelegate {
    let directory = Bundle.main.bundleURL.deletingLastPathComponent()
    var window: NSWindow!
    var panel: NSOpenPanel?
    var opens = 0
    var choice: NSPopUpButton?
    var accessoryText: NSTextField?
    var choices = 0
    func record(_ value: [String: Any]) {
        var value = value
        if let choice {
            value["choice"] = choice.titleOfSelectedItem
            value["choices"] = choices
        }
        if let accessoryText { value["text"] = accessoryText.stringValue }
        try! JSONSerialization.data(withJSONObject: value).write(
            to: directory.appendingPathComponent("state.json"), options: .atomic)
    }
    func applicationDidFinishLaunching(_ notification: Notification) {
        try! String(getpid()).write(
            to: directory.appendingPathComponent("fixture.pid"), atomically: true, encoding: .utf8)
        window = NSWindow(
            contentRect: NSRect(x: 140, y: 160, width: 440, height: 200), styleMask: [.titled, .closable],
            backing: .buffered, defer: false)
        window.title = "Hosted Panel Probe"
        window.isReleasedWhenClosed = false
        let button = NSButton(title: "Choose a file", target: self, action: #selector(open))
        button.frame = NSRect(x: 20, y: 60, width: 160, height: 30)
        button.setAccessibilityIdentifier("choose-file")
        window.contentView!.addSubview(button)
        window.orderBack(nil)
        window.makeKey()
        window.makeMain()
        record(["opens": opens])
        Timer.scheduledTimer(withTimeInterval: 120, repeats: false) { _ in
            MainActor.assumeIsolated { NSApp.terminate(nil) }
        }
    }
    @objc func open() {
        opens += 1
        record(["opens": opens])
        panel = NSOpenPanel()
        panel!.allowsMultipleSelection = false
        if FileManager.default.fileExists(atPath: directory.appendingPathComponent("accessory").path) {
            let view = NSView(frame: NSRect(x: 0, y: 0, width: 300, height: 90))
            accessoryText = NSTextField(frame: NSRect(x: 20, y: 10, width: 200, height: 25))
            accessoryText!.stringValue = "Before"
            accessoryText!.setAccessibilityIdentifier("accessory-text")
            view.addSubview(accessoryText!)
            choice = NSPopUpButton(frame: NSRect(x: 20, y: 50, width: 200, height: 30), pullsDown: false)
            choice!.addItems(withTitles: ["First format", "Second format"])
            choice!.setAccessibilityIdentifier("accessory-choice")
            choice!.target = self
            choice!.action = #selector(changedChoice)
            view.addSubview(choice!)
            panel!.accessoryView = view
            panel!.isAccessoryViewDisclosed = true
            record(["opens": opens])
        }
        panel!.beginSheetModal(for: window) { [weak self] result in
            guard let self else { return }
            self.record(["opens": self.opens, "accepted": result == .OK, "path": self.panel?.url?.path ?? ""])
        }
    }
    @objc func changedChoice() {
        choices += 1
        record(["opens": opens])
    }
}
let app = NSApplication.shared
app.setActivationPolicy(.regular)
let delegate = HostedPanelDelegate()
app.delegate = delegate
app.run()
