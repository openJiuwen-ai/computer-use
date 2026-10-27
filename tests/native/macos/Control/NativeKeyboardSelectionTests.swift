import ApplicationServices

@main struct NativeKeyboardSelectionTests {
    static func main() {
        func range(_ start: UInt8, _ end: UInt8) -> AXTextMarkerRange {
            AXTextMarkerRangeCreate(nil, AXTextMarkerCreate(nil, [start], 1), AXTextMarkerCreate(nil, [end], 1))
        }
        var numeric = NativeTextSelection(count: 10, location: 0, length: 8)
        var selected: AXRead<CFTypeRef> = .value(range(1, 8))
        var owned = true
        func sample() -> Bool {
            NativeKeyboardSelection.isScoped(numeric: { numeric }, selected: { selected }, owns: { _ in owned })
        }
        // This is a valid native command selection, not independently verified
        // full AX string coverage. No zero-width character/newline is stripped.
        precondition(sample() && !numeric.full)
        numeric = NativeTextSelection(count: 2, location: 0, length: 0)
        selected = .value(range(1, 1))
        precondition(!sample() && !numeric.full)
        numeric = NativeTextSelection(count: 10, location: 2, length: 8)
        precondition(!sample())
        numeric = NativeTextSelection(count: 10, location: 0, length: 8, contiguous: false)
        precondition(!sample())
        numeric = NativeTextSelection(count: 10, location: 0, length: 8)
        owned = false
        precondition(!sample())
        owned = true
        for invalid: AXRead<CFTypeRef> in [
            .unsupported, .unavailable(.cannotComplete), .invalid, .value("not a marker range" as CFString)
        ] {
            selected = invalid
            precondition(!sample())
        }
        selected = .value(range(1, 8))
        var reads = 0
        precondition(
            !NativeKeyboardSelection.isScoped(
                numeric: { numeric },
                selected: {
                    reads += 1
                    return .value(reads == 1 ? range(1, 8) : range(2, 8))
                }, owns: { _ in true }))
        reads = 0
        precondition(
            !NativeKeyboardSelection.isScoped(
                numeric: {
                    reads += 1
                    return NativeTextSelection(count: 10, location: 0, length: reads == 1 ? 8 : 9)
                }, selected: { selected }, owns: { _ in true }))
        precondition(!NativeKeyboardSelection.isScoped(numeric: { nil }, selected: { selected }, owns: { _ in true }))
        typealias Member = NativeKeyboardSelection.Member<Int>
        var tree = [
            1: Member(role: "AXStaticText", editingCapability: false, parent: 2),
            2: Member(role: "AXGroup", editingCapability: false, parent: 3)
        ]
        func belongs() -> Bool { NativeKeyboardSelection.belongs(1, to: 3) { tree[$0] } }
        precondition(belongs())
        for role in ["AXTextField", "AXTextArea", "AXComboBox", "AXWebArea", "AXWindow", "AXSheet", "AXApplication"] {
            tree[2] = Member(role: role, editingCapability: false, parent: 3)
            precondition(!belongs())
        }
        for editable: Bool? in [true, nil] {
            for role in ["AXGroup", "AXUnknown", "CustomEditor"] {
                tree[2] = Member(role: role, editingCapability: editable, parent: 3)
                precondition(!belongs())
            }
        }
        tree[2] = Member(role: nil, editingCapability: false, parent: 3)
        precondition(!belongs())
        tree[2] = Member(role: "AXGroup", editingCapability: false, parent: 1)
        precondition(!belongs())
        tree[2] = Member(role: "AXGroup", editingCapability: false, parent: 4)
        precondition(!belongs())
        tree[2] = Member(role: "AXGroup", editingCapability: false, parent: 3, reportedEditor: 4)
        precondition(!belongs())
        tree[2] = Member(role: "AXGroup", editingCapability: false, parent: 3, reportedEditor: 3)
        precondition(belongs())
        // Rich text descendants can inherit writable Value/SelectedText.
        // A known content role plus the exact editing root disambiguates them;
        // parentage alone or a conflicting/missing root still cannot.
        tree[1] = Member(role: "AXStaticText", editingCapability: true, parent: 2, reportedEditor: 3)
        tree[2] = Member(role: "AXGroup", editingCapability: true, parent: 3, reportedEditor: 3)
        precondition(belongs())
        for reported: Int? in [nil, 1, 2, 4] {
            tree[1] = Member(role: "AXStaticText", editingCapability: true, parent: 2, reportedEditor: reported)
            precondition(!belongs())
        }
        tree[1] = Member(role: "AXStaticText", editingCapability: true, parent: 2, reportedEditor: 3)
        for role in ["AXTextField", "AXTextArea", "AXComboBox", "AXWebArea", "CustomEditor", "AXUnknown"] {
            tree[2] = Member(role: role, editingCapability: true, parent: 3, reportedEditor: 3)
            precondition(!belongs())
        }
        tree[2] = Member(role: "AXGroup", editingCapability: nil, parent: 3, reportedEditor: 3)
        precondition(!belongs())
        precondition(NativeKeyboardSelection.belongs(3, to: 3) { _ in nil })
        let deep = (1...20).reduce(into: [Int: Member]()) {
            $0[$1] = Member(role: "AXGroup", editingCapability: false, parent: $1 + 1)
        }
        precondition(!NativeKeyboardSelection.belongs(1, to: 21) { deep[$0] })
        print("PASS keyboard selection stays scoped and stable without claiming full AX string coverage")
    }
}
