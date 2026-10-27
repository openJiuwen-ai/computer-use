import AppKit
@preconcurrency import ApplicationServices
import ScreenCaptureKit
import ImageIO
import UniformTypeIdentifiers

// The visual adapter only consumes this DTO, never the screenshot or input APIs.
// The harness links the production pointer/geometry/appearance sources unchanged.
enum WindowGeometry {
    struct WindowMapping: Sendable {
        let id: CGWindowID
        let pid: pid_t
        let frame: CGRect
        let title: String?
        let width: Int
        let height: Int
    }
}

@MainActor final class PointerStabilityProbe: NSObject, NSApplicationDelegate {
    let pointer = AgentPointer()
    var windows = [NSWindow]()
    var records = [[String: Any]]()
    var failures = [String]()
    var window: NSWindow!
    let first = NSButton(title: "First semantic control", target: nil, action: nil)
    let second = NSButton(title: "Internal editor stand-in", target: nil, action: nil)
    var realCursor = CGPoint.zero
    var front: pid_t = 0
    let directory = URL(fileURLWithPath: CommandLine.arguments[1])

    func applicationDidFinishLaunching(_ notification: Notification) {
        DispatchQueue.main.async {
            NSApp.setActivationPolicy(.accessory)
            self.front = NSWorkspace.shared.frontmostApplication?.processIdentifier ?? 0
            self.realCursor = CGEvent(source: nil)!.location
            self.window = self.makeWindow("Pointer stability", rect: CGRect(x: 100, y: 350, width: 560, height: 300))
            self.first.frame = CGRect(x: 30, y: 220, width: 180, height: 30)
            self.second.frame = CGRect(x: 320, y: 100, width: 210, height: 30)
            self.window.contentView!.addSubview(self.first)
            self.window.contentView!.addSubview(self.second)
            Task { await self.run() }
        }
    }
    func makeWindow(_ title: String, rect: CGRect, level: Int = 0) -> NSWindow {
        let w = NSWindow(contentRect: rect, styleMask: [.titled], backing: .buffered, defer: false)
        w.title = title
        w.isReleasedWhenClosed = false
        w.backgroundColor = .white
        w.level = NSWindow.Level(rawValue: level)
        w.orderFrontRegardless()
        windows.append(w)
        return w
    }
    func mapping(_ window: NSWindow) -> WindowGeometry.WindowMapping {
        let frame = window.frame
        return .init(
            id: CGWindowID(window.windowNumber), pid: getpid(),
            frame: CGRect(
                x: frame.minX, y: CGDisplayBounds(CGMainDisplayID()).height - frame.maxY, width: frame.width,
                height: frame.height), title: window.title, width: Int(frame.width), height: Int(frame.height))
    }
    func move(_ point: CGPoint) {
        let activity = pointer.begin(), w = mapping(window)
        activity.move(to: CGPoint(x: w.frame.minX + point.x, y: w.frame.minY + point.y), window: w)
        activity.finish(success: true)
    }
    func settle(_ seconds: Double = 0.55) async { try? await Task.sleep(for: .seconds(seconds)) }
    func check(_ label: String, at expected: CGPoint?, expectedWindow: NSWindow? = nil) {
        let overlay = NSApp.windows.first { $0.title == AgentPointer.windowTitle && $0.isVisible }
        let layer = overlay?.contentView?.layer?.sublayers?.first
        let expectedWindow = expectedWindow ?? window!
        let bounds = mapping(expectedWindow).frame
        let point = layer.map { layer in
            let rendered = layer.presentation()?.position ?? layer.position
            return CGPoint(x: rendered.x, y: bounds.height - rendered.y)
        }
        let positionMatches =
            expected.map { expected in point.map { hypot($0.x - expected.x, $0.y - expected.y) < 1 } == true }
            ?? (overlay == nil)
        let frameMatches = expected == nil || overlay?.frame == expectedWindow.frame
        let ok = positionMatches && frameMatches
        if !ok { failures.append(label) }
        records.append([
            "case": label, "passed": ok, "visible": overlay != nil,
            "point": point.map { [$0.x, $0.y] } as Any? ?? NSNull(),
            "expected": expected.map { [$0.x, $0.y] } as Any? ?? NSNull()
        ])
    }
    func checkPixels() async {
        do {
            let content = try await SCShareableContent.excludingDesktopWindows(false, onScreenWindowsOnly: true)
            let overlay = content.windows.first {
                $0.owningApplication?.processID == getpid() && $0.title == AgentPointer.windowTitle
            }
            guard let overlay else {
                failures.append("live-artwork-window-missing")
                return
            }
            let config = SCStreamConfiguration()
            config.width = Int(overlay.frame.width)
            config.height = Int(overlay.frame.height)
            config.showsCursor = false
            let image = try await SCScreenshotManager.captureImage(
                contentFilter: SCContentFilter(desktopIndependentWindow: overlay), configuration: config)
            let file = directory.appendingPathComponent("live-pointer.png")
            let destination = CGImageDestinationCreateWithURL(file as CFURL, UTType.png.identifier as CFString, 1, nil)!
            CGImageDestinationAddImage(destination, image, nil)
            CGImageDestinationFinalize(destination)
            let c = CGContext(
                data: nil, width: image.width, height: image.height, bitsPerComponent: 8, bytesPerRow: image.width * 4,
                space: CGColorSpaceCreateDeviceRGB(),
                bitmapInfo: CGImageAlphaInfo.premultipliedLast.rawValue | CGBitmapInfo.byteOrder32Big.rawValue)!
            c.draw(image, in: CGRect(x: 0, y: 0, width: image.width, height: image.height))
            let p = c.data!.assumingMemoryBound(to: UInt8.self)
            let pixels = (0..<image.width * image.height).filter { i in
                Int(p[i * 4 + 2]) - Int(p[i * 4]) > 9 && Int(p[i * 4 + 2]) - Int(p[i * 4 + 1]) > 7
            }.count
            if pixels < 60 { failures.append("live-artwork-pixels") }
            records.append(["case": "live-artwork-pixels", "passed": pixels >= 60, "bluePixels": pixels])
        } catch { failures.append("live-artwork-capture: \(error)") }
    }
    // Inspect composited pixels, not just whether the overlay owns an onscreen
    // window. Include the fixture surfaces so transparent and opaque covers
    // are rendered by WindowServer in their actual order.
    func checkComposite(_ label: String, at point: CGPoint, visible: Bool) async {
        do {
            let bounds = mapping(window).frame
            let region = CGRect(x: bounds.minX + point.x - 8, y: bounds.minY + point.y - 8, width: 48, height: 48)
            let content = try await SCShareableContent.excludingDesktopWindows(false, onScreenWindowsOnly: true)
            guard let display = content.displays.first(where: { $0.frame.contains(region) }) else {
                failures.append(label + ": missing display")
                return
            }
            let own = content.windows.filter { $0.owningApplication?.processID == getpid() }
            let config = SCStreamConfiguration()
            config.sourceRect = region.offsetBy(dx: -display.frame.minX, dy: -display.frame.minY)
            config.width = 48
            config.height = 48
            config.showsCursor = false
            let image = try await SCScreenshotManager.captureImage(
                contentFilter: SCContentFilter(display: display, including: own), configuration: config)
            let file = directory.appendingPathComponent(label + ".png")
            let destination = CGImageDestinationCreateWithURL(file as CFURL, UTType.png.identifier as CFString, 1, nil)!
            CGImageDestinationAddImage(destination, image, nil)
            CGImageDestinationFinalize(destination)
            let c = CGContext(
                data: nil, width: image.width, height: image.height, bitsPerComponent: 8, bytesPerRow: image.width * 4,
                space: CGColorSpaceCreateDeviceRGB(),
                bitmapInfo: CGImageAlphaInfo.premultipliedLast.rawValue | CGBitmapInfo.byteOrder32Big.rawValue)!
            c.draw(image, in: CGRect(x: 0, y: 0, width: image.width, height: image.height))
            let p = c.data!.assumingMemoryBound(to: UInt8.self)
            let pixels = (0..<image.width * image.height).filter { i in
                Int(p[i * 4 + 2]) - Int(p[i * 4]) > 9 && Int(p[i * 4 + 2]) - Int(p[i * 4 + 1]) > 7
            }.count
            let ok = visible ? pixels >= 60 : pixels == 0
            if !ok { failures.append(label) }
            records.append(["case": label, "passed": ok, "bluePixels": pixels, "expectedVisible": visible])
        } catch { failures.append(label + ": \(error)") }
    }
    struct NativeElement: @unchecked Sendable { let value: AXUIElement }
    func element(at point: CGPoint, in surface: NSWindow? = nil) async -> AXUIElement? {
        let bounds = mapping(surface ?? window).frame
        let x = Float(bounds.minX + point.x), y = Float(bounds.minY + point.y), pid = getpid()
        return await Task.detached {
            var element: AXUIElement?
            guard AXUIElementCopyElementAtPosition(AXUIElementCreateApplication(pid), x, y, &element) == .success,
                let element
            else { return nil as NativeElement? }
            return NativeElement(value: element)
        }.value?.value
    }
    func run() async {
        await settle(0.2)
        pointer.observe(mapping(window))
        await settle()
        let a = CGPoint(x: 90, y: 90), b = CGPoint(x: 410, y: 210)
        move(a)
        await settle()
        check("first-position", at: a)
        await checkPixels()
        // Two commands finish before the display queue can publish the first.
        move(b)
        let noop = pointer.begin()
        noop.finish(success: true)
        await settle()
        check("fast-action-then-noop", at: b)
        move(b)
        await settle()
        let rejected = pointer.begin()
        rejected.finish(success: false)
        await settle()
        check("rejected-command-keeps-position", at: b)
        let edge = CGPoint(x: window.frame.width - 6, y: window.frame.height - 6)
        move(edge)
        await settle()
        check("edge-hotspot-not-clamped", at: edge)
        move(a)
        await settle()
        let other = makeWindow("Read-only other window", rect: CGRect(x: 750, y: 400, width: 200, height: 150))
        pointer.observe(mapping(other))
        await settle()
        check("read-does-not-move", at: a)
        other.orderOut(nil)
        pointer.observe(mapping(window))
        move(a)
        await settle()
        let q = mapping(window).frame
        let cover = makeWindow(
            "Occlusion",
            rect: CGRect(
                x: q.minX + b.x - 40, y: CGDisplayBounds(CGMainDisplayID()).height - (q.minY + b.y) - 40, width: 110,
                height: 110), level: 30)
        await settle(0.1)
        move(b)
        await settle()
        await checkComposite("occluded-latest-target-stays-hidden", at: b, visible: false)
        cover.orderOut(nil)
        await settle()
        check("uncover-resumes-latest-target", at: b)
        let glass = makeWindow("Transparent overlay", rect: window.frame, level: 30)
        glass.isOpaque = false
        glass.backgroundColor = .clear
        glass.ignoresMouseEvents = true
        await settle()
        await checkComposite("transparent-overlay-keeps-cursor", at: b, visible: true)
        glass.orderOut(nil)
        let frontWindow = makeWindow("Normal foreground cover", rect: window.frame)
        await settle()
        await checkComposite("normal-window-cover-hides-cursor", at: b, visible: false)
        window.orderFrontRegardless()
        await settle()
        await checkComposite("target-ordered-front-restores-cursor", at: b, visible: true)
        frontWindow.orderFrontRegardless()
        await settle()
        await checkComposite("target-ordered-back-hides-cursor", at: b, visible: false)
        frontWindow.orderOut(nil)
        await settle()
        await checkComposite("uncover-composited-cursor", at: b, visible: true)
        // Movement follows the window without altering its original local anchor.
        window.setFrameOrigin(NSPoint(x: window.frame.minX + 25, y: window.frame.minY + 20))
        await settle()
        check("window-movement-keeps-offset", at: b)
        let originalSize = window.frame.size
        window.setFrame(CGRect(origin: window.frame.origin, size: CGSize(width: 220, height: 150)), display: true)
        await settle()
        check("resize-hides-without-clamping", at: nil)
        window.setFrame(CGRect(origin: window.frame.origin, size: originalSize), display: true)
        await settle()
        check("resize-restores-original-anchor", at: b)
        AgentPointer.beginDesktopCapture()
        await settle(0.2)
        check("capture-hides-pointer", at: nil)
        AgentPointer.endDesktopCapture()
        await settle()
        check("capture-restores-same-position", at: b)
        await settle(5.4)
        check("standby-keeps-position", at: b)
        let firstPoint = CGPoint(x: first.frame.midX, y: window.frame.height - first.frame.midY)
        let secondPoint = CGPoint(x: second.frame.midX, y: window.frame.height - second.frame.midY)
        if let firstAX = await element(at: firstPoint), let secondAX = await element(at: secondPoint) {
            let w = mapping(window)
            let semantic = pointer.begin()
            semantic.aim(element: firstAX, window: w, pulse: false)
            semantic.aim(element: secondAX, window: w, pulse: false)
            semantic.move(to: CGPoint(x: w.frame.minX + secondPoint.x, y: w.frame.minY + secondPoint.y), window: w)
            semantic.finish(success: true)
            await settle()
            check("semantic-target-not-internal-editor", at: firstPoint)
            move(a)
            await settle()
            let pending = pointer.begin()
            pending.aim(element: secondAX, window: w, pulse: false)
            pending.finish(success: true)
            let noop = pointer.begin()
            noop.finish(success: true)
            await settle()
            check("pending-AX-survives-command-boundary", at: secondPoint)
        } else {
            failures.append("native AX controls must be available for semantic tests")
        }
        // A real transient surface disappears after submitting/dismissing it.
        // A subsequent read of the work window must recover visual presence.
        move(a)
        await settle()
        let transient = makeWindow("Transient surface", rect: CGRect(x: 750, y: 350, width: 260, height: 140))
        let popupButton = NSButton(title: "Popup control", target: nil, action: nil)
        popupButton.frame = CGRect(x: 30, y: 70, width: 170, height: 30)
        transient.contentView!.addSubview(popupButton)
        let transientMapping = mapping(transient)
        let popupPoint = CGPoint(x: popupButton.frame.midX, y: transient.frame.height - popupButton.frame.midY)
        await settle(0.2)
        if let popupAX = await element(at: popupPoint, in: transient) {
            let popupAction = pointer.begin()
            popupAction.aim(element: popupAX, window: mapping(window), pulse: false)
            popupAction.finish(success: true)
            await settle()
            check("element-in-native-popup-surface", at: popupPoint, expectedWindow: transient)
            let workWindow = mapping(window)
            window.orderOut(nil)
            await settle(0.2)
            let hiddenWorkAction = pointer.begin()
            hiddenWorkAction.aim(element: popupAX, window: workWindow, pulse: false)
            hiddenWorkAction.finish(success: true)
            await settle()
            check("popup-with-hidden-work-window", at: popupPoint, expectedWindow: transient)
            window.orderFrontRegardless()
        } else {
            failures.append("popup AX element unavailable")
        }
        let transientAction = pointer.begin()
        transientAction.move(
            to: CGPoint(x: transientMapping.frame.midX, y: transientMapping.frame.midY), window: transientMapping)
        transientAction.finish(success: true)
        await settle()
        transient.close()
        await settle()
        check("dismissed-target-without-read-stays-hidden", at: nil)
        pointer.observe(mapping(window))
        await settle()
        check("closed-transient-restores-work-window", at: a)
        window.orderOut(nil)
        await settle()
        check("hidden-window-without-read-keeps-anchor", at: nil)
        window.orderFrontRegardless()
        await settle()
        check("hidden-window-restores-original-anchor", at: a)
        let newSurface = makeWindow("Another observed surface", rect: CGRect(x: 750, y: 350, width: 260, height: 140))
        // A closed target followed by a never-before-used App gets one neutral
        // waiting location, not an invisible cursor or the old window's offset.
        window.close()
        newSurface.orderFrontRegardless()
        pointer.observe(mapping(newSurface))
        await settle()
        check(
            "closed-target-new-observation-initializes",
            at: CGPoint(x: newSurface.frame.width / 2, y: newSurface.frame.height / 2), expectedWindow: newSurface)
        // No AX geometry must not prevent recovery from a known offscreen
        // surface. AXApplication deliberately has no element position/size.
        let unavailable = pointer.begin()
        unavailable.aim(element: AXUIElementCreateApplication(getpid()), window: transientMapping, pulse: false)
        unavailable.finish(success: true)
        pointer.observe(mapping(newSurface))
        await settle()
        check(
            "unresolved-offscreen-target-recovers",
            at: CGPoint(x: newSurface.frame.width / 2, y: newSurface.frame.height / 2), expectedWindow: newSurface)
        // A queued geometry callback must not resurrect a reset pointer.
        let closing = pointer.begin(), finalWindow = mapping(newSurface)
        closing.move(to: CGPoint(x: finalWindow.frame.midX, y: finalWindow.frame.midY), window: finalWindow)
        pointer.stop()
        await settle(0.4)
        check("stop-removes-pointer", at: nil, expectedWindow: newSurface)
        pointer.observe(finalWindow)
        await settle()
        check(
            "ordinary-stop-allows-new-observation",
            at: CGPoint(x: finalWindow.frame.width / 2, y: finalWindow.frame.height / 2), expectedWindow: newSurface)
        pointer.shutdown()
        pointer.observe(finalWindow)
        let late = pointer.begin()
        late.move(to: CGPoint(x: finalWindow.frame.midX, y: finalWindow.frame.midY), window: finalWindow)
        late.finish(success: true)
        await settle()
        check("shutdown-rejects-late-observation-and-action", at: nil, expectedWindow: newSurface)
        let current = CGEvent(source: nil)!.location
        let report: [String: Any] = [
            "passed": failures.isEmpty, "failures": failures, "cases": records,
            "realCursorUnchanged": current == realCursor,
            "foregroundUnchanged": NSWorkspace.shared.frontmostApplication?.processIdentifier == front
        ]
        try? JSONSerialization.data(withJSONObject: report, options: [.prettyPrinted, .sortedKeys]).write(
            to: directory.appendingPathComponent("report.json"))
        windows.forEach { $0.close() }
        print(failures.isEmpty ? "PASS pointer stability" : "FAIL pointer stability: \(failures)")
        NSApp.terminate(nil)
    }
}

let app = NSApplication.shared
app.setActivationPolicy(.prohibited)
let delegate = PointerStabilityProbe()
app.delegate = delegate
app.run()
