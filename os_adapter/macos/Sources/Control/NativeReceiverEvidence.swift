// Copyright (c) Huawei Technologies Co., Ltd. 2026. All rights reserved.

/// Native window-focus evidence for process-addressed keyboard events. A
/// window number in a posted event is not itself proof of the receiving window.
enum NativeReceiverEvidence {
    /// Input-context references are optional observations, not proof that the
    /// application's focused object is absent. A provider can return an expired
    /// focus proxy while the requested control itself remains valid. Do not let
    /// registration of that unrelated proxy erase the control's native routes.
    struct FocusReferences {
        enum State { case notReported, available, unavailable }
        let focus: String?
        let ancestors: [String]
        let focusState: State
        let ancestorsState: State
    }

    static func focusReferences<Element>(
        reportedFocus: Element?, ancestors: [Element], register: (Element) throws -> String,
        onUnavailable: (String, DriverError) -> Void
    ) throws -> FocusReferences {
        guard let reportedFocus else {
            return FocusReferences(focus: nil, ancestors: [], focusState: .notReported, ancestorsState: .notReported)
        }
        func optional(_ element: Element, part: String) throws -> String? {
            do { return try register(element) } catch let error as DriverError
                where [.staleTarget, .observationIncomplete, .inputUnavailable].contains(error.code)
                && (error.effect == nil || error.effect == FailureEffect.none)
            {
                onUnavailable(part, error)
                return nil
            }
        }
        guard let focus = try optional(reportedFocus, part: "focused object") else {
            return FocusReferences(focus: nil, ancestors: [], focusState: .unavailable, ancestorsState: .unavailable)
        }
        var references = [String]()
        for (index, ancestor) in ancestors.enumerated() {
            guard let reference = try optional(ancestor, part: "focus ancestor \(index)") else {
                // The wire carries no completeness marker for this hint list.
                // Keep the exact focus, but do not publish a partial parent
                // chain as though it were the complete registered ancestry.
                return FocusReferences(
                    focus: focus, ancestors: [], focusState: .available, ancestorsState: .unavailable)
            }
            references.append(reference)
        }
        return FocusReferences(focus: focus, ancestors: references, focusState: .available, ancestorsState: .available)
    }

    /// Non-window interaction surfaces can be hosted by an ancestor's keyboard
    /// window. Require both the native link and a matching live parent path;
    /// a same-process window or a cached ownership hint is insufficient.
    static func keyboardWindow<Element>(
        surface: Element, same: (Element, Element) -> Bool, role: (Element) throws -> String?,
        parent: (Element) throws -> Element?, window: (Element) throws -> Element?,
        topLevel: (Element) throws -> Element?
    ) rethrows -> Element {
        guard let ownRole = try role(surface), !["AXWindow", "AXSheet"].contains(ownRole) else { return surface }
        let declaredWindow = try window(surface), declaredTop = try topLevel(surface)
        guard declaredWindow != nil || declaredTop != nil else { return surface }
        var current = try parent(surface), seen = [surface]
        for _ in 0..<32 {
            guard let node = current, !seen.contains(where: { same($0, node) }), let value = try role(node),
                value != "AXApplication"
            else { return surface }
            if ["AXWindow", "AXSheet"].contains(value) {
                if let declaredTop, !same(declaredTop, node) { return surface }
                if value == "AXWindow", let declaredWindow, !same(declaredWindow, node) { return surface }
                return declaredTop.map { same($0, node) } == true
                    || (value == "AXWindow" && declaredWindow.map { same($0, node) } == true) ? node : surface
            }
            seen.append(node)
            current = try parent(node)
        }
        return surface
    }

    /// Missing application focus metadata does not erase an exact element's
    /// own keyboard-focus report. A present or failed report is not absence.
    static func localElementRoute(reportAbsent: Bool, windowMatches: Bool?, focused: Bool?) -> String {
        guard reportAbsent else { return "unknown" }
        if windowMatches == false || focused == false { return "no" }
        return windowMatches == true && focused == true ? "yes" : "unknown"
    }

    /// A transient text editor can be a declared AXWindows root while the
    /// application omits AXFocusedWindow. Preserve that current receiver only
    /// with an exact live native attachment to the requested window; a main
    /// window, common process or geometry never supplies this relationship.
    static func attachedTextRoot<Element>(
        surface: Element, keyboardWindow: Element, focus: Element?, windowReportAbsent: Bool, locallyFocused: Bool?,
        same: (Element, Element) -> Bool, role: (Element) throws -> String?, declaredRoot: (Element) throws -> Bool,
        windowLinks: (Element) throws -> [Element]?, nativeAttachment: (Element, Element) throws -> Bool
    ) rethrows -> Element? {
        guard windowReportAbsent, locallyFocused == true, same(surface, keyboardWindow), let focus,
            !same(focus, keyboardWindow), ["AXTextField", "AXTextArea"].contains(try role(focus) ?? ""),
            try declaredRoot(focus), let links = try windowLinks(focus),
            links.allSatisfy({ same($0, focus) || same($0, keyboardWindow) }),
            try nativeAttachment(focus, keyboardWindow)
        else { return nil }
        return focus
    }

    static func windowRoute(
        focusedWindowMatches: Bool?, focusPresent: Bool, focusWindowMatches: Bool?, focusedRoot: Bool = false,
        deliveryReady: Bool = true
    ) -> String {
        if focusedWindowMatches == false || (focusPresent && focusWindowMatches == false) { return "no" }
        // A provider can expose the active editor itself as an AXWindows root
        // and omit AXFocusedWindow. The caller must establish that exact live
        // root and its local keyboard focus; arbitrary focused descendants or
        // an absent/erroring window lookup alone do not establish this route.
        guard focusedWindowMatches == true || (focusedRoot && focusPresent && focusWindowMatches == true) else {
            return "unknown"
        }
        // The focused window establishes the window route independently of an
        // incomplete control-focus report. A known conflicting owner above
        // still refuses it. Element readiness separately requires a confirmed
        // receiver; missing control ownership cannot authorize an Element.
        return deliveryReady ? "yes" : "no"
    }
}
