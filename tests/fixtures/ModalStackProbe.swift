import AppKit

// Two standalone modal loops, without a fabricated AX parent relationship.
// The native callback and NSApplication.modalWindow are independent oracles.
@MainActor final class ModalStackProbe: NSObject, NSApplicationDelegate {
    var document: NSWindow!
    var outer: NSPanel!
    var inner: NSPanel!
    var input: NSTextField!
    static let events = Bundle.main.bundleURL.deletingPathExtension().appendingPathExtension("events.jsonl")

    static func log(_ value: [String: Any]) {
        let data = try! JSONSerialization.data(withJSONObject: value, options: [.sortedKeys]) + Data([10])
        if !FileManager.default.fileExists(atPath: events.path) {
            FileManager.default.createFile(atPath: events.path, contents: nil)
        }
        if let file = try? FileHandle(forWritingTo: events) {
            _ = try? file.seekToEnd()
            try? file.write(contentsOf: data)
            try? file.close()
        }
    }

    func button(_ title: String, id: String, action: Selector, x: CGFloat, y: CGFloat, in window: NSWindow) {
        let button = NSButton(title: title, target: self, action: action)
        button.frame = NSRect(x: x, y: y, width: 160, height: 30)
        button.setAccessibilityIdentifier(id)
        window.contentView!.addSubview(button)
    }

    func state(_ phase: String) {
        Self.log([
            "kind": "state", "phase": phase, "modal": NSApp.modalWindow?.title ?? "",
            "key": NSApp.keyWindow?.title ?? "", "active": NSApp.isActive,
            "windows": [document, outer, inner].compactMap { $0 }.map { window in
                [
                    "title": window.title, "visible": window.isVisible, "modal": window.isAccessibilityModal(),
                    "parentClass": window.accessibilityParent().map { NSStringFromClass(type(of: $0 as AnyObject)) }
                        ?? "none"
                ] as [String: Any]
            }
        ])
    }

    func applicationDidFinishLaunching(_ notification: Notification) {
        Self.log(["kind": "started", "pid": ProcessInfo.processInfo.processIdentifier])
        document = NSWindow(
            contentRect: NSRect(x: 120, y: 120, width: 390, height: 180), styleMask: [.titled, .closable],
            backing: .buffered, defer: false)
        document.title = "Modal Stack Host"
        button("Open outer dialog", id: "open-outer", action: #selector(openOuter), x: 20, y: 100, in: document)
        button("Document action", id: "host-action", action: #selector(hostAction), x: 200, y: 100, in: document)
        document.orderBack(nil)
        document.makeKey()
        document.makeMain()
        state("host")
    }

    @objc func hostAction() { Self.log(["kind": "unexpected-host-action"]) }
    @objc func outerAction() { Self.log(["kind": "unexpected-outer-action"]) }

    @objc func openOuter() {
        perform(#selector(showOuter), with: nil, afterDelay: 0.05, inModes: [.default, .modalPanel])
    }

    @objc func outerReady() { state("outer") }
    @objc func innerReady() { state("inner") }

    @objc func showOuter() {
        outer = NSPanel(
            contentRect: NSRect(x: 180, y: 180, width: 390, height: 200), styleMask: [.titled], backing: .buffered,
            defer: false)
        outer.title = "Outer Standalone Dialog"
        button("Open inner dialog", id: "open-inner", action: #selector(openInner), x: 20, y: 110, in: outer)
        button("Outer action", id: "outer-action", action: #selector(outerAction), x: 200, y: 110, in: outer)
        perform(#selector(outerReady), with: nil, afterDelay: 0.05, inModes: [.modalPanel])
        _ = NSApp.runModal(for: outer)
        outer.orderOut(nil)
    }

    @objc func openInner() {
        perform(#selector(showInner), with: nil, afterDelay: 0.05, inModes: [.default, .modalPanel])
    }

    @objc func showInner() {
        inner = NSPanel(
            contentRect: NSRect(x: 230, y: 230, width: 390, height: 220), styleMask: [.titled], backing: .buffered,
            defer: false)
        inner.title = "Inner Standalone Dialog"
        input = NSTextField(string: "")
        input.frame = NSRect(x: 20, y: 130, width: 340, height: 24)
        input.setAccessibilityIdentifier("inner-input")
        inner.contentView!.addSubview(input)
        button("Accept inner", id: "accept-inner", action: #selector(acceptInner), x: 200, y: 50, in: inner)
        inner.initialFirstResponder = input
        perform(#selector(innerReady), with: nil, afterDelay: 0.05, inModes: [.modalPanel])
        _ = NSApp.runModal(for: inner)
        inner.orderOut(nil)
        outer.makeKey()
        state("inner-closed")
    }

    @objc func acceptInner() {
        Self.log(["kind": "accepted", "text": input.stringValue, "modal": NSApp.modalWindow?.title ?? ""])
        NSApp.stopModal()
    }
}

let application = NSApplication.shared
application.setActivationPolicy(.regular)
let delegate = ModalStackProbe()
application.delegate = delegate
application.run()
