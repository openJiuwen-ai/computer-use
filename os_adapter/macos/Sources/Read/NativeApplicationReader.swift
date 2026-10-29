// Copyright (c) Huawei Technologies Co., Ltd. 2026. All rights reserved.

import AppKit
import ApplicationServices

/// Native window catalogue, validated handles and immutable text pages.
/// Semantic composition, tree expansion policy and rendering belong to Python.
@MainActor final class NativeApplicationReader {
    private typealias Identity = ApplicationElements.Identity
    private let registry: ApplicationElements
    private let inspection: NativeElementInspection
    private let query: NativeWindowQueryAPI
    init(registry: ApplicationElements, inspection: NativeElementInspection, query: NativeWindowQueryAPI? = nil) {
        self.registry = registry
        self.inspection = inspection
        self.query = query ?? NativeWindowQueryAPI(attribute: inspection.attribute)
    }

    struct ReadRoot {
        let element: AXUIElement
        let application: NSRunningApplication
        let scope: String
        let ancestors: [AXUIElement]
        let observedInteractionSurface: AXUIElement?
        let validate: () throws -> Void
        var cellEditorState: NativeCellEditorState? = nil
        var popupOwner: NativePopupOwnerStamp<AXUIElement, NativeProcessIdentity>? = nil
    }

    func readRoot(_ ref: String) throws -> ReadRoot {
        guard let target = registry.target(ref) else {
            throw DriverError("element reference expired; read its window again", code: .staleTarget)
        }
        // Inspection is allowed for disabled controls and blocked backgrounds.
        // Keep identity checks separate from input preparation and permission to act.
        let validate = {
            try BackgroundTarget(target.application).validate()
            try self.inspection.validateTargetIdentity(target)
        }
        try validate()
        return ReadRoot(
            element: target.element, application: target.application, scope: target.scope, ancestors: target.ancestors,
            observedInteractionSurface: target.observedInteractionSurface, validate: validate,
            cellEditorState: target.cellEditorState, popupOwner: target.popupOwner)
    }

    func readText(_ ref: String, offset: Int, length: Int) throws -> [String: Any] {
        guard let snapshot = registry.text(ref) else {
            throw DriverError("snapshot expired; read the element again to capture a new Value", code: .snapshotExpired)
        }
        guard let value = snapshot.value else {
            throw DriverError(
                "the observed element has no readable string Value",
                next: "read the app or use its screenshot; an unavailable value is not empty text")
        }
        let text = value as NSString
        guard offset <= text.length else {
            throw DriverError("text offset exceeds observed Value length \(text.length)", code: .invalidRequest)
        }
        func insidePair(_ index: Int) -> Bool {
            index > 0 && index < text.length && (0xD800...0xDBFF).contains(text.character(at: index - 1))
                && (0xDC00...0xDFFF).contains(text.character(at: index))
        }
        guard !insidePair(offset) else {
            throw DriverError(
                "text offset splits a Unicode surrogate pair; use the previous page's next offset",
                code: .invalidRequest)
        }
        var end = offset + min(length, text.length - offset)
        if insidePair(end) { end -= 1 }
        return [
            "text": text.substring(with: NSRange(location: offset, length: end - offset)), "offset": offset,
            "total": text.length, "next": end < text.length ? end as Any : NSNull()
        ]
    }

    struct Window {
        var ref: String
        let title: String
        let element: AXUIElement
        var kind: String = "window"
        var parent: String? = nil
        var parentUnknown: Bool = false
        var nativeID: CGWindowID? = nil
        var modal: Bool? = nil
        var document: String? = nil
    }

    func focusedWindow(_ app: NSRunningApplication, windows: [Window]) -> String? {
        guard query.trusted() else { return nil }
        let root = query.application(app.processIdentifier)
        guard let focused = query.element(root, kAXFocusedWindowAttribute) else { return nil }
        let matches = windows.filter { CFEqual($0.element, focused) }
        return matches.count == 1 ? matches[0].ref : nil
    }

    func windows(
        _ app: NSRunningApplication, nativeWindows: [NativeWindows.Window] = [], contentRoot: AXUIElement? = nil,
        purpose: NativeWindowQueryPurpose = .observation, reportIssue: ((String) -> Void)? = nil
    ) throws -> [Window] {
        guard
            case .catalogue(let windows) = try collectWindows(
                app, nativeWindows: nativeWindows, contentRoot: contentRoot, purpose: purpose, projection: .catalogue,
                reportIssue: reportIssue)
        else { preconditionFailure("catalogue adapter received a boundary projection") }
        return windows
    }

    func keyboardBoundary(_ app: NSRunningApplication, contentRoot: AXUIElement) throws
        -> NativeKeyboardBoundaryProjection<AXUIElement>
    {
        guard
            case .boundary(let result) = try collectWindows(
                app, contentRoot: contentRoot, purpose: .inputValidation, projection: .keyboardDispatch)
        else { preconditionFailure("boundary adapter received a catalogue") }
        return result
    }

    /// Physical window input needs current surface boundaries, not a rendered
    /// directory or all controls below a file browser. Rows retain uncertainty;
    /// this projection alone never grants permission to post an event.
    struct InputBoundary {
        let windows: [Window]
        let issue: String?
        let expandedRequiredSurface: Bool
        var unresolvedModalHints = false
    }

    func inputBoundary(
        _ app: NSRunningApplication, nativeWindows: [NativeWindows.Window], contentRoot: AXUIElement?,
        required: [AXUIElement], modalHints: [AXUIElement]
    ) throws -> InputBoundary {
        guard case .inputBoundary(let result) = try collectWindows(
            app, nativeWindows: nativeWindows, contentRoot: contentRoot, purpose: .inputValidation,
            projection: .inputBoundary, required: required, modalHints: modalHints)
        else { preconditionFailure("input boundary adapter received another projection") }
        return result
    }

    private enum Projection: Equatable { case catalogue, keyboardDispatch, inputBoundary }
    private enum Collection {
        case catalogue([Window])
        case boundary(NativeKeyboardBoundaryProjection<AXUIElement>)
        case inputBoundary(InputBoundary)
    }

    private func collectWindows(
        _ app: NSRunningApplication, nativeWindows: [NativeWindows.Window] = [], contentRoot: AXUIElement? = nil,
        purpose: NativeWindowQueryPurpose, projection: Projection, reportIssue: ((String) -> Void)? = nil,
        parentQuery: String? = nil, required: [AXUIElement] = [], modalHints: [AXUIElement] = []
    ) throws -> Collection {
        let timing = NativeWindowQueryTiming.start(metadata: {
            ["pid": app.processIdentifier, "purpose": purpose == .inputValidation ? "inputValidation" : "observation",
             "projection": projection == .catalogue ? "catalogue" : projection == .keyboardDispatch ? "keyboardDispatch" : "inputBoundary",
             "parentQuery": parentQuery as Any? ?? NSNull(), "hasContentRoot": contentRoot != nil]
        })
        if let contentRoot { timing?.root(contentRoot, selected: true) }
        var outcome = "threw"
        defer { timing?.finish(outcome: outcome) }
        func timed<Value>(
            _ phase: String, property: String? = nil, root: AXUIElement? = nil, _ body: () throws -> Value
        ) rethrows -> Value {
            try NativeWindowQueryTiming.measure(timing, phase, property: property, root: root, body: body)
        }
        func attribute(
            _ node: AXUIElement, _ key: String, stage: String = "getter.catalogue", root: AXUIElement? = nil
        ) -> CFTypeRef? {
            timed(stage, property: key, root: root ?? node) { query.attribute(node, key) }
        }
        func linkedElement(_ node: AXUIElement, _ key: String) -> AXUIElement? {
            timed("getter.link", property: key) { query.element(node, key) }
        }
        func queryRead(_ node: AXUIElement, _ key: String) -> AXRead<CFTypeRef> {
            timed("getter.read", property: key, root: node) { query.read(node, key) }
        }
        var boundaryIssue: String?
        func issue(_ detail: String) {
            if boundaryIssue == nil { boundaryIssue = detail }
            reportIssue?(detail)
        }
        try RequestDiagnostics.checkCancellation()
        guard query.trusted() else {
            outcome = "returned"
            if projection == .inputBoundary {
                return .inputBoundary(.init(windows: [], issue: "AX trust unavailable", expandedRequiredSurface: false,
                                            unresolvedModalHints: !modalHints.isEmpty))
            }
            return projection == .catalogue
                ? .catalogue([]) : .boundary(.init(windows: [], issue: "AX trust unavailable"))
        }
        let process =
            nativeWindows.first { $0.process.pid == app.processIdentifier }?.process
            ?? NativeProcessIdentity.read(app.processIdentifier)
        let incarnation = process?.incarnation
        let root = query.application(app.processIdentifier)
        // Only this synchronous input collector can consume the helper's last
        // actual declaration attempt. Observation and final receiver samples
        // retain their independent reads; no earlier array is fed to helpers.
        let capture: NativeWindowDeclarationCapture? =
            purpose == .inputValidation ? process.map { NativeWindowDeclarationCapture(root: root, process: $0) } : nil
        defer { capture?.close() }
        let sources: NativeWindowCandidateQuery.Sources<AXUIElement> = NativeWindowCandidateQuery.collect(
            purpose: purpose,
            declared: {
                let read = queryRead(root, kAXWindowsAttribute)
                if projection != .catalogue {
                    guard let raw = read.value, let elements = NativeWindowDeclarationCapture.completeElements(raw)
                    else {
                        issue("AXWindows catalogue unavailable or malformed")
                        return []
                    }
                    return elements
                }
                if read.value as? [AXUIElement] == nil { issue("AXWindows catalogue unavailable or malformed") }
                return read.value as? [AXUIElement] ?? []
            },
            hints: {
                var hints = [AXUIElement]()
                for key in [kAXFocusedWindowAttribute, kAXMainWindowAttribute] {
                    if let raw = attribute(root, key), CFGetTypeID(raw) == AXUIElementGetTypeID() {
                        hints.append(unsafeDowncast(raw, to: AXUIElement.self))
                    }
                }
                return hints
            },
            inputWindow: {
                let observer: NativeWindowsReadObserver?
                if let capture {
                    observer = { pid, root, read in capture.record(pid: pid, root: root, read: read) }
                } else {
                    observer = nil
                }
                return timed("helper.inputWindow") {
                    query.inputWindow(app.processIdentifier, observer, timing)
                }
            },
            latestDeclared: {
                // This additional non-AX identity query only decides reuse. Unknown
                // or changed process identity takes the original fresh getter path.
                guard let capture else { return nil }
                let captured = capture.take(currentProcess: NativeProcessIdentity.read(app.processIdentifier))
                timing?.count(captured == nil ? "declarationCaptureMiss" : "declarationCaptureHit")
                return captured
            })
        let declaredRoots = sources.declared
        var candidates = sources.candidates
        var result = [Window]()
        var owners = [(child: AXUIElement, parent: AXUIElement)]()
        var unknownBoundaryParents = [AXUIElement]()
        let surfaceRoles = [kAXWindowRole, kAXSheetRole, "AXPopover"]
        var focus = linkedElement(root, kAXFocusedUIElementAttribute), focusPath = [AXUIElement]()
        var focusedSurface: AXUIElement?
        let focusDeadline = ProcessInfo.processInfo.systemUptime + 0.1
        while let node = focus, focusPath.count < 32, ProcessInfo.processInfo.systemUptime < focusDeadline,
            !focusPath.contains(where: { CFEqual($0, node) })
        {
            try RequestDiagnostics.checkCancellation()
            focusPath.append(node)
            if surfaceRoles.contains(attribute(node, kAXRoleAttribute) as? String ?? "") {
                candidates.append(node)
                if let child = focusedSurface { owners.append((child, node)) }
                focusedSurface = node
            }
            focus = linkedElement(node, kAXParentAttribute)
        }
        if focus != nil { issue("focused surface ancestry incomplete") }
        // AppKit attaches popovers to their anchor control, not necessarily to
        // AXWindows or a window's immediate children. Discover surface roots
        // through actual child edges; never infer ownership from screen overlap.
        let surfaceDeadline = ProcessInfo.processInfo.systemUptime + 0.4
        var scanned = [AXUIElement]()
        let surfaceRelations = AXRelationReader(api: timing?.relationAPI(query.relations) ?? query.relations)
        var surfaceIssues = Set<String>()
        func surfaceIssue(_ detail: String) { if surfaceIssues.insert(detail).inserted { issue(detail) } }
        func nestedSurfaces(_ parent: AXUIElement, recursive: Bool) throws -> [AXUIElement] {
            try timed("phase.surfaceDiscovery", root: parent) {
                timing?.surfaceRoot = parent
                let initialScanned = timing.map { _ in scanned.count }
                var returnedRoles: [String: Int]? = timing.map { _ in [:] }
                var stops: [String: Int]? = timing.map { _ in [:] }
                var returned = false
                func stop(_ reason: String) { if stops != nil { stops![reason, default: 0] += 1 } }
                var pending = [parent], found = [AXUIElement](), index = 0
                defer {
                    if let timing, let initialScanned {
                        timing.surface(
                            parent, recursive: recursive, visited: scanned.count - initialScanned,
                            queued: pending.count, found: found.count, pending: pending.count - index,
                            roles: returnedRoles ?? [:], stops: stops ?? [:], outcome: returned ? "returned" : "threw")
                        timing.surfaceRoot = nil
                    }
                }
                while index < pending.count, scanned.count < 512, ProcessInfo.processInfo.systemUptime < surfaceDeadline {
                    let node = pending[index]
                    index += 1
                    try RequestDiagnostics.checkCancellation()
                    if scanned.contains(where: { CFEqual($0, node) }) { continue }
                    scanned.append(node)
                    let available = max(0, 512 - scanned.count - (pending.count - index))
                    let relation = try surfaceRelations.read(
                        node, source: "surface-discovery", property: kAXChildrenAttribute, limit: available,
                        withinBudget: {
                            try RequestDiagnostics.checkCancellation()
                            return ProcessInfo.processInfo.systemUptime < surfaceDeadline
                        })
                    guard case .page(let page) = relation else {
                        if case .failure(let failure) = relation,
                            (available == 0 || !["unsupported", "absent"].contains(failure["state"] as? String ?? "")
                                || failure["nativeCode"] as? Int32 == AXError.notImplemented.rawValue)
                        {
                            stop("children_unavailable")
                            surfaceIssue(
                                "attached surface children unavailable: "
                                    + (failure["detail"] as? String ?? "unknown AX query failure"))
                        }
                        continue
                    }
                    if page.prefix.count < page.total || page.issue != nil {
                        stop("relation_incomplete")
                        surfaceIssue("attached surface discovery incomplete: child/node budget or changing relation")
                    }
                    for child in page.targets {
                        if ProcessInfo.processInfo.systemUptime >= surfaceDeadline {
                            stop("time_budget")
                            surfaceIssue("attached surface discovery incomplete: time budget")
                            break
                        }
                        let role = attribute(child, kAXRoleAttribute, stage: "getter.surface.role", root: parent) as? String ?? ""
                        if returnedRoles != nil { returnedRoles![role, default: 0] += 1 }
                        if surfaceRoles.contains(role) {
                            found.append(child)
                        } else if recursive {
                            pending.append(child)
                        }
                    }
                }
                if index < pending.count {
                    stop("pending_time_or_node_budget")
                    surfaceIssue("attached surface discovery incomplete: time/node budget")
                }
                returned = true
                return found
            }
        }
        var inputFrontiers = [AXUIElement](), frontierCursor = 0
        var inputParents = [(child: AXUIElement, parent: AXUIElement?, known: Bool)]()
        var inputSheets = [AXUIElement]()
        var modalHintCursor = 0, absentModalHints = [AXUIElement]()
        func unresolvedModalHints(in windows: [Window]) -> Bool {
            modalHints.contains { hint in
                if absentModalHints.contains(where: { CFEqual($0, hint) }) { return false }
                return windows.first(where: { CFEqual($0.element, hint) })?.modal == nil
            }
        }
        func inputParent(_ child: AXUIElement) throws -> (parent: AXUIElement?, known: Bool) {
            try RequestDiagnostics.checkCancellation()
            var current = linkedElement(child, kAXParentAttribute), seen = [child]
            for _ in 0..<32 {
                try RequestDiagnostics.checkCancellation()
                guard let node = current, !seen.contains(where: { CFEqual($0, node) }) else { return (nil, false) }
                if CFEqual(node, root) { return (nil, true) }
                seen.append(node)
                let (status, pid) = query.pid(node)
                guard status == .success, pid == app.processIdentifier else { return (nil, false) }
                guard let role = attribute(node, kAXRoleAttribute) as? String else { return (nil, false) }
                if surfaceRoles.contains(role) { return (node, true) }
                current = linkedElement(node, kAXParentAttribute)
            }
            return (nil, false)
        }
        // One bounded page per surface; never enqueue ordinary controls here.
        // All candidates' scalars/sheets/parents are sampled before this phase.
        // Every getter shares the original request deadline; there is no renewed
        // per-root tree-discovery clock or operation budget.
        func immediateSurfaces(_ parent: AXUIElement) throws -> [AXUIElement] {
            try timed("phase.inputSurfaceFrontier", root: parent) {
                try RequestDiagnostics.checkCancellation()
                timing?.surfaceRoot = parent
                defer { timing?.surfaceRoot = nil }
                let relation = try surfaceRelations.read(
                    parent, source: "input-surface-frontier", property: kAXChildrenAttribute, limit: 512,
                    withinBudget: { try RequestDiagnostics.checkCancellation(); return true })
                guard case .page(let page) = relation else {
                    if case .failure(let failure) = relation,
                        !["unsupported", "absent"].contains(failure["state"] as? String ?? "")
                    { surfaceIssue("immediate surface relation unavailable") }
                    return []
                }
                if page.prefix.count < page.total || page.issue != nil { surfaceIssue("immediate surface relation incomplete") }
                var found = [AXUIElement]()
                for child in page.targets {
                    try RequestDiagnostics.checkCancellation()
                    if surfaceRoles.contains(attribute(child, kAXRoleAttribute, stage: "getter.frontier.role", root: parent) as? String ?? "") {
                        found.append(child)
                    }
                }
                return found
            }
        }
        enum Membership { case live, absent, unknown }
        // Only a previously observed modal identity requests this bounded path
        // query. Neither old modality/parent data nor a retained Role proves
        // liveness. Check each current parent edge in its forward relation.
        func modalMembership(_ hint: AXUIElement) throws -> Membership {
            var child = hint, seen = [AXUIElement]()
            for _ in 0..<32 {
                try RequestDiagnostics.checkCancellation()
                guard !seen.contains(where: { CFEqual($0, child) }) else { return .unknown }
                seen.append(child)
                let read = queryRead(child, kAXParentAttribute)
                if case .invalid = read, CFEqual(child, hint) { return .absent }
                guard let raw = read.value, CFGetTypeID(raw) == AXUIElementGetTypeID() else { return .unknown }
                let parent = unsafeDowncast(raw, to: AXUIElement.self)
                let (status, pid) = query.pid(parent)
                guard status == .success, pid == app.processIdentifier else { return .unknown }
                let relation = try surfaceRelations.read(
                    parent, source: "observed-modal-membership", property: kAXChildrenAttribute, limit: 512,
                    withinBudget: { try RequestDiagnostics.checkCancellation(); return true })
                guard case .page(let page) = relation else { return .unknown }
                guard page.targets.contains(where: { CFEqual($0, child) }) else {
                    guard page.prefix.count == page.total && page.issue == nil else { return .unknown }
                    // Absence from an old parent is not absence after a
                    // concurrent reparent. Keep that transition unknown.
                    try RequestDiagnostics.checkCancellation()
                    let current = queryRead(child, kAXParentAttribute)
                    if case .invalid = current, CFEqual(child, hint) { return .absent }
                    guard let raw = current.value, CFGetTypeID(raw) == AXUIElementGetTypeID(),
                        CFEqual(raw, parent) else { return .unknown }
                    return .absent
                }
                if CFEqual(parent, root) || result.contains(where: { CFEqual($0.element, parent) }) { return .live }
                child = parent
            }
            return .unknown
        }
        var cursor = 0
        while cursor < candidates.count || frontierCursor < inputFrontiers.count || modalHintCursor < modalHints.count {
            if cursor == candidates.count, frontierCursor < inputFrontiers.count {
                let parent = inputFrontiers[frontierCursor]
                frontierCursor += 1
                for child in try immediateSurfaces(parent) {
                    owners.append((child, parent))
                    candidates.append(child)
                }
                continue
            }
            if cursor == candidates.count {
                try RequestDiagnostics.checkCancellation()
                let hint = modalHints[modalHintCursor]
                modalHintCursor += 1
                if result.contains(where: { CFEqual($0.element, hint) }) { continue }
                let read = queryRead(hint, kAXModalAttribute)
                if case .invalid = read { absentModalHints.append(hint); continue }
                if read.value as? Bool == false { absentModalHints.append(hint); continue }
                switch try modalMembership(hint) {
                case .live: candidates.append(hint)
                case .absent: absentModalHints.append(hint)
                case .unknown: break
                }
                continue
            }
            try RequestDiagnostics.checkCancellation()
            let candidate = candidates[cursor]
            let element = timed("helper.presentedWindow", root: candidate) {
                query.presentedWindow(candidate, app.processIdentifier, timing)
            }
            cursor += 1
            let (pidStatus, pid) = query.pid(element)
            guard pidStatus == .success, pid == app.processIdentifier else {
                if projection == .keyboardDispatch { issue("surface candidate identity or role unavailable") }
                continue
            }
            let reportedRole = attribute(element, kAXRoleAttribute) as? String
            // Input candidates already come from native declarations/hints or
            // confirmed surface edges. A failed optional role must not erase a
            // positive AXModal or AXSheets fact on that exact candidate.
            let role = reportedRole ?? ""
            guard (projection == .inputBoundary || (reportedRole != nil &&
                ([kAXWindowRole, kAXSheetRole, "AXPopover"].contains(role)
                    || declaredRoots.contains { CFEqual($0, element) }))),
                !result.contains(where: { CFEqual($0.element, element) })
            else {
                if projection == .keyboardDispatch && !result.contains(where: { CFEqual($0.element, element) }) {
                    issue("surface candidate identity or role unavailable")
                }
                continue
            }
            timing?.root(element, role: reportedRole, selected: contentRoot.map { CFEqual($0, element) })
            if projection != .catalogue {
                // Boundary projections do not read presentation fields. The
                // input projection uses the original alias registry; private
                // keyboard keys retain their existing no-alias contract.
                let ref = projection == .keyboardDispatch ? "boundary:\(result.count)" : registry.reference(
                    for: Identity(pid: pid, launched: app.launchDate, element: element, incarnation: incarnation))
                // Private per-collection keys never allocate or rewrite public aliases.
                // Unread presentation fields stay inside this collector and are
                // removed by the typed boundary adapter before returning.
                result.append(
                    Window(
                        ref: ref, title: "", element: element,
                        kind: role == kAXSheetRole ? "sheet" : role == "AXPopover" ? "popover" : role.isEmpty ? "unknown" : role,
                        modal: attribute(element, kAXModalAttribute) as? Bool))
            } else {
                let subrole = attribute(element, kAXSubroleAttribute) as? String ?? ""
                let kind =
                    role == "AXPopover"
                    ? "popover"
                    : role == kAXSheetRole
                        ? "sheet"
                        : ["AXDialog", "AXSystemDialog"].contains(subrole)
                            ? "dialog"
                            : ["AXFloatingWindow", "AXSystemFloatingWindow"].contains(subrole) ? "panel" : "window"
                result.append(
                    Window(
                        ref: registry.reference(
                            for: Identity(
                                pid: pid, launched: app.launchDate, element: element, incarnation: incarnation)),
                        title: attribute(element, kAXTitleAttribute) as? String ?? "", element: element,
                        kind: kind, modal: attribute(element, kAXModalAttribute) as? Bool))
            }
            if let timing, let item = result.last { timing.root(item.element, ref: item.ref) }
            // Sheets are often absent from AXWindows. Explicit native ownership
            // is retained; title, screen position and current focus prove no owner.
            let sheets: [AXUIElement]
            if projection != .catalogue {
                let read = queryRead(element, "AXSheets")
                switch read {
                case .value(let raw):
                    if let values = NativeWindowDeclarationCapture.completeElements(raw) {
                        sheets = values
                    } else {
                        issue("AXSheets malformed")
                        sheets = []
                    }
                case .unsupported, .unavailable(.noValue): sheets = []
                default:
                    issue("AXSheets unavailable")
                    sheets = []
                }
            } else {
                sheets = attribute(element, "AXSheets") as? [AXUIElement] ?? []
            }
            for child in sheets {
                if projection == .inputBoundary { inputSheets.append(child) }
                owners.append((child, element))
                candidates.append(child)
            }
            if projection == .inputBoundary {
                let parent = try inputParent(element)
                inputParents.append((element, parent.parent, parent.known))
                if let parent = parent.parent { candidates.append(parent) }
                inputFrontiers.append(element)
                continue
            }
            // Discovery reads still expand every window. Input validation may
            // exclude only contents proven outside its interaction range;
            // AXWindows, focus ancestors, AXSheets and immediate surface
            // children are always live. In particular, this keeps global
            // modals and all ancestor sheets without walking their file lists
            // again before each character sent to a nested Go To sheet.
            let recursive = try timed("phase.recursiveAncestry", root: element) {
                try contentRoot.map { selected in
                    try NativeWindowBoundary.exploreContents(
                        of: element, root: selected, same: { CFEqual($0, $1) },
                        parent: { node in
                            try RequestDiagnostics.checkCancellation()
                            return linkedElement(node, kAXParentAttribute)
                        },
                        isApplication: { node in
                            let (status, pid) = query.pid(node)
                            return status == .success && pid == app.processIdentifier
                                && attribute(node, kAXRoleAttribute) as? String == kAXApplicationRole
                        })
                } ?? true
            }
            for child in try nestedSurfaces(element, recursive: recursive) {
                owners.append((child, element))
                candidates.append(child)
            }
        }
        // The retained object is only a pruning hint. A popover may be
        // reachable solely through an anchor inside an excluded ancestor.
        // Rediscover it through live child edges before declaring it closed;
        // never authorize input from the retained object alone.
        if projection == .inputBoundary,
            (required + (contentRoot.map { [$0] } ?? [])).contains(where: { expected in
                !result.contains(where: { CFEqual($0.element, expected) })
            })
        {
            // Only a missing required bound surface needs content discovery.
            // Retention or a successful role getter alone cannot prove its live
            // attachment. Expand the same collector once under the same request.
            timing?.count("requiredSurfaceDiscoveryFallback")
            guard case .catalogue(let expanded) = try collectWindows(
                app, nativeWindows: nativeWindows, purpose: purpose, projection: .catalogue,
                reportIssue: reportIssue, parentQuery: timing?.id)
            else { preconditionFailure("required-surface fallback lost its catalogue") }
            outcome = "returned"
            return .inputBoundary(.init(windows: expanded, issue: boundaryIssue, expandedRequiredSurface: true,
                                        unresolvedModalHints: unresolvedModalHints(in: expanded)))
        }
        if projection != .inputBoundary, let contentRoot, !result.contains(where: { CFEqual($0.element, contentRoot) }) {
            timing?.count("contentRootMissingFallback")
            let fallback = try timed("phase.fullDiscoveryFallback") {
                try collectWindows(
                    app, nativeWindows: nativeWindows, purpose: purpose, projection: projection,
                    reportIssue: reportIssue, parentQuery: timing?.id)
            }
            outcome = "returned"
            return fallback
        }
        for index in result.indices {
            try RequestDiagnostics.checkCancellation()
            let item = result[index]
            let forward = owners.filter { CFEqual($0.child, item.element) }.map(\.parent)
            var parent = forward.first
            if projection == .inputBoundary {
                if inputSheets.contains(where: { CFEqual($0, item.element) }) { result[index].kind = "sheet" }
                if let parent, forward.dropFirst().contains(where: { !CFEqual($0, parent) }) {
                    result[index].parentUnknown = true
                }
                let actual = inputParents.first { CFEqual($0.child, item.element) }
                if let forward = parent, let actual = actual?.parent, !CFEqual(forward, actual) {
                    result[index].parentUnknown = true
                } else if parent == nil {
                    parent = actual?.parent
                    result[index].parentUnknown = result[index].parentUnknown || actual?.known != true
                }
            }
            if projection != .inputBoundary, parent == nil, let raw = attribute(item.element, kAXParentAttribute),
                CFGetTypeID(raw) == AXUIElementGetTypeID()
            {
                parent = unsafeDowncast(raw, to: AXUIElement.self)
            }
            if projection == .keyboardDispatch,
                !NativeKeyboardBoundaryPolicy.parentKnown(
                    child: item.element, parent: parent, application: root, windows: result.map(\.element),
                    same: { CFEqual($0, $1) })
            {
                unknownBoundaryParents.append(item.element)
            }
            if let parent,
                let owner = result.first(where: { !CFEqual($0.element, item.element) && CFEqual($0.element, parent) })
            {
                result[index].parent = owner.ref
                // AXSheet is the native document-modal role; an explicit false
                // or unavailable owner is never changed into application-wide modality.
                if result[index].kind == "sheet" && item.modal == nil { result[index].modal = true }
            }
        }
        if projection == .keyboardDispatch {
            // Native parents were not queried. Only the flat/no-modal policy
            // may accept this graph; any required unknown goes to the parent Gate.
            let rows = result.map { item in
                NativeKeyboardBoundaryProjection<AXUIElement>.Window(
                    key: item.ref, element: item.element,
                    role: item.kind == "sheet" ? kAXSheetRole : item.kind == "popover" ? "AXPopover" : item.kind,
                    modal: item.modal, parent: item.parent,
                    parentKnown: !unknownBoundaryParents.contains(where: { CFEqual($0, item.element) }))
            }
            outcome = "returned"
            return .boundary(.init(windows: rows, issue: boundaryIssue))
        }
        if projection == .inputBoundary {
            // Native parent supplementation needs exact IDs, not titles,
            // document URIs or file/control contents. Keep one-to-one identity.
            let ids = try result.map { item in
                try RequestDiagnostics.checkCancellation()
                return query.nativeID(item.element)
            }
            var refs = [String: String]()
            for index in result.indices {
                guard let id = ids[index], ids.filter({ $0 == id }).count == 1,
                    let native = nativeWindows.first(where: { $0.mapping.id == id && $0.process.pid == app.processIdentifier })
                else { continue }
                refs[result[index].ref] = registry.windowReference(native, element: result[index].element)
                result[index].nativeID = id
            }
            for index in result.indices {
                result[index].ref = refs[result[index].ref] ?? result[index].ref
                result[index].parent = result[index].parent.map { refs[$0] ?? $0 }
            }
            let relations = windowParents(result, native: nativeWindows, pid: app.processIdentifier)
            for index in result.indices {
                let previous = result[index].parent
                result[index].parent = relations.parents[result[index].ref]
                // An exact native parent can resolve absent AX ancestry. A
                // conflicting live forward/native relationship stays unknown.
                result[index].parentUnknown = relations.unresolved.contains(result[index].ref)
                    || (result[index].parentUnknown && (previous != nil || result[index].parent == nil))
            }
            if let timing {
                for item in result { timing.root(item.element, ref: item.ref, nativeID: item.nativeID) }
            }
            outcome = "returned"
            return .inputBoundary(.init(windows: result, issue: boundaryIssue, expandedRequiredSurface: false,
                                        unresolvedModalHints: unresolvedModalHints(in: result)))
        }
        // Merge only one-to-one native matches. Same-title/overlapping windows
        // must not collapse, nor may a sheet borrow its parent's identity.
        var remapped = [String: String]()
        var exactNativeIDs = [String: CGWindowID]()
        // Read each AX hint once, not once per pair of AX/native windows.
        let hints = try result.map { item in
            try RequestDiagnostics.checkCancellation()
            return timed("helper.nativeHint", root: item.element) {
                query.hint(item.element, timing)
            }
        }
        let matchesByWindow = hints.map { raw -> [NativeWindows.Window] in
            guard let hint = raw else { return [] }
            return nativeWindows.filter {
                $0.process.pid == app.processIdentifier
                    && WindowSelection.identifies(
                        .init(
                            id: $0.mapping.id, frame: $0.mapping.frame, title: $0.mapping.title ?? "",
                            visible: $0.onScreen), hint: hint)
            }
        }
        for (index, item) in result.enumerated() {
            let matches = matchesByWindow[index]
            if matches.count == 1, let native = matches.first,
                matchesByWindow.filter({ $0.contains { $0.mapping.id == native.mapping.id } }).count == 1
            {
                remapped[item.ref] = registry.windowReference(native, element: item.element)
                if hints[index]?.id == native.mapping.id, let ref = remapped[item.ref] {
                    exactNativeIDs[ref] = native.mapping.id
                }
            }
        }
        try RequestDiagnostics.checkCancellation()
        var mapped = result.map { item in
            Window(
                ref: remapped[item.ref] ?? item.ref, title: item.title, element: item.element, kind: item.kind,
                parent: item.parent.map { remapped[$0] ?? $0 },
                nativeID: exactNativeIDs[remapped[item.ref] ?? item.ref], modal: item.modal)
        }
        // A native child (including system sharing controls) may report the
        // application as AXParent. Keep it visible, but do not invent another
        // independent document. Only exact, same-process native IDs qualify.
        let relations = windowParents(mapped, native: nativeWindows, pid: app.processIdentifier)
        for index in mapped.indices {
            mapped[index].parent = relations.parents[mapped[index].ref]
            mapped[index].parentUnknown = relations.unresolved.contains(mapped[index].ref)
        }
        if !relations.unresolved.isEmpty { issue("some native window parent relationships could not be confirmed") }
        // Identical titles are insufficient for choosing a newly opened file.
        // Preserve each exact window's own native URI, without inspecting its
        // contents, guessing from a parent or querying unrelated unique titles.
        let titleCounts = Dictionary(grouping: mapped, by: \.title).mapValues(\.count)
        let documentDeadline = ProcessInfo.processInfo.systemUptime + 0.2
        for index in mapped.indices where (titleCounts[mapped[index].title] ?? 0) > 1 {
            try RequestDiagnostics.checkCancellation()
            guard ProcessInfo.processInfo.systemUptime < documentDeadline else { break }
            if let raw = attribute(mapped[index].element, kAXDocumentAttribute),
                let value = (raw as? String) ?? (raw as? URL)?.absoluteString, !value.isEmpty
            {
                mapped[index].document = value
            }
        }
        if let timing {
            for item in mapped { timing.root(item.element, ref: item.ref, nativeID: item.nativeID) }
        }
        outcome = "returned"
        return .catalogue(mapped)
    }

    func windowParents(_ windows: [Window], native: [NativeWindows.Window], pid: pid_t) -> NativeWindowParents.Result {
        var facts = windows.map {
            NativeWindowParents.Window(ref: $0.ref, pid: pid, id: $0.nativeID, parent: $0.parent)
        }
        let axRefs = Set(windows.map(\.ref))
        var parents = [CGWindowID: CGWindowID]()
        for window in native {
            let ref = registry.windowReference(window)
            if !axRefs.contains(ref) {
                facts.append(.init(ref: ref, pid: window.process.pid, id: window.mapping.id, parent: nil))
            }
            if let parent = window.parentID { parents[window.mapping.id] = parent }
        }
        return NativeWindowParents.supplement(facts, native: parents)
    }
}
