import AppKit

// Each modeless window is an independent App selection. Opening a confirmation
// from Export must update the original document's catalogue without retargeting it.
@MainActor final class CatalogueProbe: NSObject, NSApplicationDelegate {
    var windows = [NSWindow]()
    let directory = Bundle.main.bundleURL.deletingLastPathComponent()
    func window(_ title: String, button: String, action: Selector) -> NSWindow {
        let index = windows.count
        let window = NSWindow(
            contentRect: NSRect(x: 100 + index * 70, y: 140 + index * 70, width: 360, height: 140),
            styleMask: [.titled, .closable], backing: .buffered, defer: false)
        window.title = title
        window.isReleasedWhenClosed = false
        window.tabbingMode = .disallowed
        let control = NSButton(title: button, target: self, action: action)
        control.frame = NSRect(x: 20, y: 25, width: 240, height: 30)
        window.contentView!.addSubview(control)
        let input = NSTextField(string: title)
        input.frame = NSRect(x: 20, y: 80, width: 300, height: 26)
        input.setAccessibilityIdentifier("input")
        window.contentView!.addSubview(input)
        windows.append(window)
        window.orderBack(nil)
        return window
    }
    func applicationDidFinishLaunching(_ notification: Notification) {
        let menu = NSMenu(), item = NSMenuItem()
        item.submenu = NSMenu()
        item.submenu!.addItem(withTitle: "Quit", action: #selector(NSApplication.terminate(_:)), keyEquivalent: "q")
        menu.addItem(item)
        NSApp.mainMenu = menu
        let root = window("Document", button: "Show Export", action: #selector(showExport))
        root.representedURL = directory.appendingPathComponent("catalogue-document.txt")
        root.makeKey()
        root.makeMain()
        try! String(getpid()).write(
            to: directory.appendingPathComponent("fixture.pid"), atomically: true, encoding: .utf8)
    }
    @objc func showExport() { _ = window("Export", button: "Show Confirmation", action: #selector(showConfirmation)) }
    @objc func showConfirmation() { _ = window("Confirmation", button: "Confirm", action: #selector(confirm)) }
    @objc func confirm() {
        try! "confirmed".write(to: directory.appendingPathComponent("confirmed.txt"), atomically: true, encoding: .utf8)
        let values = windows.map { window in
            window.contentView!.subviews.compactMap { $0 as? NSTextField }.map(\.stringValue)
        }
        try! JSONSerialization.data(withJSONObject: values).write(to: directory.appendingPathComponent("inputs.json"))
        windows.last!.close()
    }
}
let app = NSApplication.shared
app.setActivationPolicy(.regular)
let delegate = CatalogueProbe()
app.delegate = delegate
app.run()
