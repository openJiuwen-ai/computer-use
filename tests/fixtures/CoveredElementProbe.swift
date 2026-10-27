import AppKit

// Two independent documents in one application, with exactly overlapping
// pointer controls. AX hit-testing reports the front document until preparation
// raises the selected one. No AXPress shortcut masks the pointer route.
@MainActor final class ClickTarget: NSView {
    let index: Int
    var clicked: (Int) -> Void = { _ in }
    init(index: Int) {
        self.index = index
        super.init(frame: NSRect(x: 50, y: 50, width: 260, height: 120))
        setAccessibilityElement(true)
        setAccessibilityRole(.button)
        setAccessibilityLabel("Pointer target \(index)")
        setAccessibilityIdentifier("pointer-target-\(index)")
    }
    required init?(coder: NSCoder) { fatalError("not used") }
    override func acceptsFirstMouse(for event: NSEvent?) -> Bool { true }
    override func draw(_ dirtyRect: NSRect) {
        (index == 0 ? NSColor.systemBlue : NSColor.systemOrange).setFill()
        bounds.fill()
        ("Pointer target \(index)" as NSString).draw(
            at: NSPoint(x: 20, y: 45),
            withAttributes: [.font: NSFont.systemFont(ofSize: 22), .foregroundColor: NSColor.white])
    }
    override func mouseDown(with event: NSEvent) { clicked(event.clickCount) }
}

@MainActor final class Delegate: NSObject, NSApplicationDelegate {
    let directory = URL(fileURLWithPath: CommandLine.arguments[1])
    var windows = [NSWindow](), counts = [0, 0], clickCounts = [[Int](), [Int]()]
    func record() {
        let value: [String: Any] = [
            "pid": getpid(), "clicks": counts, "clickCounts": clickCounts,
            "foreground": NSWorkspace.shared.frontmostApplication?.processIdentifier ?? -1,
            "keyWindow": NSApp.keyWindow?.title ?? "none", "windows": windows.map(\.windowNumber)
        ]
        try? JSONSerialization.data(withJSONObject: value).write(
            to: directory.appendingPathComponent("state.json"), options: .atomic)
    }
    func applicationDidFinishLaunching(_ notification: Notification) {
        let menu = NSMenu(), item = NSMenuItem()
        item.submenu = NSMenu()
        item.submenu!.addItem(withTitle: "Quit", action: #selector(NSApplication.terminate(_:)), keyEquivalent: "q")
        menu.addItem(item)
        NSApp.mainMenu = menu
        for i in 0..<2 {
            let window = NSWindow(
                contentRect: NSRect(x: 180, y: 240, width: 360, height: 220), styleMask: [.titled, .closable],
                backing: .buffered, defer: false)
            window.title = "Covered document \(i)"
            window.tabbingMode = .disallowed
            window.isReleasedWhenClosed = false
            let target = ClickTarget(index: i)
            target.clicked = { [weak self] count in
                self?.counts[i] += 1
                self?.clickCounts[i].append(count)
                self?.record()
            }
            window.contentView!.addSubview(target)
            window.orderFront(nil)
            windows.append(window)
        }
        if CommandLine.arguments.contains("--same-window") {
            // A separate control inside the selected window is not a window
            // ordering problem and must never authorize click-through.
            let cover = ClickTarget(index: 1)
            cover.clicked = { [weak self] count in
                self?.counts[1] += 1
                self?.clickCounts[1].append(count)
                self?.record()
            }
            windows[0].contentView!.addSubview(cover)
            windows[0].makeKeyAndOrderFront(nil)
        } else {
            windows[1].makeKeyAndOrderFront(nil)
        }
        NSApp.activate(ignoringOtherApps: true)
        record()
        _ = Timer.scheduledTimer(withTimeInterval: 0.02, repeats: true) { _ in
            MainActor.assumeIsolated { self.record() }
        }
    }
}

let application = NSApplication.shared
application.setActivationPolicy(.regular)
let delegate = Delegate()
application.delegate = delegate
application.run()
