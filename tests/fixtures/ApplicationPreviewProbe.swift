import AppKit

/// Disposable normal AppKit windows. Commands affect only this fixture.
@MainActor final class PreviewProbe: NSObject, NSApplicationDelegate {
    private var main: NSWindow!
    private var second: NSWindow?
    private var dialog: NSOpenPanel?
    private var label: NSTextField!
    private var timer: Timer?
    private var last = "", tick = 0
    private var directory: URL { Bundle.main.bundleURL.deletingLastPathComponent() }
    private var other: Bool { Bundle.main.bundleURL.lastPathComponent.hasPrefix("Other") }
    private var prefix: String { other ? "other" : "target" }
    private func window(_ title: String, color: NSColor, rect: NSRect) -> NSWindow {
        let window = NSWindow(contentRect: rect, styleMask: [.titled, .closable], backing: .buffered, defer: false)
        window.title = title
        window.isReleasedWhenClosed = false
        window.backgroundColor = color
        window.orderFrontRegardless()
        return window
    }
    func applicationDidFinishLaunching(_ notification: Notification) {
        main = window(
            other ? "Unrelated blue window" : "Shared red window", color: other ? .blue : .red,
            rect: NSRect(x: other ? 240 : 160, y: 250, width: 400, height: 280))
        label = NSTextField(labelWithString: "Live 0")
        label.font = .systemFont(ofSize: 26)
        label.textColor = .white
        label.frame = NSRect(x: 15, y: 35, width: 260, height: 40)
        main.contentView!.addSubview(label)
        timer = Timer.scheduledTimer(withTimeInterval: 0.15, repeats: true) { [weak self] _ in
            MainActor.assumeIsolated { self?.update() }
        }
        update()
    }
    private func update() {
        tick += 1
        label.stringValue = "Live \(tick)"
        let command =
            (try? String(contentsOf: directory.appendingPathComponent("\(prefix).command"), encoding: .utf8)) ?? ""
        if command != last {
            last = command
            switch command {
            case "second":
                second = window(
                    "Shared green window", color: .green, rect: NSRect(x: 620, y: 360, width: 320, height: 240))
            case "second-display":
                if NSScreen.screens.count > 1 {
                    let frame = NSScreen.screens[1].visibleFrame
                    second?.setFrameOrigin(NSPoint(x: frame.minX + 60, y: frame.minY + 100))
                }
            case "dialog":
                let panel = NSOpenPanel()
                dialog = panel
                panel.title = "Shared Open dialog"
                panel.message = "Application preview acceptance"
                panel.directoryURL = directory
                panel.beginSheetModal(for: main) { [weak self] _ in self?.dialog = nil }
            case "close-dialog":
                dialog?.cancel(nil)
                dialog = nil
            case "close-second":
                second?.close()
                second = nil
            case "quit": NSApp.terminate(nil)
            default: break
            }
        }
        let mouse = CGEvent(source: nil)!.location
        let state: [String: Any] = [
            "pid": ProcessInfo.processInfo.processIdentifier,
            "front": NSWorkspace.shared.frontmostApplication?.processIdentifier ?? 0, "mouse": [mouse.x, mouse.y],
            "tick": tick, "command": command, "second": second?.isVisible == true, "dialog": dialog?.isVisible == true,
            "displays": NSScreen.screens.count
        ]
        if let data = try? JSONSerialization.data(withJSONObject: state) {
            try? data.write(to: directory.appendingPathComponent("\(prefix).json"), options: .atomic)
        }
    }
}
@main struct Main {
    @MainActor static func main() {
        let app = NSApplication.shared, delegate = PreviewProbe()
        app.setActivationPolicy(.accessory)
        app.delegate = delegate
        app.run()
    }
}
