import AppKit

@MainActor final class ContainmentApplication: NSApplication {
    override func accessibilityWindows() -> [Any]? { (delegate as? ContainmentDelegate)?.windows }
}

// A valid forward child whose accessibility bridge omits reverse links.
@MainActor final class ForwardOnlyField: NSTextField {
    override func accessibilityParent() -> Any? { nil }
    override func accessibilityWindow() -> Any? { nil }
    override func accessibilityTopLevelUIElement() -> Any? { nil }
}

@MainActor final class ContainmentDelegate: NSObject, NSApplicationDelegate {
    var windows = [NSWindow]()
    let field = ForwardOnlyField(string: "before")
    func applicationDidFinishLaunching(_ notification: Notification) {
        let evidence = Bundle.main.bundleURL.deletingLastPathComponent()
        try! String(getpid()).write(
            to: evidence.appendingPathComponent("fixture.pid"), atomically: true, encoding: .utf8)
        // More than the bounded global query can visit, without depending on
        // any business app or its particular widget names.
        for index in 0..<12 {
            let window = NSWindow(
                contentRect: NSRect(x: 0, y: 0, width: 440, height: 260), styleMask: [.titled, .closable],
                backing: .buffered, defer: false)
            window.title = index == 11 ? "Target document" : "Other document \(index)"
            window.tabbingMode = .disallowed
            window.isReleasedWhenClosed = false
            let body = NSView(frame: window.contentView!.bounds)
            body.setAccessibilityElement(true)
            body.setAccessibilityRole(.group)
            window.contentView!.addSubview(body)
            if index == 11 {
                var parent = body
                for _ in 0..<5 {
                    let nested = NSView(frame: parent.bounds)
                    nested.setAccessibilityElement(true)
                    nested.setAccessibilityRole(.group)
                    parent.addSubview(nested)
                    parent = nested
                }
                field.frame = NSRect(x: 20, y: 70, width: 360, height: 30)
                field.setAccessibilityIdentifier("forward-only-input")
                parent.addSubview(field)
                let button = NSButton(title: "Commit", target: self, action: #selector(commit))
                button.frame = NSRect(x: 20, y: 20, width: 100, height: 30)
                body.addSubview(button)
            } else {
                for row in 0..<60 {
                    let label = NSTextField(labelWithString: "Unrelated \(index)/\(row)")
                    label.frame = NSRect(x: 5, y: Double(row % 10) * 20, width: 300, height: 20)
                    body.addSubview(label)
                }
            }
            window.center()
            window.orderBack(nil)
            windows.append(window)
        }
        let target = windows.last!
        target.makeKey()
        target.makeMain()
        target.makeFirstResponder(field)
        try! JSONSerialization.data(withJSONObject: [
            "windows": windows.map { ["title": $0.title, "visible": $0.isVisible] },
            "nativeWindowCount": NSApp.windows.count
        ]).write(to: evidence.appendingPathComponent("native-state.json"))
    }
    @objc func commit() {
        try! field.stringValue.write(
            to: Bundle.main.bundleURL.deletingLastPathComponent().appendingPathComponent("submission.txt"),
            atomically: true, encoding: .utf8)
    }
}
let application = ContainmentApplication.shared
application.setActivationPolicy(.regular)
let delegate = ContainmentDelegate()
application.delegate = delegate
application.run()
