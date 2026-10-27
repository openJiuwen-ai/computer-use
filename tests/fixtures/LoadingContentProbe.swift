import AppKit

/// A real AppKit provider that exposes delayed content through AXElementBusy.
/// A file owned by the harness selects the scenario; no input is injected.
@MainActor final class LoadingRegion: NSView {
    let output = Bundle.main.bundleURL.deletingLastPathComponent()
    var mode = "ready"
    var reads = 0

    override func accessibilityAttributeNames() -> [NSAccessibility.Attribute] {
        // AppKit has no typed NSAccessibility property for AXElementBusy.
        Array(Set(super.accessibilityAttributeNames() + [NSAccessibility.Attribute(rawValue: "AXElementBusy")]))
    }

    override func accessibilityAttributeValue(_ attribute: NSAccessibility.Attribute) -> Any? {
        guard attribute.rawValue == "AXElementBusy" else { return super.accessibilityAttributeValue(attribute) }
        // The legacy callback is not annotated with AppKit's main-actor
        // contract. Its AX requests are delivered on this app's main run loop.
        return MainActor.assumeIsolated { readLoadingState() }
    }

    private func readLoadingState() -> Bool {
        let requested = (try? String(contentsOf: output.appendingPathComponent("mode"), encoding: .utf8)) ?? "ready"
        if requested != mode {
            mode = requested
            reads = 0
            subviews.forEach { $0.removeFromSuperview() }
        }
        reads += 1
        let busy = mode == "persistent" || mode == "transient" && reads == 1
        if !busy && mode == "transient" && subviews.isEmpty {
            let text = NSTextField(labelWithString: "Content loaded through native accessibility")
            text.frame = NSRect(x: 12, y: 25, width: 430, height: 30)
            text.setAccessibilityIdentifier("loaded-content")
            addSubview(text)
        }
        let value: [String: Any] = [
            "mode": mode, "reads": reads, "busy": busy,
            "content": subviews.compactMap { ($0 as? NSTextField)?.stringValue }
        ]
        try! JSONSerialization.data(withJSONObject: value).write(
            to: output.appendingPathComponent("state.json"), options: .atomic)
        return busy
    }

    override func accessibilityChildren() -> [Any]? { subviews }
}

@MainActor final class Delegate: NSObject, NSApplicationDelegate {
    var window: NSWindow!
    func applicationDidFinishLaunching(_ notification: Notification) {
        window = NSWindow(
            contentRect: NSRect(x: 180, y: 220, width: 520, height: 230), styleMask: [.titled, .closable],
            backing: .buffered, defer: false)
        window.title = "Loading Content Probe"
        let independent = NSTextField(labelWithString: "Independent region remains available")
        independent.frame = NSRect(x: 20, y: 170, width: 480, height: 30)
        independent.setAccessibilityIdentifier("independent-content")
        window.contentView!.addSubview(independent)
        let region = LoadingRegion(frame: NSRect(x: 20, y: 35, width: 480, height: 100))
        region.setAccessibilityElement(true)
        region.setAccessibilityRole(.group)
        region.setAccessibilityLabel("Loading region")
        region.setAccessibilityIdentifier("loading-region")
        window.contentView!.addSubview(region)
        window.orderBack(nil)
        try! Data("ready".utf8).write(
            to: Bundle.main.bundleURL.deletingLastPathComponent().appendingPathComponent("ready"))
        Timer.scheduledTimer(withTimeInterval: 120, repeats: false) { _ in
            MainActor.assumeIsolated { NSApp.terminate(nil) }
        }
    }
}

let app = NSApplication.shared
let delegate = Delegate()
app.delegate = delegate
app.setActivationPolicy(.regular)
app.run()
