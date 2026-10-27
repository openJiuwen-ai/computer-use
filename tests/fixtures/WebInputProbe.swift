import AppKit
import WebKit
import CryptoKit

// A local, disposable WebKit editor. JavaScript is part of this fixture only:
// it reports what the real editor received; the test never uses it to edit.
@MainActor func desktopSnapshot() -> [String: Any] {
    let clipboard = NSPasteboard.general
    var digest = SHA256()
    for (index, item) in (clipboard.pasteboardItems ?? []).enumerated() {
        digest.update(data: Data("item:\(index)\n".utf8))
        for type in item.types.sorted(by: { $0.rawValue < $1.rawValue }) {
            let bytes = item.data(forType: type) ?? Data()
            digest.update(data: Data("\(type.rawValue):\(bytes.count)\n".utf8))
            digest.update(data: bytes)
        }
    }
    let foreground = NSWorkspace.shared.frontmostApplication
    let point = CGEvent(source: nil)?.location ?? .zero
    return [
        "pid": foreground?.processIdentifier ?? 0, "foregroundExecutable": foreground?.executableURL?.path ?? "",
        "foregroundLaunch": foreground?.launchDate?.timeIntervalSince1970 ?? 0, "x": point.x, "y": point.y,
        "flags": CGEventSource.flagsState(.combinedSessionState).rawValue, "clipboard": clipboard.changeCount,
        "clipboardFingerprint": digest.finalize().map { String(format: "%02x", $0) }.joined()
    ]
}

@MainActor final class WebInputApplication: NSApplication {
    override func sendEvent(_ event: NSEvent) {
        // Observe only this application's delivered events, never global input.
        if [.keyDown, .keyUp, .leftMouseDown, .leftMouseUp].contains(event.type),
            let probe = delegate as? WebInputDelegate
        {
            var fields: [String: Any] = [
                "type": event.type.rawValue, "eventWindow": event.windowNumber, "flags": event.modifierFlags.rawValue
            ]
            if event.type == .keyDown || event.type == .keyUp { fields["characters"] = event.characters ?? "" }
            probe.record("native-event", fields)
        }
        super.sendEvent(event)
    }
}

@MainActor final class WebInputDelegate: NSObject, NSApplicationDelegate, WKScriptMessageHandler {
    var window: NSWindow!
    var views: [WKWebView] = []
    var states: [String: Any] = [:]
    let directory = Bundle.main.bundleURL.deletingLastPathComponent()

    func record(_ kind: String, _ fields: [String: Any] = [:]) {
        var value: [String: Any] = [
            "kind": kind, "timestampMs": Date().timeIntervalSince1970 * 1000, "hostPID": getpid(),
            "window": window?.windowNumber ?? 0,
            "foregroundPID": NSWorkspace.shared.frontmostApplication?.processIdentifier ?? 0,
            "firstResponder": window?.firstResponder.map { String(describing: type(of: $0)) } ?? ""
        ]
        value.merge(fields) { _, new in new }
        let file = directory.appendingPathComponent("events.jsonl")
        if !FileManager.default.fileExists(atPath: file.path) {
            FileManager.default.createFile(atPath: file.path, contents: nil)
        }
        let handle = try! FileHandle(forWritingTo: file)
        try! handle.seekToEnd()
        try! handle.write(
            contentsOf: JSONSerialization.data(withJSONObject: value, options: [.sortedKeys]) + Data([10]))
        try! handle.close()
    }

    func userContentController(_ userContentController: WKUserContentController, didReceive message: WKScriptMessage) {
        guard let body = message.body as? [String: Any], let page = body["page"] as? String else { return }
        states[page] = body
        record("dom-state", body)
        let snapshot: [String: Any] = [
            "hostPID": getpid(), "window": window.windowNumber, "timestampMs": Date().timeIntervalSince1970 * 1000,
            "pages": states
        ]
        try! JSONSerialization.data(withJSONObject: snapshot, options: [.sortedKeys]).write(
            to: directory.appendingPathComponent("state.json"), options: .atomic)
    }

    func addPage(_ name: String, title: String, html: String, frame: NSRect) {
        let controller = WKUserContentController()
        controller.add(self, name: "state")
        // A read-only oracle: no focus(), execCommand(), selection mutation or
        // synthesized input. It reports every actual input and focus change.
        let instrumentation = """
            (() => {
              const report = event => window.webkit.messageHandlers.state.postMessage({
                page: '\(name)', event,
                bodyText: document.body.innerText,
                bodyHTML: document.body.innerHTML,
                active: document.activeElement?.id || document.activeElement?.tagName || '',
                textarea: document.querySelector('textarea')?.value ?? null,
                rich: document.getElementById('rich')?.innerText ?? null,
                richHTML: document.getElementById('rich')?.innerHTML ?? null,
                readonly: document.getElementById('locked')?.value ?? null,
                selection: getSelection()?.toString() ?? ''
              });
              for (const name of ['input', 'change', 'focusin', 'focusout'])
                document.addEventListener(name, () => report(name), true);
              document.addEventListener('selectionchange', () => report('selectionchange'));
              report('ready');
            })();
            """
        controller.addUserScript(
            WKUserScript(source: instrumentation, injectionTime: .atDocumentEnd, forMainFrameOnly: true))
        let configuration = WKWebViewConfiguration()
        configuration.userContentController = controller
        configuration.websiteDataStore = .nonPersistent()
        let view = WKWebView(frame: frame, configuration: configuration)
        view.setAccessibilityIdentifier("probe-\(name)")
        view.setAccessibilityLabel(title)
        window.contentView!.addSubview(view)
        views.append(view)
        view.loadHTMLString(
            """
            <!doctype html><html><head><meta charset='utf-8'><title>\(title)</title><style>body{font:18px \
            sans-serif;margin:12px}textarea,#rich{display:block;width:94%;min-height:44px;border:1px solid \
            #888;margin:8px 0}</style></head>\(html)</html>
            """, baseURL: directory)
    }

    func applicationDidFinishLaunching(_ notification: Notification) {
        try! String(getpid()).write(
            to: directory.appendingPathComponent("fixture.pid"), atomically: true, encoding: .utf8)
        // Normal AppKit command-key dispatch needs real menu key equivalents.
        // Targets remain nil so standard commands use the native responder
        // chain. The fixture never implements paste or changes DOM content.
        let mainMenu = NSMenu()
        let applicationItem = NSMenuItem(title: "Application", action: nil, keyEquivalent: "")
        let applicationMenu = NSMenu(title: "Application")
        applicationMenu.addItem(
            withTitle: "Quit Web Input Probe", action: #selector(NSApplication.terminate(_:)), keyEquivalent: "q")
        applicationItem.submenu = applicationMenu
        mainMenu.addItem(applicationItem)
        let editItem = NSMenuItem(title: "Edit", action: nil, keyEquivalent: "")
        let editMenu = NSMenu(title: "Edit")
        for (title, action, key) in [
            ("Undo", "undo:", "z"), ("Cut", "cut:", "x"), ("Copy", "copy:", "c"), ("Paste", "paste:", "v"),
            ("Select All", "selectAll:", "a")
        ] { editMenu.addItem(withTitle: title, action: Selector(action), keyEquivalent: key) }
        editItem.submenu = editMenu
        mainMenu.addItem(editItem)
        NSApp.mainMenu = mainMenu
        window = NSWindow(
            contentRect: NSRect(x: 120, y: 80, width: 680, height: 680), styleMask: [.titled, .closable],
            backing: .buffered, defer: false)
        window.title = "Computer Use Web Input Acceptance"
        window.isReleasedWhenClosed = false
        window.tabbingMode = .disallowed
        window.representedURL = directory.appendingPathComponent("owned-web-editor.html")
        let designMode = CommandLine.arguments.contains("--design-mode")
        addPage(
            "body", title: designMode ? "Design Mode Web Body" : "Editable Web Body",
            html: designMode
                ? "<body><div>FIRST: 中文 e&#769; 🙂</div><div>LAST</div><script>document.designMode='on';</script></body>"
                : "<body contenteditable='true' aria-label='Editable Web Body'>BODY:</body>",
            frame: NSRect(x: 10, y: 505, width: 660, height: 165))
        addPage(
            "controls", title: "Editor Controls",
            html: """
                <body><textarea id='textarea' aria-label='Web Textarea'>TEXTAREA:</textarea><div id='rich' \
                role='textbox' contenteditable='true' aria-label='Rich Editor'>RICH:</div><input id='locked' \
                aria-label='Read Only Field' readonly value='LOCKED'></body>
                """, frame: NSRect(x: 10, y: 240, width: 660, height: 255))
        addPage(
            "readonly", title: "Read Only Web Page",
            html:
                "<body aria-label='Read Only Web Page'><h2>Read Only Web Page</h2><p>READONLY PAGE CONTENT</p></body>",
            frame: NSRect(x: 10, y: 10, width: 660, height: 220))
        window.orderBack(nil)
        record("launched")
        Timer.scheduledTimer(withTimeInterval: 240, repeats: false) { _ in
            MainActor.assumeIsolated { NSApp.terminate(nil) }
        }
    }
}

if CommandLine.arguments.contains("--desktop-snapshot") {
    FileHandle.standardOutput.write(
        try! JSONSerialization.data(withJSONObject: desktopSnapshot(), options: [.sortedKeys]) + Data([10]))
} else {
    let application = WebInputApplication.shared
    application.setActivationPolicy(.regular)
    let delegate = WebInputDelegate()
    application.delegate = delegate
    application.run()
}
