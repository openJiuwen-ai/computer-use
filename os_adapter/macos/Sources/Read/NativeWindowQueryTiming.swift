// Copyright (c) Huawei Technologies Co., Ltd. 2026. All rights reserved.

import ApplicationServices
import Foundation

/// Passive, query-local attribution for the existing window collector. No AX
/// getter lives here: wrappers call their original closure once. Nothing in
/// this record is returned to window selection or retained across queries.
@MainActor final class NativeWindowQueryTiming {
    static let enabled = ProcessInfo.processInfo.environment["COMPUTER_USE_DIAGNOSE_WINDOW_QUERY"] == "1"

    private struct Key: Hashable {
        let phase: String
        let property: String?
        let root: Int?
    }
    private struct Sample {
        var count = 0
        var duration = 0.0
        var maximum = 0.0
        var threw = 0
        var statuses = [Int32: Int]()
    }
    private struct Root {
        let element: AXUIElement
        var fields: [String: Any]
    }
    let id = UUID().uuidString
    private let clock: () -> TimeInterval
    private let emit: ([String: Any]) -> Void
    private let began: TimeInterval
    private let metadata: [String: Any]
    private var roots = [Root]()
    private var samples = [Key: Sample]()
    private var counters = [String: Int]()
    private var closed = false
    var surfaceRoot: AXUIElement?

    static func start(
        enabled: Bool = enabled, metadata: () -> [String: Any],
        clock: @escaping () -> TimeInterval = { ProcessInfo.processInfo.systemUptime },
        emit: @escaping ([String: Any]) -> Void = { diagnose($0) }
    ) -> NativeWindowQueryTiming? {
        guard enabled else { return nil }
        return NativeWindowQueryTiming(metadata: metadata(), clock: clock, emit: emit)
    }

    private init(metadata: [String: Any], clock: @escaping () -> TimeInterval, emit: @escaping ([String: Any]) -> Void) {
        self.metadata = metadata
        self.clock = clock
        self.emit = emit
        began = clock()
    }

    /// Inclusive spans are intentionally not additive. Getter spans are leaf
    /// calls; helpers include their getter/child spans and the collection
    /// includes every span. The summary labels this distinction explicitly.
    static func measure<Value>(
        _ timing: NativeWindowQueryTiming?, _ phase: String, property: String? = nil,
        root: AXUIElement? = nil, body: () throws -> Value
    ) rethrows -> Value {
        guard let timing else { return try body() }
        let began = timing.clock()
        var threw = true
        defer {
            let duration = max(0, timing.clock() - began) * 1000
            timing.sample(phase, property: property, root: root, duration: duration, threw: threw)
        }
        let value = try body()
        threw = false
        return value
    }

    private func index(_ element: AXUIElement) -> Int? {
        if let index = roots.firstIndex(where: { CFEqual($0.element, element) }) { return index }
        // Diagnostic output is bounded independently of a provider's AXWindows
        // size. A cap only drops attribution; never changes the original query.
        guard roots.count < 64 else { count("rootAttributionOverflow"); return nil }
        roots.append(Root(element: element, fields: ["index": roots.count]))
        return roots.count - 1
    }

    func root(
        _ element: AXUIElement, role: String? = nil, ref: String? = nil, nativeID: CGWindowID? = nil,
        selected: Bool? = nil
    ) {
        guard let index = index(element) else { return }
        if let role { roots[index].fields["role"] = role }
        if let ref { roots[index].fields["ref"] = ref }
        if let nativeID { roots[index].fields["nativeID"] = nativeID }
        if let selected { roots[index].fields["selected"] = selected }
    }

    func count(_ name: String, by amount: Int = 1) { counters[name, default: 0] += amount }

    private func sample(_ phase: String, property: String?, root: AXUIElement?, duration: Double, threw: Bool) {
        let key = Key(phase: phase, property: property, root: root.flatMap(index))
        guard samples[key] != nil || samples.count < 512 else { count("sampleAttributionOverflow"); return }
        var value = samples[key] ?? Sample()
        value.count += 1
        value.duration += duration
        value.maximum = max(value.maximum, duration)
        if threw { value.threw += 1 }
        samples[key] = value
    }

    func status(_ code: AXError, phase: String, property: String? = nil, root: AXUIElement? = nil) {
        let key = Key(phase: phase, property: property, root: root.flatMap(index))
        // Status is attached only to a sample that has already finished. No
        // payload is inspected and no getter is repeated to recover a status.
        guard var value = samples[key] else { return }
        value.statuses[code.rawValue, default: 0] += 1
        samples[key] = value
    }

    func surface(
        _ element: AXUIElement, recursive: Bool, visited: Int, queued: Int, found: Int, pending: Int,
        roles: [String: Int], stops: [String: Int] = [:], outcome: String = "returned"
    ) {
        guard let index = index(element) else { return }
        roots[index].fields["discovery"] = [
            "recursive": recursive, "visited": visited, "queued": queued,
            "found": found, "pending": pending, "returnedRoles": roles, "stops": stops, "outcome": outcome
        ] as [String: Any]
    }

    /// Only this collector's existing relation reader is wrapped. Its count,
    /// range, consistency count and fallback single calls remain its own choice.
    func relationAPI(_ original: AXRelationReader.API = AXRelationReader.API()) -> AXRelationReader.API {
        AXRelationReader.API(
            count: { element, property in
                let result = Self.measure(self, "getter.surface.count", property: property, root: self.surfaceRoot) {
                    original.count(element, property)
                }
                self.status(result.0, phase: "getter.surface.count", property: property, root: self.surfaceRoot)
                return result
            },
            range: { element, property, length in
                let result = Self.measure(self, "getter.surface.range", property: property, root: self.surfaceRoot) {
                    original.range(element, property, length)
                }
                self.status(result.0, phase: "getter.surface.range", property: property, root: self.surfaceRoot)
                self.count("surfaceRangeRequested", by: length)
                return result
            },
            single: { element, property in
                let result = Self.measure(self, "getter.surface.single", property: property, root: self.surfaceRoot) {
                    original.single(element, property)
                }
                self.status(result.0, phase: "getter.surface.single", property: property, root: self.surfaceRoot)
                return result
            })
    }

    func finish(outcome: String) {
        guard !closed else { return }
        closed = true
        var fields = metadata
        fields["kind"] = "window_query_timing"
        fields["query"] = id
        fields["startedUptime"] = began
        fields["durationMs"] = max(0, clock() - began) * 1000
        fields["outcome"] = outcome
        fields["timingSemantics"] = "helper/phase spans inclusive; getter spans leaf; do not add parent and child durations"
        fields["diagnosticOverhead"] = "aggregation is excluded from leaf spans and included in enclosing spans; total ends before summary serialization"
        fields["roots"] = roots.map(\.fields)
        fields["counters"] = counters
        fields["samples"] = samples.map { key, value -> [String: Any] in
            var row: [String: Any] = [
                "phase": key.phase, "count": value.count, "durationMs": value.duration,
                "maxMs": value.maximum, "threw": value.threw,
                "statuses": Dictionary(uniqueKeysWithValues: value.statuses.map { (String($0.key), $0.value) })
            ]
            if let property = key.property { row["property"] = property }
            if let root = key.root { row["root"] = root }
            return row
        }.sorted {
            let left = ($0["phase"] as! String, $0["root"] as? Int ?? -1, $0["property"] as? String ?? "")
            let right = ($1["phase"] as! String, $1["root"] as? Int ?? -1, $1["property"] as? String ?? "")
            return left < right
        }
        emit(fields)
    }
}
