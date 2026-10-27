import AppKit

@MainActor final class Region: NSView {
    let label: String
    init(_ label: String, frame: NSRect) {
        self.label = label
        super.init(frame: frame)
    }
    required init?(coder: NSCoder) { fatalError() }
    override func isAccessibilityElement() -> Bool { true }
    override func accessibilityRole() -> NSAccessibility.Role? { .group }
    override func accessibilityLabel() -> String? { label }
}

@MainActor final class FailedPress: NSButton {
    var attempted: (() -> Void)?
    override func accessibilityPerformPress() -> Bool {
        attempted?()
        return false
    }
}

@MainActor final class Probe: NSObject, NSApplicationDelegate {
    let window = NSWindow(
        contentRect: NSRect(x: 80, y: 100, width: 560, height: 460), styleMask: [.titled, .closable, .resizable],
        backing: .buffered, defer: false)
    let export = Region("Export", frame: NSRect(x: 20, y: 230, width: 240, height: 160))
    let other = Region("Other", frame: NSRect(x: 280, y: 230, width: 240, height: 160))
    var field = NSTextField(string: "original")
    var saves = 0, wrongSaves = 0, submits = 0, failedPresses = 0, generations = 0, menuSaves = 0
    var delayed: NSButton?
    var log: URL {
        URL(fileURLWithPath: Bundle.main.bundlePath).deletingLastPathComponent().appendingPathComponent("state.json")
    }
    func button(_ title: String, _ action: Selector, x: CGFloat, y: CGFloat, parent: NSView? = nil) -> NSButton {
        let button = NSButton(title: title, target: self, action: action)
        button.frame = NSRect(x: x, y: y, width: 165, height: 30)
        (parent ?? window.contentView!).addSubview(button)
        return button
    }
    func installField() {
        field.frame = NSRect(x: 10, y: 100, width: 200, height: 26)
        field.setAccessibilityLabel("Filename")
        field.target = self
        field.action = #selector(submit)
        field.cell?.sendsActionOnEndEditing = false
        export.addSubview(field)
    }
    func applicationDidFinishLaunching(_ notification: Notification) {
        let bar = NSMenu()
        let appItem = NSMenuItem(title: "Probe", action: nil, keyEquivalent: "")
        appItem.submenu = NSMenu(title: "Probe")
        bar.addItem(appItem)
        for title in ["File", "Tools"] {
            let item = NSMenuItem(title: title, action: nil, keyEquivalent: "")
            let submenu = NSMenu(title: title)
            let command = NSMenuItem(
                title: "Save receipt", action: title == "File" ? #selector(menuSave) : #selector(wrongSave),
                keyEquivalent: "")
            command.target = self
            submenu.addItem(command)
            item.submenu = submenu
            bar.addItem(item)
        }
        NSApplication.shared.mainMenu = bar
        window.title = "Element Path Probe"
        window.isReleasedWhenClosed = false
        window.contentView!.addSubview(export)
        window.contentView!.addSubview(other)
        installField()
        _ = button("Save", #selector(save), x: 10, y: 40, parent: export)
        _ = button("Save", #selector(wrongSave), x: 10, y: 40, parent: other)
        _ = button("Rebuild", #selector(rebuild), x: 20, y: 180)
        _ = button("Duplicate", #selector(duplicate), x: 280, y: 180)
        _ = button("Schedule", #selector(schedule), x: 20, y: 130)
        let failure = FailedPress(title: "Fail once", target: nil, action: nil)
        failure.frame = NSRect(x: 280, y: 130, width: 165, height: 30)
        failure.attempted = { [weak self] in
            self?.failedPresses += 1
            self?.record()
        }
        window.contentView!.addSubview(failure)
        window.orderBack(nil)
        record()
    }
    func record() {
        let data: [String: Any] = [
            "value": field.stringValue, "saves": saves, "wrongSaves": wrongSaves, "submits": submits,
            "failedPresses": failedPresses, "generation": generations, "menuSaves": menuSaves
        ]
        try! JSONSerialization.data(withJSONObject: data, options: [.sortedKeys]).write(to: log, options: .atomic)
    }
    @objc func submit() {
        submits += 1
        record()
    }
    @objc func save() {
        saves += 1
        record()
    }
    @objc func wrongSave() {
        wrongSaves += 1
        record()
    }
    @objc func menuSave() {
        menuSaves += 1
        record()
    }
    @objc func rebuild() {
        field.removeFromSuperview()
        field = NSTextField(string: "rebuilt")
        installField()
        generations += 1
        window.setFrame(NSRect(x: 160, y: 150, width: 620, height: 500), display: true)
        record()
    }
    @objc func duplicate() {
        _ = button("Save", #selector(wrongSave), x: 10, y: 5, parent: export)
        record()
    }
    @objc func schedule() {
        DispatchQueue.main.asyncAfter(deadline: .now() + 0.5) {
            self.delayed = self.button("Ready", #selector(self.save), x: 280, y: 80)
            self.delayed!.isEnabled = false
            DispatchQueue.main.asyncAfter(deadline: .now() + 0.5) { self.delayed!.isEnabled = true }
        }
    }
}

let app = NSApplication.shared
app.setActivationPolicy(.accessory)
let delegate = Probe()
app.delegate = delegate
app.run()
