// Copyright (c) Huawei Technologies Co., Ltd. 2026. All rights reserved.

import AppKit
import CoreGraphics
@preconcurrency import ScreenCaptureKit

/// Desktop observation does not traverse AX, select an application or change
/// focus. Retained mappings only translate image pixels to screen points.
@MainActor final class DesktopCapture {
    private let applications: ApplicationDiscovery
    init(applications: ApplicationDiscovery) { self.applications = applications }
    struct Mapping {
        let geometry: DesktopGeometry
        let captured: Set<CGDirectDisplayID>
    }
    struct Surface: Equatable {
        let id: CGWindowID
        let pid: pid_t
        let frame: CGRect
    }
    let scope = UUID().uuidString
    private var mappings = [String: Mapping]()
    private var order = [String]()
    private static let background = CGColor(gray: 0.18, alpha: 1)

    static func geometry(maxDimension: Int = 4096) throws -> DesktopGeometry {
        var count: UInt32 = 0
        guard CGGetActiveDisplayList(0, nil, &count) == .success, count > 0, count <= 32 else {
            throw DriverError("active desktop displays unavailable", code: .desktopUnavailable)
        }
        var ids = [CGDirectDisplayID](repeating: 0, count: Int(count))
        guard CGGetActiveDisplayList(count, &ids, &count) == .success else {
            throw DriverError("cannot enumerate displays", code: .desktopUnavailable)
        }
        let displays = try ids.prefix(Int(count)).filter { CGDisplayMirrorsDisplay($0) == kCGNullDirectDisplay }.map {
            id in
            guard let mode = CGDisplayCopyDisplayMode(id) else {
                throw DriverError("display mode unavailable", code: .desktopUnavailable)
            }
            return DesktopGeometry.Display(
                id: id, frame: CGDisplayBounds(id), pixelsWide: mode.pixelWidth, pixelsHigh: mode.pixelHeight,
                rotation: CGDisplayRotation(id))
        }
        return try DesktopGeometry(displays, maxDimension: maxDimension)
    }

    func observed(_ ref: String) throws -> Mapping {
        guard let value = mappings[ref] else {
            throw DriverError("desktop observation expired", code: .staleTarget)
        }
        return value
    }

    private static func recipient() -> NativeProcessIdentity? {
        NSWorkspace.shared.frontmostApplication.flatMap { NativeProcessIdentity.read($0.processIdentifier) }
    }

    static func surfaces(_ raw: [[String: Any]]) -> [Surface] {
        raw.compactMap { item in
            guard !AgentPointer.isDecoration(item), (item[kCGWindowAlpha as String] as? NSNumber)?.doubleValue ?? 1 > 0,
                let id = (item[kCGWindowNumber as String] as? NSNumber)?.uint32Value,
                let pid = (item[kCGWindowOwnerPID as String] as? NSNumber)?.int32Value,
                let rect = item[kCGWindowBounds as String] as? [String: Any],
                let frame = CGRect(dictionaryRepresentation: rect as CFDictionary), frame.width > 0, frame.height > 0
            else { return nil }
            return Surface(id: id, pid: pid, frame: frame)
        }
    }

    private func rectangle(_ rect: CGRect) -> [String: Double] {
        ["x": rect.minX, "y": rect.minY, "width": rect.width, "height": rect.height]
    }

    private static func visibleWindows(_ raw: [[String: Any]], geometry: DesktopGeometry? = nil) -> [[String: Any]] {
        raw.filter { item in
            guard !AgentPointer.isDecoration(item), (item[kCGWindowAlpha as String] as? NSNumber)?.doubleValue ?? 1 > 0,
                let rawRect = item[kCGWindowBounds as String] as? [String: Any],
                let rect = CGRect(dictionaryRepresentation: rawRect as CFDictionary), rect.width > 0, rect.height > 0
            else { return false }
            return geometry?.displays.contains(where: { $0.frame.intersects(rect) }) ?? true
        }
    }

    /// The image and metadata requests share only window discovery and identity
    /// retention. The latter never prepares capture or creates a pixel mapping.
    private func windowRows(
        _ raw: [[String: Any]], geometry: DesktopGeometry? = nil, appName: String? = nil,
        frontmost: NSRunningApplication? = nil,
        retainWindow: (NativeWindows.Window, NSRunningApplication) -> [String: Any]
    ) throws -> (windows: [[String: Any]], omitted: Int, unresolved: Bool) {
        let nativeWindows = Dictionary(
            NativeWindows.catalogue(raw).map { ($0.mapping.id, $0) }, uniquingKeysWith: { first, _ in first })
        var owners = [pid_t: NSRunningApplication]()
        var windows = [[String: Any]](), omitted = 0, unresolved = false
        for item in raw {
            try RequestDiagnostics.checkCancellation()
            let pid = (item[kCGWindowOwnerPID as String] as? NSNumber)?.int32Value
            let id = (item[kCGWindowNumber as String] as? NSNumber)?.uint32Value
            let native = id.flatMap { nativeWindows[$0] }
            let app: NSRunningApplication?
            if let native {
                let owner = owners[native.process.pid] ?? NativeWindows.discoveryOwner(native)
                owners[native.process.pid] = owner
                app = owner
            } else { app = pid.flatMap { NSRunningApplication(processIdentifier: $0) } }
            let name = app?.localizedName ?? item[kCGWindowOwnerName as String] as? String ?? "Unknown application"
            if let appName {
                guard name.utf8.elementsEqual(appName.utf8) else { continue }
                guard let app, let native else {
                    unresolved = true
                    continue
                }
                guard windows.count < 256 else {
                    omitted += 1
                    continue
                }
                guard let selector = applications.selector(for: app) else {
                    unresolved = true
                    continue
                }
                windows.append([
                    "selector": selector, "window": retainWindow(native, app),
                    "appIsForeground": frontmost.map { app.isEqual($0) } as Any? ?? NSNull(),
                ])
            } else if let geometry {
                // Image rows include non-selectable windows for hit testing.
                let rect = CGRect(dictionaryRepresentation: item[kCGWindowBounds as String] as! CFDictionary)!
                var row: [String: Any] = [
                    "app": String(name.prefix(512)),
                    "title": String((item[kCGWindowName as String] as? String ?? "").prefix(512)),
                    "rect": rectangle(geometry.rect(rect.intersection(geometry.bounds)))
                ]
                if let app, let selector = applications.selector(for: app) {
                    row["selector"] = selector
                    if let native { row["window"] = retainWindow(native, app) }
                }
                windows.append(row)
            }
        }
        return (windows, omitted, unresolved)
    }

    func windowCatalogue(
        appName: String, retainWindow: (NativeWindows.Window, NSRunningApplication) -> [String: Any]
    ) throws -> (windows: [[String: Any]], stopReasons: [String]) {
        try BackgroundTarget.validateDesktop()
        let frontmost = NSWorkspace.shared.frontmostApplication
        guard let raw = CGWindowListCopyWindowInfo([.optionOnScreenOnly, .excludeDesktopElements], kCGNullWindowID)
            as? [[String: Any]]
        else { return ([], ["WindowServer catalogue unavailable"]) }
        let result = try windowRows(
            Self.visibleWindows(raw), appName: appName, frontmost: frontmost, retainWindow: retainWindow)
        var reasons = [String]()
        if result.omitted > 0 { reasons.append("\(result.omitted) matching windows exceeded the catalogue limit") }
        if result.unresolved { reasons.append("Some matching windows have no confirmed native identity") }
        return (result.windows, reasons)
    }

    func read(maxDimension: Int = 4096, retainedCaptures: Set<String> = [], retainWindow: (NativeWindows.Window, NSRunningApplication) -> [String: Any]) async throws -> [String: Any] {
        AgentPointer.beginDesktopCapture()
        defer { AgentPointer.endDesktopCapture() }
        try RequestDiagnostics.checkObservationBudget()
        try BackgroundTarget.validateDesktop()
        let geometry = try Self.geometry(maxDimension: maxDimension)
        let beforeRecipient = Self.recipient()
        let beforeWindows =
            CGWindowListCopyWindowInfo([.optionOnScreenOnly, .excludeDesktopElements], kCGNullWindowID)
            as? [[String: Any]]
        let permission = CGPreflightScreenCaptureAccess()
        var failures = [CGDirectDisplayID: String](), captured = Set<CGDirectDisplayID>()
        var screenshot: CapturedImage?
        let interval = CaptureInterval()
        if permission {
            do {
                try RequestDiagnostics.checkObservationBudget()
                let content = try await SCShareableContent.excludingDesktopWindows(false, onScreenWindowsOnly: true)
                try RequestDiagnostics.checkObservationBudget()
                guard
                    let canvas = CGContext(
                        data: nil, width: geometry.width, height: geometry.height, bitsPerComponent: 8, bytesPerRow: 0,
                        space: CGColorSpaceCreateDeviceRGB(), bitmapInfo: CGImageAlphaInfo.premultipliedLast.rawValue)
                else { throw DriverError("cannot allocate desktop canvas") }
                canvas.setFillColor(Self.background)
                canvas.fill(CGRect(x: 0, y: 0, width: geometry.width, height: geometry.height))
                for display in geometry.displays {
                    try RequestDiagnostics.checkObservationBudget()
                    do {
                        guard let native = content.displays.first(where: { $0.displayID == display.id }),
                            native.frame == display.frame
                        else { throw DriverError("display is absent or changed in capture service") }
                        let rect = geometry.rect(display.frame)
                        let configuration = SCStreamConfiguration()
                        configuration.width = max(1, Int(ceil(rect.width)))
                        configuration.height = max(1, Int(ceil(rect.height)))
                        configuration.showsCursor = false
                        configuration.scalesToFit = true
                        // Only our click-through decoration is absent from both
                        // pixels and hit-test catalogues. Real occlusion, popups
                        // and interactive preview retain their existing policy.
                        let decorations = content.windows.filter { window in
                            AgentPointer.isDecoration(
                                id: window.windowID, pid: window.owningApplication?.processID ?? 0, title: window.title)
                        }
                        let filter = SCContentFilter(display: native, excludingWindows: decorations)
                        if #available(macOS 14.2, *) { filter.includeMenuBar = true }
                        try RequestDiagnostics.checkObservationBudget()
                        let image = try await SCScreenshotManager.captureImage(
                            contentFilter: filter, configuration: configuration)
                        try RequestDiagnostics.checkObservationBudget()
                        guard image.width == configuration.width, image.height == configuration.height else {
                            throw DriverError("unexpected display image dimensions")
                        }
                        canvas.draw(
                            image,
                            in: CGRect(
                                x: rect.minX, y: Double(geometry.height) - rect.maxY, width: rect.width,
                                height: rect.height))
                        captured.insert(display.id)
                    } catch {
                        try RequestDiagnostics.rethrowCancellation(error)
                        if error is ObservationBudgetExceeded { throw error }
                        try RequestDiagnostics.checkObservationBudget()
                        failures[display.id] = String(describing: error)
                    }
                }
                if !captured.isEmpty {
                    try RequestDiagnostics.checkObservationBudget()
                    guard let image = canvas.makeImage() else { throw DriverError("cannot render desktop canvas") }
                    let png = try CapturedImage.encodePNG(
                        image, creationFailure: "cannot encode desktop PNG",
                        finalizationFailure: "cannot encode desktop PNG")
                    screenshot = CapturedImage(png: png, width: image.width, height: image.height, interval: interval)
                }
            } catch {
                try RequestDiagnostics.rethrowCancellation(error)
                // Optional capture may exhaust Dc while the request still has
                // time to validate and return its desktop catalogue under D.
                // Never start another display capture or publish partial pixels.
                for display in geometry.displays { failures[display.id] = String(describing: error) }
                captured.removeAll()
                screenshot = nil
            }
        } else {
            for display in geometry.displays { failures[display.id] = "screen recording permission denied" }
        }
        try RequestDiagnostics.checkCancellation()
        try BackgroundTarget.validateDesktop()
        guard try Self.geometry(maxDimension: maxDimension) == geometry else {
            throw ObservationChanged("desktop layout changed during capture")
        }
        let frontmost = NSWorkspace.shared.frontmostApplication
        let rawWindows =
            CGWindowListCopyWindowInfo([.optionOnScreenOnly, .excludeDesktopElements], kCGNullWindowID)
            as? [[String: Any]]
        let recipient = Self.recipient()
        var changes = [String]()
        if beforeRecipient != recipient { changes.append("foreground application changed") }
        if let beforeWindows, let rawWindows {
            if Self.surfaces(beforeWindows) != Self.surfaces(rawWindows) { changes.append("windows moved or changed") }
        } else {
            changes.append("window catalogue unavailable")
        }
        // Seeing a changing desktop is useful, especially for understanding a
        // covering popup. Retain pixels and describe the uncertainty; a
        // screenshot does not grant or withhold permission for later input.
        let inputIssue =
            changes.isEmpty
            ? nil
            : """
            Desktop changed: \(changes.joined(separator: "; ")) during capture. \
            Window catalogue describes the end of capture and may differ from the image.
            """
        let visibleWindows = Self.visibleWindows(rawWindows ?? [], geometry: geometry)
        let catalogue = try windowRows(Array(visibleWindows.prefix(256)), geometry: geometry, retainWindow: retainWindow)
        let displays: [[String: Any]] = geometry.displays.enumerated().map { index, display in
            var item: [String: Any] = [
                "name": "Display \(index + 1)", "rect": rectangle(geometry.rect(display.frame)),
                "captured": captured.contains(display.id)
            ]
            if let error = failures[display.id] { item["error"] = String(error.prefix(1024)) }
            return item
        }
        var observation: [String: Any] = [
            "canvas": ["width": geometry.width, "height": geometry.height], "displays": displays,
            "windows": catalogue.windows, "omittedWindows": max(0, visibleWindows.count - 256)
        ]
        if let frontmost {
            var app: [String: Any] = ["name": frontmost.localizedName ?? "Unknown application"]
            if let selector = applications.selector(for: frontmost) { app["selector"] = selector }
            observation["frontmost"] = app
        } else {
            observation["frontmost"] = NSNull()
        }
        var catalogueNotes = [String]()
        if let inputIssue { catalogueNotes.append(inputIssue) }
        if !permission { catalogueNotes.append("window titles may be unavailable without screen recording permission") }
        if !catalogueNotes.isEmpty { observation["catalogError"] = catalogueNotes.joined(separator: " ") }
        // Pixels and their retained mapping have one opaque identity. A
        // catalogue-only observation still has a reference but no image.
        let ref = screenshot?.id ?? UUID().uuidString
        try RequestDiagnostics.checkCancellation()
        mappings[ref] = Mapping(geometry: geometry, captured: captured)
        order.append(ref)
        let expired = NativeCapture.retirements(order, retaining: retainedCaptures)
        order.removeAll { expired.contains($0) }
        for ref in expired {
            mappings.removeValue(forKey: ref)
        }
        return [
            "desktopRef": ref, "scope": scope, "observation": observation,
            "screenshot": screenshot?.protocolValue(kind: .desktop, scope: scope) as Any? ?? NSNull()
        ]
    }
}
