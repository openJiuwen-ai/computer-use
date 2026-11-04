// Copyright (c) Huawei Technologies Co., Ltd. 2026. All rights reserved.

import AppKit
import ApplicationServices

@MainActor final class ScrollPermissionApplication: NSApplication {
    var mouseEvents = [Int](), keys = 0
    override func sendEvent(_ event: NSEvent) {
        if [.leftMouseDown, .leftMouseUp, .rightMouseDown, .rightMouseUp, .scrollWheel].contains(event.type) {
            mouseEvents.append(Int(event.type.rawValue))
        }
        if [.keyDown, .keyUp].contains(event.type) { keys += 1 }
        super.sendEvent(event)
    }
}

@MainActor final class ScrollPermissionDocument: NSView {
    override var isFlipped: Bool { true }
}

@MainActor final class ScrollPermissionBar: NSView {
    var inputEnabled = true
    override nonisolated func accessibilityRole() -> NSAccessibility.Role? { .scrollBar }
    override nonisolated func accessibilityOrientation() -> NSAccessibilityOrientation { .vertical }
    override nonisolated func isAccessibilityEnabled() -> Bool { MainActor.assumeIsolated { inputEnabled } }
    override nonisolated func accessibilityValue() -> Any? { NSNumber(value: 0.2) }
    override nonisolated func accessibilityMinValue() -> Any? { NSNumber(value: 0.0) }
    override nonisolated func accessibilityMaxValue() -> Any? { NSNumber(value: 1.0) }
}

@MainActor final class ScrollPermissionPage: NSButton {
    override nonisolated func accessibilitySubrole() -> NSAccessibility.Subrole? { .incrementPage }
}

/// Real NSAccessibility relations feed the production AX bridge. The page
/// button has an explicit target/action that scrolls the real clip view.
@MainActor final class ScrollPermissionViewport: NSScrollView {
    var inputEnabled = true, conflictingBar = false
    var pageBar: ScrollPermissionBar!
    var otherBar: ScrollPermissionBar!
    override nonisolated func isAccessibilityEnabled() -> Bool { MainActor.assumeIsolated { inputEnabled } }
    override nonisolated func accessibilityVerticalScrollBar() -> Any? {
        let value: NSView? = MainActor.assumeIsolated { conflictingBar ? otherBar : pageBar }
        return value
    }
    override nonisolated func accessibilityChildren() -> [Any]? {
        let value: [NSView] = MainActor.assumeIsolated { [documentView, pageBar, otherBar].compactMap { $0 } }
        return value
    }
}

@MainActor final class ScrollPermissionDelegate: NSObject, NSApplicationDelegate {
    let folder = Bundle.main.bundleURL.deletingLastPathComponent()
    let viewport = ScrollPermissionViewport(frame: NSRect(x: 25, y: 55, width: 390, height: 280))
    let content = ScrollPermissionDocument(frame: NSRect(x: 0, y: 0, width: 365, height: 1800))
    let owner = NSTextField(string: "unchanged disabled content")
    let other = NSTextField(string: "unrelated unchanged")
    let bar = ScrollPermissionBar(frame: NSRect(x: 365, y: 0, width: 22, height: 280))
    let otherBar = ScrollPermissionBar(frame: NSRect(x: 340, y: 0, width: 22, height: 280))
    let page = ScrollPermissionPage(title: "Page down", target: nil, action: nil)
    var window: NSWindow!, sheet: NSPanel?
    var pageActions = 0, lastCommand = "", mutation = "", mode = "page"
    var callbacks = [[String: Any]]()

    @objc func pageDown() {
        pageActions += 1
        let before = viewport.contentView.bounds.minY
        viewport.contentView.scroll(to: NSPoint(x: 0, y: min(1500, before + 240)))
        viewport.reflectScrolledClipView(viewport.contentView)
        callbacks.append(["before": before, "after": viewport.contentView.bounds.minY])
        if pageActions == 1 {
            switch mode {
            case "owner_disables": owner.isEnabled = false
            case "region_disables": viewport.inputEnabled = false
            case "bar_disables": bar.inputEnabled = false
            case "page_disables": page.isEnabled = false
            case "relation_changes": viewport.conflictingBar = true
            case "modal":
                let panel = NSPanel(
                    contentRect: NSRect(x: 0, y: 0, width: 220, height: 85), styleMask: [.titled],
                    backing: .buffered, defer: false)
                panel.title = "Owned scroll blocking sheet"
                sheet = panel
                window.beginSheet(panel)
            default: break
            }
        }
        record()
    }

    func configure(_ selected: String) {
        if let attached = window.attachedSheet { window.endSheet(attached); attached.orderOut(nil) }
        sheet = nil
        mode = selected
        pageActions = 0
        callbacks = []
        viewport.inputEnabled = selected != "region_alias_disabled"
        bar.inputEnabled = selected != "bar_alias_disabled"
        viewport.conflictingBar = false
        page.isEnabled = selected != "page_alias_disabled"
        owner.isEnabled = selected == "owner_disables"
        owner.frame = NSRect(x: 20, y: selected == "wheel_hit_alias_disabled" ? 125 : 35, width: 300, height: 25)
        viewport.contentView.scroll(to: .zero)
        viewport.reflectScrolledClipView(viewport.contentView)
        if let app = NSApp as? ScrollPermissionApplication { app.mouseEvents = []; app.keys = 0 }
    }

    func record() {
        guard let window else { return }
        let app = NSApp as! ScrollPermissionApplication
        let cursor = CGEvent(source: nil)?.location
        let value: [String: Any] = [
            "pid": getpid(), "active": NSApp.isActive,
            "frontmostPID": NSWorkspace.shared.frontmostApplication?.processIdentifier as Any? ?? NSNull(),
            "cursor": cursor.map { [$0.x, $0.y] } as Any? ?? NSNull(),
            "mode": mode, "lastCommand": lastCommand, "offset": viewport.contentView.bounds.minY,
            "pageActions": pageActions, "callbacks": callbacks, "ownerEnabled": owner.isEnabled,
            "regionEnabled": viewport.inputEnabled, "barEnabled": bar.inputEnabled,
            "pageEnabled": page.isEnabled, "conflictingBar": viewport.conflictingBar,
            "owner": owner.stringValue, "other": other.stringValue,
            "mouseEvents": app.mouseEvents, "keyEvents": app.keys, "modal": window.attachedSheet != nil,
        ]
        try! JSONSerialization.data(withJSONObject: value, options: [.sortedKeys]).write(
            to: folder.appendingPathComponent("state.json"), options: .atomic)
    }

    func tick() {
        if let data = try? Data(contentsOf: folder.appendingPathComponent("command.json")),
            let command = try? JSONSerialization.jsonObject(with: data) as? [String: Any],
            let id = command["id"] as? String, id != lastCommand {
            lastCommand = id
            configure(command["mode"] as? String ?? "page")
        }
        record()
    }

    func applicationDidFinishLaunching(_ notification: Notification) {
        window = NSWindow(
            contentRect: NSRect(x: 170, y: 150, width: 445, height: 390),
            styleMask: [.titled, .closable], backing: .buffered, defer: false)
        window.title = "Scroll owner permission probe"
        owner.setAccessibilityLabel("Scroll content anchor")
        owner.setAccessibilityIdentifier("scroll-permission-owner")
        content.setAccessibilityElement(true)
        content.setAccessibilityRole(.group)
        content.setAccessibilityIdentifier("scroll-permission-document")
        content.addSubview(owner)
        for row in 0..<30 {
            let label = NSTextField(labelWithString: "Real viewport row \(row + 1)")
            label.frame = NSRect(x: 20, y: 180 + row * 48, width: 300, height: 24)
            content.addSubview(label)
        }
        viewport.documentView = content
        viewport.hasVerticalScroller = false
        viewport.hasHorizontalScroller = false
        viewport.setAccessibilityLabel("Borrowed scroll viewport")
        viewport.setAccessibilityIdentifier("scroll-permission-area")
        bar.setAccessibilityElement(true)
        bar.setAccessibilityLabel("Borrowed vertical scroll bar")
        bar.setAccessibilityIdentifier("scroll-permission-bar")
        otherBar.setAccessibilityElement(true)
        otherBar.setAccessibilityIdentifier("scroll-permission-other-bar")
        page.frame = NSRect(x: 0, y: 0, width: 22, height: 200)
        page.target = self
        page.action = #selector(pageDown)
        page.setAccessibilityLabel("Borrowed increment page")
        page.setAccessibilityIdentifier("scroll-permission-page")
        bar.addSubview(page)
        viewport.pageBar = bar
        viewport.otherBar = otherBar
        viewport.addSubview(bar)
        viewport.addSubview(otherBar)
        other.frame = NSRect(x: 25, y: 345, width: 390, height: 25)
        other.setAccessibilityLabel("Unrelated scroll input")
        window.contentView!.addSubview(viewport)
        window.contentView!.addSubview(other)
        configure("page")
        window.orderBack(nil)
        window.makeKey()
        window.makeMain()
        window.makeFirstResponder(other)
        record()
        _ = Timer.scheduledTimer(withTimeInterval: 0.03, repeats: true) { _ in
            MainActor.assumeIsolated { self.tick() }
        }
    }
}

@main enum ScrollOwnerPermissionProbe {
    @MainActor static func main() {
        let app = ScrollPermissionApplication.shared
        app.setActivationPolicy(.regular)
        let delegate = ScrollPermissionDelegate()
        app.delegate = delegate
        app.run()
    }
}
