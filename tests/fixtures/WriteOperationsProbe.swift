// Copyright (c) Huawei Technologies Co., Ltd. 2026. All rights reserved.

import AppKit
import CryptoKit

@MainActor final class WriteProbeApplication: NSApplication {
    var keys = [[String: Any]]()
    override func sendEvent(_ event: NSEvent) {
        if [.keyDown, .keyUp, .flagsChanged].contains(event.type) {
            let characters = event.type == .flagsChanged ? "" : (event.characters ?? "")
            keys.append([
                "type": event.type.rawValue, "code": event.keyCode, "flags": event.modifierFlags.rawValue,
                "characters": characters, "window": event.windowNumber, "active": isActive,
            ])
        }
        super.sendEvent(event)
    }
}

@MainActor final class WriteProbeText: NSTextView {
    var pasted = 0
    override func paste(_ sender: Any?) {
        pasted += 1
        super.paste(sender)
    }
}

@MainActor final class WriteProbeDrag: NSView {
    var downs = 0, drags = 0, ups = 0
    override func acceptsFirstMouse(for event: NSEvent?) -> Bool { false }
    override func mouseDown(with event: NSEvent) { downs += 1 }
    override func mouseDragged(with event: NSEvent) { drags += 1 }
    override func mouseUp(with event: NSEvent) { ups += 1 }
    override func draw(_ dirtyRect: NSRect) {
        NSColor.controlAccentColor.withAlphaComponent(0.3).setFill()
        bounds.fill()
        "Drag test surface".draw(at: NSPoint(x: 15, y: 15), withAttributes: [.font: NSFont.systemFont(ofSize: 18)])
    }
}

@MainActor final class WriteProbeDocument: NSView {
    override var isFlipped: Bool { true }
}

@MainActor final class WriteOperationsDelegate: NSObject, NSApplicationDelegate, NSMenuDelegate {
    let directory = Bundle.main.bundleURL.deletingLastPathComponent()
    var window: NSWindow!
    let field = NSTextField(string: "")
    let other = NSTextField(string: "unchanged")
    let editor = WriteProbeText(frame: NSRect(x: 20, y: 330, width: 315, height: 130))
    let scroll = NSScrollView(frame: NSRect(x: 380, y: 80, width: 310, height: 380))
    let drag = WriteProbeDrag(frame: NSRect(x: 20, y: 70, width: 315, height: 135))
    let popup = NSPopUpButton(frame: NSRect(x: 180, y: 245, width: 155, height: 32))
    var buttonClicks = 0, menuOpens = 0

    func localPoint(_ view: NSView, _ point: NSPoint) -> [Double] {
        let screen = window.convertPoint(toScreen: view.convert(point, to: nil))
        return [screen.x - window.frame.minX, window.frame.maxY - screen.y]
    }
    func clipboardDigest() -> String {
        var digest = SHA256()
        for item in NSPasteboard.general.pasteboardItems ?? [] {
            for type in item.types.sorted(by: { $0.rawValue < $1.rawValue }) {
                digest.update(data: Data(type.rawValue.utf8))
                if let data = item.data(forType: type) { digest.update(data: data) }
            }
        }
        return digest.finalize().map { String(format: "%02x", $0) }.joined()
    }
    func record() {
        guard let window else { return }
        let first = window.firstResponder
        var value: [String: Any] = [
            "pid": getpid(), "active": NSApp.isActive, "field": field.stringValue, "other": other.stringValue,
            "keyWindow": window.isKeyWindow, "mainWindow": window.isMainWindow,
            "keys": (NSApp as? WriteProbeApplication)?.keys ?? [],
            "editor": editor.string, "pastes": editor.pasted, "buttonClicks": buttonClicks, "menuOpens": menuOpens,
            "menuChoice": popup.titleOfSelectedItem ?? "", "scrollY": scroll.contentView.bounds.minY,
            "dragDowns": drag.downs, "dragMoves": drag.drags, "dragUps": drag.ups,
            "editorFocused": first === editor, "clipboardDigest": clipboardDigest(),
            "windowWidth": window.frame.width, "windowHeight": window.frame.height,
            "dragStart": localPoint(drag, NSPoint(x: 55, y: 75)),
            "dragEnd": localPoint(drag, NSPoint(x: 235, y: 75)),
            "scrollPoint": localPoint(scroll, NSPoint(x: 150, y: 200)),
        ]
        if let button = window.contentView?.subviews.compactMap({ $0 as? NSButton })
            .first(where: { $0.title == "Count action" })
        {
            value["buttonPoint"] = localPoint(button, NSPoint(x: button.bounds.midX, y: button.bounds.midY))
        }
        try! JSONSerialization.data(withJSONObject: value, options: [.sortedKeys]).write(
            to: directory.appendingPathComponent("state.json"), options: .atomic)
    }
    @objc func clicked() {
        buttonClicks += 1
        record()
    }
    func menuWillOpen(_ menu: NSMenu) {
        menuOpens += 1
        record()
    }
    func applicationDidFinishLaunching(_ notification: Notification) {
        window = NSWindow(
            contentRect: NSRect(x: 180, y: 120, width: 720, height: 550), styleMask: [.titled, .closable],
            backing: .buffered, defer: false)
        window.title = "Write operations probe"
        field.frame = NSRect(x: 20, y: 495, width: 315, height: 28)
        field.setAccessibilityLabel("Primary input")
        other.frame = NSRect(x: 380, y: 495, width: 310, height: 28)
        other.setAccessibilityLabel("Unrelated input")
        editor.isRichText = false
        editor.setAccessibilityLabel("Paste editor")
        editor.setAccessibilityIdentifier("paste-editor")
        drag.setAccessibilityElement(true)
        drag.setAccessibilityRole(.group)
        drag.setAccessibilityLabel("Drag test surface")
        let button = NSButton(title: "Count action", target: self, action: #selector(clicked))
        button.frame = NSRect(x: 20, y: 245, width: 140, height: 32)
        popup.addItems(withTitles: ["First choice", "Second choice"])
        popup.setAccessibilityLabel("Test choices")
        popup.menu?.delegate = self
        let document = WriteProbeDocument(frame: NSRect(x: 0, y: 0, width: 285, height: 1800))
        for row in 0..<45 {
            let text = NSTextField(labelWithString: "Scroll row \(row + 1)")
            text.frame = NSRect(x: 15, y: row * 40 + 10, width: 250, height: 25)
            document.addSubview(text)
        }
        scroll.documentView = document
        scroll.hasVerticalScroller = true
        scroll.setAccessibilityLabel("Test scroll area")
        for view in [field, other, editor, drag, button, popup, scroll] { window.contentView!.addSubview(view) }
        let menu = NSMenu()
        let applicationMenu = NSMenuItem(title: "WriteOperationsProbe", action: nil, keyEquivalent: "")
        applicationMenu.submenu = NSMenu()
        applicationMenu.submenu!.addItem(
            withTitle: "Quit", action: #selector(NSApplication.terminate(_:)), keyEquivalent: "q")
        menu.addItem(applicationMenu)
        let edit = NSMenuItem(title: "Edit", action: nil, keyEquivalent: "")
        edit.submenu = NSMenu()
        edit.submenu!.addItem(withTitle: "Select All", action: #selector(NSText.selectAll(_:)), keyEquivalent: "a")
        edit.submenu!.addItem(withTitle: "Paste", action: #selector(NSText.paste(_:)), keyEquivalent: "v")
        menu.addItem(edit)
        NSApp.mainMenu = menu
        window.orderBack(nil)
        window.makeKey()
        window.makeMain()
        window.makeFirstResponder(other)
        record()
        Timer.scheduledTimer(withTimeInterval: 0.05, repeats: true) { _ in MainActor.assumeIsolated { self.record() } }
    }
}

@main enum WriteOperationsProbe {
    @MainActor static func main() {
        let app = WriteProbeApplication.shared
        app.setActivationPolicy(.regular)
        let delegate = WriteOperationsDelegate()
        app.delegate = delegate
        app.run()
    }
}
