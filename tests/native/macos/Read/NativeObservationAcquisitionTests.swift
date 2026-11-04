// Copyright (c) Huawei Technologies Co., Ltd. 2026. All rights reserved.

import ApplicationServices
import Foundation

@main enum NativeObservationAcquisitionTests {
    static func error(_ code: AXError) -> AXValue {
        var code = code
        return AXValueCreate(.axError, &code)!
    }
    static func range(_ location: Int) -> AXValue {
        var range = CFRange(location: location, length: 1)
        return AXValueCreate(.cfRange, &range)!
    }
    static func main() {
        let fields = ["AXTitle", "AXValue"] + ElementIdentity.keys.reversed()
        let facts: [String: Any] = [
            "AXRole": "AXCell", "AXSubrole": "AXTableCell", "AXIdentifier": "original", "AXDocument": "document",
            "AXURL": error(.noValue), "AXRowIndexRange": range(2), "AXColumnIndexRange": range(3),
            "AXTitle": "mutable label", "AXValue": "mutable value"
        ]
        let raw = fields.map { facts[$0]! }
        let projected = AXIdentity.fromObservationBatch(
            status: .success, raw: raw as CFArray, fields: fields, catalogueStatus: .success, catalogue: fields as CFArray)!
        let identity = ElementIdentity(facts: projected.value!)
        precondition(identity.attributes["AXRole"] == "AXCell" && identity.attributes["AXIdentifier"] == "original")
        precondition(identity.attributes["AXRowIndexRange"] == "2:1")
        precondition(identity.attributes["AXTitle"] == nil && identity.attributes["AXValue"] == nil)
        precondition(identity.absent.contains("AXURL"), "native AXError slots keep absence evidence")

        var changed = facts
        changed["AXRowIndexRange"] = range(8)
        let next = AXIdentity.fromObservationBatch(
            status: .success, raw: fields.map { changed[$0]! } as CFArray, fields: fields,
            catalogueStatus: .success, catalogue: fields as CFArray)!
        precondition(identity.check(next.value!) == .changed, "a reused virtual cell's advertised position still conflicts")

        let names = fields.filter { !ElementIdentity.positionKeys.contains($0) }
        let undeclared = AXIdentity.fromObservationBatch(
            status: .success, raw: raw as CFArray, fields: fields,
            catalogueStatus: .success, catalogue: names as CFArray)!.value!
        precondition(undeclared.values["AXRowIndexRange"] == nil && undeclared.absent.contains("AXRowIndexRange"),
                     "unadvertised AppKit default positions cannot become identity")

        precondition(AXIdentity.fromObservationBatch(
            status: .success, raw: Array(raw.dropLast()) as CFArray, fields: fields,
            catalogueStatus: .success, catalogue: fields as CFArray) == nil)
        precondition(AXIdentity.fromObservationBatch(
            status: .cannotComplete, raw: nil, fields: fields,
            catalogueStatus: .success, catalogue: fields as CFArray) == nil,
                     "failed/malformed shared batches retain the independent identity fallback")
        var invalid = facts
        invalid["AXRole"] = error(.invalidUIElement)
        if case .invalid = AXIdentity.fromObservationBatch(
            status: .success, raw: fields.map { invalid[$0]! } as CFArray, fields: fields,
            catalogueStatus: .success, catalogue: fields as CFArray)!
        {} else { preconditionFailure("a shared batch cannot soften a confirmed invalid original object") }
        print("PASS one observation batch preserves strict identity, native positions, mutable values and fallbacks")
    }
}
