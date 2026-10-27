import AppKit
import ApplicationServices

private struct AXFixtureValue: @unchecked Sendable {
    let value: Any?
    init(_ value: Any?) { self.value = value }
}

// Real AX objects whose writable text attributes are inherited from one
// editor. This must not be mistaken for two independently editable controls.
@MainActor final class MarkerPart: NSView {
    weak var editor: MarkerEditor?
    let partRole: NSAccessibility.Role
    init(_ role: NSAccessibility.Role) {
        partRole = role
        super.init(frame: .zero)
    }
    required init?(coder: NSCoder) { fatalError("unused") }
    override func isAccessibilityElement() -> Bool { true }
    override func accessibilityRole() -> NSAccessibility.Role? { partRole }
    override func accessibilityValue() -> Any? { editor?.string }
    override func accessibilityAttributeNames() -> [NSAccessibility.Attribute] {
        super.accessibilityAttributeNames()
            + ["AXEditableAncestor", "AXSelectedText"].map(NSAccessibility.Attribute.init(rawValue:))
    }
    override func accessibilityAttributeValue(_ attribute: NSAccessibility.Attribute) -> Any? {
        switch attribute.rawValue {
        case "AXEditableAncestor":
            return MainActor.assumeIsolated {
                AXFixtureValue(editor?.host?.mode == "conflict" ? editor?.host?.other : editor)
            }.value
        case "AXSelectedText": return MainActor.assumeIsolated { AXFixtureValue(editor?.string) }.value
        default: return super.accessibilityAttributeValue(attribute)
        }
    }
    override func accessibilityIsAttributeSettable(_ attribute: NSAccessibility.Attribute) -> Bool {
        if ["AXValue", "AXSelectedText"].contains(attribute.rawValue) { return true }
        return super.accessibilityIsAttributeSettable(attribute)
    }
}

// A provider whose AX string includes generated text outside the editor's
// actual selection extent. All text/selection changes still use AppKit's real
// responder chain. The state file is an independent, read-only test oracle.
@MainActor final class MarkerEditor: NSTextView {
    weak var host: MarkerSelectionDelegate?
    var markers = [AXTextMarker]()
    let group = MarkerPart(.group), leaf = MarkerPart(.staticText)
    func marker(_ index: Int) -> AXTextMarker {
        var value = index
        let result = withUnsafeBytes(of: &value) { AXTextMarkerCreate(nil, $0.baseAddress!, $0.count) }
        markers.append(result)
        return result
    }
    func markerRange(_ range: NSRange) -> AXTextMarkerRange {
        AXTextMarkerRangeCreate(nil, marker(range.location), marker(NSMaxRange(range)))
    }
    func ownerForMarker(_ parameter: Any?) -> Any? {
        guard let parameter, CFGetTypeID(parameter as CFTypeRef) == AXTextMarkerGetTypeID(),
            markers.contains(where: { CFEqual($0, parameter as CFTypeRef) })
        else { return nil }
        if host?.mode == "handoff", host?.selectAlls ?? 0 > 0 {
            window?.makeFirstResponder(host?.other)
            host?.record()
        }
        return host?.mode == "foreign" ? host?.other : leaf
    }
    override func accessibilityNumberOfCharacters() -> Int { string.utf16.count + 2 }
    override func accessibilityValue() -> String? { string + "\u{200b}\n" }
    override func setAccessibilitySelectedTextRange(_ range: NSRange) {
        host?.rangeWrites += 1
        setSelectedRange(NSMaxRange(range) > string.utf16.count ? NSRange(location: 0, length: 0) : range)
        host?.record()
    }
    override func isAccessibilitySelectorAllowed(_ selector: Selector) -> Bool {
        if ["setAccessibilityValue:", "setAccessibilitySelectedText:"].contains(NSStringFromSelector(selector)) {
            return false
        }
        return super.isAccessibilitySelectorAllowed(selector)
    }
    override func accessibilityAttributeNames() -> [NSAccessibility.Attribute] {
        super.accessibilityAttributeNames()
            + ["AXEditable", "AXSelectedTextMarkerRange"].map(NSAccessibility.Attribute.init(rawValue:))
    }
    override func accessibilityAttributeValue(_ attribute: NSAccessibility.Attribute) -> Any? {
        switch attribute.rawValue {
        case "AXEditable": return true
        case "AXSelectedTextMarkerRange":
            return MainActor.assumeIsolated { AXFixtureValue(markerRange(selectedRange())) }.value
        default: return super.accessibilityAttributeValue(attribute)
        }
    }
    override func accessibilityParameterizedAttributeNames() -> [NSAccessibility.ParameterizedAttribute] {
        super.accessibilityParameterizedAttributeNames()
            + ["AXTextMarkerRangeForUIElement", "AXUIElementForTextMarker"].map(
                NSAccessibility.ParameterizedAttribute.init(rawValue:))
    }
    override func accessibilityAttributeValue(
        _ attribute: NSAccessibility.ParameterizedAttribute, forParameter parameter: Any?
    ) -> Any? {
        switch attribute.rawValue {
        case "AXTextMarkerRangeForUIElement":
            return MainActor.assumeIsolated {
                AXFixtureValue(markerRange(NSRange(location: 0, length: accessibilityNumberOfCharacters())))
            }.value
        case "AXUIElementForTextMarker":
            let boxed = AXFixtureValue(parameter)
            return MainActor.assumeIsolated { AXFixtureValue(ownerForMarker(boxed.value)) }.value
        default: return super.accessibilityAttributeValue(attribute, forParameter: parameter)
        }
    }
    override func selectAll(_ sender: Any?) {
        host?.selectAlls += 1
        if host?.mode == "ignore" {
            host?.record()
            return
        }
        if host?.mode == "partial" {
            setSelectedRange(NSRange(location: 0, length: min(1, string.utf16.count)))
        } else {
            super.selectAll(sender)
        }
        host?.record()
    }
    override func keyDown(with event: NSEvent) {
        super.keyDown(with: event)
        host?.record()
    }
}

@MainActor final class MarkerSelectionDelegate: NSObject, NSApplicationDelegate, NSTextViewDelegate {
    let directory = Bundle.main.bundleURL.deletingLastPathComponent()
    let editor = MarkerEditor(frame: NSRect(x: 20, y: 100, width: 440, height: 120))
    let other = NSTextView(frame: NSRect(x: 20, y: 20, width: 440, height: 60))
    var window: NSWindow!
    var rangeWrites = 0, selectAlls = 0, changes = 0
    var mode: String {
        (try? String(contentsOf: directory.appendingPathComponent("mode"), encoding: .utf8)) ?? "normal"
    }
    func record() {
        let value: [String: Any] = [
            "text": editor.string, "other": other.string, "mode": mode, "selectAlls": selectAlls,
            "rangeWrites": rangeWrites, "changes": changes, "targetResponder": window?.firstResponder === editor,
            "selection": [editor.selectedRange().location, editor.selectedRange().length]
        ]
        try? JSONSerialization.data(withJSONObject: value, options: [.sortedKeys]).write(
            to: directory.appendingPathComponent("state.json"), options: .atomic)
    }
    func textDidChange(_ notification: Notification) {
        changes += 1
        record()
    }
    func applicationDidFinishLaunching(_ notification: Notification) {
        try! String(getpid()).write(
            to: directory.appendingPathComponent("fixture.pid"), atomically: true, encoding: .utf8)
        window = NSWindow(
            contentRect: NSRect(x: 180, y: 150, width: 480, height: 240), styleMask: [.titled, .closable],
            backing: .buffered, defer: false)
        window.title = "Marker selection fixture"
        window.tabbingMode = .disallowed
        editor.host = self
        editor.delegate = self
        editor.isRichText = false
        editor.group.editor = editor
        editor.leaf.editor = editor
        editor.addSubview(editor.group)
        editor.group.addSubview(editor.leaf)
        editor.string = "SEED\u{200b}\nLAST"
        editor.setAccessibilityLabel("Marker rich input")
        editor.setSelectedRange(NSRange(location: editor.string.utf16.count, length: 0))
        other.string = "GUARD"
        other.isRichText = false
        other.setAccessibilityLabel("Independent editor")
        window.contentView!.addSubview(editor)
        window.contentView!.addSubview(other)
        let menu = NSMenu(), edit = NSMenuItem(title: "Edit", action: nil, keyEquivalent: "")
        edit.submenu = NSMenu(title: "Edit")
        edit.submenu!.addItem(withTitle: "Select All", action: #selector(NSText.selectAll(_:)), keyEquivalent: "a")
        menu.addItem(edit)
        NSApp.mainMenu = menu
        window.orderBack(nil)
        window.makeKey()
        window.makeMain()
        window.makeFirstResponder(other)
        record()
    }
}

let app = NSApplication.shared
app.setActivationPolicy(.regular)
let delegate = MarkerSelectionDelegate()
app.delegate = delegate
app.run()
