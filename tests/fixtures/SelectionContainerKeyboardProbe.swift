import AppKit

// A selectable item is not a keyboard responder. Its immediate AXMenu owns
// AXSelectedChildren and the real AppKit first responder. Focus deliberately
// clears selection, so select -> focus -> Return cannot activate the old item.
// No key is forwarded or synthesized by this fixture.
@MainActor final class ContainerApplication: NSApplication {
    override func accessibilityApplicationFocusedUIElement() -> Any? {
        guard let probe = delegate as? ContainerDelegate, let responder = probe.window?.firstResponder as? SelectionMenu
        else { return super.accessibilityApplicationFocusedUIElement() }
        return responder.reportsCellFocus ? responder.items[responder.activeItem] : responder
    }
    override func sendEvent(_ event: NSEvent) {
        if [.keyDown, .keyUp].contains(event.type) {
            (delegate as? ContainerDelegate)?.record(
                "application-key",
                extra: [
                    "keyCode": event.keyCode, "eventType": event.type.rawValue, "eventWindow": event.windowNumber,
                    "eventFlags": event.modifierFlags.rawValue
                ])
        }
        super.sendEvent(event)
    }
}

@MainActor final class ContainerWindow: NSWindow {
    weak var recorder: ContainerDelegate?
    override func setAccessibilityMain(_ value: Bool) {
        super.setAccessibilityMain(value)
        if value { makeKey() }
        recorder?.record("window-main", extra: ["requested": value])
    }
    override func makeFirstResponder(_ responder: NSResponder?) -> Bool {
        let accepted = super.makeFirstResponder(responder)
        recorder?.record("first-responder", extra: ["accepted": accepted])
        return accepted
    }
}

@MainActor final class SelectionMenuItem: NSView {
    weak var owner: SelectionMenu?
    let index: Int
    let generation: Int
    init(owner: SelectionMenu, index: Int, generation: Int) {
        self.owner = owner
        self.index = index
        self.generation = generation
        super.init(frame: .zero)
        setAccessibilityElement(true)
    }
    required init?(coder: NSCoder) { fatalError("init(coder:) not implemented") }
    var current: Bool { owner?.generation == generation }
    override func accessibilityRole() -> NSAccessibility.Role? {
        Bundle.main.object(forInfoDictionaryKey: "SelectionContainerKind") as? String == "cells"
            ? .textField : .menuItem
    }
    override func accessibilityLabel() -> String? { "\(owner?.name ?? "Retired") item \(index + 1)" }
    override func accessibilityIdentifier() -> String {
        "\(owner?.name ?? "retired")-item-\(index + 1)-generation-\(generation)"
    }
    override func accessibilityParent() -> Any? { current ? owner : nil }
    override func accessibilityWindow() -> Any? { current ? owner?.window : nil }
    override func accessibilityTopLevelUIElement() -> Any? { accessibilityWindow() }
    override func accessibilityChildren() -> [Any]? { [] }
    override func isAccessibilityEnabled() -> Bool { current }
    override func isAccessibilitySelected() -> Bool { current && owner?.selected == index }
    override func isAccessibilityFocused() -> Bool {
        // Deliberately misleading local flag: highlight is not first responder.
        isAccessibilitySelected()
    }
    override func setAccessibilityFocused(_ value: Bool) {
        guard value, current, let owner else { return }
        // A provider may accept AXFocused on an item and merely select it. The
        // menu remains the only real keyboard responder, just as before.
        owner.itemFocusSetters += 1
        owner.activeItem = index
        owner.setAccessibilitySelectedChildren([self])
        owner.recorder?.record("item-focus-only", extra: ["menu": owner.name, "item": index + 1])
    }
    override func accessibilityFrame() -> NSRect {
        guard current, let owner, let window = owner.window else { return .zero }
        return window.convertToScreen(owner.convert(owner.itemRect(index), to: nil))
    }
    override func isAccessibilitySelectorAllowed(_ selector: Selector) -> Bool {
        if [
            "setAccessibilitySelected:", "accessibilityPerformPress", "accessibilityPerformPick",
            "accessibilityPerformConfirm", "accessibilityPerformShowMenu"
        ].contains(NSStringFromSelector(selector)) {
            return false
        }
        return super.isAccessibilitySelectorAllowed(selector)
    }
}

@MainActor final class SelectionMenu: NSView {
    let name: String
    weak var recorder: ContainerDelegate?
    var generation = 0
    var selected: Int?
    var activeItem = 0
    var reportsCellFocus: Bool { Bundle.main.object(forInfoDictionaryKey: "SelectionFocusReport") as? String == "item" }
    var activated: [String] = []
    var focusSetters = 0
    var itemFocusSetters = 0
    var selectionSetters = 0
    var keyEvents = 0
    var didRebuild = false
    var items: [SelectionMenuItem] = []
    // Keep old objects alive to exercise stale identity rather than a crash.
    var retiredItems: [SelectionMenuItem] = []
    init(name: String, frame: NSRect) {
        self.name = name
        super.init(frame: frame)
        setAccessibilityElement(true)
        items = (0..<3).map { SelectionMenuItem(owner: self, index: $0, generation: generation) }
    }
    required init?(coder: NSCoder) { fatalError("init(coder:) not implemented") }
    override var acceptsFirstResponder: Bool { true }
    override func accessibilityRole() -> NSAccessibility.Role? {
        Bundle.main.object(forInfoDictionaryKey: "SelectionContainerKind") as? String == "cells" ? .table : .menu
    }
    override func accessibilityLabel() -> String? { "\(name) menu" }
    override func accessibilityIdentifier() -> String { "\(name)-selection-container" }
    override func accessibilityChildren() -> [Any]? { items }
    override func accessibilityVisibleChildren() -> [Any]? { items }
    override func accessibilitySelectedChildren() -> [Any]? { selected.map { [items[$0]] } ?? [] }
    override func accessibilitySelectedCells() -> [Any]? { accessibilitySelectedChildren() }
    override func setAccessibilitySelectedCells(_ children: [Any]?) { setAccessibilitySelectedChildren(children) }
    override func setAccessibilitySelectedChildren(_ children: [Any]?) {
        selectionSetters += 1
        let requested = (children ?? []).compactMap { $0 as? SelectionMenuItem }
        guard requested.count == (children ?? []).count, requested.count <= 1,
            requested.allSatisfy({ $0.owner === self && $0.current && items[$0.index] === $0 })
        else {
            recorder?.record("invalid-selection", extra: ["menu": name])
            return
        }
        selected = requested.first?.index
        needsDisplay = true
        recorder?.record("selection", extra: ["menu": name])
        if name == "Left", recorder?.mode == "redirect-on-selection", selected != nil, let other = recorder?.right {
            // Model a provider changing the input receiver during preparation.
            _ = window?.makeFirstResponder(other)
            recorder?.record("selection-redirect", extra: ["from": name, "to": other.name])
        }
        NSAccessibility.post(element: self, notification: .selectedChildrenChanged)
    }
    override func isAccessibilityFocused() -> Bool { window?.firstResponder === self }
    override func setAccessibilityFocused(_ value: Bool) {
        guard value else { return }
        focusSetters += 1
        if name == "Left", recorder?.mode == "refuse-focus" {
            recorder?.record("focus-refused", extra: ["menu": name])
            return
        }
        let accepted = window?.makeFirstResponder(self) ?? false
        if accepted {
            selected = nil
            if name == "Left", recorder?.mode == "rebuild-on-focus", !didRebuild {
                retiredItems.append(contentsOf: items)
                generation += 1
                didRebuild = true
                items = (0..<3).map { SelectionMenuItem(owner: self, index: $0, generation: generation) }
                recorder?.record("items-rebuilt", extra: ["menu": name])
                NSAccessibility.post(element: self, notification: .layoutChanged)
            }
            needsDisplay = true
        }
        recorder?.record("focus", extra: ["menu": name, "accepted": accepted])
        NSAccessibility.post(element: self, notification: .focusedUIElementChanged)
    }
    override func isAccessibilitySelectorAllowed(_ selector: Selector) -> Bool {
        // There is no AXPress/Pick/Confirm shortcut which would hide the
        // keyboard problem; only this container can become first responder.
        if [
            "accessibilityPerformPress", "accessibilityPerformPick", "accessibilityPerformConfirm",
            "accessibilityPerformShowMenu"
        ].contains(NSStringFromSelector(selector)) {
            return false
        }
        return super.isAccessibilitySelectorAllowed(selector)
    }
    override func keyDown(with event: NSEvent) {
        keyEvents += 1
        recorder?.record(
            "menu-key",
            extra: [
                "menu": name, "keyCode": event.keyCode, "eventWindow": event.windowNumber,
                "eventFlags": event.modifierFlags.rawValue
            ])
        switch event.keyCode {
        case 36, 76:  // Return / keypad Enter: activate only this container's selection.
            if let selected {
                activated.append("\(name) item \(selected + 1)")
                recorder?.record("activated", extra: ["menu": name, "item": selected + 1])
            } else {
                recorder?.record("return-without-selection", extra: ["menu": name])
            }
        case 125:  // Down: prove Element.pressKey must preserve the requested key.
            selected = min((selected ?? -1) + 1, items.count - 1)
            recorder?.record("navigated", extra: ["menu": name, "key": "Down"])
        case 126:
            selected = max((selected ?? 1) - 1, 0)
            recorder?.record("navigated", extra: ["menu": name, "key": "Up"])
        default:
            // No text editing or forwarding to the other menu.
            recorder?.record("unhandled-key", extra: ["menu": name, "keyCode": event.keyCode])
        }
        needsDisplay = true
        if let selected { activeItem = selected }
    }
    func itemRect(_ index: Int) -> NSRect {
        NSRect(x: 12, y: bounds.height - 86 - CGFloat(index) * 40, width: bounds.width - 24, height: 32)
    }
    override func draw(_ dirtyRect: NSRect) {
        NSColor.controlBackgroundColor.setFill()
        bounds.fill()
        ("\(name) menu" as NSString).draw(
            at: NSPoint(x: 12, y: bounds.height - 32),
            withAttributes: [.font: NSFont.boldSystemFont(ofSize: 16), .foregroundColor: NSColor.labelColor])
        for item in items {
            let rect = itemRect(item.index)
            if selected == item.index {
                NSColor.selectedContentBackgroundColor.setFill()
                rect.fill()
            }
            (item.accessibilityLabel()! as NSString).draw(
                at: NSPoint(x: rect.minX + 6, y: rect.minY + 8), withAttributes: [.foregroundColor: NSColor.labelColor])
        }
        ("Activated: \(activated.last ?? "none")" as NSString).draw(
            at: NSPoint(x: 12, y: 14), withAttributes: [.foregroundColor: NSColor.labelColor])
    }
    var state: [String: Any] {
        [
            "generation": generation, "selected": selected.map { $0 + 1 } as Any? ?? NSNull(), "activated": activated,
            "focusSetters": focusSetters, "selectionSetters": selectionSetters, "itemFocusSetters": itemFocusSetters,
            "keyEvents": keyEvents, "actualFirstResponder": isAccessibilityFocused(),
            "locallyFocusedItems": items.filter { $0.isAccessibilityFocused() }.map { $0.index + 1 }
        ]
    }
}

@MainActor final class ContainerDelegate: NSObject, NSApplicationDelegate {
    let mode = Bundle.main.object(forInfoDictionaryKey: "SelectionContainerCase") as? String ?? "normal"
    let directory = Bundle.main.bundleURL.deletingLastPathComponent()
    let left = SelectionMenu(name: "Left", frame: NSRect(x: 20, y: 20, width: 260, height: 220))
    let right = SelectionMenu(name: "Right", frame: NSRect(x: 300, y: 20, width: 260, height: 220))
    var window: ContainerWindow!
    var recording = false
    var focus: String {
        if window?.firstResponder === left { return "Left" }
        if window?.firstResponder === right { return "Right" }
        return window?.firstResponder.map { String(describing: type(of: $0)) } ?? "none"
    }
    func record(_ kind: String, extra: [String: Any] = [:]) {
        guard recording else { return }
        var value: [String: Any] = [
            "kind": kind, "mode": mode, "timestampMs": Date().timeIntervalSince1970 * 1000, "hostPID": getpid(),
            "window": window.windowNumber, "focus": focus, "left": left.state, "right": right.state,
            "foregroundPID": NSWorkspace.shared.frontmostApplication?.processIdentifier ?? 0
        ]
        value.merge(extra) { _, new in new }
        let data = try! JSONSerialization.data(withJSONObject: value, options: [.sortedKeys])
        try! data.write(to: directory.appendingPathComponent("state.json"), options: .atomic)
        let url = directory.appendingPathComponent("events.jsonl")
        if !FileManager.default.fileExists(atPath: url.path) {
            _ = FileManager.default.createFile(atPath: url.path, contents: nil)
        }
        let handle = try! FileHandle(forWritingTo: url)
        try! handle.seekToEnd()
        try! handle.write(contentsOf: data + Data([10]))
        try! handle.close()
    }
    func applicationDidFinishLaunching(_ notification: Notification) {
        precondition(["normal", "refuse-focus", "rebuild-on-focus", "redirect-on-selection"].contains(mode))
        window = ContainerWindow(
            contentRect: NSRect(x: 180, y: 180, width: 580, height: 260), styleMask: [.titled, .closable],
            backing: .buffered, defer: false)
        window.title = "Selection container keyboard — \(mode)"
        window.isReleasedWhenClosed = false
        window.tabbingMode = .disallowed
        window.recorder = self
        left.recorder = self
        right.recorder = self
        window.contentView?.addSubview(left)
        window.contentView?.addSubview(right)
        window.orderBack(nil)
        window.makeKey()
        window.makeMain()
        _ = window.makeFirstResponder(right)
        try! String(getpid()).write(
            to: directory.appendingPathComponent("fixture.pid"), atomically: true, encoding: .utf8)
        recording = true
        record("ready")
        Timer.scheduledTimer(withTimeInterval: 180, repeats: false) { _ in
            MainActor.assumeIsolated { NSApp.terminate(nil) }
        }
    }
    func applicationShouldTerminateAfterLastWindowClosed(_ sender: NSApplication) -> Bool { true }
}

let app = ContainerApplication.shared
let delegate = ContainerDelegate()
app.setActivationPolicy(.accessory)
app.delegate = delegate
app.run()
