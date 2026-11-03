// Copyright (c) Huawei Technologies Co., Ltd. 2026. All rights reserved.

import AppKit

@main enum AgentPointerLifecycleTests {
    @MainActor static func main() {
        var state = AgentPointerVisualState()
        state.observeNewWindow(at: 0)
        state.observeNewWindow(at: 29)
        precondition(!state.expireIfIdle(at: 29.99))
        precondition(state.expireIfIdle(at: 30), "reads must not renew idle decoration")
        precondition(state.expired)
        state.observeNewWindow(at: 31)
        precondition(state.expired, "reading cannot resurrect expired decoration")

        state.begin()
        precondition(!state.expired)
        precondition(!state.expireIfIdle(at: 100), "an active operation must remain visible")
        state.finish(at: 100)
        precondition(!state.expireIfIdle(at: 129.99))
        state.begin()
        precondition(!state.expireIfIdle(at: 140), "a new operation cancels the old deadline")
        state.finish(at: 150)
        state.observeNewWindow(at: 179)
        precondition(state.expireIfIdle(at: 180))

        if CommandLine.arguments.contains("--live") { liveLifecycle() }
        print("Agent pointer lifecycle passed")
    }

    // Optional real AppKit lifecycle check; operates only on its own non-key panel.
    @MainActor static func liveLifecycle() {
        _ = NSApplication.shared
        NSApp.setActivationPolicy(.accessory)
        let window = NSPanel(
            contentRect: CGRect(x: 40, y: 40, width: 180, height: 100),
            styleMask: [.borderless, .nonactivatingPanel], backing: .buffered, defer: false)
        window.title = "Pointer lifecycle test"
        window.isReleasedWhenClosed = false
        window.level = .floating
        window.ignoresMouseEvents = true
        window.orderFrontRegardless()
        let pointer = AgentPointer()
        defer { pointer.shutdown(); window.close() }
        let frame = CGRect(
            x: window.frame.minX, y: CGDisplayBounds(CGMainDisplayID()).height - window.frame.maxY,
            width: window.frame.width, height: window.frame.height)
        let mapping = WindowGeometry.WindowMapping(
            id: CGWindowID(window.windowNumber), pid: getpid(), frame: frame,
            title: window.title, width: 180, height: 100)
        func panels() -> [NSWindow] { NSApp.windows.filter { $0.title == AgentPointer.windowTitle } }
        func isCleared() -> Bool {
            // AppKit may retain a closed NSWindow until its autorelease pool drains.
            panels().allSatisfy {
                !$0.isVisible && !AgentPointer.isDecoration(id: CGWindowID($0.windowNumber), pid: getpid(), title: $0.title)
            }
        }
        func pump(_ seconds: Double) {
            RunLoop.main.run(until: Date(timeIntervalSinceNow: seconds))
        }
        let activity = pointer.begin()
        activity.move(to: CGPoint(x: frame.midX, y: frame.midY), window: mapping)
        activity.finish(success: true)
        pump(1)
        precondition(panels().contains(where: { $0.isVisible }), "fixture must actually display a pointer")
        for _ in 0..<28 { pointer.observe(mapping); pump(1) }
        precondition(!panels().isEmpty, "must not clear before 30 seconds")
        pump(2)
        print("After expiry:", panels().map { ["visible": $0.isVisible, "registered": AgentPointer.isDecoration(
            id: CGWindowID($0.windowNumber), pid: getpid(), title: $0.title)] })
        precondition(isCleared(), "expiry must hide and unregister the panel")
        pointer.observe(mapping)
        pump(0.3)
        precondition(isCleared(), "post-expiry reads must not recreate the pointer")
        let next = pointer.begin()
        next.move(to: CGPoint(x: frame.midX, y: frame.midY), window: mapping)
        next.finish(success: false)
        pump(0.5)
        precondition(panels().contains(where: { $0.isVisible }), "next operation must recreate the pointer")
        pointer.stop()
        pump(0.2)
        precondition(isCleared(), "explicit stop must still clean up immediately")
    }
}
