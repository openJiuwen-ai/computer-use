// Copyright (c) Huawei Technologies Co., Ltd. 2026. All rights reserved.

import AppKit
import ApplicationServices

@main enum NativeScalarTextPointTests {
    typealias Unit = NativeScalarTextPoint

    @MainActor static func main() throws {
        boundsRequireScalarTextEvidence()
        geometryRetainsTheOriginalTextPoint()
        preparationContentIsSeparateFromIdentity()
        try routeLocalGeometryFailureDoesNotBlockAlternatives()
        print("PASS scalar text point qualification, clipping, continuity and preparation content evidence")
    }

    @MainActor static func routeLocalGeometryFailureDoesNotBlockAlternatives() throws {
        let state = NativeDesktopState()
        let binding: [String: Any] = [
            "session": "s", "instance": "app", "rootWindow": "0", "surface": "window", "node": "field",
            "capture": NSNull()
        ]
        let intent = try NativeIntent(["kind": "replace", "parameters": ["text": "replacement"]], mode: "write")
        let op = NativeOperation(
            timeoutMs: 5000, writable: true, intent: intent, target: binding, release: {}, cancelActive: {})
        defer { _ = op.finish() }
        let query = NativeInputQueryScope(
            binding: (binding, [], "field"), desktop: false,
            selection: NativeSelectionBasis(access: state.applications), scope: "window")
        let declarations = NativePrimitiveDeclarations(
            previous: [], op: op, query: query, within: [], actuator: nil, receiver: nil, basis: [])
        let setter = NativeInputUnit.textValue(.init(role: kAXTextFieldRole, subrole: nil, independent: true))
        declarations.declare(setter, supported: true, enabled: true, readiness: { _ in NativeReadiness() })
        let missing = try Unit.optionalSample {
            throw DriverError("no pointer geometry", code: .inputUnavailable)
        }
        precondition(missing == nil, "optional pointer evidence must not become a requirement of other routes")
        let retained = try declarations.registeredContracts()
        precondition(retained.count == 1 && retained[0].unit == setter && retained[0].node == "field"
                     && declarations.primitives[0]["supported"] as? String == "yes"
                     && declarations.primitives[0]["readiness"] as? String == "yes",
                     "missing pointer geometry must preserve the qualified original-object setter")
        do {
            _ = try Unit.optionalSample { throw DriverError("original target gone", code: .staleTarget) }
            preconditionFailure("target identity failure must propagate")
        } catch let error as DriverError { precondition(error.code == .staleTarget) }
        do {
            _ = try Unit.optionalSample { throw CancellationError() }
            preconditionFailure("optional sampling must not swallow cancellation")
        } catch is CancellationError {}
    }

    @MainActor static func boundsRequireScalarTextEvidence() {
        var queries = 0
        let bounds = CGRect(x: 12, y: 15, width: 8, height: 16)
        func result(
            role: String? = kAXIncrementorRole, selection: NativeReplacementUnit.Selection? = .shortcut,
            count: AXRead<CFTypeRef> = .value(NSNumber(value: 9)), writable: Bool = true,
            numeric: NativeTextSelection = .init(count: 9, location: 0, length: 4),
            response: AXRead<CFTypeRef>? = nil
        ) -> CGRect? {
            Unit.firstCharacterBounds(
                selection: selection, before: .numeric(numeric), role: role, characterCount: count,
                rangeWritable: writable, query: { range in
                    queries += 1
                    precondition(range.location == 0 && range.length == 1, "query must address the original first character")
                    if let response { return response }
                    var value = bounds
                    return .value(AXValueCreate(.cgRect, &value)!)
                })
        }
        precondition(result() == bounds)
        precondition(queries == 1)
        for role: String? in [nil, kAXTextFieldRole, kAXTextAreaRole, kAXSliderRole] {
            precondition(result(role: role) == nil)
        }
        precondition(result(selection: nil) == nil)
        precondition(result(selection: .range) == nil)
        precondition(result(writable: false) == nil)
        precondition(result(count: .unsupported) == nil)
        precondition(result(count: .value(NSNumber(value: 8))) == nil)
        precondition(result(count: .value(NSNumber(value: 0)), numeric: .init(count: 0, location: 0, length: 0)) == nil)
        precondition(queries == 1, "ordinary/value-only/empty controls must never cause a speculative text bounds query")
        precondition(result(response: .unsupported) == nil)
        precondition(result(response: .unavailable(.noValue)) == nil)
        precondition(result(response: .value("not a rectangle" as CFString)) == nil)
        var point = CGPoint(x: 12, y: 15)
        precondition(result(response: .value(AXValueCreate(.cgPoint, &point)!)) == nil)
        for rectangle in [CGRect.zero, CGRect.null, CGRect.infinite, CGRect(x: 1, y: 2, width: -3, height: 4)] {
            var rectangle = rectangle
            precondition(result(response: .value(AXValueCreate(.cgRect, &rectangle)!)) == nil)
        }
    }

    @MainActor static func geometryRetainsTheOriginalTextPoint() {
        func mapping(id: UInt32 = 71, pid: Int32 = 42, x: CGFloat = 0) -> WindowGeometry.WindowMapping {
            .init(id: id, pid: pid, frame: CGRect(x: x, y: 0, width: 200, height: 100), title: nil, width: 200, height: 100)
        }
        let frame = CGRect(x: 10, y: 10, width: 180, height: 30)
        let glyph = CGRect(x: 14, y: 15, width: 8, height: 16)
        let selected = Unit.select(mapping: mapping(), textBounds: glyph, elementFrame: frame, clipped: frame)!
        precondition(selected.point == CGPoint(x: 18, y: 23))
        precondition(selected.point != CGPoint(x: frame.midX, y: frame.midY), "stepper center is not text geometry")
        precondition(selected.boundsRange.location == 0 && selected.boundsRange.length == 1)
        let partiallyVisible = CGRect(x: 18, y: 10, width: 100, height: 30)
        let partial = Unit.select(mapping: mapping(), textBounds: glyph, elementFrame: frame, clipped: partiallyVisible)!
        precondition(partial.point == CGPoint(x: 20, y: 23))
        precondition(Unit.select(
            mapping: mapping(), textBounds: glyph, elementFrame: frame,
            clipped: CGRect(x: 25, y: 10, width: 100, height: 30)) == nil)
        precondition(Unit.select(mapping: mapping(), textBounds: glyph, elementFrame: .null, clipped: frame) == nil)
        precondition(Unit.select(mapping: mapping(id: 0), textBounds: glyph, elementFrame: frame, clipped: frame) == nil)
        func retained(_ newMapping: WindowGeometry.WindowMapping, _ text: CGRect, _ clip: CGRect? = nil) -> Bool {
            Unit.retains(selected, mapping: newMapping, textBounds: text, elementFrame: frame, clipped: clip ?? frame)
        }
        precondition(retained(mapping(), glyph))
        precondition(retained(mapping(), CGRect(x: 12, y: 14, width: 12, height: 18)), "legal reflow retains a still-valid point")
        precondition(!retained(mapping(), CGRect(x: 30, y: 15, width: 8, height: 16)), "never follow a new glyph center")
        precondition(!retained(mapping(id: 72), glyph))
        precondition(!retained(mapping(pid: 43), glyph))
        precondition(!retained(mapping(x: 1), glyph))
        precondition(!retained(mapping(), glyph, CGRect(x: 19, y: 10, width: 100, height: 30)))
    }

    @MainActor static func preparationContentIsSeparateFromIdentity() {
        var textReads = 0
        func content(value: AXRead<CFTypeRef>, text: String = "2026/11/15") -> Unit.ContentEvidence? {
            Unit.content(
                read: { name in name == kAXValueAttribute ? value : .value(NSNumber(value: 10)) },
                text: { range in
                    textReads += 1
                    precondition(range.location == 0 && range.length == 10)
                    return .value(text as CFString)
                })
        }
        let number = content(value: .value(NSNumber(value: 15)))!
        precondition(content(value: .value(NSNumber(value: 15.0)))!.unchanged(from: number) == true)
        precondition(content(value: .value(NSNumber(value: 16)))!.unchanged(from: number) == false)
        let string = content(value: .value("2026/11/15" as CFString))!
        precondition(content(value: .value("2026/11/13" as CFString))!.unchanged(from: string) == false)
        precondition(textReads == 0, "a usable native value takes precedence over parameterized text")
        let fallback = content(value: .value("" as CFString))!
        precondition(content(value: .unsupported)!.unchanged(from: fallback) == true)
        precondition(content(value: .unsupported, text: "2026/11/16")!.unchanged(from: fallback) == false)
        precondition(fallback.unchanged(from: string) == true)
        precondition(string.unchanged(from: fallback) == true)
        let otherString = content(value: .value("2026/11/16" as CFString))!
        precondition(fallback.unchanged(from: otherString) == false)
        precondition(otherString.unchanged(from: fallback) == false)
        precondition(fallback.unchanged(from: number) == nil, "string evidence cannot reinterpret a numeric value")
        precondition(number.unchanged(from: fallback) == nil)
        let missing = Unit.content(read: { _ in .unsupported }, text: { _ in
            preconditionFailure("no full text request without a native extent")
        })
        precondition(missing == nil)
    }
}
