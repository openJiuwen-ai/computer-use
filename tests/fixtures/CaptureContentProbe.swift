import AppKit
import WebKit

// Three overlapping windows with independently known pixels. Capturing a
// background window must not substitute the covering window or reject black.
@MainActor final class ColorChart: NSView {
    override var isFlipped: Bool { true }
    override func draw(_ dirtyRect: NSRect) {
        for (index, color) in [NSColor.red, .green, .blue].enumerated() {
            color.setFill()
            NSRect(x: CGFloat(index) * bounds.width / 3, y: 0, width: bounds.width / 3, height: bounds.height).fill()
        }
    }
}

@MainActor final class CaptureContentProbe: NSObject, NSApplicationDelegate, WKNavigationDelegate {
    let directory = Bundle.main.bundleURL.deletingLastPathComponent()
    var windows: [NSWindow] = []
    var webReady = false

    func applicationDidFinishLaunching(_ notification: Notification) {
        let chart = window("Native color chart")
        chart.contentView = ColorChart(frame: chart.contentView!.bounds)
        let black = window("Black canvas")
        black.backgroundColor = .black
        let web = window("Web color chart")
        let view = WKWebView(frame: web.contentView!.bounds)
        view.navigationDelegate = self
        web.contentView = view
        view.loadHTMLString(
            """
            <!doctype html><meta charset="utf-8"><title>Capture web chart</title>
            <style>html,body{margin:0;width:100%;height:100%;display:flex}
            div{width:33.333333%;height:100%}</style>
            <div style="background:#00ffff" aria-label="cyan"></div>
            <div style="background:#ff00ff" aria-label="magenta"></div>
            <div style="background:#ffff00" aria-label="yellow"></div>
            """, baseURL: nil)
        // Do not activate or change the user's key window. These windows remain
        // behind existing applications and overlap each other intentionally.
        for window in windows { window.orderBack(nil) }
        record()
    }

    func window(_ title: String) -> NSWindow {
        let window = NSWindow(
            contentRect: NSRect(x: 120, y: 200, width: 480, height: 240), styleMask: [.titled, .closable],
            backing: .buffered, defer: false)
        window.title = title
        window.tabbingMode = .disallowed
        window.isReleasedWhenClosed = false
        windows.append(window)
        return window
    }

    func webView(_ webView: WKWebView, didFinish navigation: WKNavigation!) {
        webReady = true
        record()
    }

    func record() {
        let rows: [[String: Any]] = windows.map { window in
            [
                "title": window.title, "number": window.windowNumber, "width": window.frame.width,
                "height": window.frame.height, "contentHeight": window.contentView!.bounds.height
            ]
        }
        try! JSONSerialization.data(
            withJSONObject: ["pid": getpid(), "webReady": webReady, "active": NSApp.isActive, "windows": rows],
            options: [.sortedKeys]
        ).write(to: directory.appendingPathComponent("state.json"), options: .atomic)
    }
}

let app = NSApplication.shared
app.setActivationPolicy(.regular)
let delegate = CaptureContentProbe()
app.delegate = delegate
app.run()
