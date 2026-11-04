// Copyright (c) Huawei Technologies Co., Ltd. 2026. All rights reserved.

import AppKit
import ApplicationServices

/// Identity retained with a window image. It validates that image's document,
/// owner and geometry; input routing is owned by the current execution units.
@MainActor final class CapturedWindowIdentity {
    private var nativeWindow: AXUIElement?
    private var document: String?

    func observeCapturedWindow(_ mapping: WindowGeometry.WindowMapping, target: AXUIElement?) {
        let window = target.flatMap { WindowGeometry.matches($0, mapping) ? $0 : nil }
        // A retry for the same object must not accept a different document.
        if let window, let nativeWindow, CFEqual(window, nativeWindow) { return }
        nativeWindow = window
        document = WindowGeometry.documentIdentity(window)
    }

    func validateObservation() throws {
        let metric = RequestDiagnostics.metrics?.start(.documentValidation)
        defer { metric?.end() }
        if let document, WindowGeometry.documentIdentity(nativeWindow) != document {
            throw DriverError(
                "the observed window now represents another document or its identity is unavailable",
                code: .staleTarget, next: "read this window again before input")
        }
    }

    func validate(_ mapping: WindowGeometry.WindowMapping) throws {
        try validateObservation()
        guard try currentMapping(mapping).frame == mapping.frame else {
            throw ObservationChanged("window disappeared or geometry changed")
        }
    }

    /// QUERY: retain the image's pixel extent, resolving only the current
    /// translation of this same window. Capture-time validation stays strict.
    func inputMapping(_ mapping: WindowGeometry.WindowMapping) throws -> WindowGeometry.WindowMapping {
        try validateObservation()
        return try NativeCapturedPointerPlan.resolve(original: mapping, current: currentMapping(mapping)).mapping
    }

    /// The surrounding boundary owns document/scope validation. Before down,
    /// keep the resolved frame; during its held drag, retain identity and return
    /// the current origin solely for window-local event addressing.
    @discardableResult func validateGeometry(
        _ mapping: WindowGeometry.WindowMapping, continuingDrag: Bool = false
    ) throws -> WindowGeometry.WindowMapping {
        let current = try currentMapping(mapping)
        try NativeCapturedPointerPlan(mapping: mapping).requireCurrent(current, continuingDrag: continuingDrag)
        return current
    }

    private func currentMapping(_ mapping: WindowGeometry.WindowMapping) throws -> WindowGeometry.WindowMapping {
        let metric = RequestDiagnostics.metrics?.start(.windowValidation)
        defer { metric?.end() }
        guard let window = WindowServerInfo.window(mapping.id),
            let pid = (window[kCGWindowOwnerPID as String] as? NSNumber)?.int32Value, pid == mapping.pid
        else { throw ObservationChanged("window disappeared or owner changed") }
        NativePointerTiming.record("captureGeometrySample", metadata: {
            ["window": mapping.id, "targetPID": mapping.pid,
             "retainedFrame": [mapping.frame.minX, mapping.frame.minY, mapping.frame.width, mapping.frame.height],
             "sampledBounds": window[kCGWindowBounds as String] as Any? ?? NSNull()]
        })
        guard let rawBounds = window[kCGWindowBounds as String] as? [String: Any],
            let bounds = CGRect(dictionaryRepresentation: rawBounds as CFDictionary),
            [bounds.minX, bounds.minY, bounds.width, bounds.height].allSatisfy(\.isFinite),
            bounds.width > 0, bounds.height > 0
        else { throw ObservationChanged("window disappeared or geometry changed") }
        return .init(
            id: mapping.id, pid: pid, frame: bounds, title: mapping.title, width: mapping.width, height: mapping.height)
    }
}
