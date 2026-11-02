// Copyright (c) Huawei Technologies Co., Ltd. 2026. All rights reserved.

import AppKit

@MainActor final class ImageScopePanel: NSPanel {
    weak var owner: NSWindow?
    override nonisolated func accessibilityParent() -> Any? { MainActor.assumeIsolated { owner } }
}

@MainActor final class ImageScopeDelegate: NSObject, NSApplicationDelegate {
    let folder = Bundle.main.bundleURL.deletingLastPathComponent()
    var window: NSWindow!
    var panel: ImageScopePanel!
    var clicks = 0
    var moved = false
    var closed = false
    var translated = false
    let button = NSButton(title: "Count action", target: nil, action: nil)

    @objc func click() { clicks += 1; record() }
    func record() {
        let point = window.convertPoint(toScreen: button.convert(NSPoint(x: 70, y: 15), to: nil))
        let value: [String: Any] = [
            "pid": getpid(), "clicks": clicks, "moved": moved, "closed": closed, "translated": translated,
            "frame": [window.frame.minX, window.frame.minY, window.frame.width, window.frame.height],
            "width": window.frame.width, "height": window.frame.height,
            "buttonPoint": [point.x - window.frame.minX, window.frame.maxY - point.y]
        ]
        try! JSONSerialization.data(withJSONObject: value).write(to: folder.appendingPathComponent("state.json"), options: .atomic)
    }
    func applicationDidFinishLaunching(_ notification: Notification) {
        window = NSWindow(contentRect: NSRect(x: 180, y: 160, width: 420, height: 220),
                          styleMask: [.titled, .closable], backing: .buffered, defer: false)
        window.title = "Image scope probe"
        button.frame = NSRect(x: 20, y: 40, width: 160, height: 30)
        button.target = self; button.action = #selector(click)
        window.contentView!.addSubview(button)
        panel = ImageScopePanel(contentRect: NSRect(x: 600, y: 220, width: 150, height: 80),
                                styleMask: [.titled, .closable], backing: .buffered, defer: false)
        panel.title = "Auxiliary panel"
        panel.owner = window
        panel.hidesOnDeactivate = false
        panel.becomesKeyOnlyIfNeeded = true
        window.addChildWindow(panel, ordered: .above)
        window.orderBack(nil); panel.orderBack(nil)
        window.makeKey(); window.makeMain()
        record()
        Timer.scheduledTimer(withTimeInterval: 0.05, repeats: true) { _ in
            MainActor.assumeIsolated {
                if !self.moved, FileManager.default.fileExists(atPath: self.folder.appendingPathComponent("move-panel").path) {
                    self.panel.setFrameOrigin(NSPoint(x: 780, y: 280))
                    self.moved = true
                }
                if !self.closed, FileManager.default.fileExists(atPath: self.folder.appendingPathComponent("close-panel").path) {
                    self.window.removeChildWindow(self.panel)
                    self.panel.close()
                    self.closed = true
                }
                if !self.translated, FileManager.default.fileExists(atPath: self.folder.appendingPathComponent("translate-window").path) {
                    self.window.setFrameOrigin(NSPoint(x: 440, y: 220))
                    self.translated = true
                }
                self.record()
            }
        }
    }
}

@main enum ImageScopeProbe {
    @MainActor static func main() {
        let app = NSApplication.shared
        app.setActivationPolicy(.regular)
        let delegate = ImageScopeDelegate()
        app.delegate = delegate
        app.run()
    }
}
