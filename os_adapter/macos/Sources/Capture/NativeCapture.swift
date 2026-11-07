// Copyright (c) Huawei Technologies Co., Ltd. 2026. All rights reserved.

import AppKit
import ApplicationServices

/// Pixel captures and their immutable coordinate mappings are independent of
/// AX nodes and input receiver preparation.
@MainActor final class NativeCapture {
    struct Entry {
        let binding: [String: Any]
        let surface: NativeDirectory.Surface
        let screen: WindowCapture
        let mapping: WindowGeometry.WindowMapping
        // Keep the capture's original declarations, including explicit absence.
        // A later failed getter must not reclassify a known document as a panel.
        // These raw facts are private to the retained image; no AX completeness
        // requirement is added to pixel capture or its public result.
        let windowAttributes: [String: AXRead<CFTypeRef>]
    }
    struct Input {
        let entry: Entry
        let mapping: WindowGeometry.WindowMapping
        let blocked: [String]
    }
    private let directory: NativeDirectory
    private let sharing: WindowSharing
    private var entries = [String: Entry]()
    private var order = [String]()
    private var retainedReferences = Set<String>()

    /// A delivered image remains usable while the SDK still presents its map.
    /// The ordinary history budget applies only to unreferenced observations;
    /// reading another window must not expire an unchanged delivered image.
    nonisolated static func retirements(
        _ order: [String], retaining references: Set<String>, historyLimit: Int = 64
    ) -> Set<String> {
        let unreferenced = order.filter { !references.contains($0) }
        return Set(unreferenced.prefix(max(0, unreferenced.count - historyLimit)))
    }

    init(directory: NativeDirectory, sharing: WindowSharing) {
        self.directory = directory
        self.sharing = sharing
    }

    func retained(_ id: String) throws -> Entry {
        guard let entry = entries[id] else {
            throw DriverError("image coordinate mapping expired; capture this window again", code: .snapshotExpired)
        }
        return entry
    }

    private func bindingScope(_ binding: [String: Any]) throws -> (entry: Entry, blocked: [String]) {
        let entry = try retained(string(binding["capture"], label: "capture"))
        guard NSDictionary(dictionary: entry.binding).isEqual(to: binding) else {
            throw DriverError("capture belongs to another input binding", code: .staleTarget)
        }
        let current = try directory.inputWindowScope(
            instance: string(binding["instance"], label: "instance"),
            root: string(binding["rootWindow"], label: "rootWindow"),
            surface: string(binding["surface"], label: "surface"))
        try directory.validateWindow(entry.surface)
        return (entry, current.blocked)
    }

    /// Scope checks do not adopt new geometry for an already-started gesture.
    func inputScope(_ binding: [String: Any]) throws -> (entry: Entry, blocked: [String]) {
        let current = try bindingScope(binding)
        try current.entry.screen.validateInputScope()
        return current
    }

    func input(_ binding: [String: Any]) throws -> Input {
        let current = try bindingScope(binding)
        let mapping = try current.entry.screen.inputMapping(current.entry.mapping)
        return Input(entry: current.entry, mapping: mapping, blocked: current.blocked)
    }

    func capture(
        binding: [String: Any], maxDimension: Int, retainedCaptures: Set<String>? = nil
    ) async throws -> [String: Any] {
        let instance = try string(binding["instance"], label: "instance")
        let root = try string(binding["rootWindow"], label: "rootWindow")
        let scope = try string(binding["surface"], label: "surface")
        let interval = CaptureInterval()
        let selected = try directory.windowScope(instance: instance, root: root, surface: scope).surface
        let scopeBefore = directory.windowScopeEpoch(selected.owner)
        let screen = WindowCapture(sharing: sharing)
        let native = selected.native
        let mapping: WindowGeometry.WindowMapping?
        if let native {
            // Refresh geometry for this new image only. Earlier captures keep
            // their own mapping, and are never repaired by this observation.
            guard let info = WindowServerInfo.window(native.mapping.id),
                let raw = info[kCGWindowBounds as String] as? [String: Any],
                let frame = CGRect(dictionaryRepresentation: raw as CFDictionary), frame.minX.isFinite,
                frame.minY.isFinite, frame.width.isFinite, frame.height.isFinite, frame.width > 1, frame.height > 1
            else { throw DriverError("native window capture geometry is unavailable", code: .observationIncomplete) }
            mapping = .init(
                id: native.mapping.id, pid: native.process.pid, frame: frame,
                title: info[kCGWindowName as String] as? String, width: Int(frame.width.rounded(.up)),
                height: Int(frame.height.rounded(.up)))
        } else {
            mapping = nil
        }
        let captured = try await screen.captureWindow(
            native?.application ?? selected.owner, target: selected.element, nativeTarget: mapping,
            maxDimension: maxDimension)
        // Capture can await the system. Revalidate lifetimes before publishing
        // its pixels; a title/focus change is not a reason to change the target.
        _ = try directory.windowScope(instance: instance, root: root, surface: scope)
        try screen.validate(captured.window)
        try RequestDiagnostics.checkCancellation()
        var imageBinding = binding
        imageBinding["capture"] = captured.image.id
        let attributes =
            selected.element.map { element in
                [
                    kAXRoleAttribute: AXAccess.read(element, kAXRoleAttribute),
                    kAXSubroleAttribute: AXAccess.read(element, kAXSubroleAttribute)
                ]
            } ?? [:]
        try RequestDiagnostics.checkCancellation()
        entries[captured.image.id] = Entry(
            binding: imageBinding, surface: selected, screen: screen, mapping: captured.window,
            windowAttributes: attributes)
        order.append(captured.image.id)
        if let retainedCaptures {
            // Only already-issued native references can hold mappings alive.
            // An empty current delivery set explicitly releases earlier pins.
            retainedReferences = retainedCaptures.intersection(entries.keys)
        }
        let retired = Self.retirements(order, retaining: retainedReferences)
        order.removeAll { retired.contains($0) }
        for id in retired { entries.removeValue(forKey: id) }
        let frame = captured.window.frame
        var result: [String: Any] = [
            "kind": "capture", "binding": binding, "observed": interval.observation(),
            "image": captured.image.protocolValue(kind: .window, scope: scope),
            "coordinates": [
                "space": "window-pixels", "origin": "top-left", "nativeSpace": "screen-points",
                "toNative": [
                    frame.width / Double(captured.image.width), 0, 0, frame.height / Double(captured.image.height),
                    frame.minX, frame.minY
                ]
            ]
        ]
        if let scopeBefore, let current = directory.windowScopeEpoch(selected.owner),
            current.incarnation == scopeBefore.incarnation, current.epoch > scopeBefore.epoch {
            result["windowScopeChanged"] = true
        }
        return result
    }
}
