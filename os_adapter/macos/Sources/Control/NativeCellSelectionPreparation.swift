// Copyright (c) Huawei Technologies Co., Ltd. 2026. All rights reserved.

import AppKit
import ApplicationServices

/// Addressed selection preparation, never a text-editing or keyboard contract.
/// The opaque operation contract retains the exact original objects.
@MainActor struct NativeCellSelectionPreparation {
    struct Contract: Equatable {
        let cell: AXUIElement
        let row: AXUIElement
        let table: AXUIElement
        let region: AXUIElement
        let window: AXUIElement
        let pid: pid_t
        let rowIndex: Int
        let columnIndex: Int

        static func == (lhs: Self, rhs: Self) -> Bool {
            lhs.pid == rhs.pid && lhs.rowIndex == rhs.rowIndex && lhs.columnIndex == rhs.columnIndex
                && CFEqual(lhs.cell, rhs.cell) && CFEqual(lhs.row, rhs.row) && CFEqual(lhs.table, rhs.table)
                && CFEqual(lhs.region, rhs.region) && CFEqual(lhs.window, rhs.window)
        }
    }

    struct Node {
        let element: AXUIElement
        let pid: pid_t?
        let role: String?
        let parent: AXUIElement?
        let window: AXUIElement?
        let enabled: Bool?
        let editable: Bool?
        let readOnly: Bool?
        let focused: Bool?
        let selected: Bool?
        let rowIndex: Int?
        let columnIndex: Int?
    }

    @MainActor struct Snapshot {
        let contract: Contract
        let cell: Node
        let row: Node
        let table: Node
        let region: Node
        let state: NativeReceiverSnapshot
        let parameterizedCell: AXUIElement?
        let selectionWritable: Bool?
        let selectionCount: Int?
        let selection: [AXUIElement]?
        let regionSelection: [AXUIElement]?
        let knownOtherSelectedCell: Bool

        var identity: Bool {
            let c = contract
            return cell.role == "AXCell" && row.role == kAXRowRole && table.role == kAXTableRole
                && cell.enabled == true
                && [cell, row, table, region].allSatisfy {
                    $0.pid == c.pid && $0.enabled != false
                        && $0.window.map { CFEqual($0, c.window) } == true
                }
                && cell.parent.map { CFEqual($0, c.row) } == true
                && cell.readOnly != true && cell.editable != false && table.readOnly != true && table.editable != false
                && row.parent.map { CFEqual($0, c.table) } == true
                && table.parent.map { CFEqual($0, c.region) } == true
                && cell.rowIndex == c.rowIndex && cell.columnIndex == c.columnIndex
                && parameterizedCell.map { CFEqual($0, c.cell) } == true
                && state.windowRoute == "yes" && state.scopeRoute == "yes"
                && state.window.map { CFEqual($0, c.window) } == true
        }

        /// Coarse focus is usable only inside this exact table's region, with
        /// no selected unrelated control/editor. Other initial cells or a
        /// multi-selection inside the table do not prohibit selecting C2.
        var legalFocus: Bool {
            guard let focus = state.focus else { return false }
            let safeRegionSelection = regionSelection.map {
                $0.isEmpty || $0.count == 1 && CFEqual($0[0], contract.table)
            }
            if CFEqual(focus, contract.cell) { return cell.focused != false && safeRegionSelection != false }
            if CFEqual(focus, contract.table) { return table.focused == true && safeRegionSelection != false }
            // Only this coarse-focus topology needs the LayoutArea role.
            // Exact Cell/Table focus does not depend on its parent's role.
            return CFEqual(focus, contract.region) && region.role == "AXLayoutArea" && region.focused == true
                && safeRegionSelection == true
        }

        /// AXSelected on the retained original plus the complete table count
        /// proves the unique selection without adopting provider proxy handles.
        /// Known contradictory selected-cell facts still reject the proof.
        var selected: Bool {
            identity && cell.selected == true && selectionCount == 1 && selection?.count == 1
                && !knownOtherSelectedCell
        }
        var active: Bool {
            guard legalFocus, let focus = state.focus else { return false }
            return CFEqual(focus, contract.cell) && cell.focused != false
                || CFEqual(focus, contract.table) && table.focused == true
        }
    }

    let application: NSRunningApplication
    let window: AXUIElement
    let element: AXUIElement
    var api = NativeCellSelectionSystem.API()

    func snapshot(_ expected: Contract? = nil) throws -> Snapshot? {
        try NativeCellSelectionSystem.snapshot(
            element: element, application: application, window: window, expected: expected, api: api)
    }

    func offer() throws -> Contract? {
        guard let current = try snapshot(), current.identity, current.legalFocus,
            current.selected || current.selectionWritable == true,
            !(current.selected && current.active)
        else { return nil }
        return current.contract
    }

    /// Prepare the original cell and table. Neither setter qualifies a text
    /// receiver. The operation reinspects the original target afterwards.
    func prepare(
        _ contract: Contract, check: () throws -> Void, onPreparation: (String) -> Void
    ) async throws {
        var entered = false
        func effect(_ detail: String) {
            entered = true
            onPreparation(detail)
        }
        func confirm(active: Bool) async throws -> Snapshot {
            for attempt in 0..<12 {
                let current = try require(contract, check: check)
                guard current.legalFocus else {
                    throw DriverError("another keyboard receiver owns the original cell context", code: .inputUnavailable)
                }
                if current.selected && (!active || current.active) { return current }
                if attempt < 11 { try await api.wait() }
            }
            throw DriverError("original cell selection and focus could not be confirmed", code: .inputUnavailable)
        }
        func write(_ target: AXUIElement, _ key: String, _ value: CFTypeRef, detail: String) throws {
            try check()
            effect(detail)
            let status = api.set(target, key as CFString, value)
            guard status == .success else {
                throw DriverError("\(detail) returned AX error \(status.rawValue)", code: .inputUnavailable)
            }
        }
        do {
            let initial = try require(contract, check: check)
            guard initial.legalFocus else {
                throw DriverError("the original cell context changed before selection", code: .inputUnavailable)
            }
            if !initial.selected {
                guard initial.selectionWritable == true else {
                    throw DriverError("the original table does not offer writable cell selection", code: .inputUnavailable)
                }
                try write(contract.table, "AXSelectedCells", [contract.cell] as CFArray, detail: "native original cell selection")
            }
            let selected = try await confirm(active: false)
            if !selected.active {
                // Selection may itself have focused the cell in the interval
                // between sampling and the separate setter's effect boundary.
                let current = try require(contract, check: check)
                if current.selected && current.active { return }
                guard current.selected, current.legalFocus,
                    api.writable(contract.table, kAXFocusedAttribute) == true
                else { throw DriverError("the original selected cell table could not receive focus", code: .inputUnavailable) }
                try write(contract.table, kAXFocusedAttribute, kCFBooleanTrue, detail: "native original cell table focus")
                _ = try await confirm(active: true)
            }
        } catch {
            if entered {
                throw DriverError(
                    "cell selection or focus may have changed; requested text was not sent: \(error)",
                    code: .inputUnavailable, effect: .possible)
            }
            throw error
        }
    }

    static func route(_ context: NativeRouteContext, selection: Contract) throws -> SelectedNativeRoute {
        let command = context.command
        let contract = context.resolved.contract
        guard command.kind == "prepare_receiver", context.operation.intent == "insert", !command.foreground,
            contract.actuator == nil, contract.receiver == nil, contract.node == context.operation.target["node"] as? String
        else { throw DriverError("cell selection only prepares the original addressed insertion", code: .invalidRequest) }
        _ = try NativeObjectIntent(unit: "native_text_edit", kind: "insert", parameters: context.operation.request.parameters)
        let keyboard = try context.access.units.keyboardUnit(command.target, actuator: nil, receiver: nil)
        guard let element = keyboard.element else {
            throw DriverError("cell preparation lost its original target", code: .staleTarget)
        }
        let unit = Self(application: keyboard.application, window: keyboard.window, element: element)
        return .prepare {
            try await unit.prepare(selection, check: context.boundary.checkConditions, onPreparation: context.receipt.recordNonRepeatablePreparation)
            context.receipt.detail = "Original cell selection and table focus confirmed; no requested text was sent."
            context.receipt.confirmation = "confirmed"
        }
    }

    func require(_ expected: Contract, check: () throws -> Void) throws -> Snapshot {
        try check()
        guard let current = try snapshot(expected), current.identity else {
            throw DriverError("the original cell selection identity or writable table is unconfirmed", code: .inputUnavailable)
        }
        return current
    }
}

/// Raw AX ports. Neither a successful setter nor an opaque selected-cell
/// proxy is promoted to an editable receiver.
@MainActor enum NativeCellSelectionSystem {
    typealias Unit = NativeCellSelectionPreparation
    @MainActor struct API {
        var read: @MainActor (AXUIElement, String) -> CFTypeRef? = { AXAccess.read($0, $1).value }
        var writable: @MainActor (AXUIElement, String) -> Bool? = { AXAccess.writable($0, $1).value }
        var count: @MainActor (AXUIElement, String) -> Int? = { element, key in
            var count: CFIndex = 0
            return AXUIElementGetAttributeValueCount(element, key as CFString, &count) == .success && count >= 0
                ? count : nil
        }
        var pid: @MainActor (AXUIElement) -> pid_t? = {
            var pid: pid_t = 0
            return AXUIElementGetPid($0, &pid) == .success ? pid : nil
        }
        var cell: @MainActor (AXUIElement, Int, Int) -> AXUIElement? = { table, column, row in
            let parameter = [NSNumber(value: column), NSNumber(value: row)] as CFArray
            var raw: CFTypeRef?
            guard AXUIElementCopyParameterizedAttributeValue(
                table, "AXCellForColumnAndRow" as CFString, parameter, &raw) == .success,
                let raw, CFGetTypeID(raw) == AXUIElementGetTypeID()
            else { return nil }
            return unsafeDowncast(raw, to: AXUIElement.self)
        }
        var receiver: @MainActor (NSRunningApplication, AXUIElement) throws -> NativeReceiverSnapshot = {
            try NativeReceiverSnapshot.read(application: $0, window: $1)
        }
        var set: @MainActor (AXUIElement, CFString, CFTypeRef) -> AXError = AXMessaging.setAttributeValue
        var wait: @MainActor () async throws -> Void = { try await Task.sleep(for: .milliseconds(25)) }
    }

    static func snapshot(
        element: AXUIElement, application: NSRunningApplication, window: AXUIElement,
        expected: Unit.Contract?, api: API
    ) throws -> Unit.Snapshot? {
        try RequestDiagnostics.checkCancellation()
        func link(_ element: AXUIElement, _ key: String) -> AXUIElement? {
            guard let raw = api.read(element, key), CFGetTypeID(raw) == AXUIElementGetTypeID() else { return nil }
            return unsafeDowncast(raw, to: AXUIElement.self)
        }
        func index(_ element: AXUIElement, _ key: String) -> Int? {
            guard let raw = api.read(element, key), CFGetTypeID(raw) == AXValueGetTypeID() else { return nil }
            var range = CFRange()
            guard AXValueGetValue(unsafeDowncast(raw, to: AXValue.self), .cfRange, &range),
                range.location >= 0, range.location != NSNotFound, range.length == 1
            else { return nil }
            return range.location
        }
        func node(_ element: AXUIElement) -> Unit.Node {
            .init(element: element, pid: api.pid(element), role: api.read(element, kAXRoleAttribute) as? String,
                  parent: link(element, kAXParentAttribute), window: link(element, kAXWindowAttribute),
                  enabled: api.read(element, kAXEnabledAttribute) as? Bool,
                  editable: api.read(element, "AXEditable") as? Bool, readOnly: api.read(element, "AXReadOnly") as? Bool,
                  focused: api.read(element, kAXFocusedAttribute) as? Bool,
                  selected: api.read(element, kAXSelectedAttribute) as? Bool,
                  rowIndex: index(element, "AXRowIndexRange"), columnIndex: index(element, "AXColumnIndexRange"))
        }
        func complete(_ element: AXUIElement, _ key: String, count: Int?) -> [AXUIElement]? {
            guard let count, count <= 64, let values = api.read(element, key) as? [AXUIElement],
                values.count == count
            else { return nil }
            return values
        }
        let cell = node(element)
        guard let rowElement = cell.parent, let rowIndex = cell.rowIndex, let columnIndex = cell.columnIndex else { return nil }
        let row = node(rowElement)
        guard let tableElement = row.parent else { return nil }
        let table = node(tableElement)
        guard let regionElement = table.parent else { return nil }
        let region = node(regionElement)
        let contract = Unit.Contract(cell: element, row: rowElement, table: tableElement, region: regionElement,
                                     window: window, pid: application.processIdentifier,
                                     rowIndex: rowIndex, columnIndex: columnIndex)
        guard expected == nil || expected == contract, api.pid(window) == application.processIdentifier else { return nil }
        let count = api.count(tableElement, "AXSelectedCells")
        let selected = complete(tableElement, "AXSelectedCells", count: count)
        // Do not adopt this object. An invalid proxy is unknown; a readable
        // other cell/window is a contradiction to the retained original's selection.
        var knownOther = false
        if let selected, selected.count == 1, !CFEqual(selected[0], element) {
            let other = node(selected[0])
            knownOther = other.role.map { $0 != "AXCell" } == true
                || other.rowIndex.map { $0 != rowIndex } == true
                || other.columnIndex.map { $0 != columnIndex } == true
                || other.window.map { !CFEqual($0, window) } == true
                || other.pid.map { $0 != application.processIdentifier } == true
            if let parent = other.parent, api.read(parent, kAXRoleAttribute) as? String == kAXRowRole,
                let otherTable = link(parent, kAXParentAttribute), !CFEqual(otherTable, tableElement)
            { knownOther = true }
        }
        let state = try api.receiver(application, window)
        try RequestDiagnostics.checkCancellation()
        return Unit.Snapshot(
            contract: contract, cell: cell, row: row, table: table, region: region, state: state,
            parameterizedCell: api.cell(tableElement, columnIndex, rowIndex),
            selectionWritable: api.writable(tableElement, "AXSelectedCells"), selectionCount: count,
            selection: selected, regionSelection: complete(regionElement, kAXSelectedChildrenAttribute,
                                                          count: api.count(regionElement, kAXSelectedChildrenAttribute)),
            knownOtherSelectedCell: knownOther)
    }

}
