// Copyright (c) Huawei Technologies Co., Ltd. 2026. All rights reserved.

import ApplicationServices
import Foundation

@main struct NativeWindowQueryTimingTests {
    final class Original: Error {}

    @MainActor static func main() throws {
        if CommandLine.arguments.contains("--flag-state") {
            var metadataReads = 0, clockReads = 0, emissions = 0
            let timing = NativeWindowQueryTiming.start(metadata: {
                metadataReads += 1
                return [:]
            }, clock: { clockReads += 1; return 0 }, emit: { _ in emissions += 1 })
            timing?.finish(outcome: "returned")
            print(String(data: try JSONSerialization.data(withJSONObject: [
                "enabled": timing != nil, "metadataReads": metadataReads,
                "clockReads": clockReads, "emissions": emissions
            ]), encoding: .utf8)!)
            return
        }
        try wrapperParity()
        try relationParity()
        boundedAttribution()
        print("PASS query-local window timing: original calls/order/errors/budgets; no off clock/metadata/output or payload capture")
    }

    @MainActor static func wrapperParity() throws {
        let root = AXUIElementCreateApplication(100)
        for enabled in [false, true] {
            var now = 10.0, clockReads = 0, metadataReads = 0, calls = [String](), output = [[String: Any]]()
            let timing = NativeWindowQueryTiming.start(enabled: enabled, metadata: {
                metadataReads += 1
                return ["purpose": "inputValidation", "projection": "catalogue"]
            }, clock: { clockReads += 1; return now }, emit: { output.append($0) })
            let returned = NSObject()
            timing?.root(root, role: "AXWindow", ref: "23", nativeID: 333, selected: true)
            let value = NativeWindowQueryTiming.measure(timing, "helper.inputWindow", root: root) {
                calls.append("helper")
                return NativeWindowQueryTiming.measure(timing, "getter.test", property: "AXTitle", root: root) {
                    calls.append("getter")
                    now += 0.4
                    // The measured return value is never offered as metadata.
                    return returned
                }
            }
            precondition(value === returned && calls == ["helper", "getter"])
            let original = Original()
            do {
                try NativeWindowQueryTiming.measure(timing, "phase.cancel") {
                    calls.append("cancel")
                    throw original
                }
                preconditionFailure("original error swallowed")
            } catch let caught as Original { precondition(caught === original) }
            precondition(output.isEmpty, "getter instrumentation must not emit per-call lines")
            timing?.surface(root, recursive: true, visited: 12, queued: 15, found: 1, pending: 3, roles: ["AXList": 5])
            timing?.finish(outcome: "threw")
            timing?.finish(outcome: "returned")
            precondition(metadataReads == (enabled ? 1 : 0))
            precondition(clockReads == (enabled ? 8 : 0))
            precondition(output.count == (enabled ? 1 : 0))
            if enabled {
                let record = output[0]
                precondition(record["outcome"] as? String == "threw")
                let samples = record["samples"] as! [[String: Any]]
                for phase in ["helper.inputWindow", "getter.test"] {
                    let sample = samples.first { $0["phase"] as? String == phase }!
                    precondition(sample["count"] as? Int == 1)
                    precondition(abs((sample["durationMs"] as! Double) - 400) < 0.0001)
                }
                precondition(abs((record["durationMs"] as! Double) - 400) < 0.0001,
                             "inclusive helper and child spans must not be added")
                precondition(samples.first { $0["phase"] as? String == "phase.cancel" }?["threw"] as? Int == 1)
                let roots = record["roots"] as! [[String: Any]]
                precondition(roots.count == 1 && roots[0]["nativeID"] as? UInt32 == 333)
                precondition(roots[0]["selected"] as? Bool == true)
                precondition((roots[0]["discovery"] as! [String: Any])["visited"] as? Int == 12)
                let json = String(data: try JSONSerialization.data(withJSONObject: record), encoding: .utf8)!
                precondition(!json.contains("NSObject") && !json.contains("valueBody"))
            }
        }
    }

    @MainActor static func relationParity() throws {
        let objects = (0..<3).map { AXUIElementCreateApplication(pid_t(200 + $0)) }
        // Exercise the real relation reader's count/range/consistency and
        // fallback choices. Its API is the only injection point; no AX reads.
        for scenario in ["ordinary", "fallback", "changing", "error", "cancel"] {
            var observations = [[String]]()
            for enabled in [false, true] {
                var calls = [String](), countCalls = 0, records = [[String: Any]](), now = 0.0
                let timing = NativeWindowQueryTiming.start(enabled: enabled, metadata: { [:] }, clock: { now },
                                                          emit: { records.append($0) })
                timing?.surfaceRoot = objects[0]
                var api = AXRelationReader.API()
                api.count = { _, property in
                    calls.append("count:\(property)")
                    countCalls += 1
                    now += 0.01
                    if scenario == "fallback" { return (.notImplemented, 0) }
                    if scenario == "error" { return (.cannotComplete, 0) }
                    return (.success, scenario == "changing" && countCalls > 1 ? 3 : 2)
                }
                api.range = { _, property, length in
                    calls.append("range:\(property):\(length)")
                    now += 0.02
                    return (.success, Array(objects.prefix(length)) as CFArray)
                }
                api.single = { _, property in
                    calls.append("single:\(property)")
                    now += 0.03
                    return scenario == "error" ? (.cannotComplete, nil) : (.success, objects as CFArray)
                }
                let reader = AXRelationReader(api: timing?.relationAPI(api) ?? api)
                let original = Original()
                do {
                    let result = try NativeWindowQueryTiming.measure(timing, "phase.surfaceDiscovery", root: objects[0]) {
                        try reader.read(objects[0], source: "surface-discovery", property: "AXChildren", limit: 2,
                                        withinBudget: {
                                            calls.append("budget")
                                            if scenario == "cancel" { throw original }
                                            return true
                                        })
                    }
                    switch result {
                    case .page(let page):
                        precondition(!["error", "cancel"].contains(scenario))
                        calls.append("page:\(page.total):\(page.prefix.count):\(page.issue != nil)")
                    case .failure:
                        precondition(scenario == "error")
                        calls.append("failure")
                    }
                } catch let caught as Original {
                    precondition(scenario == "cancel" && caught === original)
                    calls.append("original-cancel")
                }
                timing?.finish(outcome: "returned")
                observations.append(calls)
                precondition(records.count == (enabled ? 1 : 0))
                if enabled, scenario == "fallback" {
                    let samples = records[0]["samples"] as! [[String: Any]]
                    let count = samples.first { $0["phase"] as? String == "getter.surface.count" }!
                    precondition((count["statuses"] as! [String: Int])[String(AXError.notImplemented.rawValue)] == 1)
                    precondition(samples.filter { $0["phase"] as? String == "getter.surface.single" }.count == 1)
                }
            }
            precondition(observations[0] == observations[1], "tracing changed existing API/budget sequence: \(scenario)")
        }
    }

    @MainActor static func boundedAttribution() {
        var output = [[String: Any]](), bodies = 0
        let timing = NativeWindowQueryTiming.start(enabled: true, metadata: { [:] }, clock: { 0 }, emit: { output.append($0) })!
        for index in 0..<100 {
            let root = AXUIElementCreateApplication(pid_t(1000 + index))
            timing.root(root, role: "AXWindow")
            NativeWindowQueryTiming.measure(timing, "getter.test", root: root) { bodies += 1 }
        }
        timing.finish(outcome: "returned")
        precondition(bodies == 100 && (output[0]["roots"] as! [[String: Any]]).count == 64)
        precondition((output[0]["counters"] as! [String: Int])["rootAttributionOverflow", default: 0] > 0)
    }
}
