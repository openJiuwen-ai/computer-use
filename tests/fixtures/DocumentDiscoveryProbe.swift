import AppKit

@MainActor final class DocumentWindow: NSWindow {
    var document: String?
    var marker = ""
    override func accessibilityDocument() -> String? {
        (NSApp.delegate as? DocumentDelegate)?.record(marker)
        return document
    }
}

@MainActor final class DocumentDelegate: NSObject, NSApplicationDelegate {
    var windows = [NSWindow]()
    let root = Bundle.main.bundleURL.deletingLastPathComponent()
    func record(_ marker: String) {
        let url = root.appendingPathComponent("document-reads.jsonl")
        guard let handle = try? FileHandle(forWritingTo: url) else { return }
        defer { try? handle.close() }
        _ = try? handle.seekToEnd()
        try? handle.write(contentsOf: JSONSerialization.data(withJSONObject: ["marker": marker]) + Data([10]))
    }
    func applicationDidFinishLaunching(_ notification: Notification) {
        FileManager.default.createFile(atPath: root.appendingPathComponent("document-reads.jsonl").path, contents: nil)
        try! String(getpid()).write(to: root.appendingPathComponent("fixture.pid"), atomically: true, encoding: .utf8)
        for (index, marker) in ["old", "current", "unknown", "unique"].enumerated() {
            let window = DocumentWindow(
                contentRect: NSRect(x: 80 + index * 280, y: 220, width: 270, height: 140),
                styleMask: [.titled, .closable], backing: .buffered, defer: false)
            window.title = marker == "unique" ? "Other.txt" : "Report.txt"
            window.marker = marker
            window.document =
                marker == "unknown" ? nil : root.appendingPathComponent(marker + "/Report.txt").absoluteString
            window.isReleasedWhenClosed = false
            window.tabbingMode = .disallowed
            let label = NSTextField(labelWithString: "Document identity: " + marker)
            label.frame = NSRect(x: 20, y: 70, width: 240, height: 30)
            window.contentView!.addSubview(label)
            window.orderBack(nil)
            windows.append(window)
        }
        try! "ready".write(to: root.appendingPathComponent("ready"), atomically: true, encoding: .utf8)
    }
}

let app = NSApplication.shared
// A regular Cocoa app can activate after open -g finishes launching. Keep this
// read-only fixture accessory so startup does not confound the discovery check.
app.setActivationPolicy(.accessory)
let delegate = DocumentDelegate()
app.delegate = delegate
app.run()
