import AppKit

@MainActor final class ProbeApplication: NSApplication {
    var unavailableHits = 0
    override func accessibilityHitTest(_ point: NSPoint) -> Any? {
        let unavailable = MainActor.assumeIsolated {
            guard Bundle.main.object(forInfoDictionaryKey: "ProbeNoHitTest") as? Bool == true else { return false }
            unavailableHits += 1
            (delegate as? Delegate)?.record()
            return true
        }
        if unavailable { return nil }
        return super.accessibilityHitTest(point)
    }
}

@MainActor final class Choice: NSView {
    let name: String
    let kind: NSAccessibility.Role
    var selected = false
    var presses = 0
    var selections = 0
    var clicks = 0
    weak var owner: ChoiceGroup?
    init(_ name: String, _ role: NSAccessibility.Role, _ frame: NSRect) {
        self.name = name
        kind = role
        super.init(frame: frame)
        setAccessibilityElement(true)
    }
    required init?(coder: NSCoder) { fatalError() }
    override func accessibilityRole() -> NSAccessibility.Role? { kind }
    override func accessibilityLabel() -> String? { name }
    override func isAccessibilitySelected() -> Bool { selected }
    override func setAccessibilitySelected(_ value: Bool) {
        selections += 1
        selected = value
        owner?.changed?()
    }
    override func accessibilityPerformPress() -> Bool {
        guard name == "Pressable row" else { return false }
        presses += 1
        owner?.changed?()
        return true
    }
    override func isAccessibilitySelectorAllowed(_ selector: Selector) -> Bool {
        if selector == NSSelectorFromString("accessibilityPerformPress") { return name == "Pressable row" }
        return super.isAccessibilitySelectorAllowed(selector)
    }
    override func mouseDown(with event: NSEvent) {
        clicks += 1
        owner?.marked = false
        selected = true
        window?.makeFirstResponder(owner)
        owner?.changed?()
    }
    override func draw(_ dirtyRect: NSRect) {
        NSColor.controlBackgroundColor.setFill()
        bounds.fill()
        (name as NSString).draw(at: NSPoint(x: 5, y: 5), withAttributes: [.foregroundColor: NSColor.labelColor])
    }
}
@MainActor final class ChoiceGroup: NSView {
    let kind: NSAccessibility.Role
    var marked = false
    var advanced = 0
    var changed: (() -> Void)?
    init(_ role: NSAccessibility.Role, _ frame: NSRect) {
        kind = role
        super.init(frame: frame)
        setAccessibilityElement(true)
    }
    required init?(coder: NSCoder) { fatalError() }
    override var acceptsFirstResponder: Bool { true }
    override func accessibilityRole() -> NSAccessibility.Role? { kind }
    override func accessibilityChildren() -> [Any]? { subviews }
    override func accessibilityVisibleChildren() -> [Any]? { subviews }
    override func isAccessibilityExpanded() -> Bool { true }
    override func accessibilitySelectedChildren() -> [Any]? { subviews.compactMap { $0 as? Choice }.filter(\.selected) }
    override func setAccessibilitySelectedChildren(_ children: [Any]?) {
        marked = true
        for case let choice as Choice in subviews {
            choice.selected = children?.contains { ($0 as? Choice) === choice } == true
        }
        changed?()
    }
    override func isAccessibilityFocused() -> Bool { window?.firstResponder === self }
    override func setAccessibilityFocused(_ value: Bool) { if value { window?.makeFirstResponder(self) } }
    override func keyDown(with event: NSEvent) {
        if event.keyCode == 48 && !marked { advanced += 1 }
        changed?()
    }
}
@MainActor final class Delegate: NSObject, NSApplicationDelegate {
    var window: NSWindow!
    var overlay: NSPanel?
    var groups = [ChoiceGroup]()
    var choices = [Choice]()
    let directory = Bundle.main.bundleURL.deletingLastPathComponent()
    func record() {
        guard !groups.isEmpty else { return }
        let data: [String: Any] = [
            "choices": choices.map {
                ["name": $0.name, "presses": $0.presses, "selections": $0.selections, "clicks": $0.clicks]
            }, "advanced": groups[0].advanced, "marked": groups[0].marked,
            "unavailableHits": (NSApp as? ProbeApplication)?.unavailableHits ?? 0
        ]
        try! JSONSerialization.data(withJSONObject: data).write(to: directory.appendingPathComponent("state.json"))
    }
    func applicationDidFinishLaunching(_ notification: Notification) {
        try! String(getpid()).write(
            to: directory.appendingPathComponent("fixture.pid"), atomically: true, encoding: .utf8)
        window = NSWindow(
            contentRect: NSRect(x: 0, y: 0, width: 450, height: 300), styleMask: [.titled, .closable],
            backing: .buffered, defer: false)
        window.title = "Selection and click"
        window.tabbingMode = .disallowed
        window.isReleasedWhenClosed = false
        let specifications: [(String, NSAccessibility.Role, NSAccessibility.Role)] = [
            ("Selectable cell", .cell, .table), ("Persistent menu choice", .menuItem, .menu),
            ("Pressable row", .row, .list)
        ]
        for (index, spec) in specifications.enumerated() {
            let group = ChoiceGroup(spec.2, NSRect(x: 30, y: 220 - index * 80, width: 390, height: 50))
            let choice = Choice(spec.0, spec.1, group.bounds)
            choice.owner = group
            group.addSubview(choice)
            group.changed = { [weak self] in self?.record() }
            groups.append(group)
            choices.append(choice)
            window.contentView?.addSubview(group)
        }
        window.center()
        window.orderBack(nil)
        window.makeKey()
        window.makeMain()
        let show = NSButton(title: "Show overlay", target: self, action: #selector(showOverlay))
        show.frame = NSRect(x: 30, y: 5, width: 150, height: 30)
        window.contentView?.addSubview(show)
        record()
    }
    @objc func showOverlay() {
        let frame = window.convertToScreen(choices[0].convert(choices[0].bounds, to: nil))
        let panel = NSPanel(
            contentRect: frame, styleMask: [.borderless, .nonactivatingPanel], backing: .buffered, defer: false)
        panel.title = "Input overlay"
        panel.level = .statusBar
        panel.hidesOnDeactivate = false
        panel.ignoresMouseEvents = Bundle.main.object(forInfoDictionaryKey: "ProbeOverlay") as? String == "passthrough"
        panel.backgroundColor = NSColor.systemRed.withAlphaComponent(0.3)
        panel.isOpaque = false
        panel.orderFrontRegardless()
        overlay = panel
    }
}
let application = ProbeApplication.shared
application.setActivationPolicy(.regular)
let delegate = Delegate()
application.delegate = delegate
application.run()
