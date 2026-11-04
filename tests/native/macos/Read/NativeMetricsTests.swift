// Copyright (c) Huawei Technologies Co., Ltd. 2026. All rights reserved.

import Foundation

@main enum NativeMetricsTests {
    final class Original: Error {}

    @MainActor static func main() throws {
        try diagnosticParity()
        nestedLocations()
        boundedLocations()
        closedRecorder()
        print("PASS located native metrics: disabled parity, original return/error, exact nested endpoints, bounded drops")
    }

    @MainActor static func diagnosticParity() throws {
        for enabled in [false, true] {
            var now = 10.0, clockReads = 0, calls = [String]()
            let metrics = NativeMetrics(clock: { clockReads += 1; return now }, retainSpans: enabled)
            let original = Original(), returned = NSObject()
            try RequestDiagnostics.$metrics.withValue(metrics) {
                let value = NativeMetrics.measureDiagnostic(.inputQueryResolution) {
                    calls.append("scope")
                    now += 0.1
                    return returned
                }
                precondition(value === returned)
                do {
                    try NativeMetrics.measureDiagnostic(.inputRouteSampling) {
                        calls.append("facts")
                        now += 0.2
                        throw original
                    }
                    preconditionFailure("diagnostic wrapper swallowed the original error")
                } catch let caught as Original { precondition(caught === original) }
                NativeMetrics.measureDiagnostic(.inputContractRegistration) { calls.append("register") }
                NativeMetrics.measureDiagnostic(.inputContextEncoding) { calls.append("encode") }
            }
            precondition(calls == ["scope", "facts", "register", "encode"])
            precondition(clockReads == (enabled ? 9 : 1), "disabled query timing must not read a clock")
            let output = metrics.finish(method: "inspect", status: "error")!
            precondition(clockReads == (enabled ? 10 : 2))
            let phases = output["phases"] as! [String: [String: Any]]
            if enabled {
                let spans = output["spans"] as! [[String: Any]]
                precondition(spans.count == 4)
                precondition(spans.map { $0["phase"] as! String } == [
                    "inputQueryResolution", "inputRouteSampling", "inputContractRegistration", "inputContextEncoding"
                ])
                precondition(spans[0]["startedUptime"] as? Double == 10)
                precondition(abs((spans[0]["endedUptime"] as! Double) - 10.1) < 0.000001)
                precondition(abs((phases["inputRouteSampling"]!["durationMs"] as! Double) - 200) < 0.000001)
                precondition(spans.allSatisfy { $0["outcome"] == nil && $0["status"] == nil },
                             "endpoints must not invent success for a throwing read")
                precondition(output["spanClock"] as? String == "systemUptime")
                precondition(output["droppedSpans"] as? Int == 0)
            } else {
                precondition(phases.isEmpty)
                precondition(Set(output.keys) == ["kind", "version", "method", "status", "durationMs", "phases", "counters"],
                             "disabled diagnostic phases must not change the original metric output shape")
            }
        }
        var calls = 0
        let absent: NativeMetrics? = nil
        RequestDiagnostics.$metrics.withValue(absent) {
            let value = NativeMetrics.measureDiagnostic(.inputQueryResolution) { calls += 1; return 7 }
            precondition(value == 7 && calls == 1)
        }
    }

    @MainActor static func nestedLocations() {
        var now = 10.0, clockReads = 0
        let metrics = NativeMetrics(clock: { clockReads += 1; return now }, retainSpans: true)
        let outer = metrics.start(.clickTargetValidation)!
        now = 11
        let inner = metrics.start(.windowMembership)!
        now = 12
        inner.end()
        now = 13
        outer.end()
        let output = metrics.finish(method: "execute", status: "ok")!
        precondition(clockReads == 6, "retaining existing endpoints must not add clock reads")
        let spans = output["spans"] as! [[String: Any]]
        precondition(spans.count == 2)
        precondition(spans[0]["startedUptime"] as? Double == 11 && spans[0]["endedUptime"] as? Double == 12)
        precondition(spans[1]["startedUptime"] as? Double == 10 && spans[1]["endedUptime"] as? Double == 13)
        let aggregate = output["phases"] as! [String: [String: Any]]
        precondition(aggregate["clickTargetValidation"]!["durationMs"] as? Double == 3000)
        precondition(aggregate["windowMembership"]!["durationMs"] as? Double == 1000)
        // These located spans overlap: four aggregate seconds are only three
        // seconds of wall work. The consumer must union, not add, their bounds.
        precondition((spans[0]["startedUptime"] as! Double) >= (spans[1]["startedUptime"] as! Double))
        precondition((spans[0]["endedUptime"] as! Double) <= (spans[1]["endedUptime"] as! Double))
    }

    @MainActor static func boundedLocations() {
        var now = 0.0
        let metrics = NativeMetrics(clock: { now }, retainSpans: true, spanLimit: 2)
        for _ in 0..<5 {
            let interval = metrics.start(.targetValidation)!
            now += 0.25
            interval.end()
        }
        let output = metrics.finish(method: "execute", status: "ok")!
        precondition((output["spans"] as! [[String: Any]]).count == 2)
        precondition(output["spanLimit"] as? Int == 2 && output["droppedSpans"] as? Int == 3)
        let aggregate = (output["phases"] as! [String: [String: Any]])["targetValidation"]!
        precondition(aggregate["count"] as? Int == 5 && aggregate["durationMs"] as? Double == 1250,
                     "bounded location storage must not drop original duration aggregates")
        precondition(JSONSerialization.isValidJSONObject(output))
    }

    @MainActor static func closedRecorder() {
        var reads = 0
        let metrics = NativeMetrics(clock: { reads += 1; return 0 }, retainSpans: true)
        let open = metrics.start(.targetValidation)!
        precondition(metrics.finish(method: "inspect", status: "ok") != nil)
        let finishedReads = reads
        open.end()
        precondition(metrics.start(.targetValidation) == nil)
        precondition(metrics.finish(method: "inspect", status: "ok") == nil && reads == finishedReads)
    }
}
