import ApplicationServices

/// Evidence for an addressed Select All keyboard recipe, not proof that an
/// AX string's complete character extent is selected. Rich editors can expose
/// generated paragraphs and non-editable text inside that extent. The native
/// command defines their editing selection; neither trim nor reinterpret it.
struct NativeKeyboardSelection {
    struct Member<Node> {
        let role: String?
        // A writable attribute can be inherited from the containing editor;
        // it does not by itself identify a separate editing root.
        let editingCapability: Bool?
        let parent: Node?
        var reportedEditor: Node? = nil
    }

    /// Marker owners may be passive text/layout children of the editor. Do
    /// not cross another editor, an unknown provider node, a cycle or a window.
    static func belongs<Node: Equatable>(_ start: Node, to editor: Node, member: (Node) -> Member<Node>?) -> Bool {
        var current = start, seen = [Node]()
        for _ in 0..<16 {
            if current == editor { return true }
            guard !seen.contains(current), let facts = member(current), let role = facts.role,
                !["AXTextField", "AXTextArea", "AXComboBox", "AXWebArea", "AXWindow", "AXSheet", "AXApplication"]
                    .contains(role), let editing = facts.editingCapability,
                !editing || ["AXStaticText", "AXGroup"].contains(role) && facts.reportedEditor == editor,
                facts.reportedEditor == nil || facts.reportedEditor == editor, let parent = facts.parent
            else { return false }
            seen.append(current)
            current = parent
        }
        return false
    }

    static func isScoped(
        numeric: () -> NativeTextSelection?, selected: () -> AXRead<CFTypeRef>, owns: (AXTextMarker) -> Bool
    ) -> Bool {
        func range() -> AXTextMarkerRange? {
            guard let value = selected().value, CFGetTypeID(value) == AXTextMarkerRangeGetTypeID() else { return nil }
            return unsafeDowncast(value, to: AXTextMarkerRange.self)
        }
        guard let before = numeric(), before.contiguous, before.location == 0, let length = before.length,
            // The count field is a scalar native character extent, not a collection.
            // swiftlint:disable:next empty_count
            before.count == 0 || length > 0, let selection = range(), owns(AXTextMarkerRangeCopyStartMarker(selection)),
            owns(AXTextMarkerRangeCopyEndMarker(selection)), let after = numeric(), after.contiguous,
            after.count == before.count, after.location == before.location, after.length == before.length,
            let current = range(), CFEqual(selection, current)
        else { return false }
        return true
    }
}
