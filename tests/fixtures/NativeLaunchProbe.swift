import AppKit

@MainActor final class Delegate: NSObject, NSApplicationDelegate {
    let directory = Bundle.main.bundleURL.deletingLastPathComponent()
    let started = UUID().uuidString
    var window: NSWindow!
    var reopens = 0
    var timer: Timer?

    func record() {
        let value: [String: Any] = [
            "pid": ProcessInfo.processInfo.processIdentifier, "started": started, "reopens": reopens,
            "title": window?.title ?? "", "visible": window?.isVisible ?? false
        ]
        try! JSONSerialization.data(withJSONObject: value, options: [.sortedKeys]).write(
            to: directory.appendingPathComponent("state.json"), options: [.atomic])
    }
    func applicationDidFinishLaunching(_ notification: Notification) {
        if Bundle.main.object(forInfoDictionaryKey: "V5DeferredWindow") as? Bool == true {
            record()
        } else {
            createWindow()
        }
        timer = Timer.scheduledTimer(withTimeInterval: 0.1, repeats: true) { [weak self] _ in
            MainActor.assumeIsolated {
                if let self {
                    if FileManager.default.fileExists(atPath: self.directory.appendingPathComponent("quit").path) {
                        NSApp.terminate(nil)
                    } else if self.window == nil
                        && FileManager.default.fileExists(
                            atPath: self.directory.appendingPathComponent("show-window").path)
                    {
                        self.createWindow()
                    }
                }
            }
        }
    }
    func createWindow() {
        window = NSWindow(
            contentRect: NSRect(x: 100, y: 100, width: 320, height: 120), styleMask: [.titled, .closable],
            backing: .buffered, defer: false)
        window.title = "V5 launch verification"
        let label = NSTextField(labelWithString: "Native launch ready")
        label.frame = NSRect(x: 30, y: 45, width: 260, height: 25)
        window.contentView!.addSubview(label)
        window.orderFront(nil)
        record()
    }
    func applicationShouldHandleReopen(_ sender: NSApplication, hasVisibleWindows flag: Bool) -> Bool {
        reopens += 1
        record()
        return false
    }
}

let app = NSApplication.shared
let delegate = Delegate()
app.delegate = delegate
app.setActivationPolicy(.regular)
app.run()
