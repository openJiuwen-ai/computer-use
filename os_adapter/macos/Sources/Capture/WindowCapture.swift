// Copyright (c) Huawei Technologies Co., Ltd. 2026. All rights reserved.

import AppKit
import ApplicationServices
@preconcurrency import ScreenCaptureKit

@MainActor final class WindowCapture {
    typealias WindowMapping = WindowGeometry.WindowMapping
    private let observation = CapturedWindowIdentity()
    private let sharing: WindowSharing?
    init(sharing: WindowSharing? = nil) { self.sharing = sharing }

    /// A successful window capture can contain no visible pixels. Check only
    /// formats whose alpha can be preserved exactly in an 8-bit alpha bitmap.
    static func requireVisiblePixels(_ image: CGImage) throws {
        guard !image.isMask, image.bitsPerComponent == 8, !image.bitmapInfo.contains(.floatComponents) else { return }
        switch image.alphaInfo {
        case .first, .last, .premultipliedFirst, .premultipliedLast, .alphaOnly: break
        default: return
        }
        guard let alpha = CGContext(
            data: nil, width: image.width, height: image.height, bitsPerComponent: 8, bytesPerRow: 0,
            space: CGColorSpaceCreateDeviceGray(), bitmapInfo: CGImageAlphaInfo.alphaOnly.rawValue
        ), let data = alpha.data else { return }
        alpha.setBlendMode(.copy)
        alpha.interpolationQuality = .none
        alpha.draw(image, in: CGRect(x: 0, y: 0, width: image.width, height: image.height))
        let bytes = data.assumingMemoryBound(to: UInt8.self)
        for row in 0..<image.height {
            let pixels = UnsafeBufferPointer(start: bytes + row * alpha.bytesPerRow, count: image.width)
            if pixels.contains(where: { $0 != 0 }) { return }
        }
        throw DriverError("window screenshot contains no visible pixels", code: .observationIncomplete)
    }

    func validate(_ mapping: WindowMapping) throws { try observation.validate(mapping) }

    func validateInputScope() throws { try observation.validateObservation() }

    func inputMapping(_ mapping: WindowMapping) throws -> WindowMapping { try observation.inputMapping(mapping) }

    @discardableResult func validateInputGeometry(
        _ mapping: WindowMapping, continuingDrag: Bool = false
    ) throws -> WindowMapping {
        try observation.validateGeometry(mapping, continuingDrag: continuingDrag)
    }

    func captureWindow(
        _ app: NSRunningApplication, target: AXUIElement? = nil, nativeTarget: WindowMapping? = nil,
        maxDimension: Int? = nil
    ) async throws -> (image: CapturedImage, window: WindowMapping) {
        let metric = RequestDiagnostics.metrics?.start(.windowCapture)
        defer { metric?.end() }
        let queryTiming = NativeWindowQueryTiming.start(enabled: RequestDiagnostics.metrics != nil) {
            ["source": "windowCapture", "pid": app.processIdentifier]
        }
        var outcome = "error"
        defer { queryTiming?.finish(outcome: outcome) }
        try RequestDiagnostics.checkObservationBudget()
        let permitted: Bool
        do {
            let metric = RequestDiagnostics.metrics?.start(.capturePermissionPreflight)
            defer { metric?.end() }
            permitted = CGPreflightScreenCaptureAccess()
        }
        guard permitted else {
            throw DriverError("screen recording permission denied", code: .desktopUnavailable)
        }
        let application: BackgroundTarget
        let retainedTarget: AXUIElement?
        let originalDocument: String?
        var retainedWindow: CGWindowID?
        do {
            let metric = RequestDiagnostics.metrics?.start(.captureStartupScope)
            defer { metric?.end() }
            application = NativeWindowQueryTiming.measure(queryTiming, "startup.processIdentity") { BackgroundTarget(app) }
            do {
                let metric = RequestDiagnostics.metrics?.start(.captureTargetResolution)
                defer { metric?.end() }
                retainedTarget = target ?? (nativeTarget == nil
                    ? WindowGeometry.inputWindow(pid: app.processIdentifier, queryTiming: queryTiming) : nil)
            }
            do {
                let metric = RequestDiagnostics.metrics?.start(.captureDocumentIdentity)
                defer { metric?.end() }
                originalDocument = WindowGeometry.documentIdentity(retainedTarget, queryTiming: queryTiming)
            }
            do {
                let metric = RequestDiagnostics.metrics?.start(.captureWindowHint)
                defer { metric?.end() }
                if let nativeTarget {
                    retainedWindow = nativeTarget.id
                } else if let retainedTarget {
                    let hint: WindowSelection.Hint? = NativeWindowQueryTiming.measure(
                        queryTiming, "startup.windowHint", root: retainedTarget
                    ) { @MainActor in
                        WindowGeometry.hint(retainedTarget, onWindowsRead: nil, queryTiming: queryTiming)
                    }
                    retainedWindow = hint?.id
                } else {
                    retainedWindow = nil
                }
            }
        }
        func validateOriginalScope() throws {
            let metric = RequestDiagnostics.metrics?.start(.captureRetryScope)
            defer { metric?.end() }
            try RequestDiagnostics.checkCancellation()
            try sharing?.checkAvailable()
            try application.validate()
            if let originalDocument {
                let metric = RequestDiagnostics.metrics?.start(.captureDocumentIdentity)
                defer { metric?.end() }
                if WindowGeometry.documentIdentity(retainedTarget, queryTiming: queryTiming) != originalDocument {
                    throw DriverError("the screenshot target changed document", code: .staleTarget)
                }
            }
            if let retainedWindow {
                guard let info = WindowServerInfo.window(retainedWindow) else {
                    throw DriverError("the original screenshot window is no longer confirmed", code: .observationIncomplete)
                }
                guard (info[kCGWindowOwnerPID as String] as? NSNumber)?.int32Value == app.processIdentifier else {
                    throw ObservationChanged("the original screenshot window changed owner")
                }
            }
            // A supplied mapping cannot be repaired by adopting a new geometry.
            if let nativeTarget { try observation.validate(nativeTarget) }
        }
        let result = try await WindowCaptureRetry.run(
            maximumAttempts: RequestDiagnostics.deadline == nil ? 4 : Int.max,
            check: RequestDiagnostics.checkObservationBudget, validateOriginalScope: validateOriginalScope,
            permits: { WindowCaptureRetry.permits($0, originalWindowKnown: retainedWindow != nil) },
            onRetry: { error, attempt in
                RequestDiagnostics.metrics?.count(.observationRetry)
                diagnose(["kind": "window_capture_retry", "attempt": attempt,
                          "window": retainedWindow as Any? ?? NSNull(), "reason": String(describing: error)])
            }
        ) {
            let result = try await self.captureWindowOnce(
                app, target: retainedTarget, nativeTarget: nativeTarget, retainedWindow: retainedWindow,
                maxDimension: maxDimension, queryTiming: queryTiming, selected: { retainedWindow = $0 })
            try validateOriginalScope()
            return result
        }
        outcome = "ok"
        return result
    }

    private func captureWindowOnce(
        _ app: NSRunningApplication, target: AXUIElement?, nativeTarget: WindowMapping?, retainedWindow: CGWindowID?,
        maxDimension: Int?, queryTiming: NativeWindowQueryTiming?, selected: (CGWindowID) -> Void
    ) async throws -> (image: CapturedImage, window: WindowMapping) {
        try RequestDiagnostics.checkObservationBudget()
        let interval = CaptureInterval()
        let content: SCShareableContent
        do {
            let metric = RequestDiagnostics.metrics?.start(.captureEnumeration)
            defer { metric?.end() }
            content = try await SCShareableContent.excludingDesktopWindows(false, onScreenWindowsOnly: false)
        }
        try RequestDiagnostics.checkObservationBudget()
        var windows = content.windows.filter {
            $0.owningApplication?.processID == app.processIdentifier && $0.frame.width > 1 && $0.frame.height > 1
        }
        if let retainedWindow { windows = windows.filter { $0.windowID == retainedWindow } }
        if let nativeTarget {
            guard nativeTarget.pid == app.processIdentifier else {
                throw DriverError("native screenshot target belongs to another process", code: .staleTarget)
            }
            do {
                let metric = RequestDiagnostics.metrics?.start(.captureIdentityValidation)
                defer { metric?.end() }
                try observation.validate(nativeTarget)
            }
            windows = windows.filter { $0.windowID == nativeTarget.id }
        }
        // A popup can own the focused control while AXFocusedWindow still names
        // its underlying dialog. Capture the window containing the actual focus.
        let hint: WindowSelection.Hint?
        do {
            let metric = RequestDiagnostics.metrics?.start(.captureWindowHint)
            defer { metric?.end() }
            if let nativeTarget {
                hint = WindowSelection.Hint(frame: nativeTarget.frame, title: nativeTarget.title ?? "", id: nativeTarget.id)
            } else {
                let resolvedTarget: AXUIElement? = target
                    ?? WindowGeometry.inputWindow(pid: app.processIdentifier, queryTiming: queryTiming)
                if let resolvedTarget {
                    hint = NativeWindowQueryTiming.measure(queryTiming, "attempt.windowHint", root: resolvedTarget) { @MainActor in
                        WindowGeometry.hint(resolvedTarget, onWindowsRead: nil, queryTiming: queryTiming)
                    }
                } else {
                    hint = nil
                }
            }
        }
        if target != nil && hint == nil { throw DriverError("selected window has no usable screenshot identity") }
        // Native modal panels may be above layer 0. Include them when AX (or an
        // existing observation) identifies the window, not as arbitrary fallbacks.
        if hint == nil { windows = windows.filter { $0.windowLayer == 0 } }
        let candidates = windows.map {
            WindowSelection.Candidate(id: $0.windowID, frame: $0.frame, title: $0.title ?? "", visible: $0.isOnScreen)
        }
        guard let id = WindowSelection.select(candidates, hint: hint, requireUnique: target != nil),
            let window = windows.first(where: { $0.windowID == id })
        else {
            if let conflict = WindowSelection.geometryConflict(candidates, hint: hint), let expected = hint?.frame {
                let source = nativeTarget == nil ? "accessibility" : "observed"
                throw WindowCapturePending(description:
                    """
                    window screenshot geometry disagrees for native window \(conflict.id): \
                    \(source)=\(expected), ScreenCaptureKit=\(conflict.frame); window identity is known, but \
                    a safe screenshot coordinate mapping is unavailable; read the window again after its \
                    layout settles
                    """)
            }
            let details = candidates.sorted { $0.visible && !$1.visible }.prefix(6).map {
                "\($0.id):\(String(reflecting: $0.title)) \($0.frame) visible=\($0.visible)"
            }.joined(separator: "; ")
            let focused = hint.map { " focused=\(String(reflecting: $0.title)) \(String(describing: $0.frame));" } ?? ""
            let description = """
                \(windows.isEmpty
                    ? "no capturable application window"
                    : "window screenshot target is ambiguous or not ready")\
                ;\(focused) candidates=\(candidates.count) [\(details)]
                """
            if retainedWindow != nil { throw WindowCapturePending(description: description) }
            throw DriverError(description)
        }
        // SCWindow comes from the enumeration above; foreground is a separate
        // sample. sampledAt bounds completion of these non-atomic state reads,
        // not the window's last draw time. Do not infer covering windows here.
        let onScreen = window.isOnScreen
        let foreground = NSWorkspace.shared.frontmostApplication
        let ownerForeground: Bool?
        if let foreground, !app.isTerminated, !foreground.isTerminated {
            ownerForeground = foreground.processIdentifier == app.processIdentifier
        } else {
            ownerForeground = nil
        }
        let windowState = CapturedImage.WindowState(
            sampledAt: CaptureClock.now(), onScreen: onScreen, ownerForeground: ownerForeground)
        let configuration = SCStreamConfiguration()
        let scale: CGFloat
        if let maxDimension {
            scale = min(1, CGFloat(maxDimension) / max(window.frame.width, window.frame.height))
        } else {
            scale = 1
        }
        configuration.width = max(1, min(maxDimension ?? Int.max, Int((window.frame.width * scale).rounded(.up))))
        configuration.height = max(1, min(maxDimension ?? Int.max, Int((window.frame.height * scale).rounded(.up))))
        configuration.showsCursor = false
        configuration.scalesToFit = true
        configuration.ignoreShadowsSingleWindow = true
        configuration.ignoreGlobalClipSingleWindow = true
        if #available(macOS 14.2, *) {
            // Otherwise a sheet may be rendered together with its parent, scaled
            // into the sheet-sized PNG. That would silently corrupt click coordinates.
            configuration.includeChildWindows = false
        }
        let mapping = WindowMapping(
            id: window.windowID, pid: app.processIdentifier, frame: window.frame, title: window.title,
            width: configuration.width, height: configuration.height)
        selected(mapping.id)
        try RequestDiagnostics.checkObservationBudget()
        do {
            let metric = RequestDiagnostics.metrics?.start(.captureIdentityValidation)
            defer { metric?.end() }
            observation.observeCapturedWindow(mapping, target: target)
            try observation.validateObservation()
        }
        try RequestDiagnostics.checkObservationBudget()
        let image: CGImage
        do {
            let metric = RequestDiagnostics.metrics?.start(.captureJoin)
            let began = ProcessInfo.processInfo.systemUptime
            var outcome = "error"
            defer {
                metric?.end()
                diagnose(["kind": "window_capture_join", "phase": "joined", "window": mapping.id,
                          "outcome": outcome, "durationMs": (ProcessInfo.processInfo.systemUptime - began) * 1000])
            }
            // Both branches consume this same fresh SCWindow on MainActor.
            // They share no pixels/filter result, and their intervals overlap:
            // captureJoin is elapsed time; sharing + pixels must not be summed.
            image = try await WindowCaptureJoin.run(check: {
                try RequestDiagnostics.checkObservationBudget()
                try self.sharing?.checkAvailable()
            }, include: {
                let metric = RequestDiagnostics.metrics?.start(.captureSharing)
                let began = ProcessInfo.processInfo.systemUptime
                var outcome = "error"
                diagnose(["kind": "window_capture_join", "phase": "sharingBegan", "window": mapping.id,
                          "uptime": began])
                defer {
                    metric?.end()
                    diagnose(["kind": "window_capture_join", "phase": "sharingEnded", "window": mapping.id,
                              "uptime": ProcessInfo.processInfo.systemUptime, "outcome": outcome])
                }
                try await self.sharing?.include(window)
                outcome = "ok"
            }, pixels: {
                let metric = RequestDiagnostics.metrics?.start(.capturePixels)
                let began = ProcessInfo.processInfo.systemUptime
                var outcome = "error"
                diagnose(["kind": "window_capture_join", "phase": "pixelsBegan", "window": mapping.id,
                          "uptime": began])
                defer {
                    metric?.end()
                    diagnose(["kind": "window_capture_join", "phase": "pixelsEnded", "window": mapping.id,
                              "uptime": ProcessInfo.processInfo.systemUptime, "outcome": outcome])
                }
                let pixels = try await SCScreenshotManager.captureImage(
                    contentFilter: SCContentFilter(desktopIndependentWindow: window), configuration: configuration)
                outcome = "ok"
                return pixels
            })
            outcome = "ok"
        }
        try RequestDiagnostics.checkObservationBudget()
        do {
            let metric = RequestDiagnostics.metrics?.start(.captureIdentityValidation)
            defer { metric?.end() }
            try observation.validate(mapping)
        }
        guard image.width == mapping.width, image.height == mapping.height else {
            throw DriverError("unexpected window screenshot dimensions")
        }
        try RequestDiagnostics.checkObservationBudget()
        try Self.requireVisiblePixels(image)
        let result: String
        do {
            // Includes ImageIO PNG finalization and its wire base64 conversion.
            let metric = RequestDiagnostics.metrics?.start(.capturePNGEncoding)
            defer { metric?.end() }
            result = try CapturedImage.encodePNG(image)
        }
        // Completed pixels and their validated identity can be published during
        // the request's finalization reserve without starting another capture.
        try RequestDiagnostics.checkCancellation()
        return (
            CapturedImage(
                png: result, width: image.width, height: image.height, interval: interval, windowState: windowState),
            mapping
        )
    }

}
