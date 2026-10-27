import AppKit

/// A title's mouse handler is independent of its accessibility action list.
/// The default native title remains AXStaticText with no AXPress. Count the
/// unrequested interaction without changing normal AppKit event processing.
@MainActor final class DocumentTitleWindow: NSWindow { var titleClicks = 0 }

@MainActor final class DocumentTitleApplication: NSApplication {
    var clicks = [[String: Any]]()
    override func sendEvent(_ event: NSEvent) {
        if event.type == .leftMouseDown, let window = event.window as? DocumentTitleWindow {
            let title = window.accessibilityTitleUIElement() as? NSObject
            let position = (title?.accessibilityAttributeValue(.position) as? NSValue)?.pointValue
            let size = (title?.accessibilityAttributeValue(.size) as? NSValue)?.sizeValue
            let frame = position.flatMap { origin in size.map { NSRect(origin: origin, size: $0) } }
            let point = window.convertPoint(toScreen: event.locationInWindow)
            clicks.append([
                "title": String(describing: title), "frame": String(describing: frame),
                "titleGeometryAvailable": frame != nil, "point": NSStringFromPoint(point),
                "location": NSStringFromPoint(event.locationInWindow)
            ])
            if frame?.contains(point) == true { window.titleClicks += 1 }
        }
        super.sendEvent(event)
    }
}

/// Ordinary NSDocument title controls, with no custom accessibility overrides.
/// An edited document's native title can open a rename popover even though its
/// AXStaticText title advertises no actions. Save must reach the document.
@MainActor final class TitleDocument: NSDocument, NSTextViewDelegate {
    let output: URL
    let editor = NSTextView()
    var writes = 0
    init(output: URL) {
        self.output = output
        super.init()
        fileURL = output.appendingPathComponent("target.txt")
        fileType = "public.plain-text"
    }
    override class var autosavesInPlace: Bool { true }
    override func makeWindowControllers() {
        let window = DocumentTitleWindow(
            contentRect: NSRect(x: 180, y: 220, width: 620, height: 300), styleMask: [.titled, .closable, .resizable],
            backing: .buffered, defer: false)
        window.tabbingMode = .disallowed
        window.isReleasedWhenClosed = false
        editor.frame = window.contentView!.bounds
        editor.autoresizingMask = [.width, .height]
        editor.isRichText = false
        editor.string = "ORIGINAL"
        editor.setAccessibilityIdentifier("document-title-input")
        editor.delegate = self
        window.contentView!.addSubview(editor)
        addWindowController(NSWindowController(window: window))
        window.orderBack(nil)
        window.makeFirstResponder(editor)
        editor.setSelectedRange(NSRange(location: editor.string.utf16.count, length: 0))
    }
    override func data(ofType typeName: String) throws -> Data {
        writes += 1
        return Data(editor.string.utf8)
    }
    func textDidChange(_ notification: Notification) { updateChangeCount(.changeDone) }
}

@MainActor final class DocumentTitleDelegate: NSObject, NSApplicationDelegate {
    let output = Bundle.main.bundleURL.deletingLastPathComponent()
    var document: TitleDocument!
    var guardWindow: NSWindow!
    let guardField = NSTextField(string: "GUARD")
    func applicationDidFinishLaunching(_ notification: Notification) {
        let root = NSMenu()
        let applicationItem = NSMenuItem()
        applicationItem.submenu = NSMenu(title: "Document Title Probe")
        root.addItem(applicationItem)
        let file = NSMenuItem(title: "File", action: nil, keyEquivalent: "")
        file.submenu = NSMenu(title: "File")
        file.submenu!.addItem(NSMenuItem(title: "Save", action: #selector(NSDocument.save(_:)), keyEquivalent: "s"))
        root.addItem(file)
        NSApp.mainMenu = root
        guardWindow = NSWindow(
            contentRect: NSRect(x: 850, y: 220, width: 300, height: 180), styleMask: [.titled, .closable],
            backing: .buffered, defer: false)
        guardWindow.title = "Unrelated document"
        guardWindow.isReleasedWhenClosed = false
        guardField.frame = NSRect(x: 20, y: 70, width: 250, height: 30)
        guardWindow.contentView!.addSubview(guardField)
        guardWindow.orderBack(nil)
        guardWindow.makeKey()
        guardWindow.makeMain()
        guardWindow.makeFirstResponder(guardField)
        NSDocumentController.shared.autosavingDelay = 3600
        document = TitleDocument(output: output)
        NSDocumentController.shared.addDocument(document)
        document.makeWindowControllers()
        file.submenu!.items[0].target = document
        record()
        Timer.scheduledTimer(withTimeInterval: 0.05, repeats: true) { _ in MainActor.assumeIsolated { self.record() } }
    }
    func record() {
        let state: [String: Any] = [
            "pid": getpid(), "text": document.editor.string, "guard": guardField.stringValue, "writes": document.writes,
            "edited": document.isDocumentEdited, "keyWindow": NSApp.keyWindow?.windowNumber ?? 0,
            "mainWindow": NSApp.mainWindow?.windowNumber ?? 0,
            "nativeEdited": document.windowControllers[0].window!.isDocumentEdited,
            "titleClicks": (document.windowControllers[0].window as! DocumentTitleWindow).titleClicks,
            "clicks": (NSApp as! DocumentTitleApplication).clicks,
            "targetWindow": document.windowControllers[0].window!.windowNumber, "active": NSApp.isActive,
            "foreground": NSWorkspace.shared.frontmostApplication?.processIdentifier ?? 0
        ]
        try! JSONSerialization.data(withJSONObject: state, options: [.sortedKeys]).write(
            to: output.appendingPathComponent("state.json"), options: .atomic)
    }
}

let application = DocumentTitleApplication.shared
application.setActivationPolicy(.regular)
let delegate = DocumentTitleDelegate()
application.delegate = delegate
application.run()
