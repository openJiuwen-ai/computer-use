// Copyright (c) Huawei Technologies Co., Ltd. 2026. All rights reserved.

import AppKit
import ApplicationServices

/// One selected cell can open a native formula editor after the first click.
/// The second click keeps its screen point and process-addressed transport.
@MainActor struct NativeCellPointerContinuation {
    private let cell: AXUIElement
    private let row: AXUIElement
    private let table: AXUIElement
    private let window: AXUIElement
    private let geometry: NativeElementPointerUnit.Geometry
    private let process: NativeProcessIdentity
    private let rowIndex: Int
    private let columnIndex: Int
    private let initialWindows: Set<CGWindowID>

    init?(cell: AXUIElement, window: AXUIElement, geometry: NativeElementPointerUnit.Geometry) throws {
        try RequestDiagnostics.checkCancellation()
        let mapping = geometry.mapping
        guard Self.role(cell) == kAXCellRole, AXAccess.read(cell, kAXSelectedAttribute).value as? Bool == true,
            let row = AXAccess.element(cell, kAXParentAttribute), Self.role(row) == kAXRowRole,
            let table = AXAccess.element(row, kAXParentAttribute), Self.role(table) == kAXTableRole,
            Self.soleSelection(in: table), try Self.attaches(table, to: window),
            let rowIndex = Self.index(cell, "AXRowIndexRange"), let columnIndex = Self.index(cell, "AXColumnIndexRange"),
            BackgroundEvents.accessibilityProcessID(cell) == mapping.pid,
            let process = NativeProcessIdentity.read(mapping.pid),
            let records = CGWindowListCopyWindowInfo([.optionAll, .excludeDesktopElements], kCGNullWindowID)
                as? [[String: Any]]
        else { return nil }
        let ids = records.filter { ($0[kCGWindowOwnerPID as String] as? NSNumber)?.int32Value == mapping.pid }
            .map { ($0[kCGWindowNumber as String] as? NSNumber)?.uint32Value }
        guard ids.allSatisfy({ $0 != nil }), ids.contains(mapping.id) else { return nil }
        self.cell = cell
        self.row = row
        self.table = table
        self.window = window
        self.geometry = geometry
        self.process = process
        self.rowIndex = rowIndex
        self.columnIndex = columnIndex
        initialWindows = Set(ids.compactMap { $0 })
    }

    func resolve(at point: CGPoint) throws -> WindowGeometry.WindowMapping? {
        try RequestDiagnostics.checkCancellation()
        guard point == geometry.point, geometry.frame.contains(point),
            NativeProcessIdentity.read(process.pid) == process
        else { return nil }
        let root = AXUIElementCreateApplication(process.pid)
        AXUIElementSetMessagingTimeout(root, 0.05)
        guard let editor = AXAccess.element(root, kAXFocusedUIElementAttribute),
            Self.role(editor) == kAXTextAreaRole,
            AXAccess.read(editor, kAXSubroleAttribute).value as? String == "AXFormulaTextArea",
            let child = AXAccess.element(editor, kAXWindowAttribute),
            Self.role(child) == kAXWindowRole,
            AXAccess.read(child, kAXSubroleAttribute).value as? String == "AXFormulaEditingWindow",
            AXAccess.read(child, kAXModalAttribute).value as? Bool == false,
            let childID = BackgroundEvents.nativeWindowID(child), !initialWindows.contains(childID),
            NativeWindowParents.read([childID, geometry.mapping.id])?[childID] == geometry.mapping.id,
            BackgroundEvents.accessibilityProcessID(editor) == process.pid,
            BackgroundEvents.accessibilityProcessID(child) == process.pid,
            AXAccess.read(editor, kAXFocusedAttribute).value as? Bool == true,
            AXAccess.read(editor, kAXEnabledAttribute).value as? Bool == true,
            AXAccess.read(editor, "AXEditable").value as? Bool != false,
            WindowGeometry.hint(editor)?.frame?.contains(point) == true, try Self.attaches(editor, to: child)
        else { return nil }

        // Provider proxies may be recycled. Keep the exact original cell's
        // table, indexes and geometry, with the original window still live.
        guard Self.role(cell) == kAXCellRole, Self.role(row) == kAXRowRole, Self.role(table) == kAXTableRole,
            AXAccess.read(cell, kAXSelectedAttribute).value as? Bool == true, Self.soleSelection(in: table),
            AXAccess.element(cell, kAXParentAttribute).map({ CFEqual($0, row) }) == true,
            AXAccess.element(row, kAXParentAttribute).map({ CFEqual($0, table) }) == true,
            AXAccess.element(cell, kAXWindowAttribute).map({ CFEqual($0, window) }) == true,
            try Self.attaches(table, to: window),
            BackgroundEvents.accessibilityProcessID(cell) == process.pid,
            BackgroundEvents.accessibilityProcessID(table) == process.pid,
            Self.index(cell, "AXRowIndexRange") == rowIndex, Self.index(cell, "AXColumnIndexRange") == columnIndex,
            WindowGeometry.hint(cell)?.frame == geometry.frame,
            BackgroundEvents.nativeWindowID(window) == geometry.mapping.id,
            WindowGeometry.hint(window)?.frame == geometry.mapping.frame,
            let original = WindowServerInfo.window(geometry.mapping.id),
            (original[kCGWindowOwnerPID as String] as? NSNumber)?.int32Value == process.pid,
            let record = WindowServerInfo.window(childID), let bounds = record[kCGWindowBounds as String] as? [String: Any],
            (record[kCGWindowOwnerPID as String] as? NSNumber)?.int32Value == process.pid,
            let frame = CGRect(dictionaryRepresentation: bounds as CFDictionary), frame.contains(point),
            WindowGeometry.hint(child)?.frame == frame,
            let width = Int(exactly: frame.width.rounded(.up)), let height = Int(exactly: frame.height.rounded(.up))
        else { return nil }

        var hit: AXUIElement?
        let status = AXUIElementCopyElementAtPosition(root, Float(point.x), Float(point.y), &hit)
        try RequestDiagnostics.checkCancellation()
        guard status == .success, hit.map({ CFEqual($0, editor) }) == true,
            AXAccess.element(root, kAXFocusedUIElementAttribute).map({ CFEqual($0, editor) }) == true,
            AXAccess.read(editor, kAXFocusedAttribute).value as? Bool == true,
            NativeProcessIdentity.read(process.pid) == process
        else { return nil }
        return .init(id: childID, pid: process.pid, frame: frame, title: nil, width: width, height: height)
    }

    private static func attaches(_ element: AXUIElement, to window: AXUIElement) throws -> Bool {
        var current = element, visited = [AXUIElement]()
        for _ in 0..<16 {
            try RequestDiagnostics.checkCancellation()
            if CFEqual(current, window) { return true }
            guard !visited.contains(where: { CFEqual($0, current) }),
                let role = role(current), ![kAXWindowRole, kAXSheetRole, kAXApplicationRole, "AXWebArea"].contains(role),
                let parent = AXAccess.element(current, kAXParentAttribute)
            else { return false }
            visited.append(current)
            current = parent
        }
        return false
    }

    private static func role(_ object: AXUIElement) -> String? {
        AXAccess.read(object, kAXRoleAttribute).value as? String
    }

    private static func soleSelection(in table: AXUIElement) -> Bool {
        var count: CFIndex = 0
        return AXUIElementGetAttributeValueCount(table, "AXSelectedCells" as CFString, &count) == .success && count == 1
    }

    private static func index(_ cell: AXUIElement, _ key: String) -> Int? {
        guard let raw = AXAccess.read(cell, key).value, CFGetTypeID(raw) == AXValueGetTypeID() else { return nil }
        var range = CFRange()
        guard AXValueGetValue(unsafeDowncast(raw, to: AXValue.self), .cfRange, &range),
            range.location >= 0, range.location != NSNotFound, range.length == 1
        else { return nil }
        return range.location
    }
}
