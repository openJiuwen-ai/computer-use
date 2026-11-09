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
            throw DriverError("text snapshot expired", code: .snapshotExpired)
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
        // A fresh AX path reached this process's application, not merely an
        // absent parent/native parent zero. Used by visual directory rows.
        var applicationParent: Bool = false
        var nativeID: CGWindowID? = nil
        var modal: Bool? = nil
        var document: String? = nil
        var contentKind: String = "unknown"
        var contentHost: String? = nil
        var contentEvidence: String = "unclassified"
        var contentElement: AXUIElement? = nil
    }

    /// Coverage belongs to the failing discovery step, not to diagnostic text.
    /// Descendant traversal cannot hide another independent application root.
    struct WindowIssue {
        enum Scope { case roots, descendants }
        let scope: Scope
        let detail: String
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
        purpose: NativeWindowQueryPurpose = .observation, profile: NativeInspectQuery.SurfaceProfile = .accessibility,
        modalHints: [ModalHint] = [], reportIssue: ((WindowIssue) -> Void)? = nil
    ) throws -> [Window] {
        guard
            case .catalogue(let windows) = try collectWindows(
                app, nativeWindows: nativeWindows, contentRoot: contentRoot, purpose: purpose,
                projection: profile == .visual ? .visualCatalogue : .catalogue,
                reportIssue: reportIssue, modalHints: modalHints)
        else { preconditionFailure("catalogue adapter received a boundary projection") }
        return windows
    }

    /// Physical window input needs current surface boundaries, not a rendered
    /// directory or all controls below a file browser. Rows retain uncertainty;
    /// this projection alone never grants permission to post an event.
    struct InputBoundary {
        let windows: [Window]
        let issue: String?
        let expandedRequiredSurface: Bool
        var unresolvedModalHints = Set<String>()
        var parentEvidence: NativeWindowParents.Result? = nil
    }

    struct ModalIdentity {
        struct Native {
            let id: CGWindowID
            let process: NativeProcessIdentity
        }
        let window: String
        let element: AXUIElement?
        let native: Native?
        var isSheet = false
    }
    enum ModalPresence { case live, ended, unknown }
    struct ModalHint {
        let identity: ModalIdentity
        let nativePresence: ModalPresence?
        var nativeOnScreen: Bool? = nil
        var nativeParentID: CGWindowID? = nil
    }

    func inputBoundary(
        _ app: NSRunningApplication, nativeWindows: [NativeWindows.Window], contentRoot: AXUIElement?,
        required: [AXUIElement], modalHints: [ModalHint], expandMissingRequired: Bool = true
    ) throws -> InputBoundary {
        guard case .inputBoundary(let result) = try collectWindows(
            app, nativeWindows: nativeWindows, contentRoot: contentRoot, purpose: .inputValidation,
            projection: .inputBoundary, required: required, modalHints: modalHints,
            expandMissingRequired: expandMissingRequired)
        else { preconditionFailure("input boundary adapter received another projection") }
        return result
    }

    private enum Projection: Equatable { case catalogue, visualCatalogue, inputBoundary }
    private enum Collection {
        case catalogue([Window])
        case inputBoundary(InputBoundary)
    }

    private func collectWindows(
        _ app: NSRunningApplication, nativeWindows: [NativeWindows.Window] = [], contentRoot: AXUIElement? = nil,
        purpose: NativeWindowQueryPurpose, projection: Projection, reportIssue: ((WindowIssue) -> Void)? = nil,
        parentQuery: String? = nil, required: [AXUIElement] = [], modalHints: [ModalHint] = [],
        discoverRequired: Bool = false, expandMissingRequired: Bool = true
    ) throws -> Collection {
        let timing = NativeWindowQueryTiming.start(metadata: {
            ["pid": app.processIdentifier, "purpose": purpose == .inputValidation ? "inputValidation" : "observation",
             "projection": String(describing: projection),
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
        func issue(_ detail: String, scope: WindowIssue.Scope = .roots) {
            if boundaryIssue == nil { boundaryIssue = detail }
            reportIssue?(.init(scope: scope, detail: detail))
        }
        try RequestDiagnostics.checkCancellation()
        guard query.trusted() else {
            outcome = "returned"
            if projection == .inputBoundary {
                return .inputBoundary(.init(windows: [], issue: "AX trust unavailable", expandedRequiredSurface: false,
                    unresolvedModalHints: Set(modalHints.filter { $0.nativePresence != .ended }.map { $0.identity.window })))
            }
            return .catalogue([])
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
                // A visual catalogue has no control recipient to resolve. The
                // helper may walk AXFocusedUIElement and its content ancestry.
                if projection == .visualCatalogue { return nil }
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
        let surfaceRoles = [kAXWindowRole, kAXSheetRole, "AXPopover"]
        let keyboardBoundary = projection == .inputBoundary && !expandMissingRequired
        var focus = projection == .visualCatalogue ? nil : linkedElement(root, kAXFocusedUIElementAttribute)
        var focusPath = [AXUIElement]()
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
        if keyboardBoundary, let contentRoot, !focusPath.contains(where: { CFEqual($0, contentRoot) }) {
            issue("focused ancestry no longer confirms the original keyboard window")
        }
        // AppKit attaches popovers to their anchor control, not necessarily to
        // AXWindows or a window's immediate children. Discover surface roots
        // through actual child edges; never infer ownership from screen overlap.
        let surfaceDeadline = ProcessInfo.processInfo.systemUptime + 0.4
        var scanned = [AXUIElement]()
        var contentEdges = [(node: AXUIElement, children: [AXUIElement])] ()
        var contentRoles = [(node: AXUIElement, role: String)] ()
        let surfaceRelations = AXRelationReader(api: timing?.relationAPI(query.relations) ?? query.relations)
        var surfaceIssues = Set<String>()
        func surfaceIssue(_ detail: String) {
            if surfaceIssues.insert(detail).inserted { issue(detail, scope: .descendants) }
        }
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
                    } else {
                        contentEdges.append((node, Array(page.targets)))
                    }
                    for child in page.targets {
                        if ProcessInfo.processInfo.systemUptime >= surfaceDeadline {
                            stop("time_budget")
                            surfaceIssue("attached surface discovery incomplete: time budget")
                            break
                        }
                        let role = attribute(child, kAXRoleAttribute, stage: "getter.surface.role", root: parent) as? String ?? ""
                        if keyboardBoundary && role.isEmpty { surfaceIssue("keyboard surface role unavailable") }
                        contentRoles.append((child, role))
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
        var inputNativeIDs = [(element: AXUIElement, id: CGWindowID?)]()
        var modalHintCursor = 0, absentModalHints = Set<String>(), inactiveModalRelations = Set<String>()
        func reportedID(_ element: AXUIElement) -> CGWindowID? {
            inputNativeIDs.first(where: { CFEqual($0.element, element) })?.id
        }
        func modalRow(_ hint: ModalHint, in windows: [Window]) -> Window? {
            if let native = hint.identity.native {
                guard hint.nativePresence == .live else { return nil }
                let matches = windows.filter { reportedID($0.element) == native.id }
                if matches.count > 1 { return nil }
                if let match = matches.first {
                    if let original = hint.identity.element, CFEqual(match.element, original) { return match }
                    // A new proxy can supply facts for the same CG surface,
                    // not for an inner sheet sharing an unknown backing window.
                    guard [kAXWindowRole, "window", "dialog", "panel", "sheet", "popover"].contains(match.kind),
                        let ancestry = inputParents.first(where: { CFEqual($0.child, match.element) }),
                        ancestry.known else { return nil }
                    if let parent = ancestry.parent {
                        guard let id = reportedID(parent), id != native.id else { return nil }
                    }
                    return match
                }
                // The original live AX object may temporarily lack its bridge;
                // an explicit different ID is a conflict, never a replacement.
                guard let original = hint.identity.element else { return nil }
                return windows.first { CFEqual($0.element, original) && reportedID($0.element) == nil }
            }
            guard let original = hint.identity.element else { return nil }
            return windows.first { CFEqual($0.element, original) }
        }
        func unresolvedModalHints(in windows: [Window]) -> Set<String> {
            Set(modalHints.compactMap { hint in
                if absentModalHints.contains(hint.identity.window)
                    || (inactiveModalRelations.contains(hint.identity.window) && detachedHiddenModal(hint, in: windows)) {
                    return nil
                }
                return modalRow(hint, in: windows)?.modal == nil ? hint.identity.window : nil
            })
        }
        func detachedHiddenModal(_ hint: ModalHint, in windows: [Window]) -> Bool {
            guard hint.identity.isSheet, hint.nativePresence == .live, hint.nativeOnScreen == false, hint.nativeParentID == 0,
                let native = hint.identity.native, let original = hint.identity.element,
                !windows.contains(where: { CFEqual($0.element, original) || reportedID($0.element) == native.id })
            else { return false }
            return true
        }
        func markInactive(_ hint: ModalHint, proof: String) {
            // This boundary has proved the presentation relation inactive,
            // not the native object's lifetime ended. Retain the query hint;
            // later presentation/attachment must be sampled again.
            inactiveModalRelations.insert(hint.identity.window)
            NativePointerTiming.record("inactiveModalRelation", metadata: {
                ["window": hint.identity.window, "nativeWindow": hint.identity.native?.id as Any? ?? NSNull(), "proof": proof]
            })
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
                    let role = attribute(child, kAXRoleAttribute, stage: "getter.frontier.role", root: parent) as? String
                    if let role, surfaceRoles.contains(role) {
                        found.append(child)
                    }
                }
                return found
            }
        }
        enum Membership { case live, invalid, forwardAbsent, unknown }
        // Only a previously observed modal identity requests this bounded path
        // query. Neither old modality/parent data nor a retained Role proves
        // liveness. Check each current parent edge in its forward relation.
        func modalMembership(_ hint: AXUIElement) throws -> Membership {
            var reason = "unknown"
            defer { NativePointerTiming.record("modalMembership", metadata: {
                ["nativeWindow": reportedID(hint) as Any? ?? NSNull(), "reason": reason]
            }) }
            var child = hint, seen = [AXUIElement]()
            for _ in 0..<32 {
                try RequestDiagnostics.checkCancellation()
                guard !seen.contains(where: { CFEqual($0, child) }) else { return .unknown }
                seen.append(child)
                let read = queryRead(child, kAXParentAttribute)
                if case .invalid = read, CFEqual(child, hint) { reason = "invalidProxy"; return .invalid }
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
                    if case .invalid = current, CFEqual(child, hint) { reason = "invalidProxy"; return .invalid }
                    guard let raw = current.value, CFGetTypeID(raw) == AXUIElementGetTypeID(),
                        CFEqual(raw, parent) else { return .unknown }
                    reason = "forwardAbsent"; return .forwardAbsent
                }
                if CFEqual(parent, root) || result.contains(where: { CFEqual($0.element, parent) }) { reason = "live"; return .live }
                child = parent
            }
            return .unknown
        }
        var cursor = 0
        func exploreScope(of element: AXUIElement) throws -> Bool {
            try timed("phase.recursiveAncestry", root: element) {
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
        }
        while cursor < candidates.count || frontierCursor < inputFrontiers.count || modalHintCursor < modalHints.count {
            if cursor == candidates.count, frontierCursor < inputFrontiers.count {
                let parent = inputFrontiers[frontierCursor]
                frontierCursor += 1
                let children: [AXUIElement]
                if discoverRequired { children = try nestedSurfaces(parent, recursive: true) }
                else if keyboardBoundary {
                    // Deep AX-only modals can block keyboard input even while
                    // the old field retains focus and CG has no new window.
                    children = try nestedSurfaces(parent, recursive: exploreScope(of: parent))
                } else { children = try immediateSurfaces(parent) }
                for child in children {
                    owners.append((child, parent))
                    candidates.append(child)
                }
                continue
            }
            if cursor == candidates.count {
                try RequestDiagnostics.checkCancellation()
                let hint = modalHints[modalHintCursor]
                modalHintCursor += 1
                if hint.nativePresence == .ended { absentModalHints.insert(hint.identity.window); continue }
                if hint.identity.native != nil && hint.nativePresence != .live { continue }
                if modalRow(hint, in: result) != nil { continue }
                let detachedHidden = detachedHiddenModal(hint, in: result)
                // Pixel discovery does not inspect an anchor control's child
                // relation to rediscover a deeply attached historical modal.
                // The sole narrow exception reads the exact retained proxy
                // after fresh CG facts prove hidden + detached. It does not
                // traverse contents or require any provider-specific AXSheets.
                if projection == .visualCatalogue && !detachedHidden { continue }
                guard let element = hint.identity.element else { continue }
                let read = queryRead(element, kAXModalAttribute)
                NativePointerTiming.record("retainedModalAXRead", metadata: {
                    let status: String
                    switch read {
                    case .value: status = "value"
                    case .invalid: status = "invalid"
                    case .unsupported: status = "unsupported"
                    case .unavailable(let error): status = "unavailable:\(error.rawValue)"
                    }
                    return ["window": hint.identity.window, "nativeWindow": hint.identity.native?.id as Any? ?? NSNull(),
                            "status": status, "modal": read.value as? Bool as Any? ?? NSNull()]
                })
                if case .invalid = read {
                    if hint.identity.native == nil { absentModalHints.insert(hint.identity.window) }
                    else if detachedHidden { markInactive(hint, proof: "exactAXInvalid") }
                    continue
                }
                if projection == .visualCatalogue { continue }
                if hint.identity.native == nil && read.value as? Bool == false {
                    absentModalHints.insert(hint.identity.window); continue
                }
                if let native = hint.identity.native, let id = query.nativeID(element), id != native.id { continue }
                switch try modalMembership(element) {
                case .live: candidates.append(element)
                case .invalid:
                    if hint.identity.native == nil { absentModalHints.insert(hint.identity.window) }
                    else if detachedHidden { markInactive(hint, proof: "exactAXInvalid") }
                case .forwardAbsent:
                    if hint.identity.native == nil { absentModalHints.insert(hint.identity.window) }
                    else if detachedHidden { markInactive(hint, proof: "forwardAbsent") }
                case .unknown: break
                }
                continue
            }
            try RequestDiagnostics.checkCancellation()
            let candidate = candidates[cursor]
            // Presenter reconciliation can compare ordinary window contents.
            // Visual discovery keeps the declared identity; uncertain native
            // correspondence remains an explicit native-only/AX-only row.
            let element = projection == .visualCatalogue ? candidate : timed("helper.presentedWindow", root: candidate) {
                query.presentedWindow(candidate, app.processIdentifier, timing)
            }
            cursor += 1
            let (pidStatus, pid) = query.pid(element)
            guard pidStatus == .success, pid == app.processIdentifier else {
                if keyboardBoundary { issue("keyboard surface candidate owner unavailable") }
                continue
            }
            let reportedRole = attribute(element, kAXRoleAttribute) as? String
            if keyboardBoundary && reportedRole == nil { issue("keyboard surface candidate role unavailable") }
            // Input candidates already come from native declarations/hints or
            // confirmed surface edges. A failed optional role must not erase a
            // positive AXModal or AXSheets fact on that exact candidate.
            let role = reportedRole ?? ""
            contentRoles.append((element, role))
            guard (projection == .inputBoundary || projection == .visualCatalogue || (reportedRole != nil &&
                ([kAXWindowRole, kAXSheetRole, "AXPopover"].contains(role)
                    || declaredRoots.contains { CFEqual($0, element) }))),
                !result.contains(where: { CFEqual($0.element, element) })
            else {
                continue
            }
            timing?.root(element, role: reportedRole, selected: contentRoot.map { CFEqual($0, element) })
            if projection == .inputBoundary {
                let read = queryRead(element, kAXModalAttribute)
                // Invalid proxies can remain in a provider's declaration.
                // Drop that row, not the separate retained CG identity.
                if case .invalid = read {
                    if keyboardBoundary { issue("keyboard surface candidate invalidated") }
                    continue
                }
                if keyboardBoundary, case .unavailable(let error) = read, error != .noValue {
                    issue("keyboard surface modality unavailable")
                }
                let modal = read.value as? Bool
                let ref = registry.reference(
                    for: Identity(pid: pid, launched: app.launchDate, element: element, incarnation: incarnation))
                result.append(
                    Window(
                        ref: ref, title: "", element: element,
                        kind: role == kAXSheetRole ? "sheet" : role == "AXPopover" ? "popover" : role.isEmpty ? "unknown" : role,
                        modal: modal))
            } else {
                // Some providers declare an inline editor/control as a window
                // root. Preserve its identity and boundary facts, without
                // asking that control for presentation/body metadata.
                let presentation = projection != .visualCatalogue || surfaceRoles.contains(role)
                let subrole = presentation ? attribute(element, kAXSubroleAttribute) as? String ?? "" : ""
                let kind =
                    !presentation ? "unknown" : role == "AXPopover"
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
                        title: presentation ? attribute(element, kAXTitleAttribute) as? String ?? "" : "", element: element,
                        kind: kind, modal: attribute(element, kAXModalAttribute) as? Bool,
                        contentKind: role == kAXApplicationRole ? "application" : role == kAXMenuRole ? "menu"
                            : role == kAXSheetRole || subrole == kAXStandardWindowSubrole ? "window" : "unknown",
                        contentEvidence: role == kAXApplicationRole || role == kAXMenuRole || role == kAXSheetRole || subrole == kAXStandardWindowSubrole
                            ? "AXRole" : "unclassified"))
            }
            if let timing, let item = result.last { timing.root(item.element, ref: item.ref) }
            if projection == .inputBoundary || projection == .visualCatalogue {
                try RequestDiagnostics.checkCancellation()
                let nativeID = query.nativeID(element)
                inputNativeIDs.append((element, nativeID))
                timing?.root(element, nativeID: nativeID)
            }
            // Sheets are often absent from AXWindows. Explicit native ownership
            // is retained; title, screen position and current focus prove no owner.
            let sheets: [AXUIElement]
            if projection != .catalogue {
                let read = queryRead(element, "AXSheets")
                NativePointerTiming.record("windowSheetsDeclaration", metadata: {
                    let status: String
                    switch read {
                    case .value: status = "value"
                    case .invalid: status = "invalid"
                    case .unsupported: status = "unsupported"
                    case .unavailable(let error): status = "unavailable:\(error.rawValue)"
                    }
                    let values = read.value.flatMap { NativeWindowDeclarationCapture.completeElements($0) }
                    return ["nativeWindow": reportedID(element) as Any? ?? NSNull(), "status": status,
                            "completeCount": values?.count as Any? ?? NSNull(), "projection": String(describing: projection)]
                })
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
                if projection == .inputBoundary || projection == .visualCatalogue { inputSheets.append(child) }
                owners.append((child, element))
                candidates.append(child)
            }
            if projection == .inputBoundary || projection == .visualCatalogue {
                let parent = try inputParent(element)
                inputParents.append((element, parent.parent, parent.known))
                if let parent = parent.parent { candidates.append(parent) }
                if projection == .inputBoundary || surfaceRoles.contains(role)
                    || inputSheets.contains(where: { CFEqual($0, element) })
                { inputFrontiers.append(element) }
                continue
            }
            // Discovery reads still expand every window. Input validation may
            // exclude only contents proven outside its interaction range;
            // AXWindows, focus ancestors, AXSheets and immediate surface
            // children are always live. In particular, this keeps global
            // modals and all ancestor sheets without walking their file lists
            // again before each character sent to a nested Go To sheet.
            let recursive = try exploreScope(of: element)
            for child in try nestedSurfaces(element, recursive: recursive) {
                owners.append((child, element))
                candidates.append(child)
            }
        }
        // The retained object is only a pruning hint. A popover may be
        // reachable solely through an anchor inside an excluded ancestor.
        // Rediscover it through live child edges before declaring it closed;
        // never authorize input from the retained object alone.
        if projection == .inputBoundary, expandMissingRequired, !discoverRequired,
            (required + (contentRoot.map { [$0] } ?? [])).contains(where: { expected in
                !result.contains(where: { CFEqual($0.element, expected) })
            })
        {
            // Only a missing required bound surface needs content discovery.
            // Retention or a successful role getter alone cannot prove its live
            // attachment. Expand the same collector once under the same request.
            timing?.count("requiredSurfaceDiscoveryFallback")
            guard case .inputBoundary(let expanded) = try collectWindows(
                app, nativeWindows: nativeWindows, purpose: purpose, projection: .inputBoundary,
                reportIssue: reportIssue, parentQuery: timing?.id,
                required: required + (contentRoot.map { [$0] } ?? []), modalHints: modalHints,
                discoverRequired: true)
            else { preconditionFailure("required-surface fallback lost its typed boundary") }
            outcome = "returned"
            return .inputBoundary(expanded)
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
            if projection == .inputBoundary || projection == .visualCatalogue {
                if inputSheets.contains(where: { CFEqual($0, item.element) }) { result[index].kind = "sheet" }
                if let parent, forward.dropFirst().contains(where: { !CFEqual($0, parent) }) {
                    result[index].parentUnknown = true
                }
                let actual = inputParents.first { CFEqual($0.child, item.element) }
                if projection == .visualCatalogue, actual?.known == true, actual?.parent == nil, parent == nil {
                    result[index].applicationParent = true
                }
                if let forward = parent, let actual = actual?.parent, !CFEqual(forward, actual) {
                    result[index].parentUnknown = true
                } else if parent == nil {
                    parent = actual?.parent
                    result[index].parentUnknown = result[index].parentUnknown || actual?.known != true
                }
            }
            if projection != .inputBoundary && projection != .visualCatalogue, parent == nil, let raw = attribute(item.element, kAXParentAttribute),
                CFGetTypeID(raw) == AXUIElementGetTypeID()
            {
                parent = unsafeDowncast(raw, to: AXUIElement.self)
            }
            if let parent,
                let owner = result.first(where: { !CFEqual($0.element, item.element) && CFEqual($0.element, parent) })
            {
                result[index].parent = owner.ref
                // AXSheet is the native document-modal role; an explicit false
                // or unavailable owner is never changed into application-wide modality.
                if result[index].kind == "sheet" && item.modal == nil { result[index].modal = true }
            } else if (projection == .inputBoundary || projection == .visualCatalogue), parent != nil {
                result[index].parentUnknown = true
            }
        }
        if projection == .inputBoundary {
            // Native parent supplementation needs exact IDs, not titles,
            // document URIs or file/control contents. Keep one-to-one identity.
            let ids = result.map { reportedID($0.element) }
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
            return .inputBoundary(.init(windows: result, issue: boundaryIssue, expandedRequiredSurface: discoverRequired,
                                        unresolvedModalHints: unresolvedModalHints(in: result), parentEvidence: relations))
        }
        // One-to-one native matches may share identity. Multiple exact AX
        // sources keep their own objects and share only physical capture facts.
        // Same-title/overlapping windows and parent/sheet identities do not merge.
        var remapped = [String: String]()
        var exactNativeIDs = [String: CGWindowID]()
        var sharedNativeHosts = [String: String]()
        // Visual matching already has the original object's native ID when available.
        let hints: [WindowSelection.Hint?] = try result.map { item in
            try RequestDiagnostics.checkCancellation()
            if projection == .visualCatalogue {
                if let id = reportedID(item.element) { return .init(frame: nil, title: "", id: id) }
                if item.kind == "unknown" { return nil }
            }
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
            } else if matches.count == 1, let native = matches.first, hints[index]?.id == native.mapping.id {
                // Distinct AX roots can expose different contents of the same
                // exact physical window. Keep each AX identity. A unique
                // standard root may own the visual row; otherwise the native
                // row is the host and no AX source is promoted by position.
                let peers = result.indices.filter { hints[$0]?.id == native.mapping.id }
                let standards = peers.filter { result[$0].contentKind == "window" }
                let host = standards.count == 1
                    ? registry.windowReference(native, element: result[standards[0]].element)
                    : registry.windowReference(native)
                if standards.count == 1 && standards[0] == index { remapped[item.ref] = host }
                else { sharedNativeHosts[item.ref] = host }
                exactNativeIDs[remapped[item.ref] ?? item.ref] = native.mapping.id
            } else if matches.isEmpty, let id = hints[index]?.id, let process,
                hints.filter({ $0?.id == id }).count == 1
            {
                // The onscreen projection was sampled before this AX root.
                // A reappearing popup can already report its exact physical
                // ID while that earlier projection still omits it. Rejoin its
                // already-proven identity rather than publish an AX-only ref
                // that would disappear when the next native list arrives.
                // This is not native presence/parent/input evidence: only a
                // current native match above enters exactNativeIDs.
                remapped[item.ref] = registry.knownWindowReference(
                    nativeID: id, process: process, owner: app, element: item.element)
            }
        }
        try RequestDiagnostics.checkCancellation()
        var mapped = result.map { item in
            Window(
                ref: remapped[item.ref] ?? item.ref, title: item.title, element: item.element, kind: item.kind,
                parent: item.parent.map { remapped[$0] ?? $0 },
                parentUnknown: item.parentUnknown, applicationParent: item.applicationParent,
                nativeID: exactNativeIDs[remapped[item.ref] ?? item.ref], modal: item.modal,
                contentKind: sharedNativeHosts[item.ref] == nil ? item.contentKind : "unknown",
                contentHost: sharedNativeHosts[item.ref],
                contentEvidence: sharedNativeHosts[item.ref] == nil ? item.contentEvidence : "nativeWindowID")
        }
        // A native child (including system sharing controls) may report the
        // application as AXParent. Keep it visible, but do not invent another
        // independent document. Only exact, same-process native IDs qualify.
        let relations = windowParents(mapped, native: nativeWindows, pid: app.processIdentifier)
        for index in mapped.indices {
            mapped[index].parent = relations.parents[mapped[index].ref]
            mapped[index].parentUnknown = relations.unresolved.contains(mapped[index].ref)
                || (projection == .visualCatalogue && mapped[index].parentUnknown)
        }
        if !relations.unresolved.isEmpty { issue("some native window parent relationships could not be confirmed") }
        // Reuse child edges already collected for discovery. A separate AX
        // dialog sharing the opener's exact list is content of that control,
        // even when its reverse parent is AXApplication or AXWindow is absent.
        // No additional tree traversal is added to visual/input projections.
        if projection == .catalogue {
            func sampledRole(_ node: AXUIElement) -> String? {
                contentRoles.last(where: { CFEqual($0.node, node) })?.role
            }
            func sampledChildren(_ node: AXUIElement) -> [AXUIElement]? {
                contentEdges.last(where: { CFEqual($0.node, node) })?.children
            }
            for index in mapped.indices {
                let item = mapped[index]
                if item.modal == true {
                    mapped[index].contentKind = "window"
                    if item.contentHost == nil { mapped[index].contentEvidence = "AXModal" }
                    continue
                }
                let content: AXUIElement?
                if [kAXMenuRole, "AXList"].contains(sampledRole(item.element) ?? "") {
                    content = item.element
                } else if item.kind == "dialog", let children = sampledChildren(item.element), children.count == 1,
                    [kAXMenuRole, "AXList"].contains(sampledRole(children[0]) ?? "") {
                    content = children[0]
                } else { content = nil }
                if let content, let host = NativeContentRelations.host(
                    content: content, excluding: item.element, roots: mapped.map(\.element),
                    same: { CFEqual($0, $1) }, role: sampledRole, children: sampledChildren
                ), let owner = mapped.first(where: { CFEqual($0.element, host) }) {
                    mapped[index].contentKind = "menu"
                    mapped[index].contentHost = owner.ref
                    mapped[index].contentEvidence = "AXChildren"
                    mapped[index].contentElement = content
                } else if item.kind == "popover", let parent = item.parent {
                    mapped[index].contentKind = "popup"
                    mapped[index].contentHost = parent
                    mapped[index].contentEvidence = "attachedSurface"
                }
            }
        }
        if projection == .visualCatalogue, !unresolvedModalHints(in: result).isEmpty {
            issue("previously observed modal identity could not be revalidated; input still requires a fresh boundary check")
        }
        // Identical titles are insufficient for choosing a newly opened file.
        // Preserve each exact window's own native URI, without inspecting its
        // contents, guessing from a parent or querying unrelated unique titles.
        let titleCounts = Dictionary(grouping: mapped, by: \.title).mapValues(\.count)
        let documentDeadline = ProcessInfo.processInfo.systemUptime + 0.2
        for index in mapped.indices where (titleCounts[mapped[index].title] ?? 0) > 1 {
            try RequestDiagnostics.checkCancellation()
            if projection == .visualCatalogue, mapped[index].kind == "unknown" { continue }
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
