import AppKit

// One real NSTextView owns the document and its actual selection. The virtual
// paragraph provider deliberately exposes only the local intersection of that
// selection. A local 0:0 therefore does not prove a document-wide caret.
@MainActor final class Paragraph: NSView {
    weak var owner: ProbeDocument?
    let index: Int
    let documentRange: NSRange
    var focusRequests = 0
    var rangeRequests = 0
    init(owner: ProbeDocument, index: Int, range: NSRange) {
        self.owner = owner
        self.index = index
        documentRange = range
        super.init(frame: .zero)
        setAccessibilityElement(true)
    }
    required init?(coder: NSCoder) { fatalError("init(coder:) not implemented") }
    var recorder: ProbeDelegate? { owner?.recorder }
    var name: String { ["Heading", "Empty paragraph", "Later paragraph"][index] }
    var text: String { (owner!.string as NSString).substring(with: documentRange) }
    override func accessibilityRole() -> NSAccessibility.Role? { .textArea }
    override func accessibilityIdentifier() -> String { "paragraph-\(index)" }
    override func accessibilityLabel() -> String? { name }
    override func accessibilityParent() -> Any? { owner }
    override func accessibilityWindow() -> Any? { owner?.window }
    override func accessibilityTopLevelUIElement() -> Any? { owner?.window }
    override func accessibilityChildren() -> [Any]? { [] }
    override func accessibilityFrame() -> NSRect { .zero }
    override func isAccessibilityEnabled() -> Bool { true }
    override func accessibilityValue() -> Any? { recorder?.mode == "unknown-value" && index == 1 ? nil : text }
    override func accessibilityNumberOfCharacters() -> Int { documentRange.length }
    override func accessibilityVisibleCharacterRange() -> NSRange { NSRange(location: 0, length: documentRange.length) }
    override func accessibilitySelectedTextRange() -> NSRange {
        guard let owner else { return NSRange(location: NSNotFound, length: 0) }
        let selected = owner.selectedRange()
        let start = max(documentRange.location, min(NSMaxRange(documentRange), selected.location))
        let end = max(start, min(NSMaxRange(documentRange), NSMaxRange(selected)))
        return NSRange(location: start - documentRange.location, length: end - start)
    }
    override func accessibilitySelectedText() -> String? {
        (text as NSString).substring(with: accessibilitySelectedTextRange())
    }
    override func isAccessibilityFocused() -> Bool {
        recorder?.focusedParagraph == index && owner?.window?.firstResponder === owner
    }
    override func setAccessibilityFocused(_ value: Bool) {
        guard value, let owner, let recorder else { return }
        focusRequests += 1
        recorder.focusedParagraph = index
        let accepted = owner.window?.makeFirstResponder(owner) ?? false
        if accepted {
            // Move the active end to this paragraph, keeping the old heading
            // anchor. This changes NSTextView's real highlighted selection.
            let anchor = recorder.selectionAnchor
            let end = documentRange.location
            owner.setSelectedRange(NSRange(location: min(anchor, end), length: abs(end - anchor)))
        }
        recorder.record("paragraph-focus", extra: ["paragraph": index, "accepted": accepted])
        NSAccessibility.post(element: self, notification: .focusedUIElementChanged)
    }
    override func setAccessibilitySelectedTextRange(_ range: NSRange) {
        guard let owner, let recorder else { return }
        rangeRequests += 1
        guard range.location != NSNotFound, range.location >= 0, range.length >= 0,
            range.location <= documentRange.length, range.length <= documentRange.length - range.location
        else {
            recorder.record("range-invalid", extra: ["paragraph": index])
            return
        }
        // A modern Void AX setter cannot promise an AXError. This controlled
        // negative case refuses the real change and records that fact, so API
        // success plus the already-local 0:0 cannot fake a passing test.
        guard recorder.mode != "setter-refuse", recorder.mode != "readonly" else {
            recorder.record("range-refused", extra: ["paragraph": index])
            return
        }
        let absolute = NSRange(location: documentRange.location + range.location, length: range.length)
        recorder.selectionAnchor = absolute.location
        owner.setSelectedRange(absolute)
        recorder.record("range-applied", extra: ["paragraph": index, "requestedLocalRange": rangeJSON(range)])
        NSAccessibility.post(element: self, notification: .selectedTextChanged)
    }
    override func isAccessibilitySelectorAllowed(_ selector: Selector) -> Bool {
        let name = NSStringFromSelector(selector)
        if [
            "accessibilityFrame", "setAccessibilityValue:", "setAccessibilitySelectedText:",
            "accessibilityPerformPress", "accessibilityPerformPick", "accessibilityPerformConfirm",
            "accessibilityRangeForPosition:", "accessibilityFrameForRange:"
        ].contains(name) {
            return false
        }
        if ["unsupported", "readonly"].contains(recorder?.mode ?? ""), name == "setAccessibilitySelectedTextRange:" {
            return false
        }
        if recorder?.mode == "unknown-value", index == 1,
            ["accessibilityValue", "accessibilityNumberOfCharacters", "accessibilityVisibleCharacterRange"].contains(
                name)
        {
            return false
        }
        return super.isAccessibilitySelectorAllowed(selector)
    }
}

@MainActor final class ProbeDocument: NSTextView {
    weak var recorder: ProbeDelegate?
    var paragraphs: [Paragraph] = []
    override func accessibilityRole() -> NSAccessibility.Role? { .group }
    override func accessibilityChildren() -> [Any]? { paragraphs }
    override func accessibilityVisibleChildren() -> [Any]? { paragraphs }
    override func accessibilityIdentifier() -> String { "real-document" }
}

@MainActor final class ProbeApplication: NSApplication {
    override func accessibilityFocusedWindow() -> Any? { (delegate as? ProbeDelegate)?.window }
    override func accessibilityApplicationFocusedUIElement() -> Any? {
        guard let probe = delegate as? ProbeDelegate, probe.document.paragraphs.indices.contains(probe.focusedParagraph)
        else { return super.accessibilityApplicationFocusedUIElement() }
        return probe.document.paragraphs[probe.focusedParagraph]
    }
    override func sendEvent(_ event: NSEvent) {
        if let probe = delegate as? ProbeDelegate {
            if [.keyDown, .keyUp, .flagsChanged].contains(event.type) {
                probe.keyEvents += 1
                probe.record("key-event", extra: ["type": event.type.rawValue, "keyCode": event.keyCode])
            }
            if [
                .leftMouseDown, .leftMouseUp, .leftMouseDragged, .rightMouseDown, .rightMouseUp, .rightMouseDragged,
                .otherMouseDown, .otherMouseUp, .otherMouseDragged, .scrollWheel, .mouseMoved
            ].contains(event.type) {
                probe.pointerEvents += 1
                probe.record("pointer-event", extra: ["type": event.type.rawValue])
            }
        }
        super.sendEvent(event)
    }
}

func rangeJSON(_ range: NSRange) -> [String: Int] { ["location": range.location, "length": range.length] }

@MainActor final class ProbeDelegate: NSObject, NSApplicationDelegate, NSTextViewDelegate {
    let directory = Bundle.main.bundleURL.deletingLastPathComponent()
    let mode = Bundle.main.object(forInfoDictionaryKey: "ProbeMode") as? String ?? "normal"
    let original = "Fixture heading\n\nLater paragraph stays unchanged."
    let document = ProbeDocument()
    var window: NSWindow!
    var focusedParagraph = 0
    var selectionAnchor = 0
    var keyEvents = 0
    var pointerEvents = 0
    var ready = false
    func record(_ event: String, extra: [String: Any] = [:]) {
        guard ready else { return }
        let selected = document.selectedRange()
        let values: [String: Any] = [
            "mode": mode, "pid": getpid(), "documentText": document.string, "originalText": original,
            "textUnchanged": document.string == original, "documentSelectedRange": rangeJSON(selected),
            "documentSelectedText": (document.string as NSString).substring(with: selected),
            "selectionAnchor": selectionAnchor, "focusedParagraph": focusedParagraph,
            "actualFirstResponderIsDocument": window.firstResponder === document, "windowKey": window.isKeyWindow,
            "keyEvents": keyEvents, "pointerEvents": pointerEvents,
            "paragraphs": document.paragraphs.map { p in
                [
                    "index": p.index, "value": p.text, "documentRange": rangeJSON(p.documentRange),
                    "localSelectedRange": rangeJSON(p.accessibilitySelectedTextRange()),
                    "focusRequests": p.focusRequests, "rangeRequests": p.rangeRequests
                ] as [String: Any]
            }
        ]
        try! JSONSerialization.data(withJSONObject: values, options: [.sortedKeys]).write(
            to: directory.appendingPathComponent("values.json"), options: .atomic)
        var row = values
        row["event"] = event
        row["time"] = Date().timeIntervalSince1970
        row.merge(extra, uniquingKeysWith: { _, new in new })
        let data = try! JSONSerialization.data(withJSONObject: row, options: [.sortedKeys]) + Data([10])
        let url = directory.appendingPathComponent("events.jsonl")
        if let handle = try? FileHandle(forWritingTo: url) {
            _ = try? handle.seekToEnd()
            try? handle.write(contentsOf: data)
            try? handle.close()
        } else {
            try! data.write(to: url)
        }
    }
    func textViewDidChangeSelection(_ notification: Notification) { record("real-selection-changed") }
    func textDidChange(_ notification: Notification) { record("real-text-changed") }
    func applicationDidFinishLaunching(_ notification: Notification) {
        precondition(["normal", "unsupported", "setter-refuse", "unknown-value", "readonly"].contains(mode))
        window = NSWindow(
            contentRect: NSRect(x: 140, y: 140, width: 620, height: 320), styleMask: [.titled, .closable],
            backing: .buffered, defer: false)
        window.title = "Empty paragraph selection: \(mode)"
        window.tabbingMode = .disallowed
        window.isReleasedWhenClosed = false
        document.frame = NSRect(x: 20, y: 30, width: 580, height: 265)
        document.recorder = self
        document.delegate = self
        document.isRichText = false
        document.font = .systemFont(ofSize: 24)
        document.textContainerInset = NSSize(width: 14, height: 18)
        document.isEditable = mode != "readonly"
        document.isSelectable = true
        document.string = original
        let headingLength = ("Fixture heading" as NSString).length
        let emptyStart = headingLength + 1
        let endStart = emptyStart + 1
        document.paragraphs = [
            NSRange(location: 0, length: headingLength), NSRange(location: emptyStart, length: 0),
            NSRange(location: endStart, length: (original as NSString).length - endStart)
        ].enumerated().map { Paragraph(owner: document, index: $0.offset, range: $0.element) }
        window.contentView?.addSubview(document)
        let menu = NSMenu(), item = NSMenuItem()
        item.submenu = NSMenu()
        item.submenu!.addItem(withTitle: "Quit", action: #selector(NSApplication.terminate(_:)), keyEquivalent: "q")
        menu.addItem(item)
        NSApp.mainMenu = menu
        window.orderBack(nil)
        window.makeKey()
        window.makeMain()
        window.makeFirstResponder(document)
        document.setSelectedRange(NSRange(location: 0, length: 7))
        ready = true
        try! String(getpid()).write(
            to: directory.appendingPathComponent("fixture.pid"), atomically: true, encoding: .utf8)
        record("ready")
        Timer.scheduledTimer(withTimeInterval: 180, repeats: false) { _ in
            MainActor.assumeIsolated { NSApp.terminate(nil) }
        }
    }
    func applicationShouldTerminateAfterLastWindowClosed(_ sender: NSApplication) -> Bool { true }
}

let application = ProbeApplication.shared
application.setActivationPolicy(.regular)
let delegate = ProbeDelegate()
application.delegate = delegate
application.run()
