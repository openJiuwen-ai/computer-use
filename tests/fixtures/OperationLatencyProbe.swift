// Copyright (c) Huawei Technologies Co., Ltd. 2026. All rights reserved.

import AppKit

/// Independent documents and an ordered-out AX-only window. The callback state
/// is independent of the SDK's observations and action receipts.
@MainActor final class ListingApplication: NSApplication {
    var windowReads = 0
    override func accessibilityWindows() -> [Any]? {
        windowReads += 1
        return (delegate as? ListingDelegate)?.documents.filter { !$0.closed }.map(\.window)
    }
}

@MainActor final class ListingDocument: NSObject {
    let marker: String
    let window: NSWindow
    let field = NSTextField(string: "")
    weak var owner: ListingDelegate?
    var clicks = 0, closed = false
    init(_ marker: String, hidden: Bool, owner: ListingDelegate) {
        self.marker = marker
        self.owner = owner
        let index = owner.documents.count
        window = NSWindow(
            contentRect: NSRect(x: 120 + index * 310, y: 250, width: 300, height: 210),
            styleMask: [.titled, .closable], backing: .buffered, defer: false)
        super.init()
        window.title = "Discovery document " + marker
        window.isReleasedWhenClosed = false
        window.tabbingMode = .disallowed
        window.representedURL = owner.directory.appendingPathComponent(marker + ".txt")
        field.stringValue = "original " + marker
        field.frame = NSRect(x: 20, y: 145, width: 260, height: 28)
        field.setAccessibilityLabel("Document input")
        field.setAccessibilityIdentifier("document-input")
        let mark = NSButton(title: "Mark document", target: self, action: #selector(markDocument))
        mark.frame = NSRect(x: 20, y: 90, width: 160, height: 32)
        let create = NSButton(title: "New document", target: self, action: #selector(newDocument))
        create.frame = NSRect(x: 20, y: 40, width: 160, height: 32)
        for view in [field, mark, create] { window.contentView!.addSubview(view) }
        NotificationCenter.default.addObserver(
            self, selector: #selector(didClose), name: NSWindow.willCloseNotification,
            object: window)
        if !hidden { window.orderBack(nil) }
    }
    @objc func markDocument() {
        clicks += 1
        owner?.record()
    }
    @objc func newDocument() { owner?.createDocument("C") }
    @objc func didClose() {
        closed = true
        owner?.record()
    }
}

@MainActor final class ListingDelegate: NSObject, NSApplicationDelegate {
    let directory = Bundle.main.bundleURL.deletingLastPathComponent()
    var documents = [ListingDocument]()
    func createDocument(_ marker: String, hidden: Bool = false) {
        guard !documents.contains(where: { $0.marker == marker }) else { return }
        documents.append(ListingDocument(marker, hidden: hidden, owner: self))
        record()
    }
    func record() {
        let value: [String: Any] = [
            "pid": getpid(), "active": NSApp.isActive,
            "windowReads": (NSApp as! ListingApplication).windowReads,
            "documents": Dictionary(
                uniqueKeysWithValues: documents.map { doc in
                    (
                        doc.marker,
                        [
                            "value": doc.field.stringValue, "clicks": doc.clicks,
                            "closed": doc.closed,
                            "visible": doc.window.isVisible,
                        ] as [String: Any]
                    )
                }),
        ]
        try! JSONSerialization.data(withJSONObject: value).write(
            to: directory.appendingPathComponent("state.json"), options: .atomic)
    }
    func applicationDidFinishLaunching(_ notification: Notification) {
        NSWindow.allowsAutomaticWindowTabbing = false
        createDocument("A")
        createDocument("B")
        createDocument("AX-only", hidden: true)
        documents[0].window.makeKey()
        documents[0].window.makeMain()
        Timer.scheduledTimer(withTimeInterval: 0.05, repeats: true) { _ in
            MainActor.assumeIsolated { self.record() }
        }
    }
}

@main enum ApplicationListingProbe {
    @MainActor static func main() {
        let app = ListingApplication.shared
        app.setActivationPolicy(.accessory)
        let delegate = ListingDelegate()
        app.delegate = delegate
        app.run()
    }
}
