import AppKit

@MainActor final class CaptureIndicatorProbe: NSObject, NSApplicationDelegate {
    var windows: [NSWindow] = []
    var count = 0
    let directory = URL(fileURLWithPath: CommandLine.arguments[0]).deletingLastPathComponent()
        .deletingLastPathComponent().deletingLastPathComponent().deletingLastPathComponent()
    func applicationDidFinishLaunching(_ notification: Notification) {
        for (i, title) in ["First window", "Second window"].enumerated() {
            let window = NSWindow(
                contentRect: NSRect(x: 80 + i * 430, y: 350, width: 400, height: 210),
                styleMask: [.titled, .closable, .miniaturizable], backing: .buffered, defer: false)
            window.title = title
            window.isReleasedWhenClosed = false
            let button = NSButton(title: "Increment test counter", target: self, action: #selector(increment))
            button.frame = NSRect(x: 40, y: 80, width: 270, height: 35)
            window.contentView!.addSubview(button)
            window.orderFrontRegardless()
            windows.append(window)
        }
        try! String(getpid()).write(
            to: directory.appendingPathComponent("fixture.pid"), atomically: true, encoding: .utf8)
        record()
    }
    @objc func increment() {
        count += 1
        record()
    }
    func record() {
        try! String(count).write(to: directory.appendingPathComponent("counter.txt"), atomically: true, encoding: .utf8)
    }
}
let app = NSApplication.shared
app.setActivationPolicy(.regular)
let delegate = CaptureIndicatorProbe()
app.delegate = delegate
app.run()
