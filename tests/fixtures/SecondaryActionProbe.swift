import AppKit

@MainActor class ActionControl: NSView {
    weak var owner: Delegate?
    override func accessibilityPerformPress() -> Bool {
        owner?.counts["primary", default: 0] += 1
        owner?.publish()
        return true
    }
    @objc func pin() -> Bool {
        owner?.counts["pin", default: 0] += 1
        owner?.publish()
        return true
    }
    @objc func removeItem() -> Bool {
        owner?.counts["delete", default: 0] += 1
        removeFromSuperview()
        owner?.publish()
        return true
    }
    @objc func uncertain() -> Bool {
        owner?.counts["uncertain", default: 0] += 1
        owner?.publish()
        return false
    }
    @objc func alternate() -> Bool {
        owner?.counts["alternate", default: 0] += 1
        owner?.publish()
        return true
    }
    @objc func moveToAnotherWindow() -> Bool {
        owner?.move(self)
        return true
    }
    @objc func changeCapturedText() -> Bool {
        owner?.longText?.stringValue = "the live field changed"
        return true
    }
    override func draw(_ dirtyRect: NSRect) {
        (accessibilityLabel()! as NSString).draw(
            at: NSPoint(x: 8, y: 12), withAttributes: [.foregroundColor: NSColor.labelColor])
    }
}

/// Distinguish primary activation from a declared context-menu callback. The
/// fixture records native dispatch, not application-menu preparation or UI layout.
@MainActor final class MenuActionControl: ActionControl {
    override func accessibilityPerformShowMenu() -> Bool {
        owner?.counts["menu", default: 0] += 1
        owner?.publish()
        return true
    }
}

/// A visible composite input whose owner cannot take keyboard focus itself.
/// Its child is a normal AppKit editor; the driver must use native facts to
/// choose that child while retaining the composite as the requested target.
@MainActor final class CompositeEditor: NSView {
    override func accessibilityChildren() -> [Any]? { subviews }
    override func isAccessibilitySelectorAllowed(_ selector: Selector) -> Bool {
        if selector == #selector(setAccessibilityFocused(_:)) { return false }
        return super.isAccessibilitySelectorAllowed(selector)
    }
}

/// The real field editor publishes selection editing only after focus. Before
/// that this control offers focus, but no writable value or text declaration.
@MainActor final class DeferredSearchCell: NSSearchFieldCell {
    private var editorAvailable: Bool {
        (controlView as? NSTextField)?.currentEditor() != nil
            && Bundle.main.object(forInfoDictionaryKey: "V5UnavailableEditor") as? Bool != true
    }
    override func accessibilityIsAttributeSettable(_ attribute: NSAccessibility.Attribute) -> Bool {
        if attribute == .value { return false }
        if [.selectedText, .selectedTextRange].contains(attribute) { return editorAvailable }
        return super.accessibilityIsAttributeSettable(attribute)
    }
    override func isAccessibilitySelectorAllowed(_ selector: Selector) -> Bool {
        if selector == #selector(setAccessibilityValue(_:)) { return false }
        if selector == #selector(setAccessibilitySelectedText(_:))
            || selector == #selector(setAccessibilitySelectedTextRange(_:))
        {
            return editorAvailable
        }
        return super.isAccessibilitySelectorAllowed(selector)
    }
}

/// Exercise a receiver change after a successful native selection mutation,
/// before the driver can send replacement characters.
@MainActor final class ReplacementBody: NSTextView {
    weak var owner: Delegate?
    var selectedTextWrites = 0
    override func setAccessibilitySelectedText(_ text: String?) {
        selectedTextWrites += 1
        // A provider can advertise this setter and acknowledge the AX call
        // without implementing the application's ordinary editing behavior.
        if Bundle.main.object(forInfoDictionaryKey: "V5InertSelectedText") as? Bool == true { return }
        super.setAccessibilitySelectedText(text)
    }
    override func setAccessibilitySelectedTextRange(_ range: NSRange) {
        super.setAccessibilitySelectedTextRange(range)
        guard let owner, let other = owner.valueOther else { return }
        let trigger = owner.output.appendingPathComponent("steal-on-replacement-selection")
        if FileManager.default.fileExists(atPath: trigger.path) {
            try! FileManager.default.removeItem(at: trigger)
            owner.window.makeFirstResponder(other)
        }
    }
}

@MainActor final class Delegate: NSObject, NSApplicationDelegate, NSSearchFieldDelegate {
    var window: NSWindow!
    var status: NSTextField!
    var extraWindow: NSWindow?
    var longText: NSTextField?
    var valueTarget: NSTextField?
    var valueOther: NSTextField?
    var pathTarget: NSTextField?
    var pathOther: NSView?
    var textBody: NSTextView?
    var compositeOwner: CompositeEditor?
    var compositeField: NSTextField?
    var searchTarget: NSSearchField?
    var searchChanges = 0, searchReturns = 0
    var submitted = [String: [String]]()
    var counts = ["primary": 0, "pin": 0, "delete": 0, "uncertain": 0, "alternate": 0]
    let output = URL(fileURLWithPath: Bundle.main.bundlePath).deletingLastPathComponent()
    func publish() {
        status.stringValue = counts.keys.sorted().map { "\($0)=\(counts[$0]!)" }.joined(separator: ", ")
        try! JSONSerialization.data(withJSONObject: counts, options: [.sortedKeys]).write(
            to: output.appendingPathComponent("state.json"), options: .atomic)
    }
    func move(_ view: NSView) {
        let other = NSWindow(
            contentRect: NSRect(x: 720, y: 100, width: 580, height: 280), styleMask: [.titled, .closable],
            backing: .buffered, defer: false)
        other.title = "Another working window"
        view.removeFromSuperview()
        other.contentView!.addSubview(view)
        extraWindow = other
        other.orderBack(nil)
    }
    @objc func commitField(_ sender: NSTextField) {
        let id = sender.accessibilityIdentifier()
        submitted[id, default: []].append(sender.stringValue)
        try! JSONSerialization.data(withJSONObject: submitted, options: [.sortedKeys]).write(
            to: output.appendingPathComponent("submitted.json"), options: .atomic)
    }
    func controlTextDidChange(_ notification: Notification) {
        if (notification.object as? NSSearchField) === searchTarget { searchChanges += 1 }
    }
    func control(_ control: NSControl, textView: NSTextView, doCommandBy selector: Selector) -> Bool {
        if control === searchTarget, selector == #selector(NSResponder.insertNewline(_:)) { searchReturns += 1 }
        return false
    }
    func applicationDidFinishLaunching(_ notification: Notification) {
        window = NSWindow(
            contentRect: NSRect(x: 100, y: 100, width: 580, height: 280), styleMask: [.titled, .closable],
            backing: .buffered, defer: false)
        window.title = "Secondary Action Probe"
        status = NSTextField(labelWithString: "")
        status.frame = NSRect(x: 15, y: 15, width: 550, height: 40)
        window.contentView!.addSubview(status)
        func control(_ label: String, y: CGFloat, enabled: Bool = true) -> ActionControl {
            let view = ActionControl(frame: NSRect(x: 20, y: y, width: 530, height: 40))
            view.owner = self
            view.setAccessibilityElement(true)
            view.setAccessibilityRole(.button)
            view.setAccessibilityLabel(label)
            view.setAccessibilityIdentifier(label)
            view.setAccessibilityEnabled(enabled)
            window.contentView!.addSubview(view)
            return view
        }
        let item = control("Disposable item", y: 215)
        item.setAccessibilityCustomActions([
            NSAccessibilityCustomAction(name: "置顶", target: item, selector: #selector(ActionControl.pin)),
            NSAccessibilityCustomAction(name: "删除", target: item, selector: #selector(ActionControl.removeItem)),
            NSAccessibilityCustomAction(name: "Uncertain", target: item, selector: #selector(ActionControl.uncertain))
        ])
        if Bundle.main.object(forInfoDictionaryKey: "V5FactProbe") as? Bool == true {
            // AppKit resolves Command-V through the application's menu. Give
            // this fixture the ordinary responder-chain edit commands used by
            // a real text application; a field alone has no such key binding.
            let main = NSMenu(), edit = NSMenu(title: "Edit"), application = NSMenuItem()
            application.submenu = NSMenu(title: "V5 Native Action Probe")
            main.addItem(application)
            edit.addItem(withTitle: "Copy", action: #selector(NSText.copy(_:)), keyEquivalent: "c")
            edit.addItem(withTitle: "Paste", action: #selector(NSText.paste(_:)), keyEquivalent: "v")
            edit.addItem(withTitle: "Select All", action: #selector(NSText.selectAll(_:)), keyEquivalent: "a")
            let heading = NSMenuItem(title: "Edit", action: nil, keyEquivalent: "")
            heading.submenu = edit
            main.addItem(heading)
            NSApp.mainMenu = main
            window.setContentSize(NSSize(width: 580, height: 735))
            let deferred = Bundle.main.object(forInfoDictionaryKey: "V5DeferredEditor") as? Bool == true
            func searchField(_ frame: NSRect) -> NSSearchField {
                let field = NSSearchField(frame: frame)
                if deferred {
                    let cell = DeferredSearchCell(textCell: "")
                    cell.isEditable = true
                    cell.isSelectable = true
                    field.cell = cell
                }
                return field
            }
            let search = searchField(NSRect(x: 20, y: 685, width: 530, height: 25))
            search.stringValue =
                Bundle.main.object(forInfoDictionaryKey: "V5EmptySearch") as? Bool == true ? "" : "query original"
            search.setAccessibilityIdentifier("v5-search-target")
            search.delegate = self
            window.contentView!.addSubview(search)
            searchTarget = search
            let composite = CompositeEditor(frame: NSRect(x: 20, y: 605, width: 530, height: 35))
            composite.setAccessibilityElement(true)
            composite.setAccessibilityRole(.textField)
            composite.setAccessibilityIdentifier("v5-composite-owner")
            let internalField: NSTextField = deferred ? searchField(.zero) : NSTextField(string: "")
            internalField.frame = NSRect(x: 5, y: 5, width: 515, height: 25)
            internalField.setAccessibilityIdentifier("v5-composite-editor")
            internalField.cell?.sendsActionOnEndEditing = false
            internalField.target = self
            internalField.action = #selector(commitField)
            composite.addSubview(internalField)
            window.contentView!.addSubview(composite)
            compositeOwner = composite
            compositeField = internalField
            let body = ReplacementBody(frame: NSRect(x: 20, y: 465, width: 530, height: 110))
            body.owner = self
            body.setAccessibilityIdentifier("v5-text-body")
            body.isRichText = true
            body.isEditable = true
            body.isSelectable = true
            body.string = ""
            window.contentView!.addSubview(body)
            textBody = body
            func group(_ name: String, x: CGFloat) -> NSView {
                let view = NSView(frame: NSRect(x: x, y: 385, width: 250, height: 55))
                view.setAccessibilityElement(true)
                view.setAccessibilityRole(.group)
                view.setAccessibilityLabel(name)
                view.setAccessibilityIdentifier(name)
                window.contentView!.addSubview(view)
                return view
            }
            let pathGroup = group("v5-path-group", x: 20)
            pathOther = group("v5-other-group", x: 300)
            let pathField = NSTextField(string: "path original")
            pathField.frame = NSRect(x: 5, y: 10, width: 235, height: 25)
            pathField.setAccessibilityIdentifier("v5-path-target")
            pathGroup.addSubview(pathField)
            pathTarget = pathField
            counts["menu"] = 0
            let menu = MenuActionControl(frame: NSRect(x: 20, y: 275, width: 530, height: 30))
            menu.owner = self
            menu.setAccessibilityElement(true)
            menu.setAccessibilityRole(.button)
            menu.setAccessibilityLabel("Menu action target")
            menu.setAccessibilityIdentifier("v5-menu-target")
            window.contentView!.addSubview(menu)
            item.setAccessibilityCustomActions(
                (item.accessibilityCustomActions() ?? []) + [
                    NSAccessibilityCustomAction(
                        name: "Move window", target: item, selector: #selector(ActionControl.moveToAnotherWindow)),
                    NSAccessibilityCustomAction(
                        name: "Change captured text", target: item,
                        selector: #selector(ActionControl.changeCapturedText))
                ])
            let text = NSTextField(labelWithString: String(repeating: "original🙂", count: 3000))
            text.setAccessibilityIdentifier("long-capture")
            text.frame = NSRect(x: 20, y: 58, width: 530, height: 20)
            window.contentView!.addSubview(text)
            longText = text
            func field(_ identifier: String, value: String, x: CGFloat) -> NSTextField {
                let field = NSTextField(string: value)
                field.setAccessibilityIdentifier(identifier)
                field.cell?.sendsActionOnEndEditing = false
                field.target = self
                field.action = #selector(commitField)
                field.frame = NSRect(x: x, y: 320, width: 250, height: 25)
                window.contentView!.addSubview(field)
                return field
            }
            valueTarget = field("v5-value-target", value: "target original", x: 20)
            valueOther = field("v5-value-other", value: "other original", x: 300)
            try! JSONSerialization.data(withJSONObject: submitted, options: [.sortedKeys]).write(
                to: output.appendingPathComponent("submitted.json"), options: .atomic)
            window.makeFirstResponder(valueOther)
            Timer.scheduledTimer(withTimeInterval: 0.05, repeats: true) { [weak self] _ in
                MainActor.assumeIsolated {
                    guard let self, let valueTarget = self.valueTarget, let valueOther = self.valueOther else { return }
                    let windowState: [String: Any] = [
                        "hidden": NSApp.isHidden, "active": NSApp.isActive,
                        "keyWindow": NSApp.keyWindow?.title ?? "none", "mainWindow": NSApp.mainWindow?.title ?? "none",
                        "foreground": NSWorkspace.shared.frontmostApplication?.processIdentifier ?? 0
                    ]
                    try! JSONSerialization.data(withJSONObject: windowState, options: [.sortedKeys]).write(
                        to: self.output.appendingPathComponent("window-state.json"), options: .atomic)
                    let report: [String: Any] = [
                        "target": valueTarget.currentEditor()?.string ?? valueTarget.stringValue,
                        "other": valueOther.currentEditor()?.string ?? valueOther.stringValue,
                        "otherEditing": valueOther.currentEditor() != nil,
                        "targetEditing": valueTarget.currentEditor() != nil
                    ]
                    try! JSONSerialization.data(withJSONObject: report, options: [.sortedKeys]).write(
                        to: self.output.appendingPathComponent("values.json"), options: .atomic)
                    if let body = self.textBody {
                        try! JSONSerialization.data(
                            withJSONObject: [
                                "text": body.string,
                                "selectedTextWrites": (body as? ReplacementBody)?.selectedTextWrites ?? 0
                            ], options: [.sortedKeys]
                        ).write(to: self.output.appendingPathComponent("body.json"), options: .atomic)
                    }
                    if let search = self.searchTarget {
                        let searchState: [String: Any] = [
                            "text": search.currentEditor()?.string ?? search.stringValue, "changes": self.searchChanges,
                            "returns": self.searchReturns
                        ]
                        try! JSONSerialization.data(withJSONObject: searchState, options: [.sortedKeys]).write(
                            to: self.output.appendingPathComponent("search.json"), options: .atomic)
                    }
                    if let field = self.compositeField {
                        let move = self.output.appendingPathComponent("move-composite-editor")
                        if FileManager.default.fileExists(atPath: move.path), field.superview === self.compositeOwner {
                            field.removeFromSuperview()
                            self.pathOther?.addSubview(field)
                        }
                        let state: [String: Any] = [
                            "text": field.currentEditor()?.string ?? field.stringValue,
                            "editing": field.currentEditor() != nil,
                            "owner": field.superview?.accessibilityIdentifier() ?? "unknown"
                        ]
                        try! JSONSerialization.data(withJSONObject: state, options: [.sortedKeys]).write(
                            to: self.output.appendingPathComponent("composite.json"), options: .atomic)
                    }
                    let focusOther = self.output.appendingPathComponent("focus-other")
                    if FileManager.default.fileExists(atPath: focusOther.path) {
                        self.window.makeFirstResponder(valueOther)
                        try! FileManager.default.removeItem(at: focusOther)
                    }
                    if let pathTarget = self.pathTarget {
                        // The test harness controls a disposable fixture, never
                        // an application under test, to force a precise race.
                        let move = self.output.appendingPathComponent("move-path-target")
                        if FileManager.default.fileExists(atPath: move.path), pathTarget.superview !== self.pathOther {
                            pathTarget.removeFromSuperview()
                            self.pathOther?.addSubview(pathTarget)
                        }
                        let state: [String: Any] = [
                            "value": pathTarget.stringValue,
                            "group": pathTarget.superview?.accessibilityIdentifier() ?? "unknown"
                        ]
                        try! JSONSerialization.data(withJSONObject: state, options: [.sortedKeys]).write(
                            to: self.output.appendingPathComponent("path.json"), options: .atomic)
                    }
                }
            }
        }
        let duplicate = control("Duplicate actions", y: 150)
        duplicate.setAccessibilityCustomActions([
            NSAccessibilityCustomAction(name: "Same", target: duplicate, selector: #selector(ActionControl.pin)),
            NSAccessibilityCustomAction(name: "Same", target: duplicate, selector: #selector(ActionControl.alternate))
        ])
        let disabled = control("Disabled action", y: 85, enabled: false)
        disabled.setAccessibilityCustomActions([
            NSAccessibilityCustomAction(name: "置顶", target: disabled, selector: #selector(ActionControl.pin))
        ])
        publish()
        window.orderBack(nil)
        Timer.scheduledTimer(withTimeInterval: 120, repeats: false) { _ in
            MainActor.assumeIsolated { NSApp.terminate(nil) }
        }
    }
}
let application = NSApplication.shared
let delegate = Delegate()
application.delegate = delegate
application.setActivationPolicy(.regular)
application.run()
