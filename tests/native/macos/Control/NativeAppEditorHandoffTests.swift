// Copyright (c) Huawei Technologies Co., Ltd. 2026. All rights reserved.

import AppKit
import ApplicationServices

@main enum NativeAppEditorHandoffTests {
    static func expect(_ condition: Bool, file: StaticString = #fileID, line: UInt = #line) {
        precondition(condition, file: file, line: line)
    }

    @MainActor static func main() throws {
        let scene = Scene()
        let handoff = try scene.capture()!
        expect(try handoff.resolve(scene.initial()) == nil)
        let proof = try scene.openEditor(handoff)!
        precondition(proof.recipient.window == 501 && proof.recipient.pid == scene.process.pid)
        precondition(proof.authorizes(scene.recipient(380), lifetime: scene.process))
        precondition(!proof.authorizes(scene.recipient(502), lifetime: scene.process))
        precondition(!proof.authorizes(scene.recipient(380), lifetime: nil))
        try proof.validateFocus()
        expect(try handoff.resolve(scene.current())?.recipient.window == 501)
        precondition(scene.endpointQueries == 1)

        // A second editor in the same proved child is still a different
        // receiver. Preserve the transition and stop rather than rebind.
        scene.focus = scene.replacement
        expect(try handoff.resolve(scene.current()) == nil && handoff.transitioned)
        try refusesFocus(proof)
        scene.focus = scene.editor
        scene.nativeParent = 999
        try refusesFocus(proof)
        scene.nativeParent = 380
        scene.modal = true
        try refusesFocus(proof)

        for breakProof: (Scene) -> Void in [
            { $0.windows.insert(501) }, // pre-existing child
            { $0.childPID = $0.process.pid + 1 },
            { $0.editorPID = $0.process.pid + 1 },
            { $0.nativeParent = nil },
            { $0.nativeParent = 999 },
            { $0.modal = nil },
            { $0.writable = false },
            { $0.editorParent = $0.original },
            { $0.tableParent = $0.child },
        ] {
            let rejected = Scene()
            breakProof(rejected)
            let candidate = try rejected.capture()!
            expect(try rejected.openEditor(candidate) == nil && !candidate.transitioned)
            precondition(rejected.endpointQueries == 0)
        }
        let raw = Scene()
        expect(try raw.capture(delivery: .desktop) == nil)
        print("PASS original table to one new attached text editor, exact local endpoint and stopped stale suffix")
    }

    @MainActor private static func refusesFocus(_ proof: NativeAppEditorHandoff.Proof) throws {
        do { try proof.validateFocus(); preconditionFailure("changed editor retained an input route") }
        catch let error as DriverError { precondition(error.code == .staleTarget) }
    }
}

@MainActor private final class Scene {
    let application = NSRunningApplication.current
    let original = AXUIElementCreateApplication(40001)
    let table = AXUIElementCreateApplication(40002)
    let child = AXUIElementCreateApplication(40003)
    let editor = AXUIElementCreateApplication(40004)
    let replacement = AXUIElementCreateApplication(40005)
    lazy var focus = table
    lazy var tableParent = original
    lazy var editorParent = child
    lazy var childPID = process.pid
    lazy var editorPID = process.pid
    lazy var process = NativeProcessIdentity(pid: application.processIdentifier, parent: 1, seconds: 8,
                                              microseconds: 0, executable: "/fixture/numbers")
    var windows: Set<CGWindowID> = [380]
    var nativeParent: CGWindowID? = 380
    var modal: Bool? = false
    var writable = true
    var endpointQueries = 0

    func recipient(_ id: CGWindowID) -> NativeKeyboardRecipient {
        .init(pid: process.pid, window: id, remoteElement: nil, presenter: process.pid,
              process: nil, axWindow: nil, remoteRoute: nil, endpoint: nil, endpointProcess: nil)
    }

    func initial() -> NativeReceiverSnapshot {
        .init(windowRoute: "yes", focus: table, ancestors: [], foreground: false, window: original)
    }

    func current() -> NativeReceiverSnapshot {
        .init(windowRoute: "no", focus: nil, ancestors: [], foreground: false, window: original,
              rawFocus: focus, rawLocalFocus: true)
    }

    func openEditor(_ candidate: NativeAppEditorHandoff) throws -> NativeAppEditorHandoff.Proof? {
        focus = editor
        return try candidate.resolve(current())
    }

    func capture(delivery: NativeKeyboardDelivery? = nil) throws -> NativeAppEditorHandoff? {
        var access = NativeAppEditorHandoff.Access()
        access.receiver.focus = { _ in self.focus }
        access.receiver.actualPID = { CFEqual($0, self.editor) ? self.editorPID : self.process.pid }
        access.receiver.process = { $0 == self.process.pid ? self.process : nil }
        access.receiver.windowID = {
            if CFEqual($0, self.original) { return 380 }
            return CFEqual($0, self.child) ? 501 : nil
        }
        access.receiver.role = {
            if CFEqual($0, self.original) || CFEqual($0, self.child) { return kAXWindowRole }
            return CFEqual($0, self.table) ? kAXTableRole : kAXTextAreaRole
        }
        access.owner = { $0 == 380 ? self.process.pid : self.childPID }
        access.parents = { _ in self.nativeParent.map { [501: $0] } }
        access.ownedWindows = { _ in self.windows }
        access.writable = { _, _ in self.writable }
        access.delivery = { _, _ in self.endpointQueries += 1; return self.recipient(501) }
        access.read = { element, attribute in
            if attribute == kAXWindowAttribute {
                return .value(CFEqual(element, self.table) ? self.original : self.child)
            }
            if attribute == kAXParentAttribute {
                return .value(CFEqual(element, self.table) ? self.tableParent : self.editorParent)
            }
            if attribute == kAXModalAttribute, let modal = self.modal {
                return .value(modal ? kCFBooleanTrue : kCFBooleanFalse)
            }
            if attribute == kAXFocusedAttribute {
                return .value(CFEqual(element, self.focus) ? kCFBooleanTrue : kCFBooleanFalse)
            }
            return .unsupported
        }
        return try NativeAppEditorHandoff.capture(
            application: application, window: original, state: initial(),
            delivery: delivery ?? .process(recipient(380)), access: access)
    }
}
