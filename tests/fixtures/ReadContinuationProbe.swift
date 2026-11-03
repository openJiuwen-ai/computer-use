// Copyright (c) Huawei Technologies Co., Ltd. 2026. All rights reserved.

import AppKit
import CryptoKit

/// A document review workspace: a long editable document and 140 section actions.
/// The section group exposes the ordinary, stable NSButton children of its view.
@MainActor final class ReviewSections: NSView {
    override var isFlipped: Bool { true }
    override nonisolated func accessibilityRole() -> NSAccessibility.Role? { .group }
    override nonisolated func accessibilityLabel() -> String? { "Document sections" }
    override nonisolated func accessibilityIdentifier() -> String { "read-sections" }
    override nonisolated func accessibilityChildren() -> [Any]? {
        MainActor.assumeIsolated { subviews }
    }
}

@MainActor final class ReadContinuationDelegate: NSObject, NSApplicationDelegate {
    let folder = Bundle.main.bundleURL.deletingLastPathComponent()
    let title = NSTextField(string: "Original review title")
    let guardField = NSTextField(string: "Unrelated draft")
    let document = NSTextView(frame: NSRect(x: 0, y: 0, width: 460, height: 15000))
    let sections = ReviewSections(frame: NSRect(x: 0, y: 0, width: 260, height: 140 * 32))
    let summary = NSTextField(labelWithString: "No sections reviewed")
    var window: NSWindow!
    var reviewed: [String: Int] = [:]
    let original = (1...500).map {
        String(format: "Paragraph %03d", $0) + ": 复核文档🙂 — retain the exact text, punctuation and section context.\n"
    }.joined()

    func record() {
        guard let window else { return }
        let cursor = CGEvent(source: nil)?.location
        let current: [String: Any] = [
            "pid": getpid(), "windowNumber": window.windowNumber,
            "windowObjectID": String(describing: ObjectIdentifier(window)),
            "documentObjectID": String(describing: ObjectIdentifier(document)),
            "sectionGroupObjectID": String(describing: ObjectIdentifier(sections)),
            "title": title.stringValue, "guard": guardField.stringValue,
            "documentSHA256": SHA256.hash(data: Data(document.string.utf8)).map { String(format: "%02x", $0) }.joined(),
            "reviewed": reviewed, "summary": summary.stringValue,
            "frontmostPID": NSWorkspace.shared.frontmostApplication?.processIdentifier as Any? ?? NSNull(),
            "cursor": cursor.map { [$0.x, $0.y] } as Any? ?? NSNull(),
        ]
        try! JSONSerialization.data(withJSONObject: current, options: [.sortedKeys]).write(
            to: folder.appendingPathComponent("state.json"), options: .atomic)
    }

    @objc func review(_ sender: NSButton) {
        let key = String(format: "%03d", sender.tag)
        reviewed[key, default: 0] += 1
        summary.stringValue = "Reviewed section \(key)"
        record()
    }

    func applicationDidFinishLaunching(_ notification: Notification) {
        try! original.write(to: folder.appendingPathComponent("source-document.txt"), atomically: true, encoding: .utf8)
        window = NSWindow(
            contentRect: NSRect(x: 160, y: 130, width: 820, height: 620),
            styleMask: [.titled, .closable], backing: .buffered, defer: false)
        window.title = "Document review continuation probe"
        title.frame = NSRect(x: 20, y: 570, width: 460, height: 28)
        title.setAccessibilityLabel("Review title")
        title.setAccessibilityIdentifier("review-title")
        guardField.frame = NSRect(x: 510, y: 570, width: 280, height: 28)
        guardField.setAccessibilityLabel("Unrelated draft")
        guardField.setAccessibilityIdentifier("review-guard")
        document.string = original
        document.isRichText = false
        document.isVerticallyResizable = true
        document.isHorizontallyResizable = false
        document.textContainer?.widthTracksTextView = true
        document.setAccessibilityLabel("Review document")
        document.setAccessibilityIdentifier("read-document")
        let documentScroll = NSScrollView(frame: NSRect(x: 20, y: 55, width: 470, height: 495))
        documentScroll.hasVerticalScroller = true
        documentScroll.documentView = document
        documentScroll.setAccessibilityLabel("Document viewport")
        sections.setAccessibilityElement(true)
        for index in 1...140 {
            let label = String(format: "Review section %03d", index)
            let button = NSButton(title: label, target: self, action: #selector(review(_:)))
            button.tag = index
            button.frame = NSRect(x: 8, y: (index - 1) * 32 + 2, width: 240, height: 28)
            button.setAccessibilityIdentifier(String(format: "review-section-%03d", index))
            sections.addSubview(button)
        }
        let sectionScroll = NSScrollView(frame: NSRect(x: 510, y: 55, width: 280, height: 495))
        sectionScroll.hasVerticalScroller = true
        sectionScroll.documentView = sections
        sectionScroll.setAccessibilityLabel("Section review viewport")
        summary.frame = NSRect(x: 20, y: 16, width: 770, height: 24)
        for view in [title, guardField, documentScroll, sectionScroll, summary] { window.contentView!.addSubview(view) }
        let menu = NSMenu()
        let application = NSMenuItem(title: "DocumentReview", action: nil, keyEquivalent: "")
        application.submenu = NSMenu()
        application.submenu!.addItem(withTitle: "Quit", action: #selector(NSApplication.terminate(_:)), keyEquivalent: "q")
        menu.addItem(application)
        NSApp.mainMenu = menu
        window.orderBack(nil)
        window.makeKey()
        window.makeMain()
        window.makeFirstResponder(guardField)
        record()
        Timer.scheduledTimer(withTimeInterval: 0.05, repeats: true) { _ in MainActor.assumeIsolated { self.record() } }
    }
}

@main enum ReadContinuationProbe {
    @MainActor static func main() {
        let app = NSApplication.shared
        app.setActivationPolicy(.regular)
        let delegate = ReadContinuationDelegate()
        app.delegate = delegate
        app.run()
    }
}
