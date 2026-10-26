import Foundation

/// A lease spans the complete public operation, not just one RPC. Input units
/// own their local cleanup; the lease cannot be released while a unit is live.
@MainActor final class NativeOperation {
    enum State: String { case active, stopping, closed }
    let token = UUID().uuidString
    let deadline: TimeInterval
    let writable: Bool
    let request: NativeIntent
    let clickContract: String?
    var intent: String { request.kind }
    let target: [String: Any]
    private(set) var state = State.active
    private(set) var busy = false
    private(set) var stopReason: String?
    private(set) var cleanup = "complete"
    private(set) var inputStarted = false
    /// Non-controlling observers follow the complete public operation, across
    /// its inspect/prepare/execute RPCs, including expiry and cancellation.
    var onClose: ((Bool) -> Void)?
    private let steps = RequestLedger()
    private var timer: Task<Void, Never>?
    private var release: (() -> Void)?
    private var inputTarget: [String: Any]?
    private var inputWithin = [String]()
    private var releaseInputTarget: (() -> Void)?
    private var inputReferences = [String: () -> Void]()
    private var unavailableBackgroundPreparation = Set<String>()
    private var confirmedPreparation = Set<String>()
    private struct ClickAuthority {
        let target: [String: Any]
        let within: [String]
        let family: String
        let step: Int64
    }
    private var clickAuthorities = [ClickAuthority]()
    private var clickPreparationRequiresObservation = false
    private let cancelActive: () -> Void
    private let now: () -> TimeInterval

    init(
        timeoutMs: Int, writable: Bool, intent: NativeIntent, target: [String: Any], clickContract: String? = nil,
        now: @escaping () -> TimeInterval = { ProcessInfo.processInfo.systemUptime }, release: @escaping () -> Void,
        cancelActive: @escaping () -> Void
    ) {
        self.now = now
        self.deadline = now() + Double(timeoutMs) / 1000
        self.writable = writable
        self.request = intent
        self.target = target
        self.clickContract = clickContract
        self.release = release
        self.cancelActive = cancelActive
        timer = Task { @MainActor [weak self] in
            do { try await Task.sleep(for: .milliseconds(timeoutMs)) } catch { return }
            self?.stop("deadline_exceeded")
        }
    }

    func check(write: Bool = false) throws {
        if now() >= deadline { stop("deadline_exceeded") }
        guard state == .active else {
            throw DriverError(
                "native operation has ended (\(stopReason ?? state.rawValue)); no new input sent",
                code: .executionFailed, next: "read the target before starting a new operation")
        }
        guard !write || writable else {
            throw DriverError("a read operation cannot dispatch input", code: .invalidRequest)
        }
        try Task.checkCancellation()
    }

    /// One public operation may prepare its receiver in several units, but may
    /// cross the requested-input barrier only once. A compound unit (drag, text
    /// sequence) owns all its events and releases inside that one dispatch.
    func recordInputDispatch() { inputStarted = true }

    func preparationConfirmed(_ primitive: String) -> Bool { confirmedPreparation.contains(primitive) }

    func confirmPreparation(_ primitive: String) throws {
        try check(write: true)
        guard !inputStarted else {
            throw DriverError("input already dispatched", code: .executionUnknown, effect: .possible)
        }
        confirmedPreparation.insert(primitive)
    }

    func backgroundPreparationUnavailable(_ primitive: String) throws {
        try check(write: true)
        guard !inputStarted else {
            throw DriverError("input already dispatched", code: .executionUnknown, effect: .possible)
        }
        unavailableBackgroundPreparation.insert(primitive)
    }

    /// QUERY evidence from the selected native strategy can establish that no
    /// background mechanism exists, without emitting a synthetic failed input.
    func recordBackgroundMechanismUnavailable(_ primitive: String) throws {
        try backgroundPreparationUnavailable(primitive)
    }

    func requireForegroundPreparation(_ primitive: String, hostAllowed: Bool) throws {
        try check(write: true)
        guard hostAllowed else {
            throw DriverError("foreground assistance is not enabled by this host", code: .modeUnsupported)
        }
        guard !inputStarted, unavailableBackgroundPreparation.contains(primitive) else {
            throw DriverError(
                """
                foreground preparation requires original-scope background unavailability evidence for this \
                primitive before any input
                """, code: .invalidRequest)
        }
    }

    /// Authority is evidence from this operation, not a refreshed opaque
    /// primitive id. This owner verifies facts but never selects a retry.
    func recordClickBackgroundUnavailable(family: String, target: [String: Any], within: [String], step: Int64) throws {
        try check(write: true)
        guard clickContract != nil, !inputStarted, !clickPreparationRequiresObservation else {
            throw DriverError(
                "click preparation cannot authorize another input", code: .executionUnknown, effect: .possible)
        }
        clickAuthorities.append(.init(target: target, within: within, family: family, step: step))
    }

    func requireClickForeground(
        target: [String: Any], within: [String], backgroundFamily: String?, backgroundUnavailable: Bool,
        hostAllowed: Bool, exclusive: Bool
    ) throws {
        try check(write: true)
        guard clickContract != nil, !inputStarted, !clickPreparationRequiresObservation else {
            throw DriverError(
                "click already produced input or incompatible preparation", code: .executionUnknown, effect: .possible)
        }
        guard hostAllowed else {
            throw DriverError("foreground assistance is not enabled by this host", code: .modeUnsupported)
        }
        if exclusive { return }
        let recorded =
            backgroundFamily.map { family in
                clickAuthorities.contains {
                    $0.family == family && $0.within == within
                        && NSDictionary(dictionary: $0.target).isEqual(to: target)
                }
            } ?? false
        guard backgroundUnavailable || recorded else {
            throw DriverError(
                "foreground click requires original-scope background unavailability evidence", code: .invalidRequest)
        }
    }

    func blockClickContinuation() { clickPreparationRequiresObservation = true }

    /// A window envelope permits local observation/selection of a node. It
    /// cannot expand into another surface, image, session or working document.
    func permits(_ value: [String: Any]) -> Bool {
        if NSDictionary(dictionary: target).isEqual(to: value) { return true }
        guard target["node"] is NSNull, target["capture"] is NSNull,
            ["session", "instance", "surface"].allSatisfy({ (target[$0] as? String)?.isEmpty == false }),
            (value["node"] as? String)?.isEmpty == false
        else { return false }
        var envelope = value
        envelope["node"] = NSNull()
        return NSDictionary(dictionary: target).isEqual(to: envelope)
    }

    /// Raw-node observation may explore already registered relations in this
    /// fixed surface. This grants no permission to redirect input or capture.
    func permitsObservation(_ value: [String: Any]) -> Bool {
        if permits(value) { return true }
        guard target["capture"] is NSNull, value["capture"] is NSNull, (target["node"] as? String)?.isEmpty == false,
            (value["node"] as? String)?.isEmpty == false,
            ["session", "instance", "surface"].allSatisfy({ (target[$0] as? String)?.isEmpty == false })
        else { return false }
        var observed = value
        observed["node"] = target["node"]
        return NSDictionary(dictionary: target).isEqual(to: observed)
    }

    /// Keep selected native helpers alive through preparation and delivery.
    /// A new editor can be selected only before requested input begins; this
    /// does not alter the requested owner fixed by bindInputTarget.
    func retainInputReference(_ ref: String, retain: () -> (() -> Void)?) throws {
        try check(write: true)
        guard !inputStarted else {
            throw DriverError("input already dispatched", code: .executionUnknown, effect: .possible)
        }
        if inputReferences[ref] != nil { return }
        guard inputReferences.count < 16 else {
            throw DriverError("input reference budget exhausted", code: .inputUnavailable)
        }
        guard let release = retain() else { throw DriverError("selected editor expired", code: .staleTarget) }
        inputReferences[ref] = release
    }

    /// Selection happens once, before the first preparation/input unit. A
    /// temporary failure cannot change the requested control to a sibling.
    func bindInputTarget(_ value: [String: Any], within: [String] = [], retain: () -> (() -> Void)?) throws {
        try check(write: true)
        guard permits(value) else {
            throw DriverError("input target is outside the operation scope", code: .staleTarget)
        }
        if let inputTarget {
            guard NSDictionary(dictionary: inputTarget).isEqual(to: value), inputWithin == within else {
                throw DriverError("the operation already selected a different input target", code: .staleTarget)
            }
            return
        }
        guard let release = retain() else { throw DriverError("selected input target expired", code: .staleTarget) }
        inputTarget = value
        inputWithin = within
        releaseInputTarget = release
    }

    /// Async collection also retains the operation until the native await has
    /// stopped. Expiry cannot release its references underneath a live capture.
    func observe<T>(_ body: () async throws -> T) async throws -> T {
        try check()
        guard !busy else { throw DriverError("another native unit is running", code: .invalidRequest) }
        busy = true
        defer {
            busy = false
            if state == .stopping { close() }
        }
        let value = try await body()
        try check()
        return value
    }

    /// The result contains the unit's effect journal even on native failure.
    /// Protocol validation happens before admission; unit failures are values.
    func execute(step: Int64, payload: Data, body: () async -> [String: Any]) async throws -> [String: Any] {
        // A duplicate completed step can be retrieved after finish/expiry. It
        // must never re-enter a native API, including after result eviction.
        switch steps.begin(id: step, request: payload) {
        case .replay(let data):
            guard let result = try JSONSerialization.jsonObject(with: data) as? [String: Any] else {
                throw DriverError("invalid retained native result", code: .executionUnknown)
            }
            if let rejection = result["rejection"] as? [String: String] {
                throw DriverError(
                    rejection["detail"] ?? "native step was rejected",
                    code: FailureCode(rawValue: rejection["code"] ?? "") ?? .executionFailed,
                    effect: FailureEffect(rawValue: rejection["effect"] ?? "none"))
            }
            return result
        case .inProgress:
            throw DriverError("native step is still running; no duplicate input sent", code: .executionUnknown)
        case .expired: throw DriverError("native step result expired; no duplicate input sent", code: .executionUnknown)
        case .conflict: throw DriverError("native step id reused with different content", code: .invalidRequest)
        case .start: break
        }
        do {
            try check(write: true)
            guard !busy else { throw DriverError("another native unit is running", code: .invalidRequest) }
            guard !inputStarted else {
                throw DriverError(
                    "this operation already dispatched input; no further unit may replay it", code: .executionUnknown,
                    effect: .possible)
            }
            guard !clickPreparationRequiresObservation else {
                throw DriverError(
                    "preparation changed or may have changed the original click context; observe before continuing",
                    code: .executionUnknown, effect: .possible)
            }
        } catch {
            // Record rejected steps too. A later RPC must not revive this ID.
            let result: [String: Any] = [
                "rejection": [
                    "detail": String(describing: error),
                    "code": ((error as? ClassifiedFailure)?.code ?? .executionFailed).rawValue,
                    "effect": ((error as? DriverError)?.effect ?? .none).rawValue
                ]
            ]
            let data = try JSONSerialization.data(withJSONObject: result, options: [.sortedKeys])
            steps.finish(id: step, response: data)
            throw error
        }
        busy = true
        defer {
            busy = false
            if state == .stopping { close() }
        }
        let result = await body()
        // Defensive sealing also covers a unit that reported dispatch but
        // omitted its early barrier. Invalid results quarantine the executor.
        guard let dispatch = result["dispatch"] as? String,
            ["notDispatched", "dispatched", "partial", "unknown"].contains(dispatch),
            let cleaned = result["cleanup"] as? String, ["complete", "failed", "unknown"].contains(cleaned),
            !inputStarted || dispatch != "notDispatched"
        else {
            cleanup = "unknown"
            stop("invalid_native_result")
            throw DriverError(
                "native unit returned inconsistent dispatch/cleanup evidence", code: .executionUnknown,
                effect: .possible)
        }
        if dispatch != "notDispatched" { inputStarted = true }
        if let value = result["cleanup"] as? String, value != "complete" {
            cleanup = value
            stop("cleanup_unconfirmed")
        }
        do {
            let data = try JSONSerialization.data(
                withJSONObject: result, options: [.sortedKeys, .withoutEscapingSlashes])
            steps.finish(id: step, response: data)
            return result
        } catch {
            cleanup = "unknown"
            stop("invalid_native_result")
            throw DriverError(
                "native result could not be retained; do not replay input", code: .executionUnknown, effect: .possible)
        }
    }

    func stop(_ reason: String) {
        guard state != .closed else { return }
        stopReason = stopReason ?? reason
        state = .stopping
        timer?.cancel()
        timer = nil
        if busy { cancelActive() } else { close() }
    }

    func finish() -> String {
        stop("finished")
        return busy ? "unknown" : cleanup
    }

    private func close() {
        guard !busy, state != .closed else { return }
        state = .closed
        timer?.cancel()
        timer = nil
        let notify = onClose
        onClose = nil
        notify?(stopReason == "finished" && cleanup == "complete")
        // Unknown paired cleanup retires the connection. Keep the physical
        // desktop lease until its executor exits rather than admitting a peer.
        if cleanup == "complete" {
            let references = inputReferences
            inputReferences.removeAll()
            references.values.forEach { $0() }
            let input = releaseInputTarget
            releaseInputTarget = nil
            input?()
            let callback = release
            release = nil
            callback?()
        }
    }
}
