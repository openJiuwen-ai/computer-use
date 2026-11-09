// Copyright (c) Huawei Technologies Co., Ltd. 2026. All rights reserved.

// Disposable AppKit fixture: one editor and a Command-S menu action. No
// autosave, AX automation, event injection, or custom shortcut handling.
import AppKit

let evidence = URL(fileURLWithPath: CommandLine.arguments[1], isDirectory: true)

func record(_ event: [String: Any]) {
    var row = event
    row["uptime"] = ProcessInfo.processInfo.systemUptime
    let data = try! JSONSerialization.data(withJSONObject: row, options: [.sortedKeys])
    let log = evidence.appendingPathComponent("events.jsonl")
    if !FileManager.default.fileExists(atPath: log.path) {
        FileManager.default.createFile(atPath: log.path, contents: nil)
    }
    let handle = try! FileHandle(forWritingTo: log)
    try! handle.seekToEnd()
    try! handle.write(contentsOf: data + Data([10]))
    try! handle.close()
}

class ProbeApplication: NSApplication {
    override func sendEvent(_ event: NSEvent) {
        if event.type == .keyDown || event.type == .keyUp {
            record(["kind": "key", "down": event.type == .keyDown,
                    "characters": event.charactersIgnoringModifiers ?? "",
                    "flags": event.modifierFlags.rawValue, "active": isActive,
                    "keyWindow": keyWindow?.title ?? "", "mainWindow": mainWindow?.title ?? "",
                    "window": event.windowNumber])
        }
        super.sendEvent(event)
    }
}

class ProbeWindow: NSWindow {
    @objc func saveDocument(_ sender: Any?) {
        let text = (contentView as! NSTextView).string
        try! text.write(to: evidence.appendingPathComponent("saved.txt"), atomically: true, encoding: .utf8)
        record(["kind": "save", "text": text, "active": NSApp.isActive])
    }
}

class Delegate: NSObject, NSApplicationDelegate {
    let window = ProbeWindow(contentRect: NSRect(x: 200, y: 200, width: 540, height: 280),
                          styleMask: [.titled, .closable], backing: .buffered, defer: false)
    let editor = NSTextView(frame: NSRect(x: 0, y: 0, width: 540, height: 280))
    var timer: Timer?

    func applicationDidFinishLaunching(_ notification: Notification) {
        window.title = "Q4 Save Probe " + evidence.deletingLastPathComponent().lastPathComponent
        editor.isRichText = false
        editor.string = "Q4 initial probe"
        editor.setAccessibilityIdentifier("q4-editor")
        window.contentView = editor
        window.makeFirstResponder(editor)
        let menu = NSMenu()
        let applicationItem = NSMenuItem()
        menu.addItem(applicationItem)
        let applicationMenu = NSMenu(title: "Save Probe")
        applicationMenu.addItem(withTitle: "Quit", action: #selector(NSApplication.terminate(_:)), keyEquivalent: "q")
        applicationItem.submenu = applicationMenu
        let file = NSMenuItem(title: "File", action: nil, keyEquivalent: "")
        let fileMenu = NSMenu(title: "File")
        let save = NSMenuItem(title: "Save", action: #selector(ProbeWindow.saveDocument(_:)), keyEquivalent: "s")
        save.keyEquivalentModifierMask = [.command]
        // Nil targets exercise the ordinary document responder chain; explicit
        // target mode is the control condition, not a different injection route.
        save.target = CommandLine.arguments.dropFirst(2).first == "explicit" ? window : nil
        fileMenu.addItem(save)
        file.submenu = fileMenu
        menu.addItem(file)
        NSApp.mainMenu = menu
        window.makeKeyAndOrderFront(nil)
        record(["kind": "ready", "title": window.title, "pid": ProcessInfo.processInfo.processIdentifier,
                "active": NSApp.isActive])
        let deadline = ProcessInfo.processInfo.systemUptime + 90
        timer = Timer.scheduledTimer(withTimeInterval: 0.2, repeats: true) { _ in
            if FileManager.default.fileExists(atPath: evidence.appendingPathComponent("stop").path)
                || ProcessInfo.processInfo.systemUptime >= deadline {
                record(["kind": "exit"])
                NSApp.terminate(nil)
            }
        }
    }

}

let app = ProbeApplication.shared
let delegate = Delegate()
app.setActivationPolicy(.regular)
app.delegate = delegate
app.run()
