import AppKit

/// A real AppKit activation/motion fixture. It never fabricates AX geometry,
/// native focus, window IDs or readiness: the recorded view/window frames and
/// mouse callbacks come from the actual AppKit objects.
@MainActor final class PreparationMotionTarget: NSView {
    weak var probe: PreparationMotionDelegate?
    let generation: Int
    var mouseDowns = 0
    init(generation: Int) {
        self.generation = generation
        super.init(frame: NSRect(x: 30, y: 90, width: 120, height: 48))
        setAccessibilityElement(true)
        setAccessibilityRole(.button)
        setAccessibilityIdentifier("original-motion-target")
        setAccessibilityLabel("Motion target")
    }
    required init?(coder: NSCoder) { fatalError("init(coder:) has not been implemented") }
    override func isAccessibilitySelectorAllowed(_ selector: Selector) -> Bool {
        // This fixture specifically exercises addressed pointer preparation,
        // not an independent AXPress implementation.
        if selector == #selector(accessibilityPerformPress) { return false }
        return super.isAccessibilitySelectorAllowed(selector)
    }
    override func mouseDown(with event: NSEvent) {
        mouseDowns += 1
        probe?.record("mouse-down-generation-\(generation)")
        needsDisplay = true
    }
    override func draw(_ dirtyRect: NSRect) {
        NSColor.systemBlue.setFill()
        bounds.fill()
        let label = "Target \(generation): \(mouseDowns)"
        (label as NSString).draw(at: NSPoint(x: 8, y: 16), withAttributes: [.foregroundColor: NSColor.white])
    }
}

@MainActor final class PreparationMotionDelegate: NSObject, NSApplicationDelegate, NSWindowDelegate {
    let directory: URL
    let mode: String
    var window: NSWindow!
    var unrelatedWindow: NSWindow?
    let original = PreparationMotionTarget(generation: 1)
    var replacement: PreparationMotionTarget?
    let guardField = NSTextField(string: "GUARD")
    var activations = 0, transitions = 0, keyRedirections = 0
    var initialWindowFrame = NSRect.zero, initialTargetFrame = NSRect.zero
    var events: [[String: Any]] = []

    init(directory: URL, mode: String) {
        self.directory = directory
        self.mode = mode
    }
    func applicationDidFinishLaunching(_ notification: Notification) {
        window = NSWindow(contentRect: NSRect(x: 80, y: 250, width: 380, height: 220),
                          styleMask: [.titled, .closable], backing: .buffered, defer: false)
        window.title = "Pointer preparation motion"
        window.isReleasedWhenClosed = false
        window.tabbingMode = .disallowed
        window.delegate = self
        original.probe = self
        window.contentView!.addSubview(original)
        guardField.frame = NSRect(x: 30, y: 25, width: 310, height: 28)
        guardField.setAccessibilityIdentifier("unrelated-guard")
        window.contentView!.addSubview(guardField)
        initialWindowFrame = window.frame
        initialTargetFrame = original.frame
        window.makeKeyAndOrderFront(nil)
        window.makeMain()
        NSApp.activate(ignoringOtherApps: true)
        record("launched")
        Timer.scheduledTimer(withTimeInterval: 0.05, repeats: true) { _ in
            MainActor.assumeIsolated { self.writeState() }
        }
        Timer.scheduledTimer(withTimeInterval: 240, repeats: false) { _ in
            MainActor.assumeIsolated { NSApp.terminate(nil) }
        }
    }
    func applicationDidBecomeActive(_ notification: Notification) {
        activations += 1
        record("application-active")
        // Initial launch establishes the observation. Root then backgrounds
        // this app explicitly; only its next real activation changes geometry.
        guard activations > 1, transitions == 0 else { return }
        transitions += 1
        switch mode {
        case "move-window":
            let visible = window.screen?.visibleFrame ?? NSScreen.main!.visibleFrame
            window.setFrameOrigin(NSPoint(x: visible.maxX - window.frame.width - 20,
                                          y: window.frame.origin.y))
        case "move-element":
            original.setFrameOrigin(NSPoint(x: 230, y: original.frame.origin.y))
        case "remove-original":
            let next = PreparationMotionTarget(generation: 2)
            next.probe = self
            next.frame = original.frame
            original.removeFromSuperview()
            window.contentView!.addSubview(next)
            replacement = next
        case "key-context":
            showUnrelatedWindow()
        default:
            break
        }
        record("activation-transition-\(mode)")
    }
    func showUnrelatedWindow() {
        if unrelatedWindow == nil {
            let helper = NSWindow(contentRect: NSRect(x: 500, y: 250, width: 260, height: 180),
                                  styleMask: [.titled, .closable], backing: .buffered, defer: false)
            helper.title = "Independent key context"
            helper.isReleasedWhenClosed = false
            helper.contentView!.addSubview(NSTextField(labelWithString: "Independent window: input must not arrive here"))
            unrelatedWindow = helper
        }
        keyRedirections += 1
        unrelatedWindow!.makeKeyAndOrderFront(nil)
    }
    func windowDidBecomeKey(_ notification: Notification) {
        guard mode == "key-context", transitions > 0,
              let selected = notification.object as? NSWindow, selected === window else { return }
        showUnrelatedWindow()
        record("key-context-redirected")
    }
    func rect(_ value: NSRect) -> [Double] {
        [value.origin.x, value.origin.y, value.width, value.height]
    }
    func screenFrame(_ view: NSView) -> NSRect? {
        guard let owner = view.window else { return nil }
        return owner.convertToScreen(view.convert(view.bounds, to: nil))
    }
    func snapshot() -> [String: Any] {
        ["pid": getpid(), "mode": mode, "activations": activations, "transitions": transitions,
         "windowNumber": window?.windowNumber ?? 0,
         "windowFrame": window.map { rect($0.frame) } ?? [],
         "initialWindowFrame": rect(initialWindowFrame), "initialTargetFrame": rect(initialTargetFrame),
         "targetFrame": rect(original.frame), "targetScreenFrame": screenFrame(original).map(rect) ?? [],
         "originalAttached": original.window === window, "originalMouseDowns": original.mouseDowns,
         "replacementMouseDowns": replacement?.mouseDowns ?? 0,
         "replacementAttached": replacement?.window === window && replacement != nil,
         "guard": guardField.stringValue,
         "frontmostPID": NSWorkspace.shared.frontmostApplication?.processIdentifier ?? 0,
         "appActive": NSApp.isActive,
         "keyWindow": NSApp.keyWindow?.windowNumber ?? 0,
         "unrelatedWindowNumber": unrelatedWindow?.windowNumber ?? 0,
         "keyRedirections": keyRedirections]
    }
    func record(_ reason: String) {
        var event = snapshot()
        event["reason"] = reason
        event["uptime"] = ProcessInfo.processInfo.systemUptime
        events.append(event)
        try! JSONSerialization.data(withJSONObject: events, options: [.sortedKeys]).write(
            to: directory.appendingPathComponent("events.json"), options: .atomic)
        writeState()
    }
    func writeState() {
        try! JSONSerialization.data(withJSONObject: snapshot(), options: [.sortedKeys]).write(
            to: directory.appendingPathComponent("state.json"), options: .atomic)
    }
}

let app = NSApplication.shared
app.setActivationPolicy(.regular)
let directory = URL(fileURLWithPath: CommandLine.arguments[1])
let mode = CommandLine.arguments.count > 2 ? CommandLine.arguments[2] : "stationary"
let supported = ["stationary", "move-window", "move-element", "remove-original", "key-context"]
precondition(supported.contains(mode), "unknown fixture mode")
let probe = PreparationMotionDelegate(directory: directory, mode: mode)
app.delegate = probe
app.run()
