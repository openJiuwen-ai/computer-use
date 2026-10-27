import CoreGraphics

/// A provider may keep the concrete cell focused while a selected sibling
/// editing surface receives the keys. This evidence never searches by name,
/// cell index, text value or a common window alone.
enum NativeCellEditorEvidence {
    struct Node<Element> {
        let id: Element
        var pid: Int32? = nil
        var role: String? = nil
        var window: Element? = nil
        var parent: Element? = nil
        var focused: Bool? = nil
        var enabled: Bool? = nil
        var frame: CGRect? = nil
        var origin: CGPoint? = nil
        var children: [Element]? = nil
        var selected: [Element]? = nil
    }

    struct Anchor<Element> {
        let owner: Element
        let table: Element
        let region: Element
        let window: Element
        let pid: Int32
        let frame: CGRect
        let siblings: [Element]
    }

    /// Only this helper can issue a positive handoff. A diagnostic snapshot's
    /// relatedEditor hint cannot construct or substitute for this proof.
    struct OwnedEditor<Element> {
        let owner: Element
        let editor: Element
        let window: Element
        let root: Element
        fileprivate init(owner: Element, editor: Element, window: Element, root: Element) {
            self.owner = owner
            self.editor = editor
            self.window = window
            self.root = root
        }
    }

    private static func frame(_ value: CGRect?) -> CGRect? {
        guard let value, !value.isNull, !value.isInfinite, value.origin.x.isFinite, value.origin.y.isFinite,
            value.width.isFinite, value.height.isFinite, value.width > 0, value.height > 0
        else { return nil }
        return value
    }

    private static func unique<Element>(_ values: [Element], same: (Element, Element) -> Bool) -> Bool {
        for (index, value) in values.enumerated() {
            if values.prefix(index).contains(where: { same($0, value) }) { return false }
        }
        return true
    }

    private static func belongs<Element>(
        _ node: Node<Element>, pid: Int32, window: Element, same: (Element, Element) -> Bool
    ) -> Bool { node.pid == pid && node.window.map { same($0, window) } == true && node.enabled != false }

    static func capture<Element>(
        owner: Node<Element>, table: Node<Element>, region: Node<Element>, window: Element, pid: Int32,
        same: (Element, Element) -> Bool
    ) -> Anchor<Element>? {
        guard owner.role == "AXTextField", owner.focused == true, owner.parent.map({ same($0, table.id) }) == true,
            owner.children?.isEmpty == true, let ownerFrame = frame(owner.frame), table.role == "AXTable",
            table.focused == true, table.parent.map({ same($0, region.id) }) == true, region.role == "AXGroup",
            region.selected?.isEmpty == true, let siblings = region.children, unique(siblings, same: same),
            siblings.contains(where: { same($0, table.id) }),
            [owner, table, region].allSatisfy({ belongs($0, pid: pid, window: window, same: same) })
        else { return nil }
        return Anchor(
            owner: owner.id, table: table.id, region: region.id, window: window, pid: pid, frame: ownerFrame,
            siblings: siblings)
    }

    /// The same concrete topology can advertise an editing preparation before
    /// focus is established. It grants no receiver or text-dispatch permission.
    static func canActivate<Element>(
        owner: Node<Element>, table: Node<Element>, region: Node<Element>, window: Element, pid: Int32,
        same: (Element, Element) -> Bool
    ) -> Bool {
        guard owner.role == "AXTextField", owner.parent.map({ same($0, table.id) }) == true,
            owner.children?.isEmpty == true, frame(owner.frame) != nil, table.role == "AXTable",
            table.parent.map({ same($0, region.id) }) == true, region.role == "AXGroup",
            region.selected?.isEmpty == true, let siblings = region.children, unique(siblings, same: same),
            siblings.contains(where: { same($0, table.id) }),
            [owner, table, region].allSatisfy({ belongs($0, pid: pid, window: window, same: same) })
        else { return false }
        return true
    }

    static func prove<Element>(
        _ anchor: Anchor<Element>, owner: Node<Element>, table: Node<Element>, region: Node<Element>,
        root: Node<Element>, editor: Node<Element>, retained: OwnedEditor<Element>? = nil,
        same: (Element, Element) -> Bool
    ) -> OwnedEditor<Element>? {
        guard owner.focused == true, table.focused == true, editor.focused == true else { return nil }
        return ownership(anchor, owner: owner, table: table, region: region, root: root, editor: editor,
                         retained: retained, same: same)
    }

    /// A previously issued active proof can survive temporary loss of keyboard
    /// focus. This establishes association lifetime only, never input readiness.
    static func association<Element>(
        _ anchor: Anchor<Element>, owner: Node<Element>, table: Node<Element>, region: Node<Element>,
        root: Node<Element>, editor: Node<Element>, retained: OwnedEditor<Element>,
        same: (Element, Element) -> Bool
    ) -> OwnedEditor<Element>? {
        ownership(anchor, owner: owner, table: table, region: region, root: root, editor: editor,
                  retained: retained, same: same)
    }

    private static func ownership<Element>(
        _ anchor: Anchor<Element>, owner: Node<Element>, table: Node<Element>, region: Node<Element>,
        root: Node<Element>, editor: Node<Element>, retained: OwnedEditor<Element>?,
        same: (Element, Element) -> Bool
    ) -> OwnedEditor<Element>? {
        guard same(owner.id, anchor.owner), same(table.id, anchor.table), same(region.id, anchor.region),
            owner.role == "AXTextField", owner.parent.map({ same($0, anchor.table) }) == true,
            owner.children?.isEmpty == true, table.role == "AXTable",
            table.parent.map({ same($0, anchor.region) }) == true, region.role == "AXGroup",
            let siblings = region.children, unique(siblings, same: same), siblings.count == anchor.siblings.count + 1,
            anchor.siblings.allSatisfy({ old in siblings.contains(where: { same($0, old) }) }),
            !anchor.siblings.contains(where: { same($0, root.id) }), siblings.contains(where: { same($0, root.id) }),
            let selected = region.selected, selected.count == 1, same(selected[0], root.id), root.role == "AXGroup",
            root.parent.map({ same($0, anchor.region) }) == true, let children = root.children, children.count == 1,
            same(children[0], editor.id), let selectedEditor = root.selected, selectedEditor.count == 1,
            same(selectedEditor[0], editor.id), editor.role == "AXTextArea",
            editor.parent.map({ same($0, root.id) }) == true, !same(owner.id, editor.id),
            [owner, table, region, root, editor].allSatisfy({
                belongs($0, pid: anchor.pid, window: anchor.window, same: same)
            })
        else { return nil }
        if let retained {
            // The first proof pins these concrete receiver objects for the
            // original target's lifetime. Width/height may grow as text is entered; moving the same
            // objects to another cell still cannot inherit the old ownership.
            guard same(retained.owner, anchor.owner), same(retained.window, anchor.window),
                same(retained.root, root.id), same(retained.editor, editor.id), let ownerFrame = frame(owner.frame),
                let rootOrigin = root.origin ?? root.frame?.origin,
                let editorOrigin = editor.origin ?? editor.frame?.origin, rootOrigin.x.isFinite, rootOrigin.y.isFinite,
                editorOrigin.x.isFinite, editorOrigin.y.isFinite, ownerFrame.contains(rootOrigin),
                ownerFrame.contains(editorOrigin)
            else { return nil }
        } else {
            guard frame(owner.frame) == anchor.frame, let rootFrame = frame(root.frame),
                let editorFrame = frame(editor.frame),
                // AX frames describe screen geometry, not parent clipping.
                // An existing cell value can require a wider editing root at
                // first paint; even an empty text line may be taller than it.
                // The complete new selected chain proves ownership. Both
                // editing origins must still anchor in the original cell, and
                // the editor must begin in that exact selected root.
                anchor.frame.contains(rootFrame.origin), anchor.frame.contains(editorFrame.origin),
                rootFrame.contains(editorFrame.origin)
            else { return nil }
        }
        return OwnedEditor(owner: anchor.owner, editor: editor.id, window: anchor.window, root: root.id)
    }
}
