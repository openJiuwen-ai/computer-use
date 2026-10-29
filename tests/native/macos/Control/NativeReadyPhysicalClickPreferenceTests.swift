// Copyright (c) Huawei Technologies Co., Ltd. 2026. All rights reserved.

import AppKit
import ApplicationServices

/// Native-produced offers and real declaration/encoding ports, without AX reads
/// or OS event posting. Python consumes --offers through public Element.click.
@main struct NativeReadyPhysicalClickPreferenceTests {
    @MainActor static let binding: [String: Any] = [
        "session": "s", "instance": "i", "rootWindow": "w", "surface": "surface", "node": "E",
        "capture": NSNull()
    ]

    static func expect(_ value: Bool) { precondition(value) }

    static func intent(_ kind: String = "click", _ parameters: [String: Any] = ["button": "left", "count": 1])
        throws -> NativeIntent
    { try NativeIntent(["kind": kind, "parameters": parameters], mode: "write") }

    @MainActor static func sample(
        foreground: Bool = true, kind: NativePointerReadiness.WindowKind = .keyRequiring,
        focus: NativePointerReadiness.WindowFocus = .target,
        hit: NativeInputWindowHit? = .init(windowServer: .window(42), accessibility: nil)
    ) -> NativePointerUnit.PhysicalClickSample {
        .init(
            ready: NativePointerReadiness.ready(
                kind: .click, foreground: foreground, windowKind: kind, focus: focus,
                backgroundPrepared: false, exclusive: true),
            foreground: foreground, hit: hit, windowKind: kind, focus: focus)
    }

    static func hid(_ admission: NativeClickContract.Admission = .ready, options: [String] = []) -> NativeClickContract {
        .init(routeFamily: "macos.hid.v1", delivery: .systemPointer, admission: admission, reason: nil,
              preparationOptions: options)
    }

    @MainActor static func project(
        _ value: NativePointerUnit.PhysicalClickSample, exact: Bool = true, exclusive: Bool = true,
        request: NativeIntent? = nil, original: NativeClickContract? = nil
    ) throws -> NativeClickContract {
        NativeReadyPhysicalClickPreference.offer(
            original ?? hid(), intent: try request ?? intent(), exclusive: exclusive,
            originalElementHit: exact, window: 42, sample: value)
    }

    @MainActor static func beginRequest(version: String = "v2", optedIn: Bool = false) -> [String: Any] {
        var raw: [String: Any] = [
            "mode": "write", "intent": ["kind": "click", "parameters": ["button": "left", "count": 1]],
            "target": binding, "clickContract": version
        ]
        if optedIn { raw["clickPreferenceHints"] = "v1" }
        return raw
    }

    @MainActor static func offers(
        raw: Any? = nil, optedIn: Bool = false, version: String = "v2", missing: Bool = false
    ) throws -> [String: Any] {
        let begin = try NativeBeginRequest(raw ?? beginRequest(version: version, optedIn: optedIn))
        let target = try NativeTarget(begin.value["target"]).wire
        let node = target["node"] as! String
        let op = NativeOperation(
            timeoutMs: 5000, writable: true, intent: begin.intent, target: target,
            clickContract: begin.clickContract, clickPreferenceHints: begin.clickPreferenceHints,
            release: {}, cancelActive: {})
        defer { _ = op.finish() }
        let query = NativeInputQueryScope(
            binding: (target, [], node), desktop: false,
            selection: NativeSelectionBasis(access: NativeApplicationAccess()), scope: "surface")
        let plan = NativeInputContextPlan(query: query, operation: op, within: [], actuator: nil, receiver: nil, basis: [])
        let declarations = NativePrimitiveDeclarations(
            previous: [], op: op, query: query, within: [], actuator: nil, receiver: nil, basis: [])
        declarations.declareClick(
            .action("AXPress", "Toggles the state"),
            offer: .init(routeFamily: "macos.axAction.AXPress", delivery: .objectAction, admission: .ready,
                         reason: nil, preparationOptions: []))
        let physical = try project(sample(focus: missing ? .absent : .target))
        declarations.declareClick(.pointer(.click), offer: physical)
        let wire = EncodeNativeInputContextPass.run(.init(
            plan: plan, declarations: declarations, enabled: true, foreground: true,
            input: ["geometryCurrent": "yes", "pointerRoute": "yes", "pointerHit": node],
            nativeRole: "AXCheckBox", actionsComplete: true, applicationMenu: false))
        precondition(declarations.current.count == 2)
        precondition(declarations.current[0].click?.admission == .ready,
                     "a physical preference must never disqualify the provider's independent action")
        precondition(!op.inputStarted)
        return wire
    }

    @MainActor static func main() async throws {
        if CommandLine.arguments.contains("--offers") {
            let raw = CommandLine.arguments.contains("--begin-json")
                ? try JSONSerialization.jsonObject(with: FileHandle.standardInput.readDataToEndOfFile())
                : nil
            let wire = try offers(
                raw: raw, optedIn: CommandLine.arguments.contains("--opt-in"),
                version: CommandLine.arguments.contains("--click-v1") ? "v1" : "v2",
                missing: CommandLine.arguments.contains("--unknown-focus"))
            print(String(data: try JSONSerialization.data(withJSONObject: wire, options: [.sortedKeys]), encoding: .utf8)!)
            return
        }
        try expect(project(sample()).preferenceHint == .readyPhysical)
        for focus in [NativePointerReadiness.WindowFocus.otherWindow, .absent, .unresolved, .failed, .notRequired] {
            let value = sample(focus: focus)
            let offer = try project(value)
            precondition(offer.preferenceHint == nil && offer.admission == .ready)
            // Existing readiness can remain permissive; missing a stronger
            // preference cannot remove that legitimate visual route.
            if focus != .otherWindow { precondition(value.ready) }
        }
        for kind in [NativePointerReadiness.WindowKind.otherDeclared, .visualOnly, .identityConflict] {
            try expect(project(sample(kind: kind)).preferenceHint == nil)
        }
        try expect(project(sample(foreground: false)).preferenceHint == nil)
        try expect(project(sample(), exact: false).preferenceHint == nil)
        try expect(project(sample(), exclusive: false).preferenceHint == nil)
        try expect(project(.init(
            ready: false, foreground: true, hit: sample().hit, windowKind: .keyRequiring, focus: .target)
        ).preferenceHint == nil)
        let hits: [NativeInputWindowHit?] = [
            nil, .init(windowServer: .window(43), accessibility: .window(42)),
            .init(windowServer: .noWindow, accessibility: .window(42)),
            .init(windowServer: .unavailable, accessibility: .window(42)),
            .init(windowServer: .failed(-1), accessibility: .window(42))
        ]
        for hit in hits { try expect(project(sample(hit: hit)).preferenceHint == nil) }
        for (kind, parameters) in [
            ("click", ["button": "right", "count": 1] as [String: Any]),
            ("click", ["button": "left", "count": 2]), ("click", ["x": 1, "y": 2]),
            ("openMenu", [:]), ("action", ["action": "AXPress"])
        ] {
            try expect(project(sample(), request: intent(kind, parameters)).preferenceHint == nil)
        }
        for original in [
            hid(.preparationRequired, options: ["foreground"]), hid(.ready, options: ["background"]),
            hid(.unavailable), hid(.insufficientEvidence),
            NativeClickContract(routeFamily: "ax", delivery: .objectAction, admission: .ready,
                                reason: nil, preparationOptions: []),
            NativeClickContract(routeFamily: "pid", delivery: .windowDirected, admission: .ready,
                                reason: nil, preparationOptions: [])
        ] {
            let value = try project(sample(), original: original)
            precondition(value == original && value.preferenceHint == nil)
        }
        let strong = try project(sample())
        precondition(strong.wire["preferenceProfile"] as? String == "default")
        precondition(strong.wire["preferenceHint"] as? String == "readyPhysical")
        let unknown = try project(sample(focus: .failed), original: strong)
        precondition(unknown.preferenceHint == nil && unknown.wire["preferenceHint"] == nil)
        precondition(unknown.admission == strong.admission && unknown.preparationOptions == strong.preparationOptions)
        try metadataNegotiation()
        try queryLocalHintLifetime()
        try await pointerEffectAndBoundaries()
        print("PASS strict current native physical preference, unchanged offers, real encoding and pointer effect/boundaries")
    }

    @MainActor static func metadataNegotiation() throws {
        for version in ["v1", "v2"] {
            for optedIn in [false, true] {
                let wire = try offers(optedIn: optedIn, version: version)
                let primitives = wire["primitives"] as! [[String: Any]]
                let physical = primitives.first { $0["unit"] as? String == "pointer_sequence" }!["click"] as! [String: Any]
                precondition((physical["preferenceHint"] as? String == "readyPhysical") == optedIn)
                if !optedIn {
                    precondition(Set(physical.keys) == [
                        "routeFamily", "delivery", "interaction", "admission", "reason", "preferenceProfile",
                        "preparationOptions"
                    ], "legacy wire must remain exact, including strict v2 clients")
                }
                let missing = try offers(optedIn: optedIn, version: version, missing: true)
                precondition((missing["primitives"] as! [[String: Any]]).allSatisfy {
                    ($0["click"] as! [String: Any])["preferenceHint"] == nil
                })
            }
        }
        var invalid = [[String: Any]]()
        var raw = beginRequest(optedIn: true)
        raw.removeValue(forKey: "clickContract"); invalid.append(raw)
        raw = beginRequest(optedIn: true); raw["clickPreferenceHints"] = "v2"; invalid.append(raw)
        raw["clickPreferenceHints"] = NSNull(); invalid.append(raw)
        raw = beginRequest(optedIn: true); raw["extra"] = true; invalid.append(raw)
        raw = beginRequest(optedIn: true); raw["intent"] = ["kind": "focus", "parameters": [:]]; invalid.append(raw)
        raw = beginRequest(optedIn: true); raw["mode"] = "read"
        raw["intent"] = ["kind": "read", "parameters": [:]]; invalid.append(raw)
        for value in invalid {
            do { _ = try NativeBeginRequest(value); preconditionFailure("invalid metadata negotiation accepted") }
            catch let error as DriverError { precondition(error.code == .invalidRequest) }
        }
    }

    @MainActor static func queryLocalHintLifetime() throws {
        let op = NativeOperation(
            timeoutMs: 5000, writable: true, intent: try intent(), target: binding, clickContract: "v2",
            clickPreferenceHints: true,
            release: {}, cancelActive: {})
        defer { _ = op.finish() }
        let query = NativeInputQueryScope(
            binding: (binding, [], "E"), desktop: false,
            selection: NativeSelectionBasis(access: NativeApplicationAccess()), scope: "surface")
        func declarations(_ previous: [NativePrimitiveContract]) -> NativePrimitiveDeclarations {
            .init(previous: previous, op: op, query: query, within: [], actuator: nil, receiver: nil, basis: [])
        }
        let first = declarations([])
        first.declareClick(.pointer(.click), offer: try project(sample()))
        let refreshed = declarations(first.current)
        refreshed.declareClick(.pointer(.click), offer: try project(sample(focus: .failed)))
        precondition(first.current[0].click?.preferenceHint == .readyPhysical,
                     "a new query cannot mutate an already delivered offer")
        precondition(refreshed.current[0].click?.preferenceHint == nil,
                     "a previous preference is not a latch or authority")
        precondition(refreshed.current[0].id == first.current[0].id,
                     "ordering freshness cannot retire the original element or ordinary contract")
        try expect(refreshed.registeredContracts().count == 1)
    }

    @MainActor static func pointerEffectAndBoundaries() async throws {
        enum Lost: Error { case modal }
        for modal in [false, true] {
            var events = [CGEventType](), activeTool = "Pan", checks = 0
            let boundary = NativeUnitBoundary(
                checkConditions: { checks += 1; if modal { throw Lost.modal } }, checkSelection: {},
                markDispatchInFlight: {}, checkKeyboardSelection: {}, markPreparedKeyboardDispatchInFlight: {},
                beforeDispatch: {})
            let request = try NativePointerIntent(
                unit: "pointer_sequence", kind: "click", parameters: ["button": "left", "count": 1],
                foreground: false, element: true)
            let sequence = try NativePointerEvents(
                request: request, from: CGPoint(x: 20, y: 20), to: nil,
                viewport: CGSize(width: 100, height: 100), source: CGEventSource(stateID: .privateState)!)
            do {
                try await sequence.deliver(
                    check: { _, _ in try boundary.checkConditions() },
                    post: { event, _ in
                        events.append(event.type)
                        if event.type == .leftMouseUp { activeTool = "Identify" }
                    }, canRelease: { true }, skipped: { preconditionFailure("cleanup") },
                    onDispatch: boundary.beforeDispatch, onPosted: {})
                precondition(!modal)
            } catch Lost.modal { precondition(modal) }
            precondition(checks > 0)
            precondition(activeTool == (modal ? "Pan" : "Identify"))
            precondition(events == (modal ? [] : [.mouseMoved, .leftMouseDown, .leftMouseUp]))
        }
    }
}
