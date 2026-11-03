// Copyright (c) Huawei Technologies Co., Ltd. 2026. All rights reserved.

import AppKit
@preconcurrency import WebKit

/// Disposable real WebKit form. The oracle reads DOM state but updates its
/// business models only from normal input/change/submit event consumers.
@MainActor final class WKWebTextDelegate: NSObject, NSApplicationDelegate, WKScriptMessageHandler {
    let folder: URL
    let title: String
    let documentID = UUID().uuidString
    var window: NSWindow!
    var web: WKWebView!
    var page = [String: Any]()

    init(folder: URL, title: String) {
        self.folder = folder
        self.title = title
    }

    func record() {
        let value: [String: Any] = [
            "pid": getpid(), "title": title, "documentID": documentID,
            "bundleID": Bundle.main.bundleIdentifier ?? "", "page": page,
            "visible": window?.isVisible ?? false, "active": NSApp.isActive,
            "windowKey": window?.isKeyWindow ?? false,
            "frontmostPID": NSWorkspace.shared.frontmostApplication?.processIdentifier ?? -1,
            "webkitVersion": Bundle(for: WKWebView.self).object(forInfoDictionaryKey: "CFBundleVersion") ?? "unknown"
        ]
        if let data = try? JSONSerialization.data(withJSONObject: value, options: [.sortedKeys]) {
            try? data.write(to: folder.appendingPathComponent("state.json"), options: .atomic)
        }
    }

    func userContentController(_ controller: WKUserContentController, didReceive message: WKScriptMessage) {
        guard message.name == "probe", let value = message.body as? [String: Any] else { return }
        page = value
        record()
    }

    func applicationDidFinishLaunching(_ notification: Notification) {
        let configuration = WKWebViewConfiguration()
        configuration.userContentController.add(self, name: "probe")
        web = WKWebView(frame: NSRect(x: 0, y: 0, width: 620, height: 300), configuration: configuration)
        window = NSWindow(contentRect: web.frame, styleMask: [.titled, .closable], backing: .buffered, defer: false)
        window.title = title
        window.tabbingMode = .disallowed
        window.setFrameOrigin(NSPoint(x: 180, y: 260))
        window.contentView = web
        window.orderBack(nil)
        web.loadHTMLString(Self.html.replacingOccurrences(of: "DOCUMENT_TOKEN", with: documentID), baseURL: nil)
        record()
    }

    static let html = #"""
        <!doctype html><meta charset="utf-8"><title>Owned Web text document</title>
        <style>body{font:18px system-ui;padding:20px}label{display:block;margin:14px 0}
        input,button{font:inherit}input{width:460px}output{display:block;margin-top:16px}</style>
        <form id="form">
          <label>Web callback input <input id="target" aria-label="Web callback input" autocomplete="off"></label>
          <button type="submit">Save disposable Web form</button>
        </form>
        <label>Unrelated Web guard <input id="guard" aria-label="Unrelated Web guard" value="unchanged"></label>
        <output id="status">No submission</output>
        <script>
        const documentID = 'DOCUMENT_TOKEN';
        document.documentElement.dataset.documentId = documentID;
        const target = document.getElementById('target');
        const guard = document.getElementById('guard');
        let draftModel = '', committedModel = '', savedModel = '';
        let inputEvents = [], changeEvents = [], submissions = [], keyEvents = [], guardEvents = [];
        let sequence = 0;
        const evidence = event => ({sequence: ++sequence, type:event.type, trusted:event.isTrusted,
          value:event.target.value ?? null, data:event.data ?? null, inputType:event.inputType ?? null});
        function snapshot() {
          window.webkit.messageHandlers.probe.postMessage({ready:true, documentID,
            documentMarker:document.documentElement.dataset.documentId, url:location.href,
            value:target.value, draftModel, committedModel, savedModel,
            inputEvents, changeEvents, submissions, keyEvents, guardEvents,
            guard:guard.value, focused:document.activeElement?.id || '',
            selectionStart:target.selectionStart, selectionEnd:target.selectionEnd});
        }
        target.addEventListener('input', event => {
          inputEvents.push(evidence(event)); draftModel = event.target.value; snapshot();
        });
        target.addEventListener('change', event => {
          changeEvents.push(evidence(event)); committedModel = event.target.value; snapshot();
        });
        for (const name of ['keydown', 'keyup']) target.addEventListener(name, event => {
          keyEvents.push({sequence:++sequence, type:event.type, key:event.key, trusted:event.isTrusted}); snapshot();
        });
        for (const name of ['input', 'change', 'keydown', 'keyup']) guard.addEventListener(name, event => {
          guardEvents.push(evidence(event)); snapshot();
        });
        document.getElementById('form').addEventListener('submit', event => {
          event.preventDefault();
          savedModel = draftModel;
          submissions.push({sequence:++sequence, value:target.value, draftModel, committedModel,
            savedModel, documentID, trusted:event.isTrusted});
          document.getElementById('status').textContent = 'Saved: ' + savedModel;
          snapshot();
        });
        // Observation only: never assigns values, dispatches events or changes focus.
        setInterval(snapshot, 50);
        snapshot();
        </script>
        """#
}

@main enum WKWebTextProbe {
    @MainActor static func main() {
        let arguments = CommandLine.arguments
        func value(_ name: String) -> String? {
            guard let index = arguments.firstIndex(of: name), index + 1 < arguments.count else { return nil }
            return arguments[index + 1]
        }
        guard let output = value("--output"), let title = value("--title") else {
            fatalError("WKWebTextProbe requires --output and --title")
        }
        let app = NSApplication.shared
        app.setActivationPolicy(.regular)
        let delegate = WKWebTextDelegate(folder: URL(fileURLWithPath: output), title: title)
        app.delegate = delegate
        app.run()
    }
}
