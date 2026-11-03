// Copyright (c) Huawei Technologies Co., Ltd. 2026. All rights reserved.

import Foundation

/// Live native identities and immutable captured text have different lifetimes.
/// A new observation can update a confirmed target without retargeting an old
/// handle. Missing nodes in a partial observation do not prove destruction.
final class ObservedElements<Key: Hashable, Evidence: Equatable, Value> {
    struct TextSnapshot { let value: String? }
    private struct Target {
        let key: Key
        var evidence: Evidence
        var value: Value
    }
    private struct Snapshot {
        let target: String
        let text: String?
        let bytes: Int
    }
    private let namespace: String
    private let snapshotLimit: Int
    private let textLimit: Int
    private let reconcile: (Evidence, Value, Evidence, Value) -> (Evidence, Value)?
    private var serial: UInt64 = 0
    private var current = [Key: String]()
    private var targets = [String: Target]()
    private var snapshots = [String: Snapshot]()
    private var latestSnapshot = [String: String]()
    private var snapshotOrder = [String]()
    private var textBytes = 0
    private var pinnedBindings = [String: Int]()

    init(
        namespace: String, snapshotLimit: Int = 16_384, textLimit: Int = 16 * 1_024 * 1_024,
        reconcile: ((Evidence, Value, Evidence, Value) -> (Evidence, Value)?)? = nil
    ) {
        precondition(snapshotLimit > 0 && textLimit >= 0)
        self.namespace = namespace
        self.snapshotLimit = snapshotLimit
        self.textLimit = textLimit
        self.reconcile = reconcile ?? { old, _, new, value in old == new ? (new, value) : nil }
    }

    private func next(_ kind: String) -> String {
        serial += 1
        return "\(namespace):\(kind)\(serial)"
    }

    /// Returns a stable lifetime, including across ordinary reads and text edits.
    /// Changed identity evidence retires the old lifetime permanently.
    func observe(_ key: Key, evidence: Evidence, value: Value) -> String {
        if let id = current[key], var known = targets[id] {
            if let (merged, value) = reconcile(known.evidence, known.value, evidence, value) {
                known.evidence = merged
                known.value = value
                targets[id] = known
                return id
            }
            retire(id)
        }
        let id = next("t")
        current[key] = id
        targets[id] = Target(key: key, evidence: evidence, value: value)
        return id
    }

    func observed(_ key: Key) -> (id: String, value: Value)? {
        guard let id = current[key], let target = targets[id] else { return nil }
        return (id, target.value)
    }
    func retain(_ reference: String) -> (() -> Void)? {
        let id = snapshots[reference]?.target ?? reference
        guard targets[id] != nil else { return nil }
        if snapshots[reference] != nil { pinnedBindings[reference, default: 0] += 1 }
        var released = false
        return { [weak self] in
            guard !released, let self else { return }
            released = true
            if let count = self.pinnedBindings[reference], count > 1 {
                self.pinnedBindings[reference] = count - 1
            } else {
                self.pinnedBindings.removeValue(forKey: reference)
            }
            self.evictSnapshots()
        }
    }

    /// Only immutable captured content is a cache. A live action reference is
    /// an identity, retained until explicit object/scope retirement or session end.
    private func evictSnapshots() {
        while textBytes > textLimit || snapshots.count > snapshotLimit {
            guard let token = snapshotOrder.first(where: { pinnedBindings[$0] == nil }),
                let expired = snapshots.removeValue(forKey: token)
            else { break }
            textBytes -= expired.bytes
            snapshotOrder.removeAll { $0 == token }
            if latestSnapshot[expired.target] == token { latestSnapshot.removeValue(forKey: expired.target) }
        }
    }

    /// Each delivered reference captures its own immutable text, while actions
    /// resolve to the same confirmed native target's most recent metadata.
    func bind(_ id: String, text: String?) -> String? {
        guard targets[id] != nil else { return nil }
        if let token = latestSnapshot[id], let previous = snapshots[token], previous.text == text { return token }
        let bytes = text?.utf8.count ?? 0
        guard bytes <= textLimit else { return nil }
        let token = next("e")
        snapshots[token] = Snapshot(target: id, text: text, bytes: bytes)
        latestSnapshot[id] = token
        snapshotOrder.append(token)
        textBytes += bytes
        evictSnapshots()
        return token
    }

    // Live-node references and captured-text tokens resolve to the same
    // retained lifetime; immutable text remains available after it expires.
    func target(_ token: String) -> Value? { targets[snapshots[token]?.target ?? token]?.value }
    /// Outer nil means expired; an existing snapshot with nil text means no string Value.
    func text(_ token: String) -> TextSnapshot? { snapshots[token].map { TextSnapshot(value: $0.text) } }

    func invalidate(where predicate: (Value) -> Bool) {
        retire(Set(targets.compactMap { predicate($0.value.value) ? $0.key : nil }))
    }

    private func retire(_ id: String) { retire([id]) }

    private func retire(_ ids: Set<String>) {
        guard !ids.isEmpty else { return }
        for id in ids {
            guard let target = targets.removeValue(forKey: id) else { continue }
            latestSnapshot.removeValue(forKey: id)
            if current[target.key] == id { current.removeValue(forKey: target.key) }
        }
        // Captured text remains readable; action aliases cannot revive this lifetime.
    }
}
