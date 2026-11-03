// Copyright (c) Huawei Technologies Co., Ltd. 2026. All rights reserved.

import AppKit
import ApplicationServices
import Darwin

/// A fixed-route diagnostic restricted to the disposable RowSelectionProbe.
/// This retains an Element target and exercises its existing pointer unit. It
/// does not change production admission, create a capture, or confirm app effect.
@MainActor private final class ElementRowPointerProbeRun {
    let options: [String: String]
    var report: [String: Any] = [
        "passed": false, "scope": "primitive_diagnostic_not_sdk_acceptance",
        "selected_delivery": "surface_pointer", "global_input_allowed": false,
        "target_kind": "element", "application_effect": "unconfirmed",
        "foreground_preparation_requested": true,
    ]
    var preparation = [String]()
    var inputActivity = [[String: Any]]()
    var dispatch = "notDispatched"
    var cleanup = "complete"
    var posted = 0

    init(_ arguments: [String]) throws {
        let required: Set<String> = ["--pid", "--window", "--bundle", "--row-index", "--output"]
        var values = [String: String]()
        var index = 1
        while index < arguments.count {
            let key = arguments[index]
            guard required.contains(key), index + 1 < arguments.count, values[key] == nil else {
                throw DriverError("invalid row primitive diagnostic arguments", code: .invalidRequest)
            }
            values[key] = arguments[index + 1]
            index += 2
        }
        guard required == Set(values.keys), values["--row-index"] == "1",
            values["--bundle"]!.hasPrefix("local.computer-use.row-selection.") else {
            throw DriverError("probe accepts only row 1 of a disposable row-selection fixture", code: .invalidRequest)
        }
        options = values
        report["request"] = Dictionary(uniqueKeysWithValues: values.map { (String($0.key.dropFirst(2)), $0.value) })
    }

    private func windowFrame(_ id: CGWindowID, pid: pid_t) throws -> CGRect {
        guard let info = WindowServerInfo.window(id),
            (info[kCGWindowOwnerPID as String] as? NSNumber)?.int32Value == pid,
            let bounds = info[kCGWindowBounds as String] as? [String: Any],
            let frame = CGRect(dictionaryRepresentation: bounds as CFDictionary),
            [frame.minX, frame.minY, frame.width, frame.height].allSatisfy(\.isFinite),
            frame.width > 0, frame.height > 0 else {
            throw DriverError("the original fixture WindowServer object is unavailable", code: .staleTarget)
        }
        return frame
    }

    private func inputGuards() -> [String: Any] {
        let cursor = CGEvent(source: nil)?.location
        return [
            "cursor": cursor.map { [$0.x, $0.y] } as Any? ?? NSNull(),
            "held_buttons": NSEvent.pressedMouseButtons,
            "clipboard_change_count": NSPasteboard.general.changeCount,
            // Do not materialize the user's promised clipboard representations
            // just to diagnose a mouse route. This is generation evidence only.
            "clipboard_evidence": "generation_only_not_content",
        ]
    }

    /// Traverse only this retained window. An identifier locates the fixture's
    /// table; labels, row text and sibling windows never retarget the diagnostic.
    private func tables(in window: AXUIElement) throws -> [AXUIElement] {
        var queue: [(AXUIElement, Int)] = [(window, 0)]
        var seen = [AXUIElement](), found = [AXUIElement]()
        var cursor = 0
        while cursor < queue.count {
            try RequestDiagnostics.checkCancellation()
            let (node, depth) = queue[cursor]
            cursor += 1
            if seen.contains(where: { CFEqual($0, node) }) { continue }
            guard seen.count < 512, depth < 16 else {
                throw DriverError("fixture table search exceeded its bounded scope", code: .inputUnavailable)
            }
            seen.append(node)
            if AXAccess.read(node, kAXIdentifierAttribute).value as? String == "selection-table",
                AXAccess.read(node, kAXRoleAttribute).value as? String == kAXTableRole {
                found.append(node)
            }
            for child in AXAccess.elements(node, kAXChildrenAttribute, limit: 128) ?? [] {
                let role = AXAccess.read(child, kAXRoleAttribute).value as? String
                guard role != kAXWindowRole && role != kAXSheetRole && role != "AXPopover" else { continue }
                queue.append((child, depth + 1))
            }
        }
        return found
    }

    private func objectEvidence(_ object: AXUIElement, window: AXUIElement, pid: pid_t) -> [String: Any] {
        var actions: CFArray?
        let actionStatus = AXUIElementCopyActionNames(object, &actions)
        return [
            "role": AXAccess.read(object, kAXRoleAttribute).value as? String ?? "unknown",
            "actions": actions as? [String] ?? [], "actionStatus": actionStatus.rawValue,
            "sameWindow": WindowGeometry.owningWindow(object, pid: pid, in: window).map { CFEqual($0, window) } == true,
        ]
    }

    private func observationEvidence(
        _ observed: NativeElementPointerUnit.Observation, row: AXUIElement, window: AXUIElement, pid: pid_t
    ) -> [String: Any] {
        let geometry = observed.geometry
        var evidence: [String: Any] = [
            "basis": observed.basis.rawValue, "dispatchGeometryReady": observed.dispatchGeometryReady,
            "point": [geometry.point.x, geometry.point.y],
            "frame": [geometry.frame.minX, geometry.frame.minY, geometry.frame.width, geometry.frame.height],
            "windowFrame": [geometry.mapping.frame.minX, geometry.mapping.frame.minY,
                            geometry.mapping.frame.width, geometry.mapping.frame.height],
            "window": geometry.mapping.id, "pid": geometry.mapping.pid,
        ]
        if let hit = observed.hit {
            var value = objectEvidence(hit, window: window, pid: pid)
            value["sameOriginalRow"] = CFEqual(hit, row)
            evidence["hit"] = value
        } else { evidence["hit"] = NSNull() }
        return evidence
    }

    private func requireExactRow(_ observed: NativeElementPointerUnit.Observation, row: AXUIElement) throws {
        guard observed.basis == .exactHit, let hit = observed.hit, CFEqual(hit, row) else {
            throw DriverError(
                "the sampled hit does not prove the original row; member or geometry substitution is not permitted",
                code: .inputUnavailable)
        }
    }

    private func perform() async throws {
        guard let pid = options["--pid"].flatMap(Int32.init), pid > 0,
            let windowID = options["--window"].flatMap(UInt32.init), windowID > 0,
            let application = NSRunningApplication(processIdentifier: pid), !application.isTerminated,
            application.bundleIdentifier == options["--bundle"], let identity = NativeProcessIdentity.read(pid) else {
            throw DriverError("the exact disposable row fixture process is unavailable", code: .staleTarget)
        }
        guard AXIsProcessTrusted() else {
            throw DriverError("the diagnostic lacks Accessibility permission; no input was sent", code: .permissionRequired)
        }
        let frame = try windowFrame(windowID, pid: pid)
        let root = AXUIElementCreateApplication(pid)
        AXUIElementSetMessagingTimeout(root, 0.2)
        guard let windows = AXAccess.elements(root, kAXWindowsAttribute, limit: 64) else {
            throw DriverError("fixture AX window catalogue is unavailable", code: .staleTarget)
        }
        let matchingWindows = windows.filter { BackgroundEvents.nativeWindowID($0) == windowID }
        guard matchingWindows.count == 1, let window = matchingWindows.first else {
            throw DriverError("the exact fixture AX window is not uniquely confirmed", code: .staleTarget)
        }
        let foundTables = try tables(in: window)
        guard foundTables.count == 1, let table = foundTables.first,
            let rows = AXAccess.elements(table, kAXRowsAttribute, limit: 64), rows.count > 1 else {
            throw DriverError("the fixture's original table and row are not uniquely available", code: .staleTarget)
        }
        let row = rows[1]
        var rowEvidence = objectEvidence(row, window: window, pid: pid)
        rowEvidence["index"] = (AXAccess.read(row, kAXIndexAttribute).value as? NSNumber) as Any? ?? NSNull()
        rowEvidence["selected"] = AXAccess.read(row, kAXSelectedAttribute).value as? Bool ?? false
        rowEvidence["parentSameTable"] = AXAccess.element(row, kAXParentAttribute).map { CFEqual($0, table) } == true
        report["original_row"] = rowEvidence
        report["process_incarnation"] = identity.incarnation
        report["frame"] = [frame.minX, frame.minY, frame.width, frame.height]
        guard rowEvidence["role"] as? String == kAXRowRole, rowEvidence["selected"] as? Bool == true,
            rowEvidence["parentSameTable"] as? Bool == true,
            rowEvidence["sameWindow"] as? Bool == true,
            (rowEvidence["index"] as? NSNumber)?.intValue == 1 else {
            throw DriverError("the selected original AX row/table relationship is unconfirmed", code: .inputUnavailable)
        }
        let element = NativeElementPointerUnit(application: application, window: window, element: row)
        let before = try element.observedGeometry()
        report["before_geometry"] = observationEvidence(before, row: row, window: window, pid: pid)
        try requireExactRow(before, row: row)
        guard before.geometry.mapping.id == windowID, before.geometry.mapping.pid == pid,
            before.geometry.mapping.frame == frame else {
            throw DriverError("element mapping differs from the retained fixture window", code: .staleTarget)
        }
        let pointer = NativePointerUnit(
            application: application, window: before.geometry.mapping, nativeWindow: window,
            pointerRequirement: .foregroundWindowPoint)
        report["receiver_qualified"] = try pointer.addressedPointerReceiver(at: before.geometry.point) == .declaredInteraction
        let target = BackgroundTarget(application)
        let checkIdentity: @MainActor () throws -> Void = {
            try RequestDiagnostics.checkCancellation()
            try target.validate(input: true)
            guard NativeProcessIdentity.read(pid) == identity,
                application.bundleIdentifier == self.options["--bundle"],
                try self.windowFrame(windowID, pid: pid) == frame else {
                throw DriverError("fixture process or original window identity changed", code: .staleTarget)
            }
            guard let currentWindows = AXAccess.elements(root, kAXWindowsAttribute, limit: 64) else {
                throw DriverError("fixture AX window catalogue became unavailable", code: .staleTarget)
            }
            let matches = currentWindows.filter { BackgroundEvents.nativeWindowID($0) == windowID }
            guard matches.count == 1, CFEqual(matches[0], window),
                AXAccess.elements(window, "AXSheets", limit: 8)?.isEmpty != false,
                !currentWindows.contains(where: { AXAccess.read($0, kAXModalAttribute).value as? Bool == true }) else {
                throw DriverError("original fixture scope or modal state changed", code: .staleTarget)
            }
            let currentTables = try self.tables(in: window)
            guard currentTables.count == 1, CFEqual(currentTables[0], table),
                let currentRows = AXAccess.elements(table, kAXRowsAttribute, limit: 64), currentRows.count > 1,
                CFEqual(currentRows[1], row),
                AXAccess.element(row, kAXParentAttribute).map({ CFEqual($0, table) }) == true,
                AXAccess.read(row, kAXSelectedAttribute).value as? Bool == true,
                (AXAccess.read(row, kAXIndexAttribute).value as? NSNumber)?.intValue == 1,
                WindowGeometry.owningWindow(row, pid: pid, in: window).map({ CFEqual($0, window) }) == true,
                WindowGeometry.owningWindow(table, pid: pid, in: window).map({ CFEqual($0, window) }) == true else {
                throw DriverError("the original selected row/table binding changed", code: .staleTarget)
            }
            try pointer.validateWindowIdentity()
            try BackgroundEvents.requirePointerRoute(window: before.geometry.mapping)
        }
        try checkIdentity()
        try BackgroundTarget.validateReleasedInput()
        try await NativeInputActivity.$stage.withValue(.preparation) {
            try await pointer.prepare(
                foreground: true, atNative: before.geometry.point, kind: .click, delivery: .windowDirected,
                check: checkIdentity, onPreparation: { self.preparation.append($0) },
                onCleanupUnknown: { self.cleanup = "unknown" })
        }
        try checkIdentity()
        let prepared = try element.observedGeometry()
        report["after_preparation_geometry"] = observationEvidence(prepared, row: row, window: window, pid: pid)
        try requireExactRow(prepared, row: row)
        let selected = prepared.geometry
        guard selected.mapping.id == windowID, selected.mapping.pid == pid, selected.mapping.frame == frame else {
            throw DriverError("original row mapping changed during preparation", code: .staleTarget)
        }
        // The production Element unit does not yet retain this new readiness
        // requirement. This diagnostic explicitly supplies it at every existing
        // check boundary, without altering production selection or input code.
        let checkDispatch: @MainActor () throws -> Void = {
            try checkIdentity()
            guard pointer.foregroundWindow(at: selected.point) else {
                throw DriverError("the original row point is no longer in the foreground window", code: .inputUnavailable)
            }
            let current = try element.observedGeometry()
            try self.requireExactRow(current, row: row)
            guard current.geometry.same(as: selected) else {
                throw DriverError("original row geometry changed during the diagnostic gesture", code: .inputUnavailable)
            }
            // A direct frozen-point query also prevents a later candidate from
            // replacing the point used to qualify this one diagnostic gesture.
            var hit: AXUIElement?
            guard AXUIElementCopyElementAtPosition(root, Float(selected.point.x), Float(selected.point.y), &hit) == .success,
                let hit, CFEqual(hit, row), pointer.foregroundWindow(at: selected.point) else {
                throw DriverError("the frozen original row point is no longer an exact hit", code: .inputUnavailable)
            }
            try RequestDiagnostics.checkCancellation()
        }
        let request = try NativePointerIntent(
            unit: "pointer_sequence", kind: "click", parameters: ["button": "left", "count": 1],
            foreground: false, element: true)
        try await element.deliver(
            request, check: checkDispatch,
            onDispatch: { try checkDispatch(); self.dispatch = "unknown" },
            onPosted: { self.posted += 1 }, onCleanupUnknown: { self.cleanup = "unknown" },
            delivery: .windowDirected)
        dispatch = "dispatched"
        report["passed"] = cleanup == "complete"
    }

    func run() async -> Int32 {
        report["input_guards_before"] = inputGuards()
        let activity = NativeInputActivity(
            operation: UUID().uuidString, step: 1, command: "fixedElementRowPointerDiagnostic", intent: "click",
            target: ["pid": options["--pid"]!, "window": options["--window"]!, "bundle": options["--bundle"]!],
            globalInputAllowed: false, emit: { self.inputActivity.append($0) })
        await NativeInputAuthorization.$globalInputAllowed.withValue(false) {
            await NativeInputActivity.$current.withValue(activity) {
                await RequestDiagnostics.$deadline.withValue(ProcessInfo.processInfo.systemUptime + 15) {
                    do { try await self.perform() }
                    catch {
                        if self.posted > 0 { self.dispatch = "partial" }
                        self.report["error"] = [
                            "code": (error as? ClassifiedFailure)?.code.rawValue ?? "execution_failed",
                            "detail": String(describing: error),
                        ]
                    }
                    SyntheticFocusCoordinator.shared.stop()
                }
            }
        }
        report["impact"] = activity.finish(dispatch: dispatch, cleanup: cleanup)
        report["input_guards_after"] = inputGuards()
        report["dispatch"] = dispatch
        report["cleanup"] = cleanup
        report["preparation"] = preparation
        report["posted_event_callbacks"] = posted
        report["input_activity"] = inputActivity
        do {
            let data = try JSONSerialization.data(withJSONObject: report, options: [.prettyPrinted, .sortedKeys])
            try (data + Data([10])).write(to: URL(fileURLWithPath: options["--output"]!), options: .atomic)
        } catch {
            diagnose("could not write row primitive diagnostic result: \(error)")
            return 2
        }
        return report["passed"] as? Bool == true ? 0 : 1
    }
}

@MainActor private final class ElementRowPointerProbeDelegate: NSObject, NSApplicationDelegate {
    let run: ElementRowPointerProbeRun
    var task: Task<Void, Never>?
    var signals = [DispatchSourceSignal]()
    init(_ run: ElementRowPointerProbeRun) { self.run = run }

    func applicationDidFinishLaunching(_ notification: Notification) {
        task = Task { @MainActor in exit(await self.run.run()) }
    }
    func observeTermination() {
        for number in [SIGTERM, SIGINT, SIGHUP] {
            signal(number, SIG_IGN)
            let source = DispatchSource.makeSignalSource(signal: number, queue: .main)
            source.setEventHandler { MainActor.assumeIsolated { self.task?.cancel() } }
            source.resume()
            signals.append(source)
        }
    }
}

@main enum ElementRowPointerMechanismProbe {
    @MainActor static func main() {
        if let status = ForegroundActivation.runIfRequested(CommandLine.arguments) { exit(status) }
        do {
            let run = try ElementRowPointerProbeRun(CommandLine.arguments)
            let application = NSApplication.shared
            application.setActivationPolicy(.prohibited)
            signal(SIGPIPE, SIG_IGN)
            let delegate = ElementRowPointerProbeDelegate(run)
            delegate.observeTermination()
            application.delegate = delegate
            application.run()
        } catch {
            diagnose("row primitive diagnostic arguments: \(error)")
            exit(2)
        }
    }
}
