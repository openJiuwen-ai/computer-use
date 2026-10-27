import AppKit
import CoreGraphics

/// A real focused editor followed by a wide, unrelated native subtree. State
/// files are passive oracles; setup after launch and every tested input use UI.
@MainActor final class EvidenceGroup: NSView {
    let label: String
    init(frame: NSRect, label: String, identifier: String) {
        self.label = label
        super.init(frame: frame)
        setAccessibilityIdentifier(identifier)
    }
    required init?(coder: NSCoder) { fatalError("init(coder:) has not been implemented") }
    override func isAccessibilityElement() -> Bool { true }
    override func accessibilityRole() -> NSAccessibility.Role? { .group }
    override func accessibilityLabel() -> String? { label }
}

@MainActor final class EvidenceBoundaryProbe: NSObject, NSApplicationDelegate, NSTextFieldDelegate {
    let directory = Bundle.main.bundleURL.deletingLastPathComponent()
    var root: NSWindow!
    var other: NSWindow!
    var sheet: NSPanel?
    let input = NSTextField(string: "")
    let adjacent = NSTextField(string: "GUARD")
    let independent = NSTextField(string: "OTHER-WINDOW")
    var child = NSTextField(string: "")
    var submits = 0, childSubmits = 0, generation = 0

    func record(_ event: String = "sample") {
        guard root != nil, other != nil else { return }
        let pointer = CGEvent(source: nil)?.location ?? .zero
        let value: [String: Any] = [
            "event": event, "pid": getpid(), "input": input.stringValue, "adjacent": adjacent.stringValue,
            "independent": independent.stringValue, "child": child.stringValue, "submits": submits,
            "childSubmits": childSubmits, "labelCount": 320, "sheet": sheet?.isVisible ?? false,
            "generation": generation, "rootWindow": root.windowNumber, "otherWindow": other.windowNumber,
            "childWindow": sheet?.windowNumber ?? -1, "keyWindow": NSApp.keyWindow?.title ?? "",
            "targetResponder": input.currentEditor() === root.firstResponder,
            "adjacentResponder": adjacent.currentEditor() === root.firstResponder,
            "foreground": NSWorkspace.shared.frontmostApplication?.processIdentifier ?? 0,
            "pointer": [pointer.x, pointer.y]
        ]
        guard let data = try? JSONSerialization.data(withJSONObject: value, options: [.sortedKeys]) else { return }
        try? data.write(to: directory.appendingPathComponent("state.json"), options: .atomic)
        if event != "sample" {
            let file = directory.appendingPathComponent("events.jsonl")
            if !FileManager.default.fileExists(atPath: file.path) {
                FileManager.default.createFile(atPath: file.path, contents: nil)
            }
            if let handle = try? FileHandle(forWritingTo: file) {
                _ = try? handle.seekToEnd()
                try? handle.write(contentsOf: data + Data([10]))
                try? handle.close()
            }
        }
    }

    func field(_ field: NSTextField, id: String, label: String, in view: NSView, frame: NSRect) {
        field.frame = frame
        field.setAccessibilityIdentifier(id)
        field.setAccessibilityLabel(label)
        field.delegate = self
        view.addSubview(field)
    }

    func button(_ title: String, action: Selector, in view: NSView, frame: NSRect) {
        let button = NSButton(title: title, target: self, action: action)
        button.frame = frame
        view.addSubview(button)
    }

    func window(_ title: String, frame: NSRect) -> NSWindow {
        let window = NSWindow(contentRect: frame, styleMask: [.titled, .closable], backing: .buffered, defer: false)
        window.title = title
        window.tabbingMode = .disallowed
        window.isReleasedWhenClosed = false
        return window
    }

    func applicationDidFinishLaunching(_ notification: Notification) {
        try! String(getpid()).write(
            to: directory.appendingPathComponent("fixture.pid"), atomically: true, encoding: .utf8)
        other = window("Evidence independent", frame: NSRect(x: 850, y: 160, width: 340, height: 140))
        field(
            independent, id: "evidence-independent", label: "Independent input", in: other.contentView!,
            frame: NSRect(x: 15, y: 70, width: 310, height: 28))
        other.orderBack(nil)
        root = window("Evidence target", frame: NSRect(x: 120, y: 160, width: 700, height: 510))
        let container = EvidenceGroup(
            frame: NSRect(x: 20, y: 155, width: 660, height: 335), label: "Wide receiver container",
            identifier: "evidence-container")
        root.contentView!.addSubview(container)
        // In native child order the editor precedes 320 actual static controls.
        field(
            input, id: "evidence-input", label: "Target input", in: container,
            frame: NSRect(x: 15, y: 280, width: 620, height: 28))
        for index in 0..<320 {
            let label = NSTextField(labelWithString: String(format: "S%03d", index))
            label.font = .systemFont(ofSize: 8)
            label.frame = NSRect(x: 15 + (index % 20) * 31, y: 20 + (index / 20) * 15, width: 30, height: 14)
            label.setAccessibilityIdentifier("evidence-static-\(index)")
            container.addSubview(label)
        }
        let peer = EvidenceGroup(
            frame: NSRect(x: 20, y: 75, width: 660, height: 60), label: "Adjacent receiver container",
            identifier: "evidence-peer-container")
        root.contentView!.addSubview(peer)
        field(
            adjacent, id: "evidence-adjacent", label: "Adjacent input", in: peer,
            frame: NSRect(x: 15, y: 15, width: 620, height: 28))
        button(
            "Open owned dialog", action: #selector(openSheet), in: root.contentView!,
            frame: NSRect(x: 20, y: 20, width: 200, height: 32))
        root.orderBack(nil)
        root.makeKey()
        root.makeMain()
        root.makeFirstResponder(input)
        record("ready")
        Timer.scheduledTimer(withTimeInterval: 0.03, repeats: true) { _ in MainActor.assumeIsolated { self.record() } }
    }

    func controlTextDidChange(_ notification: Notification) { record("text-changed") }
    func control(_ control: NSControl, textView: NSTextView, doCommandBy commandSelector: Selector) -> Bool {
        guard commandSelector == #selector(NSResponder.insertNewline(_:)) else { return false }
        if control === input {
            submits += 1
            record("submit")
            return true
        }
        if control === child {
            childSubmits += 1
            record("child-submit")
            return true
        }
        return false
    }

    @objc func openSheet() {
        guard sheet == nil else { return }
        generation += 1
        let panel = NSPanel(
            contentRect: NSRect(x: 0, y: 0, width: 420, height: 160), styleMask: [.titled], backing: .buffered,
            defer: false)
        panel.title = "Evidence owned dialog"
        child = NSTextField(string: "child-\(generation)")
        field(
            child, id: "evidence-child", label: "Child input", in: panel.contentView!,
            frame: NSRect(x: 20, y: 95, width: 380, height: 28))
        panel.initialFirstResponder = child
        button(
            "Close owned dialog", action: #selector(closeSheet), in: panel.contentView!,
            frame: NSRect(x: 100, y: 30, width: 220, height: 32))
        sheet = panel
        root.beginSheet(panel) { _ in }
        record("sheet-opened")
    }

    @objc func closeSheet() {
        guard let panel = sheet else { return }
        root.endSheet(panel)
        panel.close()
        sheet = nil
        record("sheet-closed")
    }
}

let application = NSApplication.shared
application.setActivationPolicy(.regular)
let delegate = EvidenceBoundaryProbe()
application.delegate = delegate
application.run()
