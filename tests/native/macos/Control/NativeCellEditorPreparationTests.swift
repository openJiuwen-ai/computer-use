import AppKit
import ApplicationServices

/// Exercise the production focus-effect gate with a real ownership algorithm
/// and an injected AX setter; no application is focused and no event is posted.
@main struct NativeCellEditorPreparationTests {
    @MainActor static func main() throws {
        typealias Evidence = NativeCellEditorEvidence
        typealias Node = Evidence.Node<String>
        let same: (String, String) -> Bool = { $0 == $1 }
        func node(_ id: String, _ role: String, parent: String? = nil, frame: CGRect? = nil,
                  children: [String]? = nil, selected: [String]? = nil) -> Node {
            Node(id: id, pid: 41, role: role, window: "window", parent: parent, focused: true,
                 enabled: true, frame: frame, children: children, selected: selected)
        }
        let owner = node("owner", "AXTextField", parent: "table",
                         frame: CGRect(x: 290, y: 211, width: 94, height: 19), children: [])
        let table = node("table", "AXTable", parent: "region")
        let before = node("region", "AXGroup", children: ["table"], selected: [])
        let region = node("region", "AXGroup", children: ["table", "root"], selected: ["root"])
        let root = node("root", "AXGroup", parent: "region",
                        frame: CGRect(x: 291, y: 211, width: 91, height: 18),
                        children: ["editor"], selected: ["editor"])
        let editor = node("editor", "AXTextArea", parent: "root",
                          frame: CGRect(x: 291, y: 211, width: 1, height: 22))
        let anchor = Evidence.capture(owner: owner, table: table, region: before, window: "window", pid: 41, same: same)!
        let issued = Evidence.prove(anchor, owner: owner, table: table, region: region, root: root, editor: editor, same: same)!
        var lifetime = NativeCellEditorLifetime<Evidence.OwnedEditor<String>>()
        lifetime.retain(issued)
        var currentRoot = root, currentRegion = region, currentEditor = editor
        var mutations = 0, preparation = 0
        let opaqueEditor = AXUIElementCreateApplication(41)
        func effect() throws {
            try NativeReceiverPreparation.setFocus(
                opaqueEditor, detail: "native element focus",
                check: {
                    guard lifetime.resolve({ retained in
                        Evidence.association(anchor, owner: owner, table: table, region: currentRegion,
                                             root: currentRoot, editor: currentEditor, retained: retained, same: same)
                    }) != nil else {
                        throw DriverError("original cell editor ownership changed before focus", code: .inputUnavailable)
                    }
                }, onPreparation: { _ in preparation += 1 },
                set: { _, _, _ in mutations += 1; return .success })
        }
        func rejected() {
            do { try effect(); preconditionFailure("invalid ownership reached a focus mutation") }
            catch let error as DriverError { precondition(error.code == .inputUnavailable) }
            catch { preconditionFailure("unexpected effect guard error: \(error)") }
            precondition(mutations == 0 && preparation == 0,
                         "ownership must be rejected before focus setter and preparation journal")
        }
        // A wait/activation reused the exact retained root for another cell.
        // Native geometry can move, but that does not transfer its old owner.
        currentRoot.frame!.origin.x += 100
        currentEditor.frame!.origin.x += 100
        rejected()
        // Unknown relationships also grant no current mutation; they do not
        // erase the one established proof when a complete same-object read returns.
        currentRoot = root
        currentEditor = editor
        currentRegion.children = nil
        rejected()
        currentRegion = region
        try effect()
        precondition(mutations == 1 && preparation == 1)
        print("PASS selected editor moved before PREPARE is rejected before native focus; unknown ownership recovers only after complete same-object proof")
    }
}
