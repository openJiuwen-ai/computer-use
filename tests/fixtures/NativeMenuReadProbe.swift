import AppKit

/// A real application menu with two independent documents. Control files only
/// change fixture setup; the acceptance script never uses them to read menus.
@MainActor final class MenuReadDelegate: NSObject, NSApplicationDelegate, NSMenuDelegate {
    let directory = Bundle.main.bundleURL.deletingLastPathComponent()
    var windows: [NSWindow] = []
    var windowIDs = Set<Int>()
    var closing = false
    var callbacks = 0, menuReadCallbacks = 0, activations = 0, inputEvents = 0
    var monitor: Any?

    func applicationDidFinishLaunching(_ notification: Notification) {
        NSWindow.allowsAutomaticWindowTabbing = false
        let bar = NSMenu()
        let appMenu = NSMenu(title: "Probe")
        let appItem = NSMenuItem(title: "Probe", action: nil, keyEquivalent: "")
        appItem.submenu = appMenu
        bar.addItem(appItem)
        let file = NSMenu(title: "File")
        file.delegate = self
        let fileItem = NSMenuItem(title: "File", action: nil, keyEquivalent: "")
        fileItem.submenu = file
        bar.addItem(fileItem)
        let open = command("Open fixture")
        open.keyEquivalent = "o"
        file.addItem(open)
        let shifted = command("Shifted command")
        shifted.keyEquivalent = "s"
        shifted.keyEquivalentModifierMask = [.command, .shift]
        file.addItem(shifted)
        let control = command("Control command")
        control.keyEquivalent = "a"
        control.keyEquivalentModifierMask = [.control]
        file.addItem(control)
        let recent = NSMenuItem(title: "Recent", action: nil, keyEquivalent: "")
        recent.submenu = NSMenu(title: "Recent")
        recent.submenu!.delegate = self
        recent.submenu!.addItem(command("Document"))
        file.addItem(recent)
        let ambiguous = NSMenuItem(title: "Ambiguous", action: nil, keyEquivalent: "")
        ambiguous.submenu = NSMenu(title: "Ambiguous")
        ambiguous.submenu!.delegate = self
        ambiguous.submenu!.addItem(command("Repeated"))
        ambiguous.submenu!.addItem(command("Repeated"))
        file.addItem(ambiguous)
        NSApp.mainMenu = bar
        for (index, title) in ["Document A", "Document B"].enumerated() {
            let window = NSWindow(
                contentRect: NSRect(x: 80 + index * 420, y: 240, width: 380, height: 180),
                styleMask: [.titled, .closable], backing: .buffered, defer: false)
            window.title = title
            window.isReleasedWhenClosed = false
            let label = NSTextField(labelWithString: title + " content")
            label.frame = NSRect(x: 30, y: 70, width: 300, height: 30)
            window.contentView!.addSubview(label)
            window.orderBack(nil)
            windows.append(window)
            windowIDs.insert(window.windowNumber)
        }
        monitor = NSEvent.addLocalMonitorForEvents(matching: [.keyDown, .keyUp, .leftMouseDown, .rightMouseDown]) {
            event in
            MainActor.assumeIsolated {
                self.inputEvents += 1
                self.save()
            }
            return event
        }
        save()
        Timer.scheduledTimer(withTimeInterval: 0.05, repeats: true) { _ in
            MainActor.assumeIsolated {
                let command = self.directory.appendingPathComponent("close-windows")
                if FileManager.default.fileExists(atPath: command.path) {
                    try? FileManager.default.removeItem(at: command)
                    self.closing = true
                    self.windows.forEach { $0.close() }
                    self.windows.removeAll()
                }
                if self.closing { self.save() }
            }
        }
        Timer.scheduledTimer(withTimeInterval: 180, repeats: false) { _ in
            MainActor.assumeIsolated { NSApp.terminate(nil) }
        }
    }

    func command(_ title: String) -> NSMenuItem {
        let item = NSMenuItem(title: title, action: #selector(invoke), keyEquivalent: "")
        item.target = self
        return item
    }
    @objc func invoke() {
        callbacks += 1
        save()
    }
    // AppKit also invokes this when AX materializes a submenu. Keep it as
    // evidence; it does not itself prove a menu was displayed or clicked.
    func menuWillOpen(_ menu: NSMenu) {
        menuReadCallbacks += 1
        save()
    }
    func applicationDidBecomeActive(_ notification: Notification) {
        activations += 1
        save()
    }
    func applicationShouldTerminateAfterLastWindowClosed(_ sender: NSApplication) -> Bool { false }
    func save() {
        let native = CGWindowListCopyWindowInfo(.optionOnScreenOnly, kCGNullWindowID) as? [[String: Any]] ?? []
        let visibleDocuments = native.filter { info in
            (info[kCGWindowOwnerPID as String] as? Int) == Int(ProcessInfo.processInfo.processIdentifier)
                && (info[kCGWindowNumber as String] as? Int).map { windowIDs.contains($0) } == true
        }.count
        let data: [String: Any] = [
            "pid": ProcessInfo.processInfo.processIdentifier, "callbacks": callbacks,
            "menuReadCallbacks": menuReadCallbacks, "activations": activations, "inputEvents": inputEvents,
            "windows": windows.filter { $0.isVisible }.map { $0.title },
            "visibleWindows": NSApp.windows.filter { $0.isVisible }.map { $0.title },
            "visibleDocumentWindows": visibleDocuments
        ]
        try! JSONSerialization.data(withJSONObject: data, options: [.sortedKeys]).write(
            to: directory.appendingPathComponent("fixture.json"), options: .atomic)
    }
}

let app = NSApplication.shared
let delegate = MenuReadDelegate()
app.setActivationPolicy(.regular)
app.delegate = delegate
app.run()
