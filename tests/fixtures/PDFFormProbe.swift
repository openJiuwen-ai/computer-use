import AppKit
import PDFKit

/// PDFKit's form field and its live field editor are separate AX objects.
/// Exercise the real provider, with two documents and no AX overrides.
@MainActor final class PDFFormDelegate: NSObject, NSApplicationDelegate {
    var windows = [NSWindow]()
    var documents = [String: PDFDocument]()
    var timer: Timer?
    let directory = Bundle.main.bundleURL.deletingLastPathComponent()

    func applicationDidFinishLaunching(_ notification: Notification) {
        try! String(getpid()).write(
            to: directory.appendingPathComponent("fixture.pid"), atomically: true, encoding: .utf8)
        let menu = NSMenu(), edit = NSMenu(title: "Edit")
        edit.addItem(withTitle: "Select All", action: #selector(NSText.selectAll(_:)), keyEquivalent: "a")
        edit.addItem(withTitle: "Paste", action: #selector(NSText.paste(_:)), keyEquivalent: "v")
        let item = NSMenuItem(title: "Edit", action: nil, keyEquivalent: "")
        item.submenu = edit
        menu.addItem(item)
        NSApp.mainMenu = menu
        for (offset, name) in ["A", "B"].enumerated() {
            let image = NSImage(size: NSSize(width: 500, height: 400))
            image.lockFocus()
            NSColor.white.setFill()
            NSRect(x: 0, y: 0, width: 500, height: 400).fill()
            for (index, title) in ["Customer", "Email", "Reference"].enumerated() {
                (title as NSString).draw(
                    at: NSPoint(x: 30, y: 310 - index * 90),
                    withAttributes: [.font: NSFont.systemFont(ofSize: 14), .foregroundColor: NSColor.black])
            }
            image.unlockFocus()
            let page = PDFPage(image: image)!
            for (index, field) in ["Customer", "Email", "Reference"].enumerated() {
                let annotation = PDFAnnotation(
                    bounds: NSRect(x: 30, y: 275 - index * 90, width: 420, height: 30), forType: .widget,
                    withProperties: nil)
                annotation.widgetFieldType = .text
                annotation.fieldName = field
                annotation.widgetStringValue = field == "Reference" ? "untouched-" + name : ""
                annotation.font = NSFont.systemFont(ofSize: 14)
                annotation.backgroundColor = NSColor(white: 0.92, alpha: 1)
                page.addAnnotation(annotation)
            }
            let generated = PDFDocument()
            generated.insert(page, at: 0)
            // Loading a serialized AcroForm exercises the same PDFKit field
            // provider as a file. In-memory image pages expose static text.
            let document = PDFDocument(data: generated.dataRepresentation()!)!
            documents[name] = document
            let view = PDFView(frame: NSRect(x: 0, y: 0, width: 550, height: 460))
            view.autoScales = true
            view.document = document
            let window = NSWindow(
                contentRect: view.frame, styleMask: [.titled, .closable], backing: .buffered, defer: false)
            window.title = "PDF form " + name
            window.tabbingMode = .disallowed
            window.isReleasedWhenClosed = false
            window.contentView = view
            window.setFrameOrigin(NSPoint(x: 180 + offset * 40, y: 220 + offset * 35))
            window.orderBack(nil)
            windows.append(window)
        }
        windows[0].makeKey()
        windows[0].makeMain()
        timer = Timer.scheduledTimer(withTimeInterval: 0.1, repeats: true) { [weak self] _ in
            MainActor.assumeIsolated { self?.record() }
        }
        record()
    }

    func record() {
        var values = [String: [String: String]]()
        for (name, document) in documents {
            values[name] = Dictionary(
                uniqueKeysWithValues: document.page(at: 0)!.annotations.map {
                    ($0.fieldName ?? "", $0.widgetStringValue ?? "")
                })
        }
        try! JSONSerialization.data(withJSONObject: values, options: [.sortedKeys]).write(
            to: directory.appendingPathComponent("values.json"), options: .atomic)
    }
}

let application = NSApplication.shared
application.setActivationPolicy(.regular)
let delegate = PDFFormDelegate()
application.delegate = delegate
application.run()
