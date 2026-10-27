import AppKit

/// Document commands deliberately have no explicit target. Their callbacks
/// reveal AppKit's actual responder-chain recipient, independently of AX.
@MainActor final class MenuDocument: NSWindow {
    weak var recorder: MenuActionDelegate?
    var commands = 0
    var restrictedCommands = 0
    var textField: NSTextField!

    @objc func recordDocument(_ sender: Any?) {
        commands += 1
        recorder?.events.append(["command": "document", "document": title])
        recorder?.save()
    }
    @objc func onlyDocumentA(_ sender: Any?) {
        restrictedCommands += 1
        recorder?.events.append(["command": "restricted", "document": title])
        recorder?.save()
    }
    override func validateMenuItem(_ item: NSMenuItem) -> Bool {
        item.action != #selector(onlyDocumentA(_:)) || title == "Document A"
    }
}

@MainActor final class MenuActionDelegate: NSObject, NSApplicationDelegate, NSMenuDelegate {
    let directory = Bundle.main.bundleURL.deletingLastPathComponent()
    var documents: [MenuDocument] = []
    var events: [[String: String]] = []
    var applicationCommands = 0, activations = 0, inputEvents = 0, menuReadCallbacks = 0
    var monitor: Any?
    var windowIDs = Set<Int>()

    func applicationDidFinishLaunching(_ notification: Notification) {
        NSWindow.allowsAutomaticWindowTabbing = false
        let bar = NSMenu()
        let appItem = NSMenuItem(title: "Probe", action: nil, keyEquivalent: "")
        appItem.submenu = NSMenu(title: "Probe")
        bar.addItem(appItem)
        let global = NSMenuItem(
            title: "Application action", action: #selector(recordApplication(_:)), keyEquivalent: "")
        global.target = self
        appItem.submenu!.addItem(global)
        let file = NSMenu(title: "File")
        file.delegate = self
        let fileItem = NSMenuItem(title: "File", action: nil, keyEquivalent: "")
        fileItem.submenu = file
        bar.addItem(fileItem)
        file.addItem(
            NSMenuItem(title: "Record document", action: #selector(MenuDocument.recordDocument(_:)), keyEquivalent: ""))
        file.addItem(
            NSMenuItem(title: "Only document A", action: #selector(MenuDocument.onlyDocumentA(_:)), keyEquivalent: ""))
        let disabled = NSMenuItem(title: "Always disabled", action: nil, keyEquivalent: "")
        file.addItem(disabled)
        let nested = NSMenu(title: "Advanced")
        nested.delegate = self
        let nestedItem = NSMenuItem(title: "Advanced", action: nil, keyEquivalent: "")
        nestedItem.submenu = nested
        file.addItem(nestedItem)
        nested.addItem(
            NSMenuItem(title: "Nested document", action: #selector(MenuDocument.recordDocument(_:)), keyEquivalent: ""))
        NSApp.mainMenu = bar
        for (index, title) in ["Document A", "Document B"].enumerated() {
            let window = MenuDocument(
                contentRect: NSRect(x: 80 + index * 420, y: 240, width: 380, height: 180),
                styleMask: [.titled, .closable], backing: .buffered, defer: false)
            window.title = title
            window.isReleasedWhenClosed = false
            window.recorder = self
            let field = NSTextField(string: title + " content")
            field.frame = NSRect(x: 30, y: 70, width: 300, height: 30)
            field.setAccessibilityIdentifier("document-text")
            window.contentView!.addSubview(field)
            window.textField = field
            window.orderBack(nil)
            documents.append(window)
            windowIDs.insert(window.windowNumber)
        }
        // Cold windows: no makeKey, makeMain, activation or setup click.
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
                let close = self.directory.appendingPathComponent("close-windows")
                if FileManager.default.fileExists(atPath: close.path) {
                    try? FileManager.default.removeItem(at: close)
                    self.documents.forEach { $0.close() }
                }
                // Continuously record the real key/main windows even when only AX
                // preparation (and no fixture action callback) has happened.
                self.save()
            }
        }
        Timer.scheduledTimer(withTimeInterval: 180, repeats: false) { _ in
            MainActor.assumeIsolated { NSApp.terminate(nil) }
        }
    }
    @objc func recordApplication(_ sender: Any?) {
        applicationCommands += 1
        save()
    }
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
            "pid": ProcessInfo.processInfo.processIdentifier, "applicationCommands": applicationCommands,
            "activations": activations, "inputEvents": inputEvents, "menuReadCallbacks": menuReadCallbacks,
            "events": events, "mainWindow": NSApp.mainWindow?.title ?? NSNull() as Any,
            "keyWindow": NSApp.keyWindow?.title ?? NSNull() as Any, "visibleDocumentWindows": visibleDocuments,
            "documents": documents.map {
                [
                    "title": $0.title, "visible": $0.isVisible, "commands": $0.commands,
                    "restrictedCommands": $0.restrictedCommands, "text": $0.textField.stringValue
                ] as [String: Any]
            }
        ]
        if let data = try? JSONSerialization.data(withJSONObject: data, options: [.sortedKeys]) {
            try? data.write(to: directory.appendingPathComponent("fixture.json"), options: .atomic)
        }
    }
}

let app = NSApplication.shared
let delegate = MenuActionDelegate()
app.setActivationPolicy(.regular)
app.delegate = delegate
app.run()
