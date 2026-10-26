/// PURE: call-local query strategy. Legacy is absorbing; it never stores facts,
/// grants dispatch, retries input or changes a request's budget/progress.
struct NativeKeyboardBoundaryPolicy {
    enum Mode: Equatable { case projection, legacy }
    typealias Decision = NativeKeyboardBoundaryDecision
    private(set) var mode: Mode
    private(set) var projectionHits = 0
    private(set) var legacySamples = 0
    private(set) var firstNeedLegacy: String?

    init(eligible: Bool) { mode = eligible ? .projection : .legacy }
    mutating func recordProjection(_ decision: Decision) {
        guard mode == .projection else { return }
        if case .confirmed = decision { projectionHits += 1 }
        if case .needsLegacy(let reason) = decision {
            if firstNeedLegacy == nil { firstNeedLegacy = reason }
            mode = .legacy
        }
    }
    mutating func recordLegacySample() { legacySamples += 1 }

    static func eligible(
        windowCommand: Bool, unconstrained: Bool, sameRoot: Bool, exactNativeID: Bool, localProcess: Bool,
        noObservedWeb: Bool, sameKeyboardSurface: Bool
    ) -> Bool {
        windowCommand && unconstrained && sameRoot && exactNativeID && localProcess && noObservedWeb
            && sameKeyboardSurface
    }

    static func parentKnown<Element>(
        child: Element, parent: Element?, application: Element, windows: [Element], same: (Element, Element) -> Bool
    ) -> Bool {
        guard let parent, !same(parent, child) else { return false }
        return same(parent, application) || windows.contains { same($0, parent) }
    }

    static func decide<Element>(
        original: Element, projection: NativeKeyboardBoundaryProjection<Element>, same: (Element, Element) -> Bool
    ) -> Decision {
        if let issue = projection.issue { return .needsLegacy(issue) }
        let bound = projection.windows.filter { same($0.element, original) }
        guard bound.count == 1 else { return bound.isEmpty ? .changed : .needsLegacy("ambiguous original AX window") }
        guard bound[0].role == "AXWindow", bound[0].parent == nil, bound[0].parentKnown else {
            return .needsLegacy("original surface is no longer a flat AXWindow")
        }
        // Preserve the original optional modality. A missing AXModal on an
        // unrelated auxiliary AXWindow is not a positive non-modal assertion
        // and did not become a blocker through catalogue enrichment either.
        if projection.windows.contains(where: { $0.modal == true || ($0.role == "AXSheet" && $0.modal == nil) }) {
            return .needsLegacy("modal or unresolved sheet requires full scope")
        }
        return .confirmed
    }
}
