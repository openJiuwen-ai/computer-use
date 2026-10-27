import AppKit

// An application can activate itself despite NSWorkspace's activates=false.
// Ordinary AppKit calls reproduce startup behavior without an AX override.
@MainActor final class LaunchActivationDelegate: NSObject, NSApplicationDelegate {
    var window: NSWindow!
    func applicationDidFinishLaunching(_ notification: Notification) {
        let root = Bundle.main.bundleURL.deletingLastPathComponent()
        try! String(getpid()).write(to: root.appendingPathComponent("fixture.pid"), atomically: true, encoding: .utf8)
        window = NSWindow(
            contentRect: NSRect(x: 160, y: 170, width: 400, height: 180), styleMask: [.titled, .closable],
            backing: .buffered, defer: false)
        window.title = "Application launch acceptance"
        let label = NSTextField(labelWithString: "Started without a model input action")
        label.frame = NSRect(x: 20, y: 70, width: 360, height: 28)
        window.contentView!.addSubview(label)
        window.orderBack(nil)
        window.makeKey()
        window.makeMain()
        // Request activation after NSWorkspace finishes its inactive launch.
        Timer.scheduledTimer(withTimeInterval: 0.25, repeats: false) { _ in
            MainActor.assumeIsolated {
                self.window.makeKeyAndOrderFront(nil)
                // Model applications that still request this legacy option.
                // The fixture records whether macOS actually accepted it;
                // product restoration does not depend on this launch strategy.
                let accepted = NSRunningApplication.current.activate(options: [.activateIgnoringOtherApps])
                try! JSONSerialization.data(withJSONObject: [
                    "activationAccepted": accepted,
                    "foreground": NSWorkspace.shared.frontmostApplication?.processIdentifier ?? 0
                ]).write(to: root.appendingPathComponent("activation.json"))
            }
        }
    }
}
let app = NSApplication.shared
app.setActivationPolicy(.regular)
let delegate = LaunchActivationDelegate()
app.delegate = delegate
app.run()
