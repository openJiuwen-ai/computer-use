// Copyright (c) Huawei Technologies Co., Ltd. 2026. All rights reserved.

import AppKit
import ApplicationServices

/// Bounded native acquisition only. Semantic composition and display numbering
/// belong to Python; native relations retain their real, separately readable IDs.
@MainActor final class NativeFactCollector {
    typealias Target = ApplicationElements.Target
    typealias Identity = ApplicationElements.Identity
    static let fields =
        [
            "AXRole", "AXSubrole", "AXRoleDescription", "AXTitle", "AXDescription", "AXValue", "AXIdentifier", "AXHelp",
            "AXURL", "AXDocument", "AXEnabled", "AXSelected", "AXExpanded", "AXFocused", "AXPosition", "AXSize",
            "AXMinValue", "AXMaxValue", "AXValueIncrement", "AXValueDescription", "AXModal", "AXElementBusy",
            "AXHasPopup", "AXSelectedText", "AXSelectedTextRange", "AXPlaceholderValue", "AXDisclosing",
            "AXDisclosureLevel", "AXEditable", "AXOrientation", "AXRowIndexRange", "AXColumnIndexRange", "AXRowCount",
            "AXColumnCount", "AXIndex", "AXMenuItemCmdChar", "AXMenuItemCmdModifiers", "AXMenuItemCmdVirtualKey",
            "AXMenuItemCmdGlyph"
        ] + AXAttributeProfile.defaultWritableFields.sorted()
    static let relations = ["AXChildren", "AXParent", "AXTitleUIElement", "AXServesAsTitleForUIElements", "AXWindow"]
    // Optional relationships are selected by the caller. Advertised-property
    // checks avoid invoking undeclared getters on every unrelated node.
    static let supportedRelations =
        relations + ["AXVerticalScrollBar", "AXHorizontalScrollBar", "AXHeader", "AXDisclosedByRow"]
        + AXRelationReader.tableArrays + ["AXOwns", "AXLinkedUIElements", "AXSelectedChildren"]
    private let registry: ApplicationElements
    private let inspection: NativeElementInspection
    private let relationReader: AXRelationReader
    private let attributes: AXAttributeReader.API
    private let readIdentity: (AXUIElement) -> AXRead<ElementIdentity.Facts>
    private let captureSubtree: AXSubtreePrefetch.Capture?
    private let clock: @Sendable () -> TimeInterval
    private let nativeResources = AXNativeValueResources()
    private struct Acquisition {
        let catalogue: (status: AXError, names: CFArray?)?
        let sampleFields: [String]
        let batchFields: [String]
        let batch: AXAttributeReader.Result
        let prefetched: [AXAttributeReader.Field]
        let identity: AXRead<ElementIdentity.Facts>?
        let identityConflict: Bool
    }
    init(
        registry: ApplicationElements, inspection: NativeElementInspection,
        attributes: AXAttributeReader.API = AXAttributeReader.API(),
        relations: AXRelationReader.API = AXRelationReader.API(),
        readIdentity: @escaping (AXUIElement) -> AXRead<ElementIdentity.Facts> = { AXIdentity.read($0) },
        clock: @escaping @Sendable () -> TimeInterval = { ProcessInfo.processInfo.systemUptime },
        captureSubtree: AXSubtreePrefetch.Capture? = AXSubtreePrefetch.live
    ) {
        self.registry = registry
        self.inspection = inspection
        self.attributes = attributes
        self.relationReader = AXRelationReader(api: relations)
        self.readIdentity = readIdentity
        self.captureSubtree = captureSubtree
        self.clock = clock
    }

    /// Reading an exact retained object does not require its label, enabled
    /// state, input context or a complete provider containment path. The input
    /// endpoint keeps its separate identity/owning-window checks.
    func readTarget(_ ref: String) throws -> Target {
        guard let target = registry.target(ref) else { throw DriverError("native node expired", code: .staleTarget) }
        try validateRetainedTarget(target)
        return target
    }

    nonisolated static func launchDateCheck(expected: Date?, current: Date?) -> ElementIdentity.Check {
        // Missing historical metadata is not a lifetime constraint. The same
        // retained application still receives its existing live process checks.
        guard let expected else { return .matched }
        guard let current else { return .unavailable }
        return expected == current ? .matched : .changed
    }

    private func validateRetainedTarget(_ target: Target) throws {
        func ended(_ message: String) -> DriverError {
            registry.invalidate { $0.scope == target.scope && CFEqual($0.element, target.element) }
            return DriverError(message, code: .staleTarget)
        }
        switch Self.launchDateCheck(expected: target.launched, current: target.application.launchDate) {
        case .matched: break
        case .changed: throw ended("native node application lifetime changed")
        case .unavailable:
            throw DriverError(
                "native node application lifetime metadata is temporarily unconfirmed", code: .observationIncomplete)
        }
        // Registration/readiness failures are not necessarily lifetime proof.
        // Only the concrete leaf/provider contradictions below retire a node.
        try BackgroundTarget(target.application).validate()
        var pid: pid_t = 0
        let status = AXUIElementGetPid(target.element, &pid)
        guard status == .success else {
            if status == .invalidUIElement { throw ended("native node no longer exists") }
            throw DriverError(
                "native node provider identity is unavailable", code: .observationIncomplete)
        }
        if let provider = target.provider {
            guard pid == provider.pid else { throw ended("native node provider changed") }
            guard let current = NativeProcessIdentity.read(pid) else {
                if NativeProcessIdentity.isGone(pid) { throw ended("native node provider lifetime ended") }
                throw DriverError(
                    "native node provider lifetime is unavailable", code: .observationIncomplete)
            }
            guard current == provider else {
                throw ended("native node provider lifetime changed")
            }
        } else if pid != target.application.processIdentifier {
            if target.providerKnown { throw ended("native node provider changed") }
            throw DriverError(
                "native node provider was not confirmed for this scope", code: .observationIncomplete)
        }
    }

    private func register(
        _ element: AXUIElement, app: NSRunningApplication, scope: String, parent: Target? = nil,
        diagnosticBudget: (() throws -> Bool)? = nil, observationDeadline: TimeInterval? = nil,
        identityEvidence: AXRead<ElementIdentity.Facts>? = nil
    ) throws -> (String, Target) {
        try RequestDiagnostics.checkCancellation()
        AXMessaging.configure(element)
        let key = Identity(pid: app.processIdentifier, launched: app.launchDate, element: element, scope: scope)
        let previous = registry.observed(key)?.value
        let read = identityEvidence ?? readIdentity(element)
        if case .invalid = read {
            registry.invalidate { $0.scope == scope && CFEqual($0.element, element) }
            throw DriverError("native node no longer exists", code: .staleTarget)
        }
        var identity = ElementIdentity(facts: read.value ?? .init())
        let role = identity.attributes["AXRole"] ?? ""
        let provider = inspection.providerFacts(element, app: app)
        var target = Target(
            application: app, launched: app.launchDate, element: element, scope: scope, role: role, identity: identity,
            provider: provider.identity, providerKnown: provider.known,
            ancestors: parent.map { $0.ancestors + [$0.element] } ?? [],
            context: parent.map(ApplicationElements.childContext) ?? [])
        target.contextComplete = parent?.contextComplete ?? (role == kAXWindowRole)
        target.popupOwner = parent?.popupOwner
        if role == "AXWebArea", identity.attributes[kAXURLAttribute] != nil, provider.known {
            let until = min(observationDeadline ?? .infinity, clock() + 0.12)
            if let previous, previous.providerKnown, previous.provider == provider.identity {
                if previous.identity.refining(with: identity) != nil {
                    target.documentLocation = previous.documentLocation
                } else if let retained = previous.documentLocation,
                    let projected = identity.preservingLocation(from: previous.identity)
                {
                    if try retained.confirms(target: element, deadline: until) {
                        target.documentLocation = retained
                        identity = projected
                        target.identity = projected
                    } else if clock() >= until, observationDeadline != nil, diagnosticBudget == nil {
                        // The optional focused seed must not retire a
                        // document before ordinary collection can examine it.
                        throw DriverError(
                            "document location continuity was not sampled before the observation deadline",
                            code: .observationIncomplete)
                    }
                }
            }
            if target.documentLocation == nil, observationDeadline != nil, target.contextComplete,
                let window = target.context.last(where: { $0.identity.attributes[kAXRoleAttribute] == kAXWindowRole })?
                    .native.element
            {
                target.documentLocation = try NativeDocumentLocation.capture(
                    document: element, window: window, deadline: until)
            }
        }
        let directPopupMember = role == kAXListRole || role == kAXMenuItemRole && parent?.ancestors.isEmpty == true
        if directPopupMember, let parent, parent.role == kAXWindowRole,
            parent.identity.attributes[kAXSubroleAttribute] == "AXDialog", let budget = diagnosticBudget,
            let observationDeadline
        {
            // Record the list or the exact submenu item during original
            // forward observation. Input cannot fill an old stamp from a new
            // owner. Unknown provenance leaves the ordinary tree intact.
            do {
                // Optional provenance leaves a reserve for the ordinary tree
                // and never resets this collection's original deadline.
                let until = min(observationDeadline - 0.1, ProcessInfo.processInfo.systemUptime + 0.1)
                let facts = NativePopupAXFacts(
                    pid: app.processIdentifier, until: until,
                    check: {
                        guard try budget(), ProcessInfo.processInfo.systemUptime < until else {
                            throw NativePopupObservationUnavailable()
                        }
                    }
                ).facts()
                target.popupOwner =
                    try role == kAXListRole
                    ? NativePopupAttachment.observe(
                        popup: parent.element, list: element, facts: facts, same: { CFEqual($0, $1) })
                    : NativePopupAttachment.observeSubmenu(
                        popup: parent.element, leaf: element, facts: facts, same: { CFEqual($0, $1) })
            } catch is NativePopupObservationUnavailable {}
        }
        if parent == nil, let previous, previous.identity.refining(with: identity) != nil,
            !provider.known || !(previous.providerKnown || previous.provider != nil)
                || previous.provider == provider.identity
        {
            // An independent relationship does not provide a new containment
            // path and cannot erase the one already observed.
            target.ancestors = previous.ancestors
            target.context = previous.context
            target.popupOwner = previous.popupOwner
        }
        let ref = registry.observe(
            key,
            evidence: .init(
                identity: identity, provider: target.provider, context: target.context,
                providerKnown: target.providerKnown), value: target)
        return (ref, registry.target(ref) ?? target)
    }

    func registerRoot(_ element: AXUIElement, app: NSRunningApplication, scope: String) throws -> String {
        try register(element, app: app, scope: scope).0
    }

    private func related(
        _ element: AXUIElement, app: NSRunningApplication, scope: String, parent: Target? = nil,
        diagnosticBudget: (() throws -> Bool)? = nil, observationDeadline: TimeInterval? = nil,
        identityEvidence: AXRead<ElementIdentity.Facts>? = nil
    ) throws -> (String, Target)? {
        do {
            return try register(
                element, app: app, scope: scope, parent: parent, diagnosticBudget: diagnosticBudget,
                observationDeadline: observationDeadline, identityEvidence: identityEvidence)
        } catch let error as DriverError where error.code == .staleTarget { return nil }
    }

    /// Identity fields returned by the display sample itself. Unknown fields add no constraint.
    private static func sampledIdentity(
        _ batch: AXAttributeReader.Result, keys: Set<String> = Set(ElementIdentity.keys), absent: Set<String> = []
    ) -> AXRead<ElementIdentity.Facts> {
        if batch.status == .invalidUIElement { return .invalid }
        var facts = ElementIdentity.Facts(absent: absent)
        for field in batch.fields where keys.contains(field.name) && !field.timedOut {
            if field.status == .invalidUIElement { return .invalid }
            if [.noValue, .attributeUnsupported, .notImplemented].contains(field.status) {
                facts.absent.insert(field.name)
                continue
            }
            guard field.status == .success, let value = field.value else { continue }
            switch AXIdentity.decode(.success, [value] as CFArray, keys: [field.name]) {
            case .invalid: return .invalid
            case .value(let decoded):
                facts.values.merge(decoded.values) { _, current in current }
                facts.absent.formUnion(decoded.absent)
            default: break
            }
        }
        return .value(facts)
    }

    /// An unavailable read contributes nothing. A definite disagreement leaves
    /// this slot unresolved, while a native invalid result retires the object.
    private static func reconcileSamples(
        _ observed: AXRead<ElementIdentity.Facts>, _ independent: AXRead<ElementIdentity.Facts>
    ) -> AXRead<ElementIdentity.Facts>? {
        switch (observed, independent) {
        case (.invalid, _), (_, .invalid):
            return .invalid
        case (.value(let first), .value(let second)):
            let sampled = ElementIdentity(facts: first), later = ElementIdentity(facts: second)
            guard let combined = sampled.refining(with: later), later.refining(with: sampled) != nil else {
                return nil
            }
            return .value(.init(values: combined.attributes, absent: combined.absent))
        case (.value(let facts), _) where !facts.values.isEmpty || !facts.absent.isEmpty:
            return observed
        default:
            return independent
        }
    }

    private static func conflicts(_ identity: ElementIdentity, with sample: AXRead<ElementIdentity.Facts>) -> Bool {
        if case .invalid = sample { return true }
        guard let facts = sample.value else { return false }
        return identity.check(facts) == .changed
    }

    private func stamp(_ began: Int64) -> [String: Any] {
        ["id": UUID().uuidString, "startedAt": began, "completedAt": Int64(Date().timeIntervalSince1970 * 1000)]
    }

    func collect(
        _ ref: String, session: String, fields requested: [String], maxNodes: Int, maxDepth: Int,
        childrenPage: NativeInspectQuery.ChildrenPage? = nil, relationPage: NativeInspectQuery.RelationPage? = nil,
        advertisedFields: [String] = [], followRelations: [String] = [], textContent: Bool = false,
        actionDetail: NativeActionDetail = .full, focusedElement: Bool = false
    ) throws -> [String: Any] {
        var root = try readTarget(ref)
        // Only the path traversed in this collection is complete. Historical
        // ancestors on a subtree root do not prove omitted owners disappeared.
        root.contextComplete = root.role == kAXWindowRole
        let began = Int64(Date().timeIntervalSince1970 * 1000)
        let startedAt = clock()
        // The request already owns its absolute deadline. Preserve its local
        // encoding/final-check reserve once, proportionally to time actually
        // left; a fixed 200ms reserve used to discard every short observation.
        let budget = ObservationBudget(
            deadline: min(RequestDiagnostics.deadline ?? .infinity, startedAt + 5), startedAt: startedAt, clock: clock)
        let deadline = budget.collectionDeadline
        // Settable is a distinct native query, not an AX attribute name. Keep
        // its false/unsupported/error results separate from value reads.
        var queue: [(Target, String, Int)] = [(root, ref, 0)]
        var known = [
            Identity(
                pid: root.application.processIdentifier, launched: root.launched, element: root.element,
                scope: root.scope): ref
        ]
        var queued = Set([ref])
        var nodes = [[String: Any]](), links = [[String: Any]](), reasons = Set<String>()
        var pendingLinks = [AXRelationReader.Page]()
        var pendingValues = [(index: Int, property: String, value: CFTypeRef)]()
        var pendingNativeValues = [(index: Int, property: String, value: CFTypeRef)]()
        var pendingDeclarations = [(index: Int, property: String, issue: [String: Any])]()
        let nativeEncoder = AXNativeValueEncoding(resources: nativeResources)
        var index = 0
        var previewBytes = 0, capturedBytes = 0, edgeCount = 0
        let followed = Set(["AXChildren"] + followRelations)
        var relations = Self.relations + followRelations.filter { !Self.relations.contains($0) }
        let rootPage = relationPage?.page ?? childrenPage
        let pageProperty = relationPage?.property ?? "AXChildren"
        if rootPage != nil { relations = [pageProperty] + relations.filter { $0 != pageProperty } }
        func reading() throws -> Bool {
            try Task.checkCancellation()
            return budget.canCollect()
        }
        let displayFields = requested.filter(AXSubtreePrefetch.displayFields.contains)
        let subtree: AXSubtreePrefetch?
        if let captureSubtree, rootPage == nil,
            root.role == kAXWindowRole, root.identity.attributes["AXSubrole"] == "AXStandardWindow",
            root.provider?.pid == nil || root.provider?.pid == root.application.processIdentifier,
            maxNodes >= 128, maxDepth >= 4, !displayFields.isEmpty, try reading(), deadline - clock() >= 1
        {
            // Optional work leaves most of the original collection time for
            // public reads. The provider can return fewer rows than requested.
            let metric = RequestDiagnostics.metrics?.start(.readSubtreePrefetch)
            subtree = captureSubtree(
                root.element, displayFields + ElementIdentity.keys, min(maxDepth, 32),
                min(0.5, (deadline - clock()) / 4))
            metric?.end()
        } else {
            subtree = nil
        }
        let batchedRelations = subtree == nil ? [] : ["AXParent", "AXTitleUIElement", "AXWindow"].filter {
            !followed.contains($0)
        }
        var liveRelations = [String: [String: AXUIElement]]()
        func key(_ element: AXUIElement) -> Identity {
            Identity(
                pid: root.application.processIdentifier, launched: root.launched, element: element, scope: root.scope)
        }
        func encodeField(_ value: CFTypeRef) throws -> (value: Any?, failure: [String: Any]?) {
            guard CFGetTypeID(value) == AXUIElementGetTypeID() else { return AXFactEncoding.scalar(value) }
            let element = unsafeDowncast(value, to: AXUIElement.self), identity = key(element)
            if let ref = known[identity] { return AXFactEncoding.scalar(value, elementReference: ref) }
            guard known.count < 8192 else {
                return (nil, ["state": "error", "detail": "native value reference count limit"])
            }
            guard try reading() else {
                return (nil, ["state": "timeout", "detail": "native value reference collection deadline"])
            }
            guard let (ref, _) = try related(element, app: root.application, scope: root.scope) else {
                return (nil, ["state": "error", "detail": "native value object expired before registration"])
            }
            // This is a value relation, not a child or input-authority edge.
            // Values resolve after traversal so they cannot change which
            // children are expanded or the containment paths they retain.
            known[identity] = ref
            return AXFactEncoding.scalar(value, elementReference: ref)
        }
        func encodeNative(_ raw: CFTypeRef) throws -> [String: Any] {
            try nativeEncoder.encode(raw) { element in
                let identity = key(element)
                if let previous = known[identity] { return previous }
                guard known.count < 8192, try reading() else { throw RequestDeadlineExceeded() }
                guard let (reference, _) = try related(element, app: root.application, scope: root.scope) else {
                    throw DriverError("native value object expired before registration", code: .staleTarget)
                }
                known[identity] = reference
                return reference
            }
        }
        let needsCatalogue = !advertisedFields.isEmpty || !followRelations.isEmpty || textContent
        func acquire(
            _ element: AXUIElement, includeIdentity: Bool, withinBudget: (() throws -> Bool)? = nil
        ) throws -> Acquisition {
            if withinBudget == nil { AXMessaging.configure(element) }
            let canRead = withinBudget ?? reading
            let catalogue: (status: AXError, names: CFArray?)?
            if (includeIdentity || needsCatalogue), try canRead() {
                let metric = RequestDiagnostics.metrics?.start(.readCatalogue)
                var names: CFArray?
                let status = AXUIElementCopyAttributeNames(element, &names)
                metric?.end()
                catalogue = (status, names)
            } else { catalogue = nil }
            let names = advertisedFields.map { AXAttributeProfile.writableAttribute($0) ?? $0 }
            let unavailable = catalogue.map {
                AXFactEncoding.advertisedFieldFailures(Array(Set(names)), status: $0.status, names: $0.names)
            } ?? [:]
            let sampleFields = requested.filter {
                AXAttributeProfile.writableAttribute($0) == nil
                    && unavailable[AXAttributeProfile.writableAttribute($0) ?? $0]?["state"] as? String != "unsupported"
            }
            var fields = sampleFields
            let identityFields = includeIdentity ? catalogue.map {
                AXIdentity.positionRequest(ElementIdentity.keys, status: $0.status, names: $0.names)
            } : nil
            if let identity = identityFields {
                fields += identity.keys.filter { !fields.contains($0) }
            }
            // These single-object links are read from the same live object as
            // its geometry. Child order, arrays and continuations stay on the
            // public relation reader. Prefetch never supplies an owner link.
            if withinBudget == nil {
                fields += batchedRelations.filter { !fields.contains($0) }
            }
            var prefetched = [AXAttributeReader.Field]()
            if withinBudget == nil, let subtree {
                var pid: pid_t = 0
                if AXUIElementGetPid(element, &pid) == .success, pid == root.application.processIdentifier,
                    subtree.row(element, "AXRole") != nil
                {
                    for field in displayFields where sampleFields.contains(field) {
                        guard let value = subtree.row(element, field), CFGetTypeID(value) == CFStringGetTypeID()
                        else { continue }
                        prefetched.append(.init(name: field, value: value, status: .success, batchIndex: nil,
                                                timedOut: false))
                    }
                }
            }
            let metric = RequestDiagnostics.metrics?.start(.readAttributes)
            defer { metric?.end() }
            let prefetchedNames = Set(prefetched.map(\.name))
            var liveFields = fields.filter { !prefetchedNames.contains($0) }
            // The live role ties a descriptive row to the same native object.
            if !prefetched.isEmpty, !liveFields.contains("AXRole") { liveFields.append("AXRole") }
            var batch = try AXAttributeReader.read(element, fields: liveFields, api: attributes, withinBudget: canRead)
            if !prefetched.isEmpty, let subtree {
                let identity = Self.sampledIdentity(batch).value
                let role = identity?.values["AXRole"]
                func capturedIdentity(_ field: String) -> String? {
                    subtree.row(element, field).flatMap {
                        AXIdentity.decode(.success, [$0] as CFArray, keys: [field]).value?.values[field]
                    }
                }
                let consistent = role != nil && role == subtree.row(element, "AXRole") as? String
                    && identity?.values.allSatisfy { field, value in capturedIdentity(field) == value } == true
                    && identity?.absent.allSatisfy { (capturedIdentity($0) ?? "").isEmpty } == true
                if !consistent {
                    prefetched = []
                    liveFields = fields
                    batch = try AXAttributeReader.read(element, fields: liveFields, api: attributes, withinBudget: canRead)
                } else {
                    RequestDiagnostics.metrics?.count(.readPrefetchedRow)
                }
            }
            var identity: AXRead<ElementIdentity.Facts>?
            var identityConflict = false
            if let identityFields, let catalogue {
                identity = AXIdentity.fromObservationBatch(
                    status: batch.status, raw: batch.raw, fields: liveFields,
                    catalogueStatus: catalogue.status, catalogue: catalogue.names)
                if identity == nil {
                    let sampled = Self.sampledIdentity(
                        batch, keys: Set(identityFields.keys), absent: identityFields.absent)
                    if case .invalid = sampled {
                        identity = .invalid
                    } else {
                        let fallback = RequestDiagnostics.metrics?.start(.readIdentityFallback)
                        let independent = try canRead() ? readIdentity(element) : .unavailable(.cannotComplete)
                        fallback?.end()
                        identity = Self.reconcileSamples(sampled, independent)
                        identityConflict = identity == nil
                    }
                }
            }
            return Acquisition(
                catalogue: catalogue, sampleFields: sampleFields, batchFields: liveFields, batch: batch,
                prefetched: prefetched, identity: identity,
                identityConflict: identityConflict)
        }
        func sample(
            _ target: Target, nodeID: String, acquisition: Acquisition, withinBudget: (() throws -> Bool)? = nil
        ) throws -> [String: [String: Any]] {
            if withinBudget == nil { AXMessaging.configure(target.element) }
            let canRead = withinBudget ?? reading
            var values = [String: Any](), missing = [String: Any]()
            var relationFailures = [String: [String: Any]]()
            var nativeProperties = [[String: Any]]()
            var nativeReads = [String: [String: Any]]()
            var textCountDeclared = false
            if needsCatalogue, let catalogue = acquisition.catalogue {
                let status = catalogue.status, attributes = catalogue.names
                textCountDeclared =
                    status == .success && (attributes as? [String])?.contains("AXNumberOfCharacters") == true
                // Preserve the actual catalogue already obtained for profile
                // validation. It is a method result, not a fabricated AX field.
                var catalogue: [String: Any] = ["key": ["toolkit", "attribute_names"], "value": NSNull()]
                if status == .success, let attributes {
                    catalogue["value"] = try encodeNative(attributes)
                } else {
                    catalogue["state"] = "error"
                    catalogue["read"] = [
                        "api": "AXUIElementCopyAttributeNames", "complete": false,
                        "issues": [NativeObservationEncoding.issue(AXFactEncoding.status(status))]
                    ]
                }
                nativeProperties.append(catalogue)
                // Writable probes use a different API, but their underlying
                // attribute declaration is still the provider's native name.
                let names = advertisedFields.map { AXAttributeProfile.writableAttribute($0) ?? $0 }
                let unavailable = AXFactEncoding.advertisedFieldFailures(
                    Array(Set(names)), status: status, names: attributes)
                for name in advertisedFields {
                    if let failure = unavailable[AXAttributeProfile.writableAttribute(name) ?? name] {
                        if failure["state"] as? String == "unsupported" {
                            missing[name] = failure
                        } else {
                            pendingDeclarations.append((nodes.count, name, failure))
                        }
                    }
                }
                relationFailures = AXFactEncoding.advertisedFieldFailures(
                    followRelations, status: status, names: attributes)
            }
            let keys = acquisition.batchFields
            let batch = acquisition.batch
            let raw = batch.raw, status = batch.status
            let canReadFields = batch.queried
            if withinBudget == nil, batch.fields.contains(where: { $0.timedOut }) {
                reasons.insert("collection time budget")
            }
            let attributes = raw as? [Any]
            if let raw, attributes?.count != keys.count {
                // A malformed batch cannot be zipped to requested names.
                // Retain its actual shape separately from per-field failures.
                nativeProperties.append([
                    "key": ["toolkit", "attribute_batch"], "arguments": [NativeObservationEncoding.scalar(keys)],
                    "value": try encodeNative(raw),
                    "read": [
                        "api": "AXUIElementCopyMultipleAttributeValues", "complete": false,
                        "details": NativeObservationEncoding.details(["native_status": status.rawValue]),
                        "issues": [
                            [
                                "reason": "malformed_batch",
                                "detail": "native result count differs from requested attributes"
                            ]
                        ]
                    ]
                ])
            }
            if withinBudget == nil, !batchedRelations.isEmpty {
                let links = batch.fields.compactMap { field -> (String, AXUIElement)? in
                    guard batchedRelations.contains(field.name), field.batchIndex != nil,
                        field.status == .success, let value = field.value, CFGetTypeID(value) == AXUIElementGetTypeID()
                    else { return nil }
                    return (field.name, unsafeDowncast(value, to: AXUIElement.self))
                }
                if !links.isEmpty {
                    liveRelations[nodeID] = Dictionary(uniqueKeysWithValues: links)
                    RequestDiagnostics.metrics?.count(.readBatchedSingleRelations)
                }
            }
            for field in batch.fields + acquisition.prefetched {
                let name = field.name
                guard acquisition.sampleFields.contains(name) else { continue }
                if let position = field.batchIndex {
                    nativeReads[name] = NativeObservationEncoding.batchPropertyRead(owner: nodeID, index: position)
                } else if acquisition.prefetched.contains(where: { $0.name == name }) {
                    nativeReads[name] = ["api": AXSubtreePrefetch.nativeAPI]
                } else {
                    nativeReads[name] = [
                        "api": "AXUIElementCopyAttributeValue",
                        "details": NativeObservationEncoding.details([
                            "attribute": name, "native_status": field.status.rawValue,
                            "batch_fallback_status": status.rawValue
                        ])
                    ]
                }
                if field.status == .success, let value = field.value {
                    pendingNativeValues.append((nodes.count, name, value))
                    if CFGetTypeID(value) == AXUIElementGetTypeID() {
                        pendingValues.append((nodes.count, name, value))
                        continue
                    }
                }
                if field.timedOut {
                    missing[name] = ["state": "timeout", "detail": "collection time budget"]
                    continue
                }
                let decoded =
                    field.status == .success
                    ? AXFactEncoding.scalar(field.value) : (value: nil, failure: AXFactEncoding.status(field.status))
                if let value = decoded.value { values[name] = value }
                if let failure = decoded.failure { missing[name] = failure }
            }
            for name in requested {
                guard missing[name] == nil else { continue }
                guard let attribute = AXAttributeProfile.writableAttribute(name) else { continue }
                guard try canRead() else {
                    missing[name] = ["state": "timeout", "detail": "collection time budget"]
                    if withinBudget == nil { reasons.insert("collection time budget") }
                    continue
                }
                var writable = DarwinBoolean(false)
                let status = AXUIElementIsAttributeSettable(target.element, attribute as CFString, &writable)
                nativeReads[name] = [
                    "api": "AXUIElementIsAttributeSettable",
                    "details": NativeObservationEncoding.details([
                        "attribute": attribute, "native_status": status.rawValue
                    ])
                ]
                if status == .success {
                    values[name] = writable.boolValue
                } else {
                    missing[name] = AXFactEncoding.status(status)
                }
            }
            if textContent && textCountDeclared {
                nativeProperties += try AXTextContentReader.read(
                    target.element, values: values, failures: missing, withinBudget: canRead)
            }
            for (name, value) in values {
                guard let text = value as? String else { continue }
                let available = max(0, 8 * 1024 * 1024 - previewBytes)
                if text.utf16.count > 16_384 || text.utf8.count > available {
                    // This immutable text token is separate from the live node.
                    let preview = String(text.unicodeScalars.prefix(min(4096, available / 4)))
                    values[name] = preview
                    previewBytes += preview.utf8.count
                    var limited: [String: Any] = ["state": "truncated", "totalUnits": text.utf16.count]
                    if text.utf8.count <= 8 * 1024 * 1024 - capturedBytes {
                        if let textRef = registry.bind(nodeID, text: text), registry.text(textRef) != nil {
                            limited["textRef"] = textRef
                            capturedBytes += text.utf8.count
                        } else {
                            limited["detail"] = "immutable text continuation unavailable; preview only"
                        }
                    } else {
                        limited["detail"] = "immutable text capture budget exhausted; preview only"
                    }
                    missing[name] = limited
                } else {
                    previewBytes += text.utf8.count
                }
            }
            let actionRead = try AXActionCatalogue.collect(
                detail: actionDetail, withinBudget: canRead,
                names: {
                    var names: CFArray?
                    let status = AXUIElementCopyActionNames(target.element, &names)
                    return (status, names)
                },
                description: { name in
                    var value: CFString?
                    let status = AXUIElementCopyActionDescription(target.element, name as CFString, &value)
                    return (status, value)
                }, encode: encodeNative)
            let actionFacts = actionRead.facts
            nativeProperties += actionRead.nativeProperties
            nodes.append([
                "node": nodeID, "requestedFields": requested, "values": values, "fieldStatus": missing,
                "actions": actionFacts, "nativeProperties": nativeProperties, "nativeReads": nativeReads,
                "nativeRead": NativeObservationEncoding.batchRead(
                    fields: keys, status: status.rawValue, queried: canReadFields)
            ])

            return relationFailures
        }
        // Capture facts when an object is discovered, before another container
        // can replace its virtual children. Keep already discovered siblings'
        // own fields/actions even if a deep branch exhausts the shared budget.
        let rootAcquisition = try acquire(root.element, includeIdentity: false)
        let rootIdentity = Self.sampledIdentity(rootAcquisition.batch)
        if Self.conflicts(root.identity, with: rootIdentity) {
            let until = min(deadline, clock() + 0.12)
            let locationOnly = rootIdentity.value.map { root.identity.locationChange($0) } == true
            let confirmed =
                try locationOnly && root.documentLocation?.confirms(target: root.element, deadline: until) == true
            if !confirmed {
                if locationOnly, root.documentLocation != nil, clock() >= until {
                    throw DriverError(
                        "document location continuity was not sampled before the observation deadline",
                        code: .observationIncomplete)
                }
                registry.invalidate { $0.scope == root.scope && CFEqual($0.element, root.element) }
                throw DriverError("native read target identity changed", code: .staleTarget)
            }
        }
        var sampled = [ref: try sample(root, nodeID: ref, acquisition: rootAcquisition)]
        var supplement: (target: Target, reference: String, parent: String)?
        var focusedListPath = [String]()
        if focusedElement, rootPage == nil, [kAXWindowRole, kAXSheetRole].contains(root.role) {
            // Reserve a single slot for a proved focused object before a wide
            // Children traversal can exhaust it. Keep most of the time for
            // ordinary collection even on short observations.
            let focusStarted = clock()
            let focusDeadline = focusStarted + min(0.075, max(0, deadline - focusStarted) / 4)
            let app = AXUIElementCreateApplication(root.application.processIdentifier)
            func canReadFocus() throws -> Bool { try reading() && clock() < focusDeadline }
            func setFocusTimeout(_ element: AXUIElement) throws -> Bool {
                guard try canReadFocus() else { return false }
                AXUIElementSetMessagingTimeout(element, Float(max(0.001, min(0.05, focusDeadline - clock()))))
                return true
            }
            func readFocusElement(
                _ element: AXUIElement, _ attribute: String, optional: Bool = false
            ) throws -> AXUIElement? {
                guard try setFocusTimeout(element) else { throw RequestDeadlineExceeded() }
                defer { AXMessaging.configure(element) }
                let (status, value) = attributes.single(element, attribute)
                if [.noValue, .attributeUnsupported, .notImplemented].contains(status) { return nil }
                guard status == .success, let value, CFGetTypeID(value) == AXUIElementGetTypeID() else {
                    if optional, status != .invalidUIElement { return nil }
                    throw DriverError("focused element evidence is unavailable", code: .observationIncomplete)
                }
                return unsafeDowncast(value, to: AXUIElement.self)
            }
            var focusRef: String?
            var focusState = "unavailable"
            do {
                if let focus = try readFocusElement(app, kAXFocusedUIElementAttribute) {
                    if let existing = known[key(focus)], sampled[existing] != nil {
                        focusRef = existing
                        focusState = "included"
                    } else if nodes.count < maxNodes, maxDepth > 0,
                        let path = try NativeFocusedReadSeed.path(
                            from: focus, to: root.element, maximumDepth: min(128, maxDepth),
                            parent: { try readFocusElement($0, kAXParentAttribute) },
                            canContinue: canReadFocus)
                    {
                        let owner = try readFocusElement(focus, kAXWindowAttribute, optional: true)
                        // A control in the proved Parent path may report the
                        // selected sheet itself as its AXWindow.
                        let window =
                            root.role == kAXWindowRole || owner.map({ CFEqual($0, root.element) }) == true
                            ? root.element : try readFocusElement(root.element, kAXWindowAttribute, optional: true)
                        if let owner, let window, !CFEqual(owner, window) {
                            focusState = "outside"
                            throw DriverError("reported focus is outside this window", code: .observationIncomplete)
                        }
                        let canReadLeaf = { try setFocusTimeout(focus) }
                        let acquisition = try acquire(focus, includeIdentity: false, withinBudget: canReadLeaf)
                        defer { AXMessaging.configure(focus) }
                        let identity = Self.sampledIdentity(acquisition.batch)
                        if identity.value?.values[kAXRoleAttribute] != nil,
                            let final = try readFocusElement(app, kAXFocusedUIElementAttribute), CFEqual(focus, final)
                        {
                            var parent = root
                            var ancestorRefs = [ref]
                            for element in path.dropFirst().reversed() {
                                guard try setFocusTimeout(element) else { throw RequestDeadlineExceeded() }
                                defer { AXMessaging.configure(element) }
                                let (status, fields) = attributes.batch(element, ElementIdentity.keys)
                                let evidence = AXIdentity.decode(status, fields, keys: ElementIdentity.keys)
                                guard let (reference, retained) = try related(
                                    element, app: root.application, scope: root.scope, parent: parent,
                                        observationDeadline: focusDeadline, identityEvidence: evidence
                                ), !retained.role.isEmpty else {
                                    throw DriverError(
                                        "focused element's parent is unavailable", code: .observationIncomplete)
                                }
                                parent = retained
                                ancestorRefs.append(reference)
                            }
                            guard try canReadFocus(),
                                let (reference, target) = try related(
                                    focus, app: root.application, scope: root.scope, parent: parent,
                                    observationDeadline: focusDeadline, identityEvidence: identity)
                            else { throw RequestDeadlineExceeded() }
                            sampled[reference] = try sample(
                                target, nodeID: reference, acquisition: acquisition, withinBudget: canReadLeaf)
                            known[key(focus)] = reference
                            supplement = (target, reference, ancestorRefs.last ?? ref)
                            if target.role == kAXListRole {
                                focusedListPath = [reference] + ancestorRefs.reversed()
                            }
                            focusRef = reference
                            focusState = "confirmed"
                        }
                    }
                } else {
                    focusState = "absent"
                }
            } catch is RequestDeadlineExceeded {
                // Ordinary tree coverage is independent of the optional focus sample.
            } catch let error as DriverError where [.observationIncomplete, .staleTarget].contains(error.code) {}
            var properties = nodes[0]["nativeProperties"] as? [[String: Any]] ?? []
            let seed = focusRef.map { NativeObservationEncoding.value("AXUIElement", ["ref": $0]) }
                ?? NativeObservationEncoding.value("CFNull", NSNull())
            properties.append([
                "key": ["toolkit", "focused_read_seed"], "value": seed,
                "read": ["api": "AXUIElementCopyAttributeValue", "complete": focusState != "unavailable",
                         "details": NativeObservationEncoding.details(["attribute": kAXFocusedUIElementAttribute,
                                                                        "source": "application", "state": focusState])]
            ])
            nodes[0]["nativeProperties"] = properties
        }
        while index < queue.count, try reading() {
            let (target, nodeID, depth) = queue[index]
            index += 1
            let preferredChild = focusedListPath.last == nodeID ? focusedListPath.dropLast().last : nil
            var preferredIndex: Int?
            let relationFailures = sampled[nodeID] ?? [:]
            // Expand the current branch before materializing another container.
            // Retaining an AX proxy does not keep the remote view alive. Native
            // sibling/relation order and total queue reservations stay intact.
            var childInsertion = index

            // The caller chooses which raw edges to follow. Collecting an edge
            // does not reinterpret it as parentage or input ownership.
            for property in relations {
                if let failure = relationFailures[property], failure["state"] as? String == "unsupported" {
                    var link: [String: Any] = [
                        "source": nodeID, "property": property, "targets": [String](), "certainty": "unknown"
                    ]
                    link.merge(failure) { _, value in value }
                    links.append(link)
                    continue
                }
                guard try reading() else {
                    links.append(AXFactEncoding.relationFailure(source: nodeID, property: property))
                    reasons.insert("collection time budget")
                    continue
                }
                let page = nodeID == ref && property == pageProperty ? rootPage : nil
                let shouldFollow = followed.contains(property) || page != nil
                var limit =
                    shouldFollow
                    ? (depth < maxDepth ? min(maxNodes - queue.count, 8192 - known.count, 32768 - edgeCount) : 0)
                    : min(128, 32768 - edgeCount)
                // A wide collection must not reserve the entire node budget
                // for sibling shells before their labels/actions can be read.
                // Use the same page size as ordinary Element.read; retain the
                // native total and cursor instead of declaring omitted rows
                // absent. An explicitly requested page keeps its own limit.
                limit = min(limit, page?.limit ?? 128)
                let relation: AXRelationReader.Result
                do {
                    relation = try relationReader.read(
                        target.element, source: nodeID, property: property, limit: limit, cursor: page?.cursor,
                        liveBatchValue: liveRelations[nodeID]?[property],
                        withinBudget: reading)
                } catch let error as DriverError where error.code == .snapshotExpired {
                    // A changing branch is a local observation gap, not loss of
                    // the fields and unrelated branches already collected.
                    var failure = AXFactEncoding.relationFailure(source: nodeID, property: property, error: .failure)
                    failure["detail"] = error.localizedDescription
                    links.append(failure)
                    reasons.insert("native relation changed during collection")
                    continue
                }
                switch relation {
                case .failure(var value):
                    if let raw = value.removeValue(forKey: "rawNativeValue") {
                        value["wrappedNativeValue"] = try encodeNative(raw as CFTypeRef)
                    }
                    links.append(value)
                case .page(let page):
                    if !shouldFollow {
                        pendingLinks.append(page)
                        continue
                    }
                    var unresolved: String?
                    let registration = try relationReader.register(page) { element in
                        guard try reading() else { return .stop }
                        let nativeKey = key(element)
                        if let previous = known[nativeKey] {
                            if let supplement, previous == supplement.reference, queued.insert(previous).inserted {
                                // The ordinary relation reached the existing
                                // sample. Reuse its reserved slot at this depth.
                                queue.insert((supplement.target, previous, depth + 1), at: childInsertion)
                                childInsertion += 1
                            }
                            return .target(previous)
                        }
                        let reserved = supplement.map { queued.contains($0.reference) ? 0 : 1 } ?? 0
                        guard queue.count + reserved < maxNodes else { return .stop }
                        let acquisition = try acquire(element, includeIdentity: true)
                        if acquisition.identityConflict {
                            unresolved = "native identity samples disagreed before registration"
                            return .stop
                        }
                        guard
                            let (life, child) = try related(
                                element, app: root.application, scope: root.scope,
                                parent: property == "AXChildren" ? target : nil, diagnosticBudget: reading,
                                observationDeadline: deadline, identityEvidence: acquisition.identity)
                        else { return .unavailable }
                        known[nativeKey] = life
                        if queued.insert(life).inserted {
                            sampled[life] = try sample(child, nodeID: life, acquisition: acquisition)
                            queue.insert((child, life, depth + 1), at: childInsertion)
                            childInsertion += 1
                        }
                        return .target(life)
                    }
                    edgeCount += registration.targets.count
                    var link = relationReader.encode(page, registration: registration)
                    if let issue = relationFailures[property] {
                        // The getter can still return useful objects when its
                        // attribute catalogue is unavailable. Keep that payload
                        // without claiming a complete declaration/empty relation.
                        link["state"] = "partial"
                        link["detail"] = [link["detail"] as? String, issue["detail"] as? String].compactMap { $0 }
                            .joined(separator: "; ")
                        if registration.targets.isEmpty { link["certainty"] = "unknown" }
                    }
                    if let unresolved {
                        link["detail"] = [link["detail"] as? String, unresolved].compactMap { $0 }
                            .joined(separator: "; ")
                    }
                    if link["state"] as? String != "complete" { reasons.insert("partial native relation: " + property) }
                    links.append(link)
                    if let preferredChild, property == kAXChildrenAttribute, page.issue == nil,
                        registration.targets.contains(preferredChild)
                    {
                        preferredIndex = queue[index..<childInsertion].firstIndex { $0.1 == preferredChild }
                    }
                }
            }
            // A focused list needs its contents to be useful. Advance its
            // proved path only through freshly registered forward children.
            // Keep the native page order and use the same traversal budget.
            if let preferredIndex {
                if preferredIndex != index {
                    let child = queue.remove(at: preferredIndex)
                    queue.insert(child, at: index)
                }
                focusedListPath.removeLast()
            } else {
                focusedListPath.removeAll()
            }
        }
        if index < queue.count { reasons.insert("collection time budget") }
        // Prefetched fields are real observations, but their unexplored edges
        // must remain explicitly unknown and offer the same subtree read route.
        for (_, nodeID, _) in queue.dropFirst(index) where sampled[nodeID] != nil {
            for property in relations {
                if let failure = sampled[nodeID]?[property] {
                    var link: [String: Any] = [
                        "source": nodeID, "property": property, "targets": [String](), "certainty": "unknown"
                    ]
                    link.merge(failure) { _, value in value }
                    links.append(link)
                } else {
                    links.append(AXFactEncoding.relationFailure(source: nodeID, property: property))
                }
            }
        }
        let detached = supplement.flatMap { queued.contains($0.reference) ? nil : $0 }
        if let detached {
            links.append([
                "source": detached.reference, "property": kAXParentAttribute, "targets": [detached.parent],
                "state": "complete", "certainty": "confirmed", "total": 1,
                "nativeAPI": "AXUIElementCopyAttributeValue", "nativeType": "AXUIElement"
            ])
            // Every sampled node accounts for the requested relationship
            // profile. Only the real Parent was sampled by the focus read.
            for property in relations where property != kAXParentAttribute {
                var link: [String: Any] = [
                    "source": detached.reference, "property": property, "targets": [String](),
                    "certainty": "unknown", "state": "partial",
                    "detail": "relationship not collected in the supplemental focus sample"
                ]
                if let failure = sampled[detached.reference]?[property] { link.merge(failure) { _, value in value } }
                links.append(link)
            }
        }
        let collected = Set(nodes.compactMap { $0["node"] as? String })
        for page in pendingLinks {
            var availableEdges = 32768 - edgeCount
            let registration = try relationReader.register(page) { element in
                guard availableEdges > 0 else { return .stop }
                if let previous = known[key(element)] {
                    availableEdges -= 1
                    return .target(previous)
                }
                guard known.count < 8192, try reading() else { return .stop }
                // A native related object remains inspectable even when it is
                // not a child. This does not authorize it as an input recipient.
                guard let (life, _) = try related(element, app: root.application, scope: root.scope) else {
                    return .unavailable
                }
                known[key(element)] = life
                availableEdges -= 1
                return .target(life)
            }
            edgeCount += registration.targets.count
            links.append(relationReader.encode(page, registration: registration))
        }
        for item in pendingValues {
            let encoded = try encodeField(item.value)
            // The collector constructs this internal field with the asserted type.
            // swiftlint:disable:next force_cast
            var values = nodes[item.index]["values"] as! [String: Any]
            // The collector constructs this internal field with the asserted type.
            // swiftlint:disable:next force_cast
            var missing = nodes[item.index]["fieldStatus"] as! [String: Any]
            if let value = encoded.value { values[item.property] = value }
            if let failure = encoded.failure { missing[item.property] = failure }
            nodes[item.index]["values"] = values
            nodes[item.index]["fieldStatus"] = missing
        }
        for item in pendingNativeValues {
            // Immutable text previews remain bounded; their read issue carries
            // the captured full text token and native UTF-16 count.
            // The collector constructs this internal field with the asserted type.
            // swiftlint:disable:next force_cast
            let statuses = nodes[item.index]["fieldStatus"] as! [String: [String: Any]]
            // The collector constructs this internal field with the asserted type.
            // swiftlint:disable:next force_cast
            let values = nodes[item.index]["values"] as! [String: Any]
            let raw =
                statuses[item.property]?["state"] as? String == "truncated"
                ? values[item.property]! as CFTypeRef : item.value
            do {
                let encoded = try encodeNative(raw)
                var native = nodes[item.index]["nativeValues"] as? [String: [String: Any]] ?? [:]
                // The collector constructs this internal field with the asserted type.
                // swiftlint:disable:next force_cast
                var missing = nodes[item.index]["fieldStatus"] as! [String: [String: Any]]
                if encoded["nativeType"] as? String == "AXValue.AXError", var status = missing[item.property] {
                    // AX batch success can contain an AXError value for one
                    // slot. Keep that wrapper as error evidence, not a value.
                    status["wrappedNativeValue"] = encoded
                    missing[item.property] = status
                } else {
                    native[item.property] = encoded
                    if missing[item.property]?["detail"] as? String
                        == "native property type is not representable in this field profile"
                    {
                        missing.removeValue(forKey: item.property)
                    }
                }
                nodes[item.index]["fieldStatus"] = missing
                nodes[item.index]["nativeValues"] = native
            } catch {
                if error is CancellationError { throw error }
                // The raw field's own value/error remains in its local record;
                // losing a nested object registration is an explicit gap.
                // The collector constructs this internal field with the asserted type.
                // swiftlint:disable:next force_cast
                var missing = nodes[item.index]["fieldStatus"] as! [String: Any]
                missing[item.property] = ["state": "error", "detail": String(describing: error)]
                nodes[item.index]["fieldStatus"] = missing
            }
        }
        for item in pendingDeclarations {
            // The collector constructs this internal field with the asserted type.
            // swiftlint:disable:next force_cast
            var missing = nodes[item.index]["fieldStatus"] as! [String: [String: Any]]
            if missing[item.property] == nil {
                missing[item.property] = item.issue
                nodes[item.index]["fieldStatus"] = missing
            }
        }
        relationReader.retainAvailableCursors(&links)
        guard !nodes.isEmpty else { throw RequestDeadlineExceeded() }
        var unread = Set(known.values).subtracting(collected)
        if let detached, !collected.contains(detached.parent) { unread.insert(detached.parent) }
        return [
            "kind": "nodes", "session": session, "surface": root.scope, "platform": "macos", "observed": stamp(began),
            "roots": [ref], "nodes": nodes, "requestedRelations": relations, "relations": links,
            "unread": unread.sorted(), "stopReasons": reasons.sorted(),
            "nativeResources": nativeEncoder.resources
        ]
    }

    func relation(_ ref: String, session: String, property: String, cursor: String?, limit: Int) throws -> [String: Any]
    {
        var root = try readTarget(ref)
        root.contextComplete = root.role == kAXWindowRole
        let began = Int64(Date().timeIntervalSince1970 * 1000)
        let startedAt = clock()
        let budget = ObservationBudget(
            deadline: min(RequestDiagnostics.deadline ?? .infinity, startedAt + 5), startedAt: startedAt, clock: clock)
        AXMessaging.configure(root.element)
        let result = try relationReader.read(
            root.element, source: ref, property: property, limit: limit, cursor: cursor,
            withinBudget: {
                try Task.checkCancellation()
                return budget.canCollect()
            })
        var link: [String: Any]
        let nativeEncoder = AXNativeValueEncoding(resources: nativeResources)
        var rawReferences = Set<String>()
        switch result {
        case .failure(var failure):
            if let raw = failure.removeValue(forKey: "rawNativeValue") {
                failure["wrappedNativeValue"] = try nativeEncoder.encode(raw as CFTypeRef) { element in
                    guard budget.canCollect() else { throw RequestDeadlineExceeded() }
                    if CFEqual(element, root.element) { return ref }
                    guard let (reference, _) = try related(element, app: root.application, scope: root.scope) else {
                        throw DriverError("native relation value expired before registration", code: .staleTarget)
                    }
                    rawReferences.insert(reference)
                    return reference
                }
            }
            link = failure
        case .page(let page):
            let registration = try relationReader.register(page) { element in
                try RequestDiagnostics.checkCancellation()
                guard budget.canCollect() else { return .stop }
                if CFEqual(element, root.element) { return .target(ref) }
                guard
                    let (life, _) = try related(
                        element, app: root.application, scope: root.scope, parent: property == "AXChildren" ? root : nil
                    )
                else { return .unavailable }
                return .target(life)
            }
            link = relationReader.encode(page, registration: registration)
        }
        return [
            "kind": "relation", "session": session, "surface": root.scope, "platform": "macos",
            "observed": stamp(began), "relation": link, "unread": rawReferences.sorted(),
            "nativeResources": nativeEncoder.resources
        ]
    }
}
