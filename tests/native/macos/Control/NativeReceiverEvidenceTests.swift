@main struct NativeReceiverEvidenceTests {
    static func main() throws {
        let roles = [
            "document": "AXWindow", "other": "AXWindow", "sheet": "AXSheet", "popup": "AXPopover",
            "nested": "AXPopover", "custom": "AXGroup", "anchor": "AXButton", "cycle": "AXGroup",
            "application": "AXApplication"
        ]
        let parents = [
            "popup": "anchor", "anchor": "document", "document": "application", "sheet": "document", "nested": "sheet",
            "custom": "anchor", "cycle": "cycle"
        ]
        for (surface, window, top, expected) in [
            ("document", "other", "other", "document"), ("sheet", "document", "sheet", "sheet"),
            ("popup", "document", "document", "document"), ("custom", "document", "document", "document"),
            ("popup", "document", nil, "document"), ("popup", nil, "document", "document"),
            ("nested", "document", "sheet", "sheet"), ("nested", "document", nil, "nested"),
            ("popup", "other", "document", "popup"), ("popup", "document", "other", "popup"),
            ("popup", nil, nil, "popup"), ("cycle", "document", "document", "cycle"),
            ("unknown", "document", "document", "unknown")
        ] as [(String, String?, String?, String)] {
            let actual = NativeReceiverEvidence.keyboardWindow(
                surface: surface, same: ==, role: { roles[$0] }, parent: { parents[$0] }, window: { _ in window },
                topLevel: { _ in top })
            precondition(actual == expected, "Window routing requires consistent native ancestry and links")
        }
        enum Cancelled: Error { case request }
        do {
            _ = try NativeReceiverEvidence.keyboardWindow(
                surface: "popup", same: ==, role: { _ in throw Cancelled.request }, parent: { _ in nil },
                window: { _ in nil }, topLevel: { _ in nil })
            preconditionFailure("Cancellation must not fall back to an unvalidated route")
        } catch Cancelled.request {}
        // The application query is optional. Local evidence must be positive
        // for both this element and its window; nil is never affirmative.
        for absent in [false, true] {
            for owner: Bool? in [nil, false, true] {
                for focused: Bool? in [nil, false, true] {
                    let result = NativeReceiverEvidence.localElementRoute(
                        reportAbsent: absent, windowMatches: owner, focused: focused)
                    precondition((result == "yes") == (absent && owner == true && focused == true))
                }
            }
        }
        // Window-scoped keys do not require a full tree or named input field.
        precondition(
            NativeReceiverEvidence.windowRoute(focusedWindowMatches: true, focusPresent: false, focusWindowMatches: nil)
                == "yes")
        precondition(
            NativeReceiverEvidence.windowRoute(focusedWindowMatches: true, focusPresent: true, focusWindowMatches: true)
                == "yes")
        // Cached AX focus alone does not establish background event delivery.
        precondition(
            NativeReceiverEvidence.windowRoute(
                focusedWindowMatches: true, focusPresent: true, focusWindowMatches: true, deliveryReady: false) == "no")
        precondition(
            NativeReceiverEvidence.windowRoute(
                focusedWindowMatches: true, focusPresent: false, focusWindowMatches: nil, deliveryReady: false) == "no")
        // A window ID on an event cannot override contradictory AX ownership.
        precondition(
            NativeReceiverEvidence.windowRoute(
                focusedWindowMatches: true, focusPresent: true, focusWindowMatches: false) == "no")
        precondition(
            NativeReceiverEvidence.windowRoute(
                focusedWindowMatches: false, focusPresent: true, focusWindowMatches: true) == "no")
        precondition(
            NativeReceiverEvidence.windowRoute(
                focusedWindowMatches: false, focusPresent: false, focusWindowMatches: nil) == "no")
        // Missing control ownership is not a conflicting window. This proves
        // only the window route, not a particular Element receiver.
        precondition(
            NativeReceiverEvidence.windowRoute(focusedWindowMatches: true, focusPresent: true, focusWindowMatches: nil)
                == "yes")
        precondition(
            NativeReceiverEvidence.windowRoute(
                focusedWindowMatches: true, focusPresent: true, focusWindowMatches: nil, deliveryReady: false) == "no")
        precondition(
            NativeReceiverEvidence.windowRoute(focusedWindowMatches: nil, focusPresent: true, focusWindowMatches: true)
                == "unknown")
        precondition(
            NativeReceiverEvidence.windowRoute(focusedWindowMatches: nil, focusPresent: false, focusWindowMatches: nil)
                == "unknown")
        // An explicitly confirmed top-level editor supplies the missing window
        // report without authorizing another receiver, owner or delivery route.
        precondition(
            NativeReceiverEvidence.windowRoute(
                focusedWindowMatches: nil, focusPresent: true, focusWindowMatches: true, focusedRoot: true) == "yes")
        precondition(
            NativeReceiverEvidence.windowRoute(
                focusedWindowMatches: nil, focusPresent: true, focusWindowMatches: true, focusedRoot: true,
                deliveryReady: false) == "no")
        precondition(
            NativeReceiverEvidence.windowRoute(
                focusedWindowMatches: false, focusPresent: true, focusWindowMatches: true, focusedRoot: true) == "no")
        precondition(
            NativeReceiverEvidence.windowRoute(
                focusedWindowMatches: nil, focusPresent: true, focusWindowMatches: false, focusedRoot: true) == "no")
        precondition(
            NativeReceiverEvidence.windowRoute(
                focusedWindowMatches: nil, focusPresent: true, focusWindowMatches: nil, focusedRoot: true) == "unknown")
        precondition(
            NativeReceiverEvidence.windowRoute(
                focusedWindowMatches: nil, focusPresent: false, focusWindowMatches: true, focusedRoot: true)
                == "unknown")
        try attachedTextRoots()
        try optionalFocusReferences()
        print("PASS native receiver: exact window, missing tree, conflicting ownership and unknown focus")
    }

    static func attachedTextRoots() throws {
        func resolve(
            surface: String = "document", keyboardWindow: String = "document", focus: String? = "editor",
            absent: Bool = true, local: Bool? = true, role: String? = "AXTextField", declared: Bool = true,
            links: [String]? = [], attached: Bool = true
        ) -> String? {
            NativeReceiverEvidence.attachedTextRoot(
                surface: surface, keyboardWindow: keyboardWindow, focus: focus, windowReportAbsent: absent,
                locallyFocused: local, same: ==, role: { _ in role }, declaredRoot: { _ in declared },
                windowLinks: { _ in links }, nativeAttachment: { _, _ in attached })
        }
        for role in ["AXTextField", "AXTextArea"] {
            for links in [[], ["editor"], ["document"], ["editor", "document"]] {
                precondition(resolve(role: role, links: links) == "editor")
            }
        }
        // The original document stays the public boundary, while the exact
        // native child is the actual key recipient. Same-app/AXMain metadata
        // alone is not enough, nor can a nested logical range adopt its sibling.
        precondition(resolve(surface: "popover") == nil)
        precondition(resolve(focus: nil) == nil)
        precondition(resolve(focus: "document") == nil)
        precondition(resolve(absent: false) == nil)
        precondition(resolve(local: false) == nil)
        precondition(resolve(local: nil) == nil)
        precondition(resolve(role: "AXWindow") == nil)
        precondition(resolve(role: "AXButton") == nil)
        precondition(resolve(role: nil) == nil)
        precondition(resolve(declared: false) == nil)
        precondition(resolve(links: nil) == nil)
        precondition(resolve(links: ["other-document"]) == nil)
        precondition(resolve(attached: false) == nil)
        enum Cancelled: Error { case request }
        do {
            _ = try NativeReceiverEvidence.attachedTextRoot(
                surface: "document", keyboardWindow: "document", focus: "editor", windowReportAbsent: true,
                locallyFocused: true, same: ==, role: { _ in "AXTextField" }, declaredRoot: { _ in true },
                windowLinks: { _ in [] }, nativeAttachment: { _, _ in throw Cancelled.request })
            preconditionFailure("attachment query cancellation cannot authorize another receiver")
        } catch Cancelled.request {}
        print(
            """
            PASS attached text roots: exact native child, absent window report, original scope and \
            conflicting receiver protection
            """)
    }

    static func optionalFocusReferences() throws {
        // A valid requested editor must not inherit the lifetime of an expired
        // object returned by the provider's unrelated application-focus query.
        var registered = [String](), gaps = [String]()
        func register(_ object: String) throws -> String {
            registered.append(object)
            if object == "expired" { throw DriverError("native node no longer exists", code: .staleTarget) }
            return "ref:" + object
        }
        let unavailable = try NativeReceiverEvidence.focusReferences(
            reportedFocus: "expired", ancestors: ["old-parent"], register: register,
            onUnavailable: { part, _ in gaps.append(part) })
        precondition(unavailable.focus == nil && unavailable.ancestors.isEmpty)
        precondition(unavailable.focusState == .unavailable && unavailable.ancestorsState == .unavailable)
        precondition(registered == ["expired"] && gaps == ["focused object"])
        let reportAbsent = false  // The original successful getter returned a bad object, not noValue.
        precondition(
            NativeReceiverEvidence.localElementRoute(reportAbsent: reportAbsent, windowMatches: true, focused: true)
                == "unknown", "registration failure cannot enable local-focus fallback")

        registered = []
        gaps = []
        let partial = try NativeReceiverEvidence.focusReferences(
            reportedFocus: "actual-focus", ancestors: ["valid-parent", "expired", "unread-parent"], register: register,
            onUnavailable: { part, _ in gaps.append(part) })
        precondition(partial.focus == "ref:actual-focus" && partial.ancestors.isEmpty)
        precondition(partial.focusState == .available && partial.ancestorsState == .unavailable)
        precondition(registered == ["actual-focus", "valid-parent", "expired"] && gaps == ["focus ancestor 1"])
        let normal = try NativeReceiverEvidence.focusReferences(
            reportedFocus: "actual-focus", ancestors: ["parent", "window"], register: register,
            onUnavailable: { _, _ in preconditionFailure() })
        precondition(normal.focus == "ref:actual-focus" && normal.ancestors == ["ref:parent", "ref:window"])
        precondition(normal.focusState == .available && normal.ancestorsState == .available)
        let unreported = try NativeReceiverEvidence.focusReferences(
            reportedFocus: nil as String?, ancestors: [], register: register,
            onUnavailable: { _, _ in preconditionFailure() })
        precondition(unreported.focusState == .notReported && unreported.focus == nil)
        for code in [FailureCode.staleTarget, .observationIncomplete, .inputUnavailable] {
            let gap = try NativeReceiverEvidence.focusReferences(
                reportedFocus: "focus", ancestors: [],
                register: { _ in throw DriverError("optional object unreadable", code: code, effect: FailureEffect.none)
                }, onUnavailable: { _, error in precondition(error.code == code) })
            precondition(gap.focusState == .unavailable)
        }

        enum Cancelled: Error { case request }
        do {
            _ = try NativeReceiverEvidence.focusReferences(
                reportedFocus: "focus", ancestors: [], register: { _ in throw Cancelled.request },
                onUnavailable: { _, _ in preconditionFailure() })
            preconditionFailure("cancellation cannot be downgraded to an optional focus gap")
        } catch Cancelled.request {}
        for failure in [
            DriverError("deadline", code: .executionFailed), DriverError("permission", code: .desktopUnavailable),
            DriverError("bad request", code: .invalidRequest),
            DriverError("uncertain effect", code: .staleTarget, effect: .possible)
        ] {
            do {
                _ = try NativeReceiverEvidence.focusReferences(
                    reportedFocus: "focus", ancestors: [], register: { _ in throw failure },
                    onUnavailable: { _, _ in preconditionFailure() })
                preconditionFailure("operation failures and uncertain effects must propagate")
            } catch let actual as DriverError { precondition(actual.description == failure.description) }
        }
        // The optional-reference helper has no target, scope or dispatch API.
        // Keeping its gap never weakens existing conflicting-window evidence.
        precondition(
            NativeReceiverEvidence.windowRoute(
                focusedWindowMatches: false, focusPresent: true, focusWindowMatches: true) == "no")
        precondition(
            NativeReceiverEvidence.windowRoute(
                focusedWindowMatches: true, focusPresent: true, focusWindowMatches: false) == "no")
        print(
            """
            PASS optional focus references: unavailable is not absent; partial ancestry omitted; \
            cancellation and scope guards retained
            """)
    }
}
