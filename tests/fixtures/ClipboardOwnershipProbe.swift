// Copyright (c) Huawei Technologies Co., Ltd. 2026. All rights reserved.

import AppKit
import CoreGraphics
import CryptoKit

/// Fixture resource state, independent of the SDK transaction. Materialized
/// representations remain in memory only and are released after cleanup.
@MainActor private struct ClipboardMemorySnapshot {
    struct Item: Equatable {
        let types: [NSPasteboard.PasteboardType]
        let data: [NSPasteboard.PasteboardType: Data]
    }
    let generation: Int
    let items: [Item]

    static func capture(_ board: NSPasteboard) -> ClipboardMemorySnapshot? {
        let generation = board.changeCount
        var items = [Item]()
        for item in board.pasteboardItems ?? [] {
            let types = item.types
            var data = [NSPasteboard.PasteboardType: Data]()
            for type in types {
                guard let bytes = item.data(forType: type) else { return nil }
                data[type] = bytes
            }
            items.append(Item(types: types, data: data))
        }
        guard !items.isEmpty || (board.types ?? []).isEmpty,
            board.changeCount == generation else { return nil }
        return ClipboardMemorySnapshot(generation: generation, items: items)
    }

    var digest: String {
        var hash = SHA256()
        func field(_ data: Data) {
            hash.update(data: Data("\(data.count):".utf8))
            hash.update(data: data)
        }
        field(Data("\(items.count)".utf8))
        for item in items {
            field(Data("\(item.types.count)".utf8))
            for type in item.types {
                field(Data(type.rawValue.utf8))
                field(item.data[type]!)
            }
        }
        return hash.finalize().map { String(format: "%02x", $0) }.joined()
    }
}

@MainActor final class ClipboardProbeApplication: NSApplication {
    var inputEvents = [[String: Any]]()
    override func sendEvent(_ event: NSEvent) {
        if [.keyDown, .keyUp, .flagsChanged].contains(event.type) {
            inputEvents.append([
                "kind": "keyboard", "type": event.type.rawValue, "code": event.keyCode,
                "flags": event.modifierFlags.rawValue, "window": event.windowNumber
            ])
        } else if [.leftMouseDown, .leftMouseUp, .rightMouseDown, .rightMouseUp,
                   .otherMouseDown, .otherMouseUp].contains(event.type) {
            inputEvents.append([
                "kind": "mouse", "type": event.type.rawValue, "button": event.buttonNumber,
                "window": event.windowNumber
            ])
        }
        super.sendEvent(event)
    }
}

/// The normal paste callback is the independent effect oracle. Only after
/// AppKit consumes the payload does the application take clipboard ownership.
@MainActor final class ClipboardOwnershipText: NSTextView {
    let newOwnerValue = "ClipboardOwnershipProbe new owner " + UUID().uuidString
    var pastes = [[String: Any]]()
    var changed: () -> Void = {}
    var ownershipTaken: (Int) -> Void = { _ in }

    override func paste(_ sender: Any?) {
        let board = NSPasteboard.general
        let received = board.string(forType: .string)
        let incomingGeneration = board.changeCount
        super.paste(sender)
        let ownedGeneration = board.clearContents()
        let written = board.setString(newOwnerValue, forType: .string)
        ownershipTaken(ownedGeneration)
        pastes.append([
            "received": received as Any? ?? NSNull(), "editorAfterPaste": string,
            "incomingGeneration": incomingGeneration, "ownedGeneration": ownedGeneration,
            "generationAfterWrite": board.changeCount, "ownershipWriteSucceeded": written,
            "window": window?.windowNumber as Any? ?? NSNull()
        ])
        changed()
    }
}

@MainActor final class ClipboardGuardText: NSTextView {
    var pasteCallbacks = 0, textChanges = 0, mouseCallbacks = 0
    override func paste(_ sender: Any?) {
        pasteCallbacks += 1
        super.paste(sender)
    }
    override func didChangeText() {
        textChanges += 1
        super.didChangeText()
    }
    override func mouseDown(with event: NSEvent) {
        mouseCallbacks += 1
        super.mouseDown(with: event)
    }
}

@MainActor final class ClipboardOwnershipDelegate: NSObject, NSApplicationDelegate {
    let folder = Bundle.main.bundleURL.deletingLastPathComponent()
    let editor = ClipboardOwnershipText(frame: NSRect(x: 20, y: 95, width: 350, height: 180))
    let guardEditor = ClipboardGuardText(frame: NSRect(x: 390, y: 95, width: 260, height: 180))
    var window: NSWindow!
    private var originalClipboard: ClipboardMemorySnapshot?
    private var ownedClipboard: ClipboardMemorySnapshot?
    private var processedCommands = Set<String>()
    private var snapshotEvidence: [String: Any] = ["status": "unavailable"]
    private var cleanupEvidence: [String: Any] = ["commandId": NSNull(), "status": "notRequested"]

    private func recordOwnership(_ generation: Int) {
        let board = NSPasteboard.general
        guard let current = ClipboardMemorySnapshot.capture(board), current.generation == generation,
            board.string(forType: .string) == editor.newOwnerValue,
            board.changeCount == generation else {
            ownedClipboard = nil
            return
        }
        ownedClipboard = current
    }

    private func stillOwns(_ expected: ClipboardMemorySnapshot) -> Bool {
        let board = NSPasteboard.general
        guard board.changeCount == expected.generation,
            board.string(forType: .string) == editor.newOwnerValue,
            let current = ClipboardMemorySnapshot.capture(board),
            current.generation == expected.generation, current.items == expected.items,
            board.changeCount == expected.generation else { return false }
        return true
    }

    private func cleanupClipboard(commandId: String) {
        // A command never repeats cleanup, even with a new ID. Reporting the
        // previous terminal state permits the runner to finish its handshake.
        guard cleanupEvidence["status"] as? String == "notRequested" else {
            cleanupEvidence["commandId"] = commandId
            return
        }
        let board = NSPasteboard.general
        cleanupEvidence = [
            "commandId": commandId, "status": "failed", "beforeGeneration": board.changeCount,
            "originalDigest": originalClipboard?.digest as Any? ?? NSNull()
        ]
        defer {
            cleanupEvidence["afterGeneration"] = board.changeCount
            cleanupEvidence["finalDigest"] = ClipboardMemorySnapshot.capture(board)?.digest as Any? ?? NSNull()
            originalClipboard = nil
            ownedClipboard = nil
        }
        guard let ownedClipboard else {
            cleanupEvidence["status"] = editor.pastes.isEmpty ? "notOwned" : "ownershipLost"
            cleanupEvidence["detail"] = "No confirmed fixture clipboard ownership; nothing was written."
            return
        }
        guard stillOwns(ownedClipboard) else {
            cleanupEvidence["status"] = "ownershipLost"
            cleanupEvidence["detail"] = "Later clipboard ownership or representation was preserved."
            return
        }
        guard let originalClipboard else {
            cleanupEvidence["detail"] = "Original representations were unavailable; nothing was written."
            return
        }
        var items = [NSPasteboardItem]()
        for original in originalClipboard.items {
            let item = NSPasteboardItem()
            for type in original.types {
                guard item.setData(original.data[type]!, forType: type) else {
                    cleanupEvidence["detail"] = "Could not prepare an original representation; nothing was written."
                    return
                }
            }
            items.append(item)
        }
        guard stillOwns(ownedClipboard) else {
            cleanupEvidence["status"] = "ownershipLost"
            cleanupEvidence["detail"] = "Ownership changed while preparing cleanup; nothing was written."
            return
        }
        // NSPasteboard has no compare-and-swap. Recheck around each shared
        // mutation and never retry after a racing external writer is observed.
        let restoreGeneration = board.clearContents()
        guard let empty = ClipboardMemorySnapshot.capture(board),
            empty.generation == restoreGeneration, empty.items.isEmpty,
            board.changeCount == restoreGeneration else {
            cleanupEvidence["status"] = "ownershipLost"
            cleanupEvidence["detail"] = "Clipboard changed after cleanup clear; no original data was written."
            return
        }
        let written = items.isEmpty || board.writeObjects(items)
        guard board.changeCount == restoreGeneration else {
            cleanupEvidence["status"] = "ownershipLost"
            cleanupEvidence["detail"] = "A later clipboard generation was preserved after cleanup write."
            return
        }
        guard written, let restored = ClipboardMemorySnapshot.capture(board),
            restored.generation == restoreGeneration, restored.items == originalClipboard.items,
            board.changeCount == restoreGeneration else {
            cleanupEvidence["detail"] = "Restored representations could not be confirmed; no further write attempted."
            return
        }
        cleanupEvidence["status"] = "restored"
        cleanupEvidence["restoredDigest"] = restored.digest
        cleanupEvidence["restoredGeneration"] = restored.generation
    }

    private func processCommand() {
        guard let data = try? Data(contentsOf: folder.appendingPathComponent("command.json")),
            let command = (try? JSONSerialization.jsonObject(with: data)) as? [String: Any],
            let id = command["id"] as? String, !id.isEmpty,
            command["kind"] as? String == "cleanupClipboard",
            processedCommands.insert(id).inserted else { return }
        cleanupClipboard(commandId: id)
    }
    func record() {
        guard let window else { return }
        let cursor = CGEvent(source: nil)?.location
        let value: [String: Any] = [
            "pid": getpid(), "window": window.windowNumber, "active": NSApp.isActive,
            "frontmostPID": NSWorkspace.shared.frontmostApplication?.processIdentifier as Any? ?? NSNull(),
            "editor": editor.string, "pastes": editor.pastes, "newOwnerValue": editor.newOwnerValue,
            "editorFocused": window.firstResponder === editor,
            "guardText": guardEditor.string, "guardPasteCallbacks": guardEditor.pasteCallbacks,
            "guardTextChanges": guardEditor.textChanges, "guardMouseCallbacks": guardEditor.mouseCallbacks,
            "inputEvents": (NSApp as? ClipboardProbeApplication)?.inputEvents ?? [],
            "cursor": cursor.map { [$0.x, $0.y] } as Any? ?? NSNull(),
            "heldButtons": [
                "left": CGEventSource.buttonState(.combinedSessionState, button: .left),
                "right": CGEventSource.buttonState(.combinedSessionState, button: .right),
                "middle": CGEventSource.buttonState(.combinedSessionState, button: .center)
            ],
            // Do not publish the user's original clipboard representations.
            "clipboardDigest": ClipboardMemorySnapshot.capture(.general)?.digest as Any? ?? NSNull(),
            "clipboardChangeCount": NSPasteboard.general.changeCount,
            "clipboardMatchesNewOwner": NSPasteboard.general.string(forType: .string) == editor.newOwnerValue,
            "clipboardSnapshot": snapshotEvidence, "cleanup": cleanupEvidence
        ]
        try? JSONSerialization.data(withJSONObject: value, options: [.sortedKeys]).write(
            to: folder.appendingPathComponent("state.json"), options: .atomic)
    }
    func applicationDidFinishLaunching(_ notification: Notification) {
        originalClipboard = ClipboardMemorySnapshot.capture(.general)
        if let originalClipboard {
            snapshotEvidence = [
                "status": "captured", "generation": originalClipboard.generation, "digest": originalClipboard.digest
            ]
        }
        window = NSWindow(
            contentRect: NSRect(x: 160, y: 180, width: 670, height: 345),
            styleMask: [.titled, .closable], backing: .buffered, defer: false)
        window.title = "Clipboard ownership probe"
        window.tabbingMode = .disallowed
        window.isReleasedWhenClosed = false
        editor.isRichText = false
        editor.setAccessibilityLabel("Ownership paste editor")
        editor.setAccessibilityIdentifier("ownership-paste-editor")
        editor.changed = { [weak self] in self?.record() }
        editor.ownershipTaken = { [weak self] generation in self?.recordOwnership(generation) }
        guardEditor.isRichText = false
        guardEditor.string = "unchanged guard text"
        guardEditor.setAccessibilityLabel("Unrelated guard input")
        let label = NSTextField(labelWithString: "One normal paste; application then takes clipboard ownership")
        label.frame = NSRect(x: 20, y: 300, width: 630, height: 25)
        for view in [editor, guardEditor, label] { window.contentView!.addSubview(view) }
        let menu = NSMenu()
        let appMenu = NSMenuItem(title: "ClipboardOwnershipProbe", action: nil, keyEquivalent: "")
        appMenu.submenu = NSMenu()
        appMenu.submenu!.addItem(withTitle: "Quit", action: #selector(NSApplication.terminate(_:)), keyEquivalent: "q")
        menu.addItem(appMenu)
        let editMenu = NSMenuItem(title: "Edit", action: nil, keyEquivalent: "")
        editMenu.submenu = NSMenu()
        editMenu.submenu!.addItem(withTitle: "Paste", action: #selector(NSText.paste(_:)), keyEquivalent: "v")
        menu.addItem(editMenu)
        NSApp.mainMenu = menu
        window.orderBack(nil)
        window.makeKey()
        window.makeMain()
        window.makeFirstResponder(guardEditor)
        record()
        _ = Timer.scheduledTimer(withTimeInterval: 0.05, repeats: true) { _ in
            MainActor.assumeIsolated {
                self.processCommand()
                self.record()
            }
        }
    }
}

@main enum ClipboardOwnershipProbe {
    @MainActor static func main() {
        let app = ClipboardProbeApplication.shared
        app.setActivationPolicy(.regular)
        let delegate = ClipboardOwnershipDelegate()
        app.delegate = delegate
        app.run()
    }
}
