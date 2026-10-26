import CoreGraphics

@main struct NativeCellEditorEvidenceTests {
    typealias Node = NativeCellEditorEvidence.Node<String>
    static func main() {
        let same: (String, String) -> Bool = { $0 == $1 }
        func node(
            _ id: String, _ role: String, parent: String? = nil, focus: Bool? = nil, frame: CGRect? = nil,
            children: [String]? = nil, selected: [String]? = nil
        ) -> Node {
            Node(
                id: id, pid: 41, role: role, window: "window", parent: parent, focused: focus, enabled: true,
                frame: frame, children: children, selected: selected)
        }
        let owner = node(
            "owner", "AXTextField", parent: "table", focus: true, frame: CGRect(x: 298, y: 249, width: 54, height: 19),
            children: [])
        let table = node("table", "AXTable", parent: "region", focus: true)
        let beforeRegion = node("region", "AXGroup", children: ["table", "decoration"], selected: [])
        let anchor = NativeCellEditorEvidence.capture(
            owner: owner, table: table, region: beforeRegion, window: "window", pid: 41, same: same)!
        let region = node("region", "AXGroup", children: ["table", "decoration", "root"], selected: ["root"])
        let root = node(
            "root", "AXGroup", parent: "region", frame: CGRect(x: 299, y: 249, width: 51, height: 18),
            children: ["editor"], selected: ["editor"])
        let editor = node(
            "editor", "AXTextArea", parent: "root", focus: true, frame: CGRect(x: 299, y: 249, width: 10, height: 18))
        func prove(
            _ o: Node = owner, _ t: Node = table, _ r: Node = region, _ rootValue: Node = root, _ e: Node = editor
        ) -> NativeCellEditorEvidence.OwnedEditor<String>? {
            NativeCellEditorEvidence.prove(
                anchor, owner: o, table: t, region: r, root: rootValue, editor: e, same: same)
        }
        let positive = prove()!
        precondition(positive.owner == "owner" && positive.editor == "editor" && positive.window == "window")
        func reject(_ edit: (inout Node, inout Node, inout Node, inout Node, inout Node) -> Void) {
            var o = owner, t = table, r = region, p = root, e = editor
            edit(&o, &t, &r, &p, &e)
            precondition(prove(o, t, r, p, e) == nil)
        }
        reject { o, _, _, _, _ in o.focused = false }
        reject { o, _, _, _, _ in o.focused = nil }
        reject { o, _, _, _, _ in o.pid = nil }  // Dead/unavailable original object.
        reject { o, _, _, _, _ in
            o = node("replaced", "AXTextField", parent: "table", focus: true, frame: owner.frame, children: [])
        }
        reject { o, _, _, _, _ in o.parent = "different-table" }
        reject { o, _, _, _, _ in o.children = nil }
        reject { o, _, _, _, _ in o.children = ["another-editor"] }
        reject { o, _, _, _, _ in o.role = "AXGroup" }
        reject { o, _, _, _, _ in o.enabled = false }
        reject { o, _, _, _, _ in o.frame = nil }
        reject { o, _, _, _, _ in o.frame!.origin.x += 1 }
        reject { _, t, _, _, _ in t.focused = false }
        reject { _, t, _, _, _ in t.parent = "another-region" }
        reject { _, t, _, _, _ in t.role = "AXOutline" }
        reject { _, _, r, _, _ in r.children = nil }
        reject { _, _, r, _, _ in r.children = ["table", "root"] }  // Lost prior sibling.
        reject { _, _, r, _, _ in r.children = ["table", "decoration", "root", "other-root"] }
        reject { _, _, r, _, _ in r.children = ["table", "root", "root"] }
        reject { _, _, r, _, _ in r.selected = ["root", "other-root"] }
        reject { _, _, r, _, _ in r.selected = [] }
        reject { _, _, r, _, _ in r.selected = nil }
        reject { _, _, r, _, _ in r.selected = ["table"] }
        reject { _, _, _, p, _ in p.parent = "another-region" }
        reject { _, _, _, p, _ in p.children = ["editor", "second-editor"] }
        reject { _, _, _, p, _ in p.children = nil }
        reject { _, _, _, p, _ in p.selected = ["editor", "second-editor"] }
        reject { _, _, _, p, _ in p.selected = nil }
        reject { _, _, _, p, _ in p.role = "AXTextField" }  // Independent input.
        reject { _, _, _, p, _ in p.frame!.origin.x += 54 }  // Another cell.
        reject { _, _, _, p, _ in p.frame = anchor.frame }  // Not an inset editing surface.
        reject { _, _, _, p, _ in p.frame = CGRect(x: 0, y: 0, width: CGFloat.infinity, height: 10) }
        reject { _, _, _, _, e in e.parent = "another-root" }
        reject { _, _, _, _, e in e.role = "AXTextField" }
        reject { _, _, _, _, e in e.focused = false }
        reject { _, _, _, _, e in e.focused = nil }
        reject { _, _, _, _, e in e.frame!.origin.x += 100 }
        for index in 0..<5 {
            reject { o, t, r, p, e in
                switch index {
                case 0: o.window = "other"
                case 1: t.window = nil
                case 2: r.pid = 42
                case 3: p.window = "other"
                default: e.pid = 42
                }
            }
        }
        var initial = beforeRegion
        initial.children = nil
        precondition(
            NativeCellEditorEvidence.capture(
                owner: owner, table: table, region: initial, window: "window", pid: 41, same: same) == nil)
        initial = beforeRegion
        initial.selected = ["existing-editor"]
        precondition(
            NativeCellEditorEvidence.capture(
                owner: owner, table: table, region: initial, window: "window", pid: 41, same: same) == nil)
        // Width growth is a layout change only after a complete first proof.
        // An unknown/replaced receiver cannot enter through that second phase.
        func continued(_ o: Node = owner, _ t: Node = table, _ r: Node = region, _ p: Node = root, _ e: Node = editor)
            -> NativeCellEditorEvidence.OwnedEditor<String>?
        {
            NativeCellEditorEvidence.prove(
                anchor, owner: o, table: t, region: r, root: p, editor: e, retained: positive, same: same)
        }
        var wideRoot = root, wideEditor = editor
        wideRoot.frame!.size.width = 145
        wideEditor.frame!.size.width = 53
        precondition(prove(owner, table, region, wideRoot, wideEditor) == nil)
        precondition(continued(owner, table, region, wideRoot, wideEditor) != nil)
        // Continuation needs current origins, not a new size prerequisite.
        wideRoot.origin = wideRoot.frame!.origin
        wideRoot.frame = nil
        wideEditor.origin = wideEditor.frame!.origin
        wideEditor.frame = nil
        precondition(continued(owner, table, region, wideRoot, wideEditor) != nil)
        var scrolledOwner = owner, scrolledRoot = root, scrolledEditor = editor
        scrolledOwner.frame!.origin.x += 100
        scrolledRoot.frame!.origin.x += 100
        scrolledEditor.frame!.origin.x += 100
        precondition(continued(scrolledOwner, table, region, scrolledRoot, scrolledEditor) != nil)
        func rejectContinuation(_ edit: (inout Node, inout Node, inout Node, inout Node, inout Node) -> Void) {
            var o = owner, t = table, r = region, p = root, e = editor
            edit(&o, &t, &r, &p, &e)
            precondition(continued(o, t, r, p, e) == nil)
        }
        rejectContinuation { o, _, _, _, _ in o.focused = false }
        rejectContinuation { o, _, _, _, _ in o.pid = nil }
        rejectContinuation { o, _, _, _, _ in o.parent = "another-table" }
        rejectContinuation { o, _, _, _, _ in o.frame = nil }
        rejectContinuation { _, t, _, _, _ in t.parent = "another-region" }
        rejectContinuation { _, _, r, _, _ in r.children = ["table", "root"] }
        rejectContinuation { _, _, r, _, _ in r.children = nil }
        rejectContinuation { _, _, r, _, _ in r.selected = ["root", "other"] }
        rejectContinuation { _, _, _, p, _ in p.children = ["editor", "other"] }
        rejectContinuation { _, _, _, p, _ in p.selected = nil }
        rejectContinuation { _, _, _, p, _ in p.frame!.origin.x += 54 }
        rejectContinuation { _, _, _, _, e in e.frame!.origin.x += 54 }
        rejectContinuation { _, _, _, p, _ in p.frame = nil }
        rejectContinuation { _, _, _, p, _ in p.origin = CGPoint(x: CGFloat.nan, y: 249) }
        rejectContinuation { _, _, _, _, e in e.origin = CGPoint(x: 299, y: CGFloat.infinity) }
        rejectContinuation { o, _, _, _, _ in o.frame!.origin.x += 100 }
        rejectContinuation { _, _, _, p, _ in p.window = "other-window" }
        rejectContinuation { _, _, _, _, e in e.pid = 42 }
        rejectContinuation { _, _, _, _, e in e.focused = nil }
        rejectContinuation { _, _, _, _, e in e.role = "AXTextField" }
        rejectContinuation { _, _, _, p, e in
            p.children = ["new-editor"]
            p.selected = ["new-editor"]
            e = node("new-editor", "AXTextArea", parent: "root", focus: true, frame: editor.frame)
        }
        rejectContinuation { _, _, r, p, e in
            r.children = ["table", "decoration", "new-root"]
            r.selected = ["new-root"]
            p = node(
                "new-root", "AXGroup", parent: "region", frame: root.frame, children: ["editor"], selected: ["editor"])
            e.parent = "new-root"
        }
        print(
            """
            PASS: cell handoff requires initial inset proof; retained concrete editor permits growth while \
            ownership, origins and foreign/replaced receiver guards stay live
            """)
    }
}
