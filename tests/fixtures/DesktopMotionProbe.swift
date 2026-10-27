import AppKit

@MainActor final class Delegate: NSObject, NSApplicationDelegate {
    var window: NSWindow!
    var ticks = 0
    let directory = URL(fileURLWithPath: CommandLine.arguments[1])
    func applicationDidFinishLaunching(_ notification: Notification) {
        window = NSWindow(
            contentRect: NSRect(x: 25, y: 35, width: 220, height: 80), styleMask: [.titled], backing: .buffered,
            defer: false)
        window.title = "Desktop observation motion probe"
        window.contentView = NSTextField(labelWithString: "Moving observation fixture")
        window.orderBack(nil)
        try! String(ProcessInfo.processInfo.processIdentifier).write(
            to: directory.appendingPathComponent("ready"), atomically: true, encoding: .utf8)
        Timer.scheduledTimer(withTimeInterval: 0.02, repeats: true) { _ in
            MainActor.assumeIsolated {
                if FileManager.default.fileExists(atPath: self.directory.appendingPathComponent("pause").path) {
                    return
                }
                self.ticks += 1
                self.window.setFrameOrigin(NSPoint(x: 25 + Double(self.ticks) * 0.2, y: 35))
            }
        }
        Timer.scheduledTimer(withTimeInterval: 60, repeats: false) { _ in
            MainActor.assumeIsolated { NSApp.terminate(nil) }
        }
    }
}

let app = NSApplication.shared
app.setActivationPolicy(.accessory)
let delegate = Delegate()
app.delegate = delegate
app.run()
