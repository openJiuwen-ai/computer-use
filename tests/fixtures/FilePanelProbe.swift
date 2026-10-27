import AppKit
import UniformTypeIdentifiers

@MainActor final class FilePanelWindow: NSOpenPanel {
    override func sendEvent(_ event: NSEvent) {
        if event.type == .keyDown { FilePanelProbe.log(["kind": "panel-event", "keyCode": event.keyCode]) }
        super.sendEvent(event)
    }
    override func performKeyEquivalent(with event: NSEvent) -> Bool {
        let handled = super.performKeyEquivalent(with: event)
        FilePanelProbe.log(["kind": "panel-equivalent", "handled": handled, "keyCode": event.keyCode])
        return handled
    }
}

// Exercise AppKit's synchronous modal file panel, including its real responder
// chain. Captured event metadata belongs only to this disposable fixture.
@MainActor final class FilePanelApplication: NSApplication {
    override func sendEvent(_ event: NSEvent) {
        if [.keyDown, .keyUp, .flagsChanged].contains(event.type) {
            FilePanelProbe.log([
                "kind": "event", "type": event.type.rawValue, "keyCode": event.keyCode, "window": event.windowNumber,
                "keyWindow": keyWindow?.windowNumber ?? -1, "active": isActive,
                "modalWindow": modalWindow?.windowNumber ?? -1, "eventWindow": event.window?.windowNumber ?? -1,
                "responder": keyWindow?.firstResponder.map { NSStringFromClass(type(of: $0)) } ?? "none",
                "flags": event.modifierFlags.rawValue,
                "characters": event.type == .flagsChanged ? "" : event.characters ?? "",
                "charactersIgnoringModifiers": event.type == .flagsChanged
                    ? "" : event.charactersIgnoringModifiers ?? ""
            ])

        }
        super.sendEvent(event)
    }
}

@MainActor final class FilePanelProbe: NSObject, NSApplicationDelegate {
    var document: NSWindow!
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
    func applicationDidFinishLaunching(_ notification: Notification) {
        Self.log(["kind": "started", "pid": ProcessInfo.processInfo.processIdentifier])
        let menu = NSMenu(), item = NSMenuItem()
        item.submenu = NSMenu()
        item.submenu!.addItem(withTitle: "Quit", action: #selector(NSApplication.terminate(_:)), keyEquivalent: "q")
        menu.addItem(item)
        let file = NSMenuItem(title: "File", action: nil, keyEquivalent: "")
        file.submenu = NSMenu(title: "File")
        let saving = Bundle.main.object(forInfoDictionaryKey: "TestSavePanel") as? Bool == true
        let open = file.submenu!.addItem(
            withTitle: saving ? "Save…" : "Open…", action: #selector(openPanel), keyEquivalent: saving ? "s" : "o")
        open.target = self
        menu.addItem(file)
        NSApp.mainMenu = menu
        document = NSWindow(
            contentRect: NSRect(x: 80, y: 90, width: 360, height: 170), styleMask: [.titled, .closable],
            backing: .buffered, defer: false)
        document.title = "File Panel Host"
        let input = NSTextField(string: "Document input")
        input.frame = NSRect(x: 20, y: 90, width: 300, height: 25)
        input.setAccessibilityIdentifier("host-input")
        document.contentView!.addSubview(input)
        document.orderBack(nil)
        document.makeKey()
        document.makeMain()
        document.makeFirstResponder(input)
    }
    @objc func openPanel() {
        DispatchQueue.main.async {
            let saving = Bundle.main.object(forInfoDictionaryKey: "TestSavePanel") as? Bool == true
            let panel: NSSavePanel =
                saving
                ? NSSavePanel()
                : Bundle.main.object(forInfoDictionaryKey: "TestRemotePanel") as? Bool == true
                    ? NSOpenPanel() : FilePanelWindow()
            panel.title = "File Panel Probe"
            if let open = panel as? NSOpenPanel {
                open.canChooseDirectories = false
                open.allowsMultipleSelection = false
            } else {
                panel.allowedContentTypes = [UTType(filenameExtension: "xlsx")!]
                panel.nameFieldStringValue = "Original.xlsx"
                panel.isExtensionHidden = false
            }
            panel.directoryURL = Bundle.main.bundleURL.deletingLastPathComponent()
            Self.log(["kind": "panel", "class": NSStringFromClass(type(of: panel)), "number": panel.windowNumber])
            @MainActor func completed(_ result: NSApplication.ModalResponse) {
                Self.log(["kind": "result", "accepted": result == .OK, "path": panel.url?.path ?? ""])
                NSApp.terminate(nil)
            }
            if Bundle.main.object(forInfoDictionaryKey: "TestDocumentSheet") as? Bool == true {
                panel.beginSheetModal(for: self.document, completionHandler: completed)
            } else {
                completed(panel.runModal())
            }
        }
    }
}

let application = FilePanelApplication.shared
application.setActivationPolicy(.regular)
let delegate = FilePanelProbe()
application.delegate = delegate
application.run()
