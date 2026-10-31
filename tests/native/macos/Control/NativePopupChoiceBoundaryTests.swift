// Copyright (c) Huawei Technologies Co., Ltd. 2026. All rights reserved.

import AppKit
import ApplicationServices

/// Production write/UnitBoundary/Click/dispatch, with only native provider
/// facts and the OS input adapter substituted. No desktop input is posted.
@main struct NativePopupChoiceBoundaryTests {
    @MainActor static func main() async throws {
        setenv("COMPUTER_USE_DESKTOP", "exclusive", 1)
        if CommandLine.arguments.contains("--offers") {
            let probe = PopupChoiceProbe()
            let wire = try probe.offerWire(
                noHit: CommandLine.arguments.contains("--no-hit"),
                legacy: CommandLine.arguments.contains("--legacy-offers"))
            print(
                String(data: try JSONSerialization.data(withJSONObject: wire, options: [.sortedKeys]), encoding: .utf8)!
            )
            return
        }
        let probe = PopupChoiceProbe()
        let result = try await probe.click()
        guard probe.mode == "Manual", probe.presses == 0, probe.events == [.mouseMoved, .leftMouseDown, .leftMouseUp]
        else {
            print(
                "FAIL original Manual first effect: mode=\(probe.mode), dispatch=\(result["dispatch"] ?? "unknown"), AXPress=\(probe.presses)"
            )
            exit(1)
        }
        precondition(result["target"] as? NSDictionary == probe.binding as NSDictionary)
        precondition((result["preparation"] as? [Any])?.isEmpty == true)
        try await rejections()
        try await strictRoutes()
        try versions()
        try registryVersions()
        try await noReplayAndDeclaredPress()
        try await foregroundQualification()
        print(
            "PASS original Manual effect through production pointer pipeline; strict context/physical/modal/route/version/no-replay boundaries"
        )
    }

    @MainActor private static func reject(
        _ name: String, _ change: (PopupChoiceProbe) -> Void, ownerQuery: Bool = false
    ) async throws {
        let probe = PopupChoiceProbe()
        _ = probe.proof
        change(probe)
        let result = try await probe.click()
        precondition(result["dispatch"] as? String == "notDispatched", "\(name): \(result)")
        precondition(probe.mode == "Auto" && probe.events.isEmpty && probe.presses == 0, name)
        if ownerQuery { precondition(probe.scopes.ownerQueries > 0, name) }
    }

    @MainActor static func rejections() async throws {
        try await reject("samePID document B with unchanged forward children") {
            $0.scopes.currentOwner = $0.nodes["B"]!
            $0.scopes.parents["D"] = "B"
        }
        try await reject("original list reparented") { $0.scopes.parents["L"] = "other" }
        try await reject("process incarnation") { $0.scopes.process = NativeProcessIdentity.read(getppid())! }
        try await reject("popup native identity") { $0.scopes.ids["P"] = 285 }
        try await reject("logical owner native identity") { $0.scopes.ids["M"] = 208 }
        try await reject("leaf window conflict") { $0.scopes.ids["E"] = 999 }
        try await reject("destroyed original owner") { $0.scopes.roles.removeValue(forKey: "M") }
        try await reject("detached original popup") { $0.scopes.children["P"] = [] }
        try await reject("same-name replacement leaf") { $0.scopes.children["L"] = ["Auto", "replacement"] }
        try await reject("destroyed original leaf") { $0.scopes.roles.removeValue(forKey: "E") }
        try await reject("changed original role") { $0.scopes.roles["E"] = "AXButton" }
        try await reject("ordinary document is not popup") { $0.scopes.subroles["P"] = "AXStandardWindow" }
        try await reject("modal popup") { $0.scopes.modals["P"] = true }
        try await reject("unknown popup modality") { $0.scopes.modals.removeValue(forKey: "P") }
        try await reject("missing immutable observation") { $0.scopes.stamp = nil }
        try await reject("incomplete forward path") { $0.scopes.incomplete = true }
        try await reject("original read budget") { $0.scopes.failure = RequestDeadlineExceeded() }
        try await reject("different current AX hit") { $0.scopes.hit = $0.nodes["Auto"] }
        try await reject("no current AX hit") { $0.scopes.hit = nil }
        try await reject("no physical window hit") {
            $0.scopes.physical = .init(windowServer: .noWindow, accessibility: nil)
        }
        try await reject("known physical conflict cannot be covered by AX fallback") {
            $0.scopes.physical = .init(windowServer: .window(999), accessibility: .window(284))
        }
        try await reject("known empty physical hit cannot be covered by AX fallback") {
            $0.scopes.physical = .init(windowServer: .noWindow, accessibility: .window(284))
        }
        try await reject("unknown physical hit") {
            $0.scopes.physical = .init(windowServer: .unavailable, accessibility: .unresolvedWindow)
        }
        try await reject("no longer foreground") { $0.scopes.foreground = false }
        try await reject("geometry changed before first event") { $0.scopes.changedFrame = true }
        try await reject("physical window PID changed") { $0.scopes.samplePID = getppid() }
        try await reject("original object replaced") { $0.scopes.leaf = $0.nodes["Auto"]! }
        try await reject(
            "owned sheet",
            { $0.scopes.windows.append(.init(ref: "sheet", element: $0.nodes["sheet"]!, parent: "M", modal: true)) },
            ownerQuery: true)
        try await reject(
            "app modal", { $0.scopes.windows.append(.init(ref: "modal", element: $0.nodes["sheet"]!, modal: true)) },
            ownerQuery: true)
        try await reject("owner gone from fresh graph", { $0.scopes.windows.removeLast() }, ownerQuery: true)
        try await reject("known unrelated popup parent", { $0.scopes.windows[0].parent = "B" }, ownerQuery: true)
        try await reject("owner modality unknown", { $0.scopes.windows[1].modal = nil }, ownerQuery: true)
        let publicFallback = PopupChoiceProbe()
        publicFallback.scopes.physical = .init(windowServer: .unavailable, accessibility: .window(284))
        _ = try await publicFallback.click()
        precondition(publicFallback.mode == "Manual")
        let unrelated = PopupChoiceProbe()
        unrelated.scopes.windows.append(.init(ref: "B", element: unrelated.nodes["B"]!))
        unrelated.scopes.windows.append(
            .init(ref: "unrelated-sheet", element: unrelated.nodes["sheet"]!, parent: "B", modal: true))
        _ = try await unrelated.click()
        precondition(unrelated.mode == "Manual")
    }

    @MainActor static func strictRoutes() async throws {
        for (unit, kind, intent, params) in [
            (
                NativeInputUnit.pointer(.click), "pointer_sequence", "click",
                ["button": "right", "count": 1] as [String: Any]
            ), (.pointer(.click), "pointer_sequence", "click", ["button": "left", "count": 2]),
            (.action("AXPress", nil), "native_action", "click", ["button": "left", "count": 1]),
            (.keyboard(.key), "key_sequence", "key", ["key": "Return"]),
            (.pointer(.drag), "pointer_sequence", "drag", ["start": [1, 2], "end": [3, 4]])
        ] {
            let probe = PopupChoiceProbe()
            let result = try await probe.click(unit: unit, kind: kind, intent: intent, parameters: params)
            precondition(result["dispatch"] as? String == "notDispatched" && probe.events.isEmpty && probe.presses == 0)
        }
        let capture = PopupChoiceProbe()
        capture.binding["capture"] = "an-image"
        let result = try await capture.click()
        precondition(result["dispatch"] as? String == "notDispatched" && capture.events.isEmpty)
        for role in ["AXButton", "AXLink", "AXMenuItem", "AXCheckBox", "AXTextField", "AXStaticText"] {
            let probe = PopupChoiceProbe()
            let noContext: NativePopupOwnerStamp<AXUIElement, NativeProcessIdentity>? = nil
            precondition(
                !NativePopupClickPolicy.requiresPointer(
                    context: noContext, window: probe.nodes["P"]!, currentWindowID: 207, role: role, enabled: true,
                    focusWritable: true, actions: [.init(name: "AXPress", description: nil)], same: { CFEqual($0, $1) })
            )
            if role != "AXStaticText" {
                precondition(
                    !NativePopupClickPolicy.requiresPointer(
                        context: probe.proof.context, window: probe.nodes["P"]!, currentWindowID: 207, role: role,
                        enabled: true, focusWritable: true, actions: [.init(name: "AXPress", description: nil)],
                        same: { CFEqual($0, $1) }))
            }
        }
        let probe = PopupChoiceProbe()
        precondition(
            !NativePopupClickPolicy.requiresPointer(
                context: probe.proof.context, window: probe.nodes["P"]!, currentWindowID: 207, role: "AXStaticText",
                enabled: true, focusWritable: false, actions: [], same: { CFEqual($0, $1) }))
        let original = NativeClickContract(
            routeFamily: "native", delivery: .objectAction, admission: .ready, reason: nil, preparationOptions: [])
        precondition(NativePopupClickPolicy.actionOffer(original, requiresPointer: false) == original)
        for current: UInt32? in [284, nil, 999] {
            let ordinary = NativePopupClickPolicy.requiresPointer(
                context: probe.proof.context, window: probe.nodes["P"]!, currentWindowID: current, role: "AXStaticText",
                enabled: true, focusWritable: true, actions: [.init(name: "AXPress", description: nil)],
                same: { CFEqual($0, $1) })
            precondition(!ordinary)
            precondition(NativePopupClickPolicy.actionOffer(original, requiresPointer: ordinary) == original)
        }
    }

    @MainActor static func versions() throws {
        let probe = PopupChoiceProbe()
        let (op, query) = try probe.query()
        defer { _ = op.finish() }
        func declare(_ previous: [NativePrimitiveContract], _ proof: NativePopupPointerQualification?) throws
            -> NativePrimitiveDeclarations
        {
            let result = NativePrimitiveDeclarations(
                previous: previous, op: op, query: query, within: [], actuator: nil, receiver: nil, basis: [])
            result.declareClick(.pointer(.click), offer: NativePopupClickPolicy.pointerOffer(proof))
            return result
        }
        let missing = try declare([], nil)
        let known = try declare(missing.current, probe.proof)
        precondition(known.current[0].id != missing.current[0].id)
        let same = try declare(known.current, probe.proof)
        precondition(same.current[0].id == known.current[0].id)
        var sample = probe.scopes.sample()
        let old = sample.geometry
        sample = .init(
            geometry: .init(
                mapping: old.mapping, frame: old.frame, visible: old.visible,
                candidate: .init(point: CGPoint(x: old.point.x + 1, y: old.point.y), source: .edge)),
            hit: probe.nodes["E"], physicalHit: sample.physicalHit, foreground: true)
        let moved = NativePopupPointerQualification(
            context: probe.proof.context, element: probe.nodes["E"]!, pid: getpid(), sample: sample,
            same: { CFEqual($0, $1) })!
        let next = try declare(known.current, moved)
        precondition(next.current[0].id != known.current[0].id)
        let registered = try next.registeredContracts()
        precondition(!registered.contains { $0.id == known.current[0].id })
        precondition(
            probe.scopes.stamp!.matches(probe.proof.context, same: { CFEqual($0, $1) }),
            "point refresh cannot change observation owner")
        let originalSample = probe.scopes.sample()
        precondition(
            NativePopupPointerQualification(
                context: probe.proof.context, element: probe.nodes["E"]!, pid: getppid(), sample: originalSample,
                same: { CFEqual($0, $1) }) == nil)
        let badMapping = WindowGeometry.WindowMapping(
            id: 284, pid: getpid(), frame: .infinite, title: nil, width: 204, height: 50)
        let unbounded = NativePopupPointerSample(
            geometry: NativeElementPointerUnit.Geometry(
                mapping: badMapping, frame: old.frame, visible: old.visible, candidate: old.candidate),
            hit: probe.nodes["E"], physicalHit: originalSample.physicalHit, foreground: true)
        precondition(
            NativePopupPointerQualification(
                context: probe.proof.context, element: probe.nodes["E"]!, pid: getpid(), sample: unbounded,
                same: { CFEqual($0, $1) }) == nil)
    }

    @MainActor static func registryVersions() throws {
        let registry = ApplicationElements(), app = NSRunningApplication.current
        let probe = PopupChoiceProbe(), leaf = probe.nodes["E"]!
        let identity = ElementIdentity(facts: .init(values: ["AXRole": "AXStaticText"]))
        let key = ApplicationElements.Identity(pid: getpid(), launched: app.launchDate, element: leaf, scope: "P")
        func observe(_ stamp: NativePopupOwnerStamp<AXUIElement, NativeProcessIdentity>?) -> String {
            var target = ApplicationElements.Target(
                application: app, launched: app.launchDate, element: leaf, scope: "P", role: "AXStaticText",
                identity: identity, ancestors: [probe.nodes["P"]!, probe.nodes["L"]!])
            target.popupOwner = stamp
            return registry.observe(key, evidence: .init(identity: identity, provider: nil, context: []), value: target)
        }
        let missing = observe(nil), boundMissing = registry.bind(missing, text: nil)!
        let a = observe(probe.proof.context), boundA = registry.bind(a, text: nil)!
        precondition(a != missing && registry.target(boundMissing) == nil)
        precondition(observe(nil) == a && registry.target(boundA) != nil)
        let b = NativePopupOwnerStamp(
            popup: probe.nodes["P"]!, list: probe.nodes["L"]!, reverse: [probe.nodes["L"]!, probe.nodes["B"]!],
            roles: ["AXList", "AXWindow"], popupWindowID: 284, ownerWindowID: 208, process: probe.scopes.process)!
        precondition(observe(b) != a && registry.target(boundA) == nil)
        precondition(observe(probe.proof.context) != a, "later explicit observation cannot revive old A")
    }

    @MainActor static func noReplayAndDeclaredPress() async throws {
        let uncertain = PopupChoiceProbe()
        uncertain.failAfterMove = true
        let result = try await uncertain.click()
        precondition(result["dispatch"] as? String != "notDispatched" && uncertain.events == [.mouseMoved])
        precondition(uncertain.presses == 0 && uncertain.mode == "Auto" && uncertain.secondStepRejected)
        let newModal = PopupChoiceProbe()
        newModal.modalAfterMove = true
        let modalResult = try await newModal.click()
        precondition(modalResult["dispatch"] as? String != "notDispatched" && newModal.events == [.mouseMoved])
        precondition(newModal.presses == 0 && newModal.secondStepRejected)
        let inFlight = PopupChoiceProbe()
        inFlight.failInFlight = true
        let possible = try await inFlight.click()
        precondition(possible["dispatch"] as? String == "unknown" && inFlight.events.isEmpty)
        precondition(inFlight.presses == 0 && inFlight.secondStepRejected)
        // Counterexample: admitting a declared AXPress is not its consumption.
        // The real d25/c7ee attempt has the same status0/Auto outcome archived.
        let hollow = PopupChoiceProbe()
        hollow.scopes.currentOwner = hollow.nodes["P"]!
        let noEffect = try await hollow.click(qualified: false, unit: .action("AXPress", nil), kind: "native_action")
        precondition(noEffect["dispatch"] as? String == "dispatched" && hollow.presses == 1 && hollow.mode == "Auto")
        let ordinary = PopupChoiceProbe()
        ordinary.scopes.currentOwner = ordinary.nodes["P"]!
        ordinary.scopes.ids["E"] = 284
        ordinary.consumePress = true
        let sameWindow = try ordinary.nativeActionFacts("E")
        precondition(
            !NativePopupClickPolicy.requiresPointer(
                context: sameWindow.popupContext, window: ordinary.nodes["P"]!,
                currentWindowID: sameWindow.popupElementWindowID, role: sameWindow.role, enabled: sameWindow.enabled,
                focusWritable: sameWindow.focusWritable, actions: sameWindow.actions, same: { CFEqual($0, $1) }))
        _ = try await ordinary.click(qualified: false, unit: .action("AXPress", nil), kind: "native_action")
        precondition(ordinary.mode == "Manual" && ordinary.presses == 1 && ordinary.events.isEmpty)
    }

    @MainActor static func foregroundQualification() async throws {
        precondition(
            NativePointerReadiness.ready(
                kind: .click, foreground: true, windowKind: .otherDeclared, focus: .notRequired,
                backgroundPrepared: false))
        precondition(
            !NativePointerReadiness.ready(
                kind: .click, foreground: false, windowKind: .otherDeclared, focus: .notRequired,
                backgroundPrepared: false))
        let ready = PopupChoiceProbe()
        let result = try await ready.click()
        precondition(ready.mode == "Manual" && ready.presses == 0)
        precondition((result["preparation"] as? [Any])?.isEmpty == true)
        let background = PopupChoiceProbe()
        _ = background.proof
        background.scopes.foreground = false
        let blocked = try await background.click()
        precondition(blocked["dispatch"] as? String == "notDispatched")
        precondition(background.events.isEmpty && background.presses == 0)
        precondition((blocked["preparation"] as? [Any])?.isEmpty == true)
        let sample = background.scopes.sample()
        precondition(
            NativePopupPointerQualification(
                context: background.proof.context, element: background.nodes["E"]!, pid: getpid(), sample: sample,
                same: { CFEqual($0, $1) }) == nil)
    }
}

@MainActor private final class PopupChoiceScopes: NativeScopeAccess {
    unowned let probe: PopupChoiceProbe
    let nodes: [String: AXUIElement]
    var roles = [
        "P": "AXWindow", "L": "AXList", "E": "AXStaticText", "G": "AXGroup", "D": "AXCheckBox", "M": "AXWindow",
        "B": "AXWindow"
    ]
    var subroles = ["P": "AXDialog", "M": "AXStandardWindow", "B": "AXStandardWindow"]
    var modals = ["P": false, "M": false, "B": false]
    var children = ["P": ["L"], "L": ["Auto", "E", "MinMax"], "G": [], "D": ["G"], "M": ["D"]]
    var parents = ["E": "L", "L": "G", "G": "D", "D": "M"]
    var ids: [String: UInt32] = ["P": 284, "L": 207, "E": 207, "G": 207, "D": 207, "M": 207, "B": 208]
    var stamp: NativePopupOwnerStamp<AXUIElement, NativeProcessIdentity>?
    var process = NativeProcessIdentity.read(getpid())!
    var currentOwner: AXUIElement
    var leaf: AXUIElement
    var hit: AXUIElement?
    var physical = NativeInputWindowHit(windowServer: .window(284), accessibility: nil)
    var foreground = true, incomplete = false, changedFrame = false
    var samplePID = getpid()
    var failure: Error?
    var windows: [NativePopupOwnerBoundary.Window<AXUIElement>]
    var ownerQueries = 0
    init(_ probe: PopupChoiceProbe) {
        self.probe = probe
        nodes = probe.nodes
        currentOwner = nodes["M"]!
        leaf = nodes["E"]!
        hit = leaf
        windows = [.init(ref: "P", element: nodes["P"]!), .init(ref: "M", element: nodes["M"]!)]
        stamp = try! NativePopupAttachment.observe(
            popup: nodes["P"]!, list: nodes["L"]!, facts: facts(), same: { CFEqual($0, $1) })
    }
    func key(_ element: AXUIElement) -> String { nodes.first { CFEqual($0.value, element) }!.key }
    func facts() -> NativePopupFacts<AXUIElement, NativeProcessIdentity> {
        func check() throws { if let failure { throw failure } }
        return .init(
            role: {
                try check()
                return self.roles[self.key($0)]
            },
            subrole: {
                try check()
                return self.subroles[self.key($0)]
            },
            modal: {
                try check()
                return self.modals[self.key($0)]
            },
            parent: {
                try check()
                return self.parents[self.key($0)].flatMap { self.nodes[$0] }
            },
            children: {
                try check()
                return self.incomplete ? nil : self.children[self.key($0)]?.compactMap { self.nodes[$0] }
            },
            windowID: {
                try check()
                return self.ids[self.key($0)]
            },
            process: {
                try check()
                return self.process
            })
    }
    func sample() -> NativePopupPointerSample<AXUIElement> {
        let frame = CGRect(x: 517, y: changedFrame ? 367 : 366, width: 202, height: 16)
        let mapping = WindowGeometry.WindowMapping(
            id: 284, pid: samplePID, frame: CGRect(x: 516, y: 349, width: 204, height: 50), title: nil, width: 204,
            height: 50)
        return .init(
            geometry: .init(
                mapping: mapping, frame: frame, visible: frame,
                candidate: .init(point: CGPoint(x: 618, y: 374), source: .center)), hit: hit, physicalHit: physical,
            foreground: foreground)
    }
    func inputBinding(_ raw: Any?, input: Bool, attachedMenuAction: Bool) throws -> (
        wire: [String: Any], blocked: [String], node: String?
    ) {
        if input {
            try NativeInputMembershipBoundary.validate(
                leaf: leaf, ancestors: [nodes["P"]!, nodes["L"]!], window: nodes["P"]!, currentWindow: currentOwner,
                attachedMenuAction: attachedMenuAction, same: { CFEqual($0, $1) }, role: { self.roles[self.key($0)] },
                children: { self.children[self.key($0)]?.compactMap { self.nodes[$0] } ?? [] })
        }
        return (probe.binding, [], "E")
    }
    func popupPointerBoundary(_ raw: Any?, qualification: NativePopupPointerQualification) throws -> [String] {
        try NativePopupPointerBoundary.validate(
            qualification, original: stamp, leaf: leaf, ancestors: [nodes["P"]!, nodes["L"]!], popup: nodes["P"]!,
            currentOwner: currentOwner, facts: facts(), sample: sample, same: { CFEqual($0, $1) },
            ownerBoundary: { popup, owner in
                self.ownerQueries += 1
                return try NativePopupOwnerBoundary.blocked(
                    popup: popup, owner: owner, windows: self.windows, same: { CFEqual($0, $1) })
            })
    }
    func checkContainment(_ binding: [String: Any], within: [String], op: NativeOperation) throws {}
    func checkActuator(_ binding: [String: Any], actuator: NativeInspectQuery.Actuator?, op: NativeOperation) throws {}
    func isDesktop(_ binding: [String: Any]) -> Bool { false }
    func directoryTarget(_ raw: Any?) throws -> [String: Any]? { nil }
    func observationTarget(_ raw: Any?) throws -> (wire: [String: Any], node: String) { (probe.binding, "E") }
    func observePointer(_ binding: [String: Any]) {}
    func aimPointer(_ binding: [String: Any], intent: NativeIntent, receiver: String?) {}
}

@MainActor private final class PopupChoiceContracts: NativeContractLookup {
    var values = [NativePrimitiveContract]()
    let basis: NativeSelectionBasis
    init(_ applications: NativeApplicationAccess) { basis = NativeSelectionBasis(access: applications) }
    func primitiveContracts(_ token: String) -> [NativePrimitiveContract] { values }
    func selectionBasis(_ token: String) -> NativeSelectionBasis { basis }
}

@MainActor private final class PopupChoiceUnits: NativeInputUnitAccess, NativePopupPointerInput {
    unowned let probe: PopupChoiceProbe
    var proof: NativePopupPointerQualification!
    init(_ probe: PopupChoiceProbe) { self.probe = probe }
    func popupPointerUnit(_ binding: [String: Any], qualification: NativePopupPointerQualification) throws
        -> any NativePopupPointerInput
    {
        proof = qualification
        return self
    }
    func receiverRelation(
        _ binding: [String: Any], actuator: NativeInspectQuery.Actuator?, receiver: NativeInspectQuery.Receiver?
    ) throws -> NativeReceiverRelation? { preconditionFailure("unselected unit") }
    func desktopUnit(_ binding: [String: Any]) throws -> NativeDesktopUnit { preconditionFailure("unselected unit") }
    func keyboardUnit(
        _ binding: [String: Any], actuator: NativeInspectQuery.Actuator?, receiver: NativeInspectQuery.Receiver?
    ) throws -> NativeKeyboardUnit { preconditionFailure("unselected unit") }
    func selectedTextUnit(
        _ binding: [String: Any], actuator: NativeInspectQuery.Actuator?, receiver: NativeInspectQuery.Receiver?
    ) throws -> NativeSelectedTextUnit { preconditionFailure("unselected unit") }
    func elementPointerUnit(_ binding: [String: Any], actuator: NativeInspectQuery.Actuator?) throws
        -> NativeElementPointerUnit
    { preconditionFailure("unselected unit") }
    func menuUnit(_ binding: [String: Any], within: [String]) throws -> NativeMenuUnit {
        preconditionFailure("unselected unit")
    }
    func scrollUnit(_ binding: [String: Any], actuator: NativeInspectQuery.Actuator) throws -> NativeScrollUnit {
        preconditionFailure("unselected unit")
    }
    func deliver(
        _ request: NativePointerIntent, check: @escaping @MainActor () throws -> Void, onDispatch: () throws -> Void,
        onPosted: () -> Void, onCleanupUnknown: @escaping () -> Void
    ) async throws {
        if probe.failInFlight {
            try check()
            try onDispatch()
            throw DriverError("OS posting result unknown", code: .executionUnknown, effect: .possible)
        }
        // Production event construction/pacing/release; the only consumer
        // replaced is the OS mouse receiver. It commits on the actual up event.
        let source = CGEventSource(stateID: .privateState)!
        let sequence = try NativePointerEvents(
            request: request, from: proof.geometry.point, to: nil, viewport: proof.geometry.visible.size, source: source
        )
        try await sequence.deliver(
            entry: nil, check: { _, _ in try check() },
            post: { event, point in
                precondition(point == self.proof.geometry.point)
                self.probe.events.append(event.type)
                if event.type == .leftMouseUp { self.probe.mode = "Manual" }
                if self.probe.failAfterMove, event.type == .mouseMoved {
                    self.probe.scopes.physical = .init(windowServer: .window(999), accessibility: nil)
                }
                if self.probe.modalAfterMove, event.type == .mouseMoved {
                    self.probe.scopes.windows.append(
                        .init(ref: "sheet", element: self.probe.nodes["sheet"]!, parent: "M", modal: true))
                }
            }, canRelease: { true }, skipped: onCleanupUnknown, onDispatch: onDispatch, onPosted: onPosted)
    }
}

@MainActor private final class PopupChoiceProbe: NativeControlObjectAccess {
    let state = NativeDesktopState()
    let nodes: [String: AXUIElement] = Dictionary(
        uniqueKeysWithValues: ["P", "L", "E", "G", "D", "M", "B", "Auto", "MinMax", "replacement", "sheet"].enumerated()
            .map { ($1, AXUIElementCreateApplication(pid_t(20001 + $0))) })
    var binding: [String: Any] = [
        "session": "popup-test", "instance": "original-popup", "rootWindow": "P", "surface": "s", "node": "E",
        "capture": NSNull()
    ]
    lazy var scopes = PopupChoiceScopes(self)
    lazy var units = PopupChoiceUnits(self)
    lazy var proof = NativePopupPointerQualification(
        context: scopes.stamp!, element: nodes["E"]!, pid: getpid(), sample: scopes.sample(), same: { CFEqual($0, $1) })!
    var presses = 0, events = [CGEventType](), mode = "Auto", consumePress = false, failAfterMove = false,
        secondStepRejected = false
    var modalAfterMove = false, failInFlight = false
    func retainNode(_ ref: String) -> (() -> Void)? { {} }
    func nativeActionFacts(_ ref: String) throws -> NativeElementControl.ActionFacts {
        .init(
            role: "AXStaticText", actions: [.init(name: "AXPress", description: nil)], enabled: true,
            applicationMenuContext: false, foreground: true, focusWritable: true, documentEvidence: .other,
            popupContext: scopes.stamp, popupElementWindowID: scopes.ids["E"])
    }
    func executeNativeAction(
        _ ref: String, action: String, description: String?, onDispatch: @MainActor () throws -> Void,
        onReturned: @MainActor () -> Void
    ) throws {
        try onDispatch()
        presses += 1
        if consumePress { mode = "Manual" }
        onReturned()
    }
    func executeNativeSelection(
        _ ref: String, contract: NativeRowSelection.Contract, onDispatch: @MainActor () throws -> Void,
        onReturned: @MainActor () -> Void
    ) throws -> NativeElementControl.ActionResult { preconditionFailure("unselected route") }
    func executeNativeTextValue(
        _ ref: String, contract: NativeElementControl.TextValueContract, text: String,
        onDispatch: @MainActor () throws -> Void
    ) throws -> NativeElementControl.ActionResult { preconditionFailure("unselected route") }
    func query() throws -> (NativeOperation, NativeInputQueryScope) {
        let op = NativeOperation(
            timeoutMs: 5000, writable: true,
            intent: try NativeIntent(["kind": "click", "parameters": ["button": "left", "count": 1]], mode: "write"),
            target: binding, clickContract: "v2", release: {}, cancelActive: {})
        return (
            op,
            .init(
                binding: (binding, [], "E"), desktop: false,
                selection: NativeSelectionBasis(access: state.applications), scope: "s")
        )
    }
    func offerWire(noHit: Bool, legacy: Bool = false) throws -> [String: Any] {
        let (op, query) = try query()
        defer { _ = op.finish() }
        let declarations = NativePrimitiveDeclarations(
            previous: [], op: op, query: query, within: [], actuator: nil, receiver: nil, basis: [])
        let original = NativeClickContract(
            routeFamily: "macos.axAction.AXPress", delivery: .objectAction, admission: .ready, reason: nil,
            preparationOptions: [])
        let facts = try nativeActionFacts("E")
        let required = NativePopupClickPolicy.requiresPointer(
            context: facts.popupContext, window: nodes["P"]!, currentWindowID: facts.popupElementWindowID,
            role: facts.role, enabled: facts.enabled, focusWritable: facts.focusWritable, actions: facts.actions,
            same: { CFEqual($0, $1) })
        declarations.declareClick(
            .action("AXPress", nil),
            offer: NativePopupClickPolicy.actionOffer(original, requiresPointer: required && !legacy))
        declarations.declareClick(.pointer(.click), offer: NativePopupClickPolicy.pointerOffer(noHit ? nil : proof))
        return ["primitives": declarations.primitives]
    }
    func click(
        qualified: Bool = true, unit: NativeInputUnit = .pointer(.click), kind: String = "pointer_sequence",
        intent: String = "click", parameters: [String: Any] = ["button": "left", "count": 1]
    ) async throws -> [String: Any] {
        _ = proof  // Retain query evidence before any negative changes are consumed.
        let directory = NativeDirectory(state: state, session: "popup-test"),
            captures = NativeCapture(
                directory: NativeDirectory(state: state, session: "popup-test"), sharing: state.sharing)
        let contracts = PopupChoiceContracts(state.applications)
        var offer =
            qualified
            ? NativePopupClickPolicy.pointerOffer(proof)
            : NativeClickContract(
                routeFamily: "macos.axAction.AXPress", delivery: .objectAction, admission: .ready, reason: nil,
                preparationOptions: [])
        if intent != "click" { offer.popupPointer = proof }
        contracts.values = [
            .init(
                id: "choice", node: "E", within: [], actuator: nil, receiver: nil, basis: [], unit: unit, click: offer)
        ]
        let op = NativeOperation(
            timeoutMs: 5000, writable: true,
            intent: try NativeIntent(["kind": intent, "parameters": parameters], mode: "write"), target: binding,
            clickContract: intent == "click" ? "v2" : nil, release: {}, cancelActive: {})
        defer { _ = op.finish() }
        let access = NativeControlAccess(
            scopes: scopes, units: units, captures: captures, desktop: { self.state.desktop }, objects: self,
            sharing: state.sharing, contracts: contracts, recordEffect: {})
        let launch = NativeLaunchAccess(
            sharing: state.sharing, directory: directory, contracts: contracts, discovery: state.discovery,
            recordEffect: {})
        func request(_ step: Int) -> [String: Any] {
            [
                "operation": op.token, "step": step,
                "command": ["kind": kind, "primitiveId": "choice", "target": binding, "foreground": false]
            ]
        }
        let result = try await NativeWritePipeline.execute(
            request(1), operation: { _ in op }, access: access, launch: launch)
        if failAfterMove || modalAfterMove || failInFlight {
            do {
                _ = try await NativeWritePipeline.execute(
                    request(2), operation: { _ in op }, access: access, launch: launch)
            } catch let error as DriverError {
                precondition(error.code == .executionUnknown)
                secondStepRejected = true
            }
        }
        return result
    }
}
