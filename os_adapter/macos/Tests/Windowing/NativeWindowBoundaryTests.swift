import Foundation

@main struct NativeWindowBoundaryTests {
    static func main() throws {
        let parents = [
            "document": "application", "sheet": "document", "inner": "sheet", "anchor": "document", "popover": "anchor",
            "peer": "application", "cycle": "cycle"
        ]
        for (target, modal, expected) in [
            ("document", true, "document"), ("sheet", true, "sheet"), ("inner", true, "inner"),
            ("sheet", false, "document"), ("inner", nil, "document"), ("popover", false, "document"),
            ("peer", true, "document"), ("unknown", true, "document"), ("cycle", true, "document")
        ] as [(String, Bool?, String)] {
            let selected = NativeWindowBoundary.contentRoot(
                workingRoot: "document", target: target, same: ==, modal: { _ in modal }, parent: { parents[$0] })
            precondition(selected == expected, "Only a confirmed modal descendant narrows content discovery")
        }
        enum Cancelled: Error { case request }
        do {
            _ = try NativeWindowBoundary.contentRoot(
                workingRoot: "document", target: "sheet", same: ==, modal: { _ in true },
                parent: { _ in throw Cancelled.request })
            preconditionFailure("Cancelled boundary discovery must not select a content root")
        } catch Cancelled.request {}
        for (window, root, expected) in [
            ("document", "document", true), ("inner", "document", true), ("popover", "document", true),
            ("peer", "document", false), ("document", "inner", false), ("sheet", "inner", false),
            ("inner", "inner", true), ("unknown", "document", true), ("cycle", "document", true)
        ] {
            let actual = NativeWindowBoundary.exploreContents(
                of: window, root: root, same: ==, parent: { parents[$0] }, isApplication: { $0 == "application" })
            precondition(actual == expected, "Uncertain/related contents must not be pruned")
        }
        typealias W = AppSelection.Window
        let root = W(ref: "root", parent: nil, modal: false), other = W(ref: "other", parent: nil, modal: false)
        let panel = W(ref: "panel", parent: "root", modal: false), sheet = W(ref: "sheet", parent: "root", modal: true)
        let second = W(ref: "second", parent: "root", modal: true),
            nested = W(ref: "nested", parent: "sheet", modal: true)
        let global = W(ref: "global", parent: nil, modal: true)
        // Parent-tree elements retain their original nearest sheet. The
        // catalogue identity match may narrow that observation, while window
        // input and document elements must stay behind the same modal.
        for (observed, candidate, windows, expected, blockers) in [
            ("root", "sheet", [root, sheet], "sheet", []), ("root", "nested", [root, sheet, nested], "nested", []),
            ("root", nil, [root, sheet], "root", ["sheet"]), ("root", "root", [root, sheet], "root", ["sheet"]),
            ("root", "other", [root, other, sheet], "root", ["sheet"]),
            ("root", "expired", [root, sheet], "root", ["sheet"]), ("sheet", "root", [root, sheet], "sheet", []),
            ("root", "cycle", [root, sheet, W(ref: "cycle", parent: "cycle", modal: false)], "root", ["sheet"])
        ] as [(String, String?, [W], String, [String])] {
            let window = NativeWindowBoundary.interactionWindow(
                observed: observed, candidate: candidate, windows: windows)
            precondition(window == expected, "Only the exact observed descendant may narrow input")
            let actual = try NativeWindowBoundary.blocked(root: "root", target: window, windows: windows)
            precondition(actual == blockers, "An observed sheet must not block itself or unblock its document")
        }
        func blocked(_ target: String, _ windows: [W]) throws -> [String] {
            try NativeWindowBoundary.blocked(root: "root", target: target, windows: windows)
        }
        // A fixed native root remains usable without any AX window catalogue.
        for (target, windows, expected) in [
            ("root", [], []), ("panel", [root, panel, other], []), ("root", [root, sheet, second], ["second", "sheet"]),
            ("sheet", [root, sheet, nested], ["nested"]), ("nested", [root, sheet, nested], []),
            ("root", [root, global], ["global"]),
            ("root", [root, other, W(ref: "other-sheet", parent: "other", modal: true)], [])
        ] {
            let actual = try blocked(target, windows)
            precondition(actual == expected)
        }
        // Selecting a nested modal as a working root must not make its outer
        // app-modal panel a blocker. This is the NSOpenPanel -> Go To case,
        // independent of titles, roles or the application creating the panel.
        let inner = W(ref: "inner", parent: "global", modal: true)
        let leaf = W(ref: "leaf", parent: "inner", modal: false)
        let deeper = W(ref: "deeper", parent: "inner", modal: true)
        for (selectedRoot, target, windows, expected) in [
            ("inner", "inner", [root, global, inner], []), ("inner", "leaf", [root, global, inner, leaf], []),
            ("inner", "inner", [root, global, inner, deeper], ["deeper"]),
            ("global", "global", [root, global, inner], ["inner"])
        ] {
            let actual = try NativeWindowBoundary.blocked(root: selectedRoot, target: target, windows: windows)
            precondition(actual == expected)
        }
        // Default-view ambiguity is not permission to interact with a document
        // or its menu while two independent app-modal windows are present.
        let another = W(ref: "another", parent: nil, modal: true)
        let ambiguous = try blocked("root", [root, global, another])
        precondition(ambiguous == ["another", "global"])
        // Standalone nested modal loops need not expose an AX parent edge.
        // Admit only the currently focused modal; never infer a new owner,
        // unblock the document, or ignore a confirmed deeper modal.
        let modalChild = W(ref: "modal-child", parent: "global", modal: false)
        for (selectedRoot, target, windows, focused, expected) in [
            ("global", "global", [root, global, another], "global", []),
            ("global", "global", [root, global, another], "another", ["another"]),
            ("global", "global", [root, global, another], nil, ["another"]),
            ("global", "global", [root, global, another], "expired", ["another"]),
            ("root", "root", [root, global, another], "root", ["another", "global"]),
            ("root", "root", [root, global, another], "global", ["another", "global"]),
            ("global", "modal-child", [root, global, another, modalChild], "global", []),
            ("global", "global", [root, global, another, inner], "global", ["inner"]),
            ("root", "sheet", [root, sheet, second], "sheet", ["second"])
        ] as [(String, String, [W], String?, [String])] {
            let actual = try NativeWindowBoundary.blocked(
                root: selectedRoot, target: target, windows: windows, focused: focused)
            precondition(actual == expected, "Native focus resolves only unrelated ownerless-modal ambiguity")
        }
        var focusReads = 0
        func liveFocus() -> String? {
            focusReads += 1
            return "global"
        }
        _ = try NativeWindowBoundary.blocked(
            root: "root", target: "root", windows: [root, global], focused: liveFocus())
        precondition(focusReads == 0, "Ordinary document input must not add a focus IPC")
        _ = try NativeWindowBoundary.blocked(
            root: "global", target: "global", windows: [root, global, another], focused: liveFocus())
        precondition(focusReads == 1, "Modal input requires a fresh, single focus report")
        for windows in [
            [root], [root, W(ref: "panel", parent: "other", modal: false), other],
            [root, W(ref: "panel", parent: "cycle", modal: false), W(ref: "cycle", parent: "panel", modal: false)]
        ] {
            do {
                _ = try blocked("panel", windows)
                preconditionFailure("unconfirmed child scope admitted")
            } catch let error as DriverError { precondition(error.code == .staleTarget) }
        }
        do {
            _ = try blocked("root", [root, root])
            preconditionFailure("ambiguous catalogue admitted")
        } catch let error as DriverError { precondition(error.code == .inputUnavailable) }
        print(
            """
            PASS native input scope: missing AX, related panel, explicit nested roots, multiple modals, \
            changed ancestry and ambiguity
            """)
    }
}
