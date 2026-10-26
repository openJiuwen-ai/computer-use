import ApplicationServices

/// Read-only, call-local adapter for one originally focused table cell. Never
/// enumerates the table, changes focus or adopts another semantic owner.
@MainActor final class NativeCellEditorHandoff {
    typealias Node = NativeCellEditorEvidence.Node<AXUIElement>
    typealias Proof = NativeCellEditorEvidence.OwnedEditor<AXUIElement>
    private let anchor: NativeCellEditorEvidence.Anchor<AXUIElement>
    private var confirmed: Proof?
    private init(anchor: NativeCellEditorEvidence.Anchor<AXUIElement>) { self.anchor = anchor }

    private static func position(_ element: AXUIElement) -> CGPoint? {
        guard let raw = AXAccess.read(element, kAXPositionAttribute).value, CFGetTypeID(raw) == AXValueGetTypeID()
        else { return nil }
        var point = CGPoint.zero
        return AXValueGetValue(unsafeDowncast(raw, to: AXValue.self), .cgPoint, &point) ? point : nil
    }
    private static func bounds(_ element: AXUIElement, origin: CGPoint?) -> CGRect? {
        guard let origin, let dimensions = AXAccess.read(element, kAXSizeAttribute).value,
            CFGetTypeID(dimensions) == AXValueGetTypeID()
        else { return nil }
        var size = CGSize.zero
        guard AXValueGetValue(unsafeDowncast(dimensions, to: AXValue.self), .cgSize, &size) else { return nil }
        return CGRect(origin: origin, size: size)
    }

    private static func node(
        _ element: AXUIElement, children: Int? = nil, selected: Bool = false, geometry: Bool = false,
        origin: Bool = false
    ) throws -> Node {
        try RequestDiagnostics.checkCancellation()
        var pid: pid_t = 0
        let ownerPID = AXUIElementGetPid(element, &pid) == .success ? pid : nil
        let point = geometry || origin ? position(element) : nil
        let result = Node(
            id: element, pid: ownerPID, role: AXAccess.read(element, kAXRoleAttribute).value as? String,
            window: AXAccess.element(element, kAXWindowAttribute),
            parent: AXAccess.element(element, kAXParentAttribute),
            focused: AXAccess.read(element, kAXFocusedAttribute).value as? Bool,
            enabled: AXAccess.read(element, kAXEnabledAttribute).value as? Bool,
            frame: geometry ? bounds(element, origin: point) : nil, origin: point,
            children: children.flatMap { AXAccess.elements(element, kAXChildrenAttribute, limit: $0) },
            selected: selected ? AXAccess.elements(element, kAXSelectedChildrenAttribute, limit: 2) : nil)
        try RequestDiagnostics.checkCancellation()
        return result
    }

    static func capture(element: AXUIElement?, window: AXUIElement, focus: AXUIElement?, pid: pid_t) throws -> Self? {
        guard let focus, element == nil || element.map({ CFEqual($0, window) || CFEqual($0, focus) }) == true,
            AXAccess.read(focus, kAXRoleAttribute).value as? String == kAXTextFieldRole,
            let table = AXAccess.element(focus, kAXParentAttribute),
            AXAccess.read(table, kAXRoleAttribute).value as? String == kAXTableRole,
            let region = AXAccess.element(table, kAXParentAttribute),
            AXAccess.read(region, kAXRoleAttribute).value as? String == kAXGroupRole
        else { return nil }
        guard
            let anchor = try NativeCellEditorEvidence.capture(
                owner: node(focus, children: 1, geometry: true), table: node(table),
                region: node(region, children: 64, selected: true), window: window, pid: pid, same: { CFEqual($0, $1) })
        else { return nil }
        return Self(anchor: anchor)
    }

    func prove(_ state: NativeReceiverSnapshot) throws -> Proof? {
        guard state.windowRoute == "yes", state.scopeRoute == "yes",
            state.window.map({ CFEqual($0, anchor.window) }) == true, let current = state.focus,
            !CFEqual(current, anchor.owner), let root = AXAccess.element(current, kAXParentAttribute)
        else { return nil }
        guard
            let proof = try NativeCellEditorEvidence.prove(
                anchor, owner: Self.node(anchor.owner, children: 1, geometry: true), table: Self.node(anchor.table),
                region: Self.node(anchor.region, children: 64, selected: true),
                root: Self.node(root, children: 2, selected: true, geometry: confirmed == nil, origin: true),
                editor: Self.node(current, geometry: confirmed == nil, origin: true), retained: confirmed,
                same: { CFEqual($0, $1) })
        else { return nil }
        try RequestDiagnostics.checkCancellation()
        let application = AXUIElementCreateApplication(anchor.pid)
        guard AXAccess.element(application, kAXFocusedUIElementAttribute).map({ CFEqual($0, current) }) == true,
            AXAccess.read(anchor.owner, kAXFocusedAttribute).value as? Bool == true
        else { return nil }
        // Retain only an already issued proof after the final live focus
        // check. This receiver lifetime ends with this native insert call.
        confirmed = proof
        return proof
    }
}
