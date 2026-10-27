import AppKit

/// Diagnostics only: every override forwards the original arguments to super.
/// Event arrival, a mouseDown stack frame, cell tracking and value changes are
/// separate observations; none is used to manufacture successful dragging.
@MainActor enum DragTrace {
    static let path = Bundle.main.bundleURL.deletingLastPathComponent().appendingPathComponent("dispatch.jsonl")
    static func isMouse(_ event: NSEvent) -> Bool {
        [.leftMouseDown, .leftMouseDragged, .leftMouseUp].contains(event.type)
    }
    static func append(
        _ layer: String, _ phase: String, event: NSEvent? = nil, view: NSView? = nil, window: NSWindow? = nil,
        extra: [String: Any] = [:]
    ) {
        let window = window ?? view?.window ?? event?.window
        var row: [String: Any] = [
            "layer": layer, "phase": phase, "time": ProcessInfo.processInfo.systemUptime,
            "pressedButtons": NSEvent.pressedMouseButtons,
            "combinedLeftDown": CGEventSource.buttonState(.combinedSessionState, button: .left),
            "hidLeftDown": CGEventSource.buttonState(.hidSystemState, button: .left), "appActive": NSApp.isActive,
            "keyWindow": NSApp.keyWindow?.windowNumber ?? 0, "mainWindow": NSApp.mainWindow?.windowNumber ?? 0,
            "runLoopMode": RunLoop.current.currentMode?.rawValue ?? "none"
        ]
        if let window {
            row["window"] = window.windowNumber
            row["windowKey"] = window.isKeyWindow
            row["firstResponder"] = window.firstResponder.map { String(describing: type(of: $0)) } ?? "none"
            row["pointerOutsideEventStream"] = NSStringFromPoint(window.mouseLocationOutsideOfEventStream)
        }
        if let view {
            row["viewClass"] = String(describing: type(of: view))
            row["bounds"] = NSStringFromRect(view.bounds)
        }
        if let slider = view as? TrackedSlider {
            row["slider"] = slider.traceName
            row["value"] = slider.doubleValue
            row["mouseDownOnStack"] = slider.tracking
            row["cellClass"] = slider.cell.map { String(describing: type(of: $0)) } ?? "none"
        }
        if let event, isMouse(event) {
            row["eventType"] = event.type.rawValue
            row["eventWindow"] = event.windowNumber
            row["eventTime"] = event.timestamp
            row["eventNumber"] = event.eventNumber
            row["subtype"] = event.subtype.rawValue
            row["location"] = NSStringFromPoint(event.locationInWindow)
            row["clickCount"] = event.clickCount
            row["pressure"] = event.pressure
            if let cg = event.cgEvent {
                let rawID = cg.getIntegerValueField(.eventSourceStateID)
                row["sourceState"] = rawID
                // Query the event's actual source table. .privateState (-1)
                // requests a new table; it does not identify this gesture.
                if let raw = Int32(exactly: rawID), raw != CGEventSourceStateID.privateState.rawValue,
                    let state = CGEventSourceStateID(rawValue: raw)
                {
                    row["eventSourceLeftDown"] = CGEventSource.buttonState(state, button: .left)
                } else {
                    row["eventSourceLeftDown"] = NSNull()
                }
            }
        }
        row.merge(extra) { _, new in new }
        guard var data = try? JSONSerialization.data(withJSONObject: row) else { return }
        data.append(10)
        if !FileManager.default.fileExists(atPath: path.path) {
            FileManager.default.createFile(atPath: path.path, contents: nil)
        }
        guard let file = try? FileHandle(forWritingTo: path) else { return }
        defer { try? file.close() }
        _ = try? file.seekToEnd()
        try? file.write(contentsOf: data)
    }
}

@MainActor final class DragApplication: NSApplication {
    override func sendEvent(_ event: NSEvent) {
        if DragTrace.isMouse(event) { DragTrace.append("application.sendEvent", "before", event: event) }
        super.sendEvent(event)
        if DragTrace.isMouse(event) { DragTrace.append("application.sendEvent", "after", event: event) }
    }
}

@MainActor final class DragWindow: NSWindow {
    override func sendEvent(_ event: NSEvent) {
        if DragTrace.isMouse(event) { DragTrace.append("window.sendEvent", "before", event: event, window: self) }
        super.sendEvent(event)
        if DragTrace.isMouse(event) { DragTrace.append("window.sendEvent", "after", event: event, window: self) }
    }
}

@MainActor final class TracedSliderCell: NSSliderCell {
    override func trackMouse(with event: NSEvent, in cellFrame: NSRect, of controlView: NSView, untilMouseUp flag: Bool)
        -> Bool
    {
        DragTrace.append(
            "cell.trackMouse", "before", event: event, view: controlView,
            extra: ["untilMouseUp": flag, "cellFrame": NSStringFromRect(cellFrame)])
        let result = super.trackMouse(with: event, in: cellFrame, of: controlView, untilMouseUp: flag)
        DragTrace.append("cell.trackMouse", "after", event: event, view: controlView, extra: ["result": result])
        return result
    }
    override func startTracking(at startPoint: NSPoint, in controlView: NSView) -> Bool {
        DragTrace.append(
            "cell.startTracking", "before", event: NSApp.currentEvent, view: controlView,
            extra: ["point": NSStringFromPoint(startPoint)])
        let result = super.startTracking(at: startPoint, in: controlView)
        DragTrace.append(
            "cell.startTracking", "after", event: NSApp.currentEvent, view: controlView, extra: ["result": result])
        return result
    }
    override func continueTracking(last lastPoint: NSPoint, current currentPoint: NSPoint, in controlView: NSView)
        -> Bool
    {
        DragTrace.append(
            "cell.continueTracking", "before", event: NSApp.currentEvent, view: controlView,
            extra: ["last": NSStringFromPoint(lastPoint), "point": NSStringFromPoint(currentPoint)])
        let result = super.continueTracking(last: lastPoint, current: currentPoint, in: controlView)
        DragTrace.append(
            "cell.continueTracking", "after", event: NSApp.currentEvent, view: controlView, extra: ["result": result])
        return result
    }
    override func stopTracking(
        last lastPoint: NSPoint, current stopPoint: NSPoint, in controlView: NSView, mouseIsUp flag: Bool
    ) {
        DragTrace.append(
            "cell.stopTracking", "before", event: NSApp.currentEvent, view: controlView,
            extra: ["last": NSStringFromPoint(lastPoint), "point": NSStringFromPoint(stopPoint), "mouseIsUp": flag])
        super.stopTracking(last: lastPoint, current: stopPoint, in: controlView, mouseIsUp: flag)
        DragTrace.append("cell.stopTracking", "after", event: NSApp.currentEvent, view: controlView)
    }
}

@MainActor class TrackedSlider: NSSlider {
    override class var cellClass: AnyClass? {
        get { TracedSliderCell.self }
        set { super.cellClass = newValue }
    }
    var traceName = ""
    // This tracks our mouseDown wrapper's stack, not AppKit's private state.
    var tracking = false, starts = 0, ends = 0
    var record: (() -> Void)?
    override func mouseDown(with event: NSEvent) {
        tracking = true
        starts += 1
        record?()
        DragTrace.append("slider.mouseDown", "before", event: event, view: self)
        super.mouseDown(with: event)  // Keep AppKit's real tracking loop and first-mouse policy.
        DragTrace.append("slider.mouseDown", "after", event: event, view: self)
        tracking = false
        ends += 1
        record?()
    }
    override func mouseDragged(with event: NSEvent) {
        DragTrace.append("slider.mouseDragged", "before", event: event, view: self)
        super.mouseDragged(with: event)
        DragTrace.append("slider.mouseDragged", "after", event: event, view: self)
    }
    override func mouseUp(with event: NSEvent) {
        DragTrace.append("slider.mouseUp", "before", event: event, view: self)
        super.mouseUp(with: event)
        DragTrace.append("slider.mouseUp", "after", event: event, view: self)
    }
}

@MainActor final class VisualSlider: TrackedSlider {
    override func isAccessibilityElement() -> Bool { false }
    override func accessibilityChildren() -> [Any]? { [] }
}

@MainActor final class DragDelegate: NSObject, NSApplicationDelegate {
    let directory = Bundle.main.bundleURL.deletingLastPathComponent()
    var window: NSWindow!
    let native = TrackedSlider(value: 20, minValue: 0, maxValue: 100, target: nil, action: nil)
    let visual = VisualSlider(value: 20, minValue: 0, maxValue: 100, target: nil, action: nil)
    let status = NSTextField(labelWithString: "")
    var changes = 0, moveAt: Double?
    var monitor: Any?
    func applicationDidFinishLaunching(_ notification: Notification) {
        try? Data(String(getpid()).utf8).write(to: directory.appendingPathComponent("fixture.pid"))
        let menu = NSMenu(), item = NSMenuItem(), commands = NSMenu()
        commands.addItem(withTitle: "Quit", action: #selector(NSApplication.terminate(_:)), keyEquivalent: "q")
        item.submenu = commands
        menu.addItem(item)
        NSApp.mainMenu = menu
        monitor = NSEvent.addLocalMonitorForEvents(matching: [.leftMouseDown, .leftMouseDragged, .leftMouseUp]) {
            [weak self] event in
            MainActor.assumeIsolated {
                guard let self else { return }
                DragTrace.append("localMonitor", "observed", event: event)
                let row: [String: Any] = [
                    "type": event.type.rawValue, "window": event.windowNumber, "timestamp": event.timestamp,
                    "received": ProcessInfo.processInfo.systemUptime, "x": event.locationInWindow.x,
                    "y": event.locationInWindow.y, "number": event.eventNumber, "subtype": event.subtype.rawValue,
                    "pressedButtons": NSEvent.pressedMouseButtons, "keyWindow": NSApp.keyWindow?.windowNumber ?? 0,
                    "nativeTracking": self.native.tracking, "visualTracking": self.visual.tracking,
                    "native": self.native.doubleValue, "visual": self.visual.doubleValue
                ]
                if var data = try? JSONSerialization.data(withJSONObject: row) {
                    data.append(10)
                    let url = self.directory.appendingPathComponent("events.jsonl")
                    if !FileManager.default.fileExists(atPath: url.path) {
                        FileManager.default.createFile(atPath: url.path, contents: nil)
                    }
                    if let file = try? FileHandle(forWritingTo: url) {
                        _ = try? file.seekToEnd()
                        try? file.write(contentsOf: data)
                        try? file.close()
                    }
                }
            }
            return event
        }
        window = DragWindow(
            contentRect: NSRect(x: 220, y: 200, width: 740, height: 380), styleMask: [.titled, .closable],
            backing: .buffered, defer: false)
        window.title = "Drag sliders"
        window.isReleasedWhenClosed = false
        for (name, slider, y) in [("Native slider", native, 265), ("Visual slider", visual, 130)] {
            let label = NSTextField(labelWithString: name + " — 0 to 100")
            label.frame = NSRect(x: 35, y: y + 42, width: 500, height: 30)
            window.contentView?.addSubview(label)
            slider.frame = NSRect(x: 35, y: y, width: 660, height: 30)
            slider.traceName = name
            slider.setAccessibilityLabel(name)
            slider.isContinuous = true
            slider.target = self
            slider.action = #selector(changed(_:))
            slider.record = { [weak self] in self?.record() }
            window.contentView?.addSubview(slider)
        }
        status.frame = NSRect(x: 35, y: 50, width: 520, height: 26)
        window.contentView?.addSubview(status)
        let arm = NSButton(title: "Move during drag", target: self, action: #selector(armMove))
        arm.frame = NSRect(x: 535, y: 12, width: 180, height: 30)
        window.contentView?.addSubview(arm)
        if FileManager.default.fileExists(atPath: directory.appendingPathComponent("visible").path) {
            window.level = .floating
            window.orderFrontRegardless()
        } else {
            window.orderBack(nil)
        }
        // NSSliderCell's bar/knob rects are not initialized before the first
        // display. Record native geometry after AppKit has laid out the view.
        DispatchQueue.main.async {
            self.window.displayIfNeeded()
            self.record()
        }
    }
    @objc func armMove() {
        moveAt = native.doubleValue
        record()
    }
    @objc func changed(_ sender: NSSlider) {
        changes += 1
        DragTrace.append("slider.action", "observed", event: NSApp.currentEvent, view: sender)
        if let moveAt, sender === native, abs(native.doubleValue - moveAt) > 1 {
            self.moveAt = nil
            window.setFrameOrigin(NSPoint(x: window.frame.minX + 40, y: window.frame.minY))
        }
        record()
    }
    func point(_ slider: NSSlider, value: Double) -> [String: Double] {
        // Ground acceptance coordinates in the native slider's own bar/knob geometry.
        let cell = slider.cell as! NSSliderCell
        let knob = cell.knobRect(flipped: false)
        let bar = cell.barRect(flipped: false)
        let local = NSPoint(x: bar.minX + knob.width / 2 + (bar.width - knob.width) * value / 100, y: knob.midY)
        let screen = window.convertPoint(toScreen: slider.convert(local, to: nil))
        return ["x": screen.x - window.frame.minX, "y": window.frame.maxY - screen.y]
    }
    func record() {
        guard window != nil else { return }
        status.stringValue =
            "Native value: \(Int(native.doubleValue.rounded())); Visual value: \(Int(visual.doubleValue.rounded()))"
        let result: [String: Any] = [
            "native": native.doubleValue, "visual": visual.doubleValue, "nativeMouseDownOnStack": native.tracking,
            "visualMouseDownOnStack": visual.tracking,
            "trackingMeaning": """
            nativeTracking/visualTracking are legacy aliases for mouseDown wrapper on-stack, not system \
            tracking verification
            """, "nativeTracking": native.tracking, "visualTracking": visual.tracking, "nativeStarts": native.starts,
            "nativeEnds": native.ends, "visualStarts": visual.starts, "visualEnds": visual.ends, "changes": changes,
            "nativeStart": point(native, value: native.doubleValue), "native80": point(native, value: 80),
            "native30": point(native, value: 30), "visualStart": point(visual, value: visual.doubleValue),
            "visual70": point(visual, value: 70)
        ]
        if let bytes = try? JSONSerialization.data(withJSONObject: result) {
            try? bytes.write(to: directory.appendingPathComponent("values.json"), options: .atomic)
        }
    }
}

let app = DragApplication.shared
let delegate = DragDelegate()
app.setActivationPolicy(.regular)
app.delegate = delegate
app.run()
