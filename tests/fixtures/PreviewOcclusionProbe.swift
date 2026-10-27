import AppKit

// A normal target deliberately underneath the driver's floating preview.
@MainActor final class Probe: NSObject, NSApplicationDelegate {
    private var window: NSWindow!
    private var button: NSButton!
    private var timer: Timer?
    private var clicks = 0
    private let directory = URL(fileURLWithPath: CommandLine.arguments[0]).deletingLastPathComponent()
        .deletingLastPathComponent().deletingLastPathComponent().deletingLastPathComponent()

    func applicationDidFinishLaunching(_ notification: Notification) {
        let frame = NSScreen.screens[0].visibleFrame.insetBy(dx: 20, dy: 20)
        window = NSWindow(contentRect: frame, styleMask: [.titled, .closable], backing: .buffered, defer: false)
        window.title = "Preview occlusion target"
        window.setFrame(frame, display: false)
        button = NSButton(title: "Receive one click", target: self, action: #selector(clicked))
        button.frame = NSRect(x: window.contentView!.bounds.width - 170, y: 40, width: 150, height: 40)
        window.contentView!.addSubview(button)
        window.makeKeyAndOrderFront(nil)
        NSApp.activate(ignoringOtherApps: true)
        record()
        timer = Timer.scheduledTimer(withTimeInterval: 0.05, repeats: true) { [weak self] _ in
            MainActor.assumeIsolated { self?.record() }
        }
    }

    @objc private func clicked() {
        clicks += 1
        record()
    }

    private func record() {
        let local = button.convert(NSPoint(x: button.bounds.midX, y: button.bounds.midY), to: nil)
        let cocoa = window.convertPoint(toScreen: local)
        let point = CGPoint(x: cocoa.x, y: NSScreen.screens[0].frame.maxY - cocoa.y)
        let windows =
            CGWindowListCopyWindowInfo([.optionOnScreenOnly, .excludeDesktopElements], kCGNullWindowID)
            as? [[String: Any]] ?? []
        // Other processes' window titles need Screen Recording permission. The
        // fixture only needs public PID/layer/geometry, not another permission.
        let drivers = Set(
            NSRunningApplication.runningApplications(withBundleIdentifier: "local.computer-use.driver").map(
                \.processIdentifier))
        let previews = windows.filter { item in
            guard let pid = (item[kCGWindowOwnerPID as String] as? NSNumber)?.int32Value else { return false }
            return drivers.contains(pid)
                && (item[kCGWindowLayer as String] as? NSNumber)?.intValue == NSWindow.Level.floating.rawValue
        }
        let covers = previews.contains { item in
            guard let raw = item[kCGWindowBounds as String] as? [String: Any],
                let rect = CGRect(dictionaryRepresentation: raw as CFDictionary)
            else { return false }
            return rect.contains(point)
        }
        let state: [String: Any] = [
            "pid": ProcessInfo.processInfo.processIdentifier, "clicks": clicks, "x": cocoa.x - window.frame.minX,
            "y": window.frame.maxY - cocoa.y, "width": window.frame.width, "height": window.frame.height,
            "previewVisible": !previews.isEmpty, "previewCoversTarget": covers,
            "previewPIDs": previews.compactMap { ($0[kCGWindowOwnerPID as String] as? NSNumber)?.int32Value }
        ]
        if let data = try? JSONSerialization.data(withJSONObject: state) {
            try? data.write(to: directory.appendingPathComponent("state.json"), options: .atomic)
        }
    }
}

@main struct Main {
    @MainActor static func main() {
        let app = NSApplication.shared, delegate = Probe()
        app.setActivationPolicy(.regular)
        app.delegate = delegate
        app.run()
    }
}
