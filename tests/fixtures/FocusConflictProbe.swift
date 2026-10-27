import AppKit
import ApplicationServices

// Deliberately stale application AX focus, with real NSTextView input. The
// provider changes only its focus reports: it never redirects keyboard events.
@MainActor final class ProbeTextView: NSTextView {
    var name = ""
    var focusRequests = 0
    weak var probe: ProbeDelegate?
    override func isAccessibilityFocused() -> Bool { window?.firstResponder === self }
    override func setAccessibilityFocused(_ value: Bool) {
        guard value else { return }
        focusRequests += 1
        let accepted = window?.makeFirstResponder(self) ?? false
        probe?.record(event: ["event": "focus", "target": name, "accepted": accepted])
    }
    override func keyDown(with event: NSEvent) {
        probe?.keyReceivers.append(["receiver": name, "characters": event.characters ?? "", "keyCode": event.keyCode])
        super.keyDown(with: event)
        probe?.record(event: ["event": "key", "receiver": name])
    }
    override func insertTab(_ sender: Any?) {
        guard probe?.mode == "navigation" else {
            super.insertTab(sender)
            return
        }
        // A form's normal Tab command uses AppKit's real key-view loop. The
        // stale AX application report does not participate in this transition.
        window?.selectNextKeyView(sender)
        probe?.record(event: ["event": "tab-navigation", "from": name])
    }
}

@MainActor final class SharedOwner: NSView {
    weak var editor: ProbeTextView?
    var focusRequests = 0
    override func accessibilityRole() -> NSAccessibility.Role? { .comboBox }
    override func accessibilityChildren() -> [Any]? { editor.map { [$0] } ?? [] }
    override func accessibilityValue() -> Any? { editor?.string }
    override func isAccessibilityFocused() -> Bool { false }
    override func setAccessibilityFocused(_ value: Bool) {
        guard value else { return }
        focusRequests += 1
        editor?.setAccessibilityFocused(true)
    }
}

@MainActor final class SharedReporter: NSView {
    weak var editor: ProbeTextView?
    override func accessibilityRole() -> NSAccessibility.Role? { .list }
    override func accessibilitySharedFocusElements() -> [Any]? { editor.map { [$0] } ?? [] }
    override func isAccessibilityFocused() -> Bool { false }
}

@MainActor final class FocusApplication: NSApplication {
    override func accessibilityFocusedWindow() -> Any? { (delegate as? ProbeDelegate)?.window }
    override func accessibilityApplicationFocusedUIElement() -> Any? {
        guard let probe = delegate as? ProbeDelegate else { return super.accessibilityApplicationFocusedUIElement() }
        return probe.mode == "shared" ? probe.reporter : probe.a
    }
}

@MainActor final class ProbeDelegate: NSObject, NSApplicationDelegate, NSTextViewDelegate {
    let directory = Bundle.main.bundleURL.deletingLastPathComponent()
    let mode = Bundle.main.object(forInfoDictionaryKey: "ProbeFocusMode") as? String ?? "conflict"
    var window: NSWindow!
    let a = ProbeTextView(), b = ProbeTextView()
    let owner = SharedOwner(), reporter = SharedReporter()
    var keyReceivers = [[String: Any]]()
    func record(event: [String: Any]? = nil) {
        guard window != nil else { return }
        let actual =
            window.firstResponder === a
            ? "A" : window.firstResponder === b ? "B" : String(describing: window.firstResponder)
        let values: [String: Any] = [
            "mode": mode, "actualFirstResponder": actual, "a": a.string, "b": b.string,
            "aFocusRequests": a.focusRequests, "bFocusRequests": b.focusRequests,
            "ownerFocusRequests": owner.focusRequests, "keyWindow": window.isKeyWindow, "keyReceivers": keyReceivers
        ]
        try! JSONSerialization.data(withJSONObject: values, options: [.sortedKeys]).write(
            to: directory.appendingPathComponent("values.json"), options: .atomic)
        if let event {
            var row = event
            row["actualFirstResponder"] = actual
            row["time"] = Date().timeIntervalSince1970
            let data = try! JSONSerialization.data(withJSONObject: row, options: [.sortedKeys]) + Data([10])
            let url = directory.appendingPathComponent("events.jsonl")
            if let handle = try? FileHandle(forWritingTo: url) {
                _ = try? handle.seekToEnd()
                try? handle.write(contentsOf: data)
                try? handle.close()
            } else {
                try! data.write(to: url)
            }
        }
    }
    func textDidChange(_ notification: Notification) { record(event: ["event": "text-changed"]) }
    func applicationDidFinishLaunching(_ notification: Notification) {
        try! String(getpid()).write(
            to: directory.appendingPathComponent("fixture.pid"), atomically: true, encoding: .utf8)
        window = NSWindow(
            contentRect: NSRect(x: 100, y: 100, width: 520, height: 330), styleMask: [.titled, .closable],
            backing: .buffered, defer: false)
        window.title = "Focus conflict probe: \(mode)"
        window.tabbingMode = .disallowed
        window.isReleasedWhenClosed = false
        for (view, name, y) in [(a, "A", 180.0), (b, "B", 40.0)] {
            view.name = name
            view.probe = self
            view.delegate = self
            view.frame = NSRect(x: 20, y: y, width: 470, height: 110)
            view.isRichText = false
            view.font = NSFont.systemFont(ofSize: 17)
            view.string = "\(name):"
            view.setSelectedRange(NSRange(location: 2, length: 0))
            view.setAccessibilityIdentifier("input-" + name)
            view.setAccessibilityLabel("Input " + name)
            window.contentView?.addSubview(view)
        }
        a.nextKeyView = b
        b.nextKeyView = a
        if mode == "shared" || mode == "editor" {
            a.removeFromSuperview()
            owner.frame = a.frame
            a.frame.origin = .zero
            owner.setAccessibilityElement(true)
            owner.setAccessibilityIdentifier("shared-owner")
            owner.setAccessibilityLabel("Shared control A")
            owner.editor = a
            owner.addSubview(a)
            reporter.frame = NSRect(x: 20, y: 10, width: 470, height: 20)
            reporter.setAccessibilityElement(true)
            reporter.setAccessibilityIdentifier("shared-reporter")
            reporter.setAccessibilityLabel("Shared focus reporter")
            reporter.editor = a
            window.contentView?.addSubview(owner)
            window.contentView?.addSubview(reporter)
        }
        let menu = NSMenu(), item = NSMenuItem()
        item.submenu = NSMenu()
        item.submenu!.addItem(withTitle: "Quit", action: #selector(NSApplication.terminate(_:)), keyEquivalent: "q")
        menu.addItem(item)
        NSApp.mainMenu = menu
        window.orderBack(nil)
        window.makeKey()
        window.makeMain()
        window.makeFirstResponder(mode == "conflict" ? b : a)
        record(event: ["event": "ready"])
        Timer.scheduledTimer(withTimeInterval: 90, repeats: false) { _ in
            MainActor.assumeIsolated { NSApp.terminate(nil) }
        }
    }
}

// Separate process, read-only AX inspection. This proves what the OS exposes;
// an overridden Swift getter alone is not evidence that an attribute is absent.
if CommandLine.arguments.count == 3, CommandLine.arguments[1] == "--inspect", let pid = pid_t(CommandLine.arguments[2])
{
    func read(_ e: AXUIElement, _ key: String) -> (AXError, CFTypeRef?) {
        var value: CFTypeRef?
        let status = AXUIElementCopyAttributeValue(e, key as CFString, &value)
        return (status, value)
    }
    func identifier(_ e: AXUIElement) -> String { read(e, kAXIdentifierAttribute).1 as? String ?? "" }
    let root = AXUIElementCreateApplication(pid)
    var result = [[String: Any]](), visited = [AXUIElement]()
    func visit(_ e: AXUIElement) {
        guard visited.count < 120, !visited.contains(where: { CFEqual($0, e) }) else { return }
        visited.append(e)
        let id = identifier(e)
        if !id.isEmpty {
            let (status, focused) = read(e, kAXFocusedAttribute)
            var row: [String: Any] = [
                "id": id, "focusedStatus": status.rawValue, "role": read(e, kAXRoleAttribute).1 as? String ?? ""
            ]
            if let focused = focused as? NSNumber { row["focused"] = focused.boolValue }
            if let shared = read(e, kAXSharedFocusElementsAttribute).1 as? [AXUIElement] {
                row["sharedFocus"] = shared.map(identifier)
            }
            result.append(row)
        }
        for child in read(e, kAXChildrenAttribute).1 as? [AXUIElement] ?? [] { visit(child) }
    }
    visit(root)
    let (status, raw) = read(root, kAXFocusedUIElementAttribute)
    var focus = ""
    if let raw, CFGetTypeID(raw) == AXUIElementGetTypeID() {
        focus = identifier(unsafeDowncast(raw, to: AXUIElement.self))
    }
    print(
        String(
            data: try! JSONSerialization.data(
                withJSONObject: ["focusStatus": status.rawValue, "reportedFocus": focus, "elements": result],
                options: [.sortedKeys]), encoding: .utf8)!)
    exit(0)
}

let application = FocusApplication.shared
application.setActivationPolicy(.regular)
let delegate = ProbeDelegate()
application.delegate = delegate
application.run()
