import AppKit
import ApplicationServices

// A disposable AppKit oracle. It never injects input; setup changes only its own
// windows. The target deliberately has no AXPress implementation.
@MainActor final class ClickSafetyView: NSView {
    let probeIdentifier: String
    let color: NSColor
    var activationOffset: NSPoint?
    var onMouse: ((String, NSEvent) -> Void)?
    var onFirstMouse: ((NSEvent?, Bool) -> Void)?
    private var pressed = false
    init(frame: NSRect, identifier: String, color: NSColor) {
        self.probeIdentifier = identifier
        self.color = color
        super.init(frame: frame)
    }
    required init?(coder: NSCoder) { fatalError("unused") }
    override func isAccessibilityElement() -> Bool { true }
    override func accessibilityRole() -> NSAccessibility.Role? { .group }
    override func accessibilityLabel() -> String? { probeIdentifier }
    override func accessibilityIdentifier() -> String { probeIdentifier }
    override func accessibilityActivationPoint() -> NSPoint {
        NSAccessibility.screenPoint(fromView: self, point: activationOffset ?? NSPoint(x: bounds.midX, y: bounds.midY))
    }
    override func acceptsFirstMouse(for event: NSEvent?) -> Bool {
        let accepted = CommandLine.arguments.contains("--accepts-first-mouse")
        onFirstMouse?(event, accepted)
        return accepted
    }
    override func mouseDown(with event: NSEvent) {
        pressed = true
        onMouse?("down", event)
    }
    override func mouseUp(with event: NSEvent) {
        onMouse?("up", event)
        if pressed { onMouse?("callback", event) }
        pressed = false
    }
    override func draw(_ dirtyRect: NSRect) {
        color.withAlphaComponent(0.4).setFill()
        bounds.fill()
        (probeIdentifier as NSString).draw(
            at: NSPoint(x: 9, y: bounds.midY - 7),
            withAttributes: [.font: NSFont.systemFont(ofSize: 14), .foregroundColor: NSColor.labelColor])
    }
}

@MainActor final class ClickSafetyWindow: NSWindow {
    weak var activationDecoy: NSView?
    override func accessibilityActivationPoint() -> NSPoint {
        guard let view = activationDecoy else { return super.accessibilityActivationPoint() }
        return NSAccessibility.screenPoint(fromView: view, point: NSPoint(x: view.bounds.midX, y: view.bounds.midY))
    }
}

@MainActor final class ClickSafetyApplication: NSApplication {
    override func sendEvent(_ event: NSEvent) {
        (delegate as? ClickSafetyDelegate)?.received(event)
        super.sendEvent(event)
    }
}

@MainActor final class ClickSafetyDelegate: NSObject, NSApplicationDelegate {
    let folder = Bundle.main.bundleURL.deletingLastPathComponent()
    var window: ClickSafetyWindow!
    var other: NSWindow!
    var target: ClickSafetyView!
    var decoy: NSButton!
    var scroll: NSScrollView?
    var status: NSTextField?
    var scenario = "plain"
    var generation = 0
    var counts = ["target": 0, "decoy": 0, "overlay": 0]
    var applicationDowns = 0, applicationUps = 0, targetDowns = 0, targetUps = 0
    var eventCount = 0

    func append(_ value: [String: Any]) {
        let file = folder.appendingPathComponent("events.jsonl")
        if !FileManager.default.fileExists(atPath: file.path) {
            FileManager.default.createFile(atPath: file.path, contents: nil)
        }
        if let handle = try? FileHandle(forWritingTo: file),
            let bytes = try? JSONSerialization.data(withJSONObject: value, options: [.sortedKeys])
        {
            _ = try? handle.seekToEnd()
            try? handle.write(contentsOf: bytes + Data([10]))
            try? handle.close()
        }
    }
    func screenPoint(_ view: NSView, _ local: NSPoint) -> NSPoint {
        NSAccessibility.screenPoint(fromView: view, point: local)
    }
    func pointArray(_ p: NSPoint) -> [Double] { [p.x, p.y] }
    func quartz(_ p: NSPoint) -> NSPoint { NSPoint(x: p.x, y: NSScreen.screens[0].frame.maxY - p.y) }
    func imagePoint(_ p: NSPoint) -> NSPoint { NSPoint(x: p.x - window.frame.minX, y: window.frame.maxY - p.y) }
    func snapshot() -> [String: Any] {
        let p = target.accessibilityActivationPoint()
        let dp = window.accessibilityActivationPoint()
        let click = screenPoint(target, NSPoint(x: target.bounds.midX, y: target.bounds.midY))
        return [
            "pid": getpid(), "title": window.title, "scenario": scenario, "generation": generation, "counts": counts,
            "applicationDowns": applicationDowns, "applicationUps": applicationUps, "targetDowns": targetDowns,
            "targetUps": targetUps, "eventCount": eventCount,
            "foregroundPid": NSWorkspace.shared.frontmostApplication?.processIdentifier ?? 0, "active": NSApp.isActive,
            "keyWindow": NSApp.keyWindow?.title ?? "",
            "acceptsFirstMouse": CommandLine.arguments.contains("--accepts-first-mouse"),
            "leftPressed": CGEventSource.buttonState(.combinedSessionState, button: .left),
            "windowNumber": window.windowNumber, "windowSize": [window.frame.width, window.frame.height],
            "targetFrameAppKit": [
                target.accessibilityFrame().minX, target.accessibilityFrame().minY, target.accessibilityFrame().width,
                target.accessibilityFrame().height
            ], "targetActivationAppKit": pointArray(p), "targetActivationQuartz": pointArray(quartz(p)),
            "targetActivationImage": pointArray(imagePoint(p)), "targetCenterImage": pointArray(imagePoint(click)),
            "targetActivationWindow": pointArray(window.convertPoint(fromScreen: p)),
            "windowActivationAppKit": pointArray(dp), "windowActivationQuartz": pointArray(quartz(dp)),
            "windowActivationImage": pointArray(imagePoint(dp)),
            "scrollVisibleRect": scroll.map {
                [
                    $0.contentView.bounds.minX, $0.contentView.bounds.minY, $0.contentView.bounds.width,
                    $0.contentView.bounds.height
                ]
            } ?? []
        ]
    }
    func save() {
        guard target != nil else { return }
        status?.stringValue = """
            Target: \(counts["target"]!)   Decoy: \(counts["decoy"]!)   Overlay: \(counts["overlay"]!)   \
            Mouse: \(applicationDowns) / \(applicationUps)
            """
        if let bytes = try? JSONSerialization.data(withJSONObject: snapshot(), options: [.sortedKeys]) {
            try? bytes.write(to: folder.appendingPathComponent("state.json"), options: .atomic)
        }
    }
    func received(_ event: NSEvent) {
        guard target != nil, [.leftMouseDown, .leftMouseUp].contains(event.type) else { return }
        if event.type == .leftMouseDown { applicationDowns += 1 } else { applicationUps += 1 }
        eventCount += 1
        let content = event.window?.contentView
        let local = content?.convert(event.locationInWindow, from: nil) ?? .zero
        let receiver = content?.hitTest(local)?.accessibilityIdentifier() ?? "unknown"
        append([
            "kind": "application", "type": event.type.rawValue, "receiver": receiver, "generation": generation,
            "scenario": scenario, "eventWindow": event.windowNumber, "point": pointArray(event.locationInWindow),
            "foregroundPid": NSWorkspace.shared.frontmostApplication?.processIdentifier ?? 0
        ])
        save()
    }
    func mouse(_ who: String, _ kind: String, _ event: NSEvent) {
        if who == "target" {
            if kind == "down" { targetDowns += 1 }
            if kind == "up" { targetUps += 1 }
        }
        if kind == "callback" { counts[who, default: 0] += 1 }
        append([
            "kind": kind, "receiver": who, "scenario": scenario, "generation": generation,
            "point": pointArray(event.locationInWindow), "counts": counts
        ])
        save()
    }
    @objc func decoyClicked() {
        counts["decoy", default: 0] += 1
        append(["kind": "callback", "receiver": "decoy", "generation": generation])
        save()
    }

    func setup(_ mode: String) {
        scenario = mode
        generation += 1
        counts = ["target": 0, "decoy": 0, "overlay": 0]
        applicationDowns = 0
        applicationUps = 0
        targetDowns = 0
        targetUps = 0
        eventCount = 0
        scroll = nil
        let content = NSView(frame: NSRect(x: 0, y: 0, width: 620, height: 440))
        window.contentView = content
        let heading = NSTextField(labelWithString: "Click safety: \(mode) — green target, red decoy")
        heading.frame = NSRect(x: 20, y: 390, width: 580, height: 24)
        content.addSubview(heading)
        status = NSTextField(labelWithString: "")
        status!.frame = NSRect(x: 20, y: 360, width: 580, height: 24)
        content.addSubview(status!)
        decoy = NSButton(title: "UNRELATED ACTIVATION DECOY", target: self, action: #selector(decoyClicked))
        decoy.frame = NSRect(x: 25, y: 30, width: 255, height: 44)
        decoy.setAccessibilityIdentifier("activation-decoy")
        content.addSubview(decoy)
        window.activationDecoy = decoy
        target = ClickSafetyView(
            frame: NSRect(x: 290, y: 135, width: 300, height: 230), identifier: "pointer-target", color: .systemGreen)
        target.onMouse = { [weak self] kind, event in self?.mouse("target", kind, event) }
        target.onFirstMouse = { [weak self] event, accepted in
            guard let self else { return }
            append([
                "kind": "firstMouse", "receiver": "target", "accepted": accepted, "generation": generation,
                "scenario": scenario, "active": NSApp.isActive, "keyWindow": NSApp.keyWindow?.title ?? "",
                "eventWindow": event?.windowNumber ?? 0, "point": event.map { pointArray($0.locationInWindow) } ?? []
            ])
        }
        if mode == "activation" { target.activationOffset = NSPoint(x: 34, y: 110) }
        if mode == "clipped" {
            let area = NSScrollView(frame: NSRect(x: 290, y: 155, width: 300, height: 175))
            area.borderType = .lineBorder
            area.hasVerticalScroller = false
            let document = NSView(frame: NSRect(x: 0, y: 0, width: 296, height: 500))
            target.frame = NSRect(x: 20, y: 105, width: 260, height: 180)
            document.addSubview(target)
            area.documentView = document
            content.addSubview(area)
            area.contentView.scroll(to: .zero)
            area.reflectScrolledClipView(area.contentView)
            scroll = area
        } else {
            content.addSubview(target)
            if mode == "covered" {
                let cover = ClickSafetyView(
                    frame: NSRect(x: 390, y: 205, width: 100, height: 90), identifier: "center-overlay",
                    color: .systemOrange)
                cover.onMouse = { [weak self] kind, event in self?.mouse("overlay", kind, event) }
                content.addSubview(cover)
            }
        }
        window.orderBack(nil)
        other.makeKey()
        other.makeMain()
        append(["kind": "setup", "generation": generation, "scenario": mode])
        save()
    }
    func commands() {
        let file = folder.appendingPathComponent("command.json")
        if let bytes = try? Data(contentsOf: file),
            let request = try? JSONSerialization.jsonObject(with: bytes) as? [String: String]
        {
            try? FileManager.default.removeItem(at: file)
            if request["command"] == "close" {
                NSApp.terminate(nil)
                return
            }
            if request["command"] == "setup", let mode = request["scenario"] { setup(mode) }
        }
        save()
    }
    func applicationDidFinishLaunching(_ notification: Notification) {
        try? String(getpid()).write(to: folder.appendingPathComponent("fixture.pid"), atomically: true, encoding: .utf8)
        window = ClickSafetyWindow(
            contentRect: NSRect(x: 140, y: 160, width: 620, height: 440), styleMask: [.titled, .closable],
            backing: .buffered, defer: false)
        window.title = "Click safety target"
        window.tabbingMode = .disallowed
        window.isReleasedWhenClosed = false
        other = NSWindow(
            contentRect: NSRect(x: 800, y: 160, width: 220, height: 180), styleMask: [.titled, .closable],
            backing: .buffered, defer: false)
        other.title = "Click safety other"
        other.tabbingMode = .disallowed
        other.isReleasedWhenClosed = false
        other.orderBack(nil)
        setup("plain")
        Timer.scheduledTimer(withTimeInterval: 0.03, repeats: true) { _ in MainActor.assumeIsolated { self.commands() }
        }
    }
}

// A separate process reads the provider's real AX point and performs a read-only
// AX hit query. It records raw coordinate bridging independently of SDK policy.
func readAX(_ element: AXUIElement, _ attribute: String) -> CFTypeRef? {
    var value: CFTypeRef?
    return AXUIElementCopyAttributeValue(element, attribute as CFString, &value) == .success ? value : nil
}
func axPoint(_ element: AXUIElement) -> [Double]? {
    guard let raw = readAX(element, "AXActivationPoint"), CFGetTypeID(raw) == AXValueGetTypeID() else { return nil }
    var p = CGPoint.zero
    return AXValueGetValue(unsafeDowncast(raw, to: AXValue.self), .cgPoint, &p) ? [p.x, p.y] : nil
}
func axProbe(_ pid: pid_t) -> [String: Any] {
    let app = AXUIElementCreateApplication(pid)
    let windows = readAX(app, kAXWindowsAttribute) as? [AXUIElement] ?? []
    guard let window = windows.first(where: { readAX($0, kAXTitleAttribute) as? String == "Click safety target" })
    else { return ["error": "target window absent"] }
    var pending = [window], found: AXUIElement?
    for _ in 0..<200 {
        guard let item = pending.popLast() else { break }
        if readAX(item, kAXIdentifierAttribute) as? String == "pointer-target" {
            found = item
            break
        }
        pending.append(contentsOf: readAX(item, kAXChildrenAttribute) as? [AXUIElement] ?? [])
    }
    guard let target = found else { return ["error": "target element absent"] }
    var output: [String: Any] = [
        "windowActivationAX": axPoint(window) as Any? ?? NSNull(),
        "targetActivationAX": axPoint(target) as Any? ?? NSNull(), "pid": pid
    ]
    if let point = axPoint(target) {
        var hit: AXUIElement?
        let status = AXUIElementCopyElementAtPosition(app, Float(point[0]), Float(point[1]), &hit)
        output["activationHitStatus"] = status.rawValue
        output["activationHitIdentifier"] = hit.flatMap { readAX($0, kAXIdentifierAttribute) as? String } ?? ""
        output["activationExactTarget"] = hit.map { CFEqual($0, target) } ?? false
    }
    return output
}

if CommandLine.arguments.count == 3, CommandLine.arguments[1] == "--inspect", let pid = Int32(CommandLine.arguments[2])
{
    let value = axProbe(pid)
    let bytes = try! JSONSerialization.data(withJSONObject: value, options: [.sortedKeys])
    print(String(data: bytes, encoding: .utf8)!)
} else {
    let application = ClickSafetyApplication.shared
    application.setActivationPolicy(.regular)
    let delegate = ClickSafetyDelegate()
    application.delegate = delegate
    application.run()
}
