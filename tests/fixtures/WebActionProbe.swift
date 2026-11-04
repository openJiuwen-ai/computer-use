// Copyright (c) Huawei Technologies Co., Ltd. 2026. All rights reserved.

import AppKit
@preconcurrency import WebKit

/// Real WebKit AX objects. The page does not synthesize accessibility roles.
@MainActor final class WebActionDelegate: NSObject, NSApplicationDelegate, WKScriptMessageHandler, WKUIDelegate {
    let folder = Bundle.main.bundleURL.deletingLastPathComponent()
    var window: NSWindow!
    var web: WKWebView!
    var panel: NSOpenPanel?
    var page: [String: Any] = [:]
    var lastCommand = ""
    var filePanelRequests = 0
    var yieldedPID: pid_t = -1
    var setupError = ""

    func record() {
        let front = NSWorkspace.shared.frontmostApplication
        let mouse = NSEvent.mouseLocation
        let value: [String: Any] = [
            "pid": getpid(), "active": NSApp.isActive, "frontmostPID": front?.processIdentifier ?? -1,
            "visible": window?.isVisible ?? false, "miniaturized": window?.isMiniaturized ?? false,
            "mouseX": mouse.x, "mouseY": mouse.y, "lastCommand": lastCommand, "page": page,
            "filePanelRequests": filePanelRequests, "filePanelVisible": panel?.isVisible ?? false,
            "yieldedPID": yieldedPID, "setupError": setupError,
            "windows": NSApp.windows.filter(\.isVisible).map { ["title": $0.title, "sheet": $0.isSheet] },
        ]
        if let data = try? JSONSerialization.data(withJSONObject: value) {
            try? data.write(to: folder.appendingPathComponent("state.json"), options: .atomic)
        }
    }

    func userContentController(_ controller: WKUserContentController, didReceive message: WKScriptMessage) {
        if let value = message.body as? [String: Any] { page = value }
        record()
    }

    func webView(
        _ webView: WKWebView, runOpenPanelWith parameters: WKOpenPanelParameters,
        initiatedByFrame frame: WKFrameInfo, completionHandler: @escaping @MainActor @Sendable ([URL]?) -> Void
    ) {
        filePanelRequests += 1
        let chooser = NSOpenPanel()
        chooser.title = "Web fixture file picker"
        chooser.canChooseDirectories = parameters.allowsDirectories
        chooser.canChooseFiles = true
        chooser.allowsMultipleSelection = parameters.allowsMultipleSelection
        panel = chooser
        record()
        chooser.beginSheetModal(for: window) { [weak self] response in
            completionHandler(response == .OK ? chooser.urls : nil)
            self?.panel = nil
            self?.record()
        }
    }

    func tick() {
        defer { record() }
        guard let data = try? Data(contentsOf: folder.appendingPathComponent("command.json")),
            let command = try? JSONSerialization.jsonObject(with: data) as? [String: Any],
            let id = command["id"] as? String, id != lastCommand
        else { return }
        lastCommand = id
        switch command["kind"] as? String {
        case "yield":
            guard let pid = command["sentinelPID"] as? Int32,
                let sentinel = NSRunningApplication(processIdentifier: pid),
                sentinel.bundleIdentifier == Bundle.main.bundleIdentifier.map({ $0 + ".sentinel" })
            else { setupError = "yield requires this fixture's owned sentinel"; return }
            if #available(macOS 14.0, *) {
                NSApp.yieldActivation(to: sentinel)
                yieldedPID = pid
            } else {
                setupError = "cooperative activation requires macOS 14"
            }
        case "foreground":
            NSApp.activate(ignoringOtherApps: true)
            window.makeKeyAndOrderFront(nil)
        case "cancel": panel?.cancel(nil)
        case "reset": web.evaluateJavaScript("resetProbe()", completionHandler: nil)
        default: break
        }
    }

    func applicationDidFinishLaunching(_ notification: Notification) {
        let configuration = WKWebViewConfiguration()
        configuration.userContentController.add(self, name: "probe")
        web = WKWebView(frame: NSRect(x: 0, y: 0, width: 520, height: 360), configuration: configuration)
        web.uiDelegate = self
        window = NSWindow(contentRect: web.frame, styleMask: [.titled, .closable], backing: .buffered, defer: false)
        window.title = "Web action probe"
        window.setFrameOrigin(NSPoint(x: 200, y: 240))
        window.contentView = web
        window.orderBack(nil)
        window.makeMain()
        window.makeKey()
        web.loadHTMLString(Self.html, baseURL: nil)
        record()
        Timer.scheduledTimer(withTimeInterval: 0.05, repeats: true) { _ in
            MainActor.assumeIsolated { self.tick() }
        }
    }

    static let html = #"""
        <!doctype html><meta charset="utf-8"><title>Web action probe</title>
        <style>body{font:18px system-ui;padding:22px}label,button{display:block;margin:16px 0}
        select,input,button{font:inherit}output{display:block;margin-top:20px}</style>
        <button id="ordinary">Web ordinary button</button>
        <label><input id="checkbox" type="checkbox" aria-label="Web checkbox"> Web checkbox</label>
        <label>Web selection <select id="selection" aria-label="Web selection">
          <option value="first">First option</option><option value="second">Second option</option>
        </select></label>
        <label>Web file upload <input id="file" type="file" aria-label="Web file upload"></label>
        <output id="status">No action</output>
        <script>
        let clicks = {}, changes = {}, buttonActions = 0;
        const node = id => document.getElementById(id);
        function snapshot() {
          const result = {ready:true, clicks, changes, buttonActions,
            checked:node('checkbox').checked, selection:node('selection').value,
            files:node('file').files.length, focused:document.activeElement?.id || ''};
          node('status').textContent = JSON.stringify(result);
          window.webkit.messageHandlers.probe.postMessage(result);
        }
        function resetProbe() {
          clicks = {}; changes = {}; buttonActions = 0;
          node('checkbox').checked = false; node('selection').value = 'first'; node('file').value = '';
          snapshot();
        }
        for (const id of ['ordinary','checkbox','selection','file']) {
          node(id).addEventListener('click', () => {clicks[id] = (clicks[id] || 0) + 1;
            if(id === 'ordinary') buttonActions += 1; snapshot();});
          node(id).addEventListener('change', () => {changes[id] = (changes[id] || 0) + 1; snapshot();});
        }
        snapshot();
        </script>
        """#
}

/// A separate disposable foreground application records accidental input.
@MainActor final class WebSentinelDelegate: NSObject, NSApplicationDelegate {
    let folder = Bundle.main.bundleURL.deletingLastPathComponent()
    let other = NSTextField(string: "unchanged")
    var window: NSWindow!
    var monitor: Any?
    var keys = 0, keyUps = 0, mouseEvents = 0
    var lastCommand = ""
    var activationRequests = 0

    func record() {
        let value: [String: Any] = [
            "pid": getpid(), "active": NSApp.isActive,
            "frontmostPID": NSWorkspace.shared.frontmostApplication?.processIdentifier ?? -1,
            "other": other.stringValue, "keys": keys, "keyUps": keyUps, "mouseEvents": mouseEvents,
            "lastCommand": lastCommand, "activationRequests": activationRequests,
        ]
        if let data = try? JSONSerialization.data(withJSONObject: value) {
            try? data.write(to: folder.appendingPathComponent("state.json"), options: .atomic)
        }
    }

    func tick() {
        defer { record() }
        guard let data = try? Data(contentsOf: folder.appendingPathComponent("command.json")),
            let command = try? JSONSerialization.jsonObject(with: data) as? [String: Any],
            let id = command["id"] as? String, id != lastCommand
        else { return }
        lastCommand = id
        if command["kind"] as? String == "activate", activationRequests == 0 {
            activationRequests += 1
            window.makeKeyAndOrderFront(nil)
            if #available(macOS 14.0, *) { NSApp.activate() }
            else { NSApp.activate(ignoringOtherApps: true) }
            window.makeFirstResponder(other)
        }
    }

    func applicationDidFinishLaunching(_ notification: Notification) {
        window = NSWindow(
            contentRect: NSRect(x: 760, y: 240, width: 330, height: 130),
            styleMask: [.titled, .closable], backing: .buffered, defer: false)
        window.title = "Web action foreground sentinel"
        window.tabbingMode = .disallowed
        other.frame = NSRect(x: 20, y: 50, width: 290, height: 28)
        other.setAccessibilityLabel("Unrelated sentinel input")
        window.contentView!.addSubview(other)
        monitor = NSEvent.addLocalMonitorForEvents(
            matching: [.keyDown, .keyUp, .leftMouseDown, .rightMouseDown, .otherMouseDown]
        ) { event in
            MainActor.assumeIsolated {
                if event.type == .keyDown { self.keys += 1 }
                else if event.type == .keyUp { self.keyUps += 1 }
                else { self.mouseEvents += 1 }
                self.record()
            }
            return event
        }
        window.orderBack(nil)
        record()
        Timer.scheduledTimer(withTimeInterval: 0.05, repeats: true) { _ in
            MainActor.assumeIsolated { self.tick() }
        }
    }
}

@main enum WebActionProbe {
    @MainActor static func main() {
        let app = NSApplication.shared
        app.setActivationPolicy(.regular)
        if CommandLine.arguments.contains("--sentinel") {
            let delegate = WebSentinelDelegate()
            app.delegate = delegate
            app.run()
        } else {
            let delegate = WebActionDelegate()
            app.delegate = delegate
            app.run()
        }
    }
}
