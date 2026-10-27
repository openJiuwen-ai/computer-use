import AppKit

private var reparentHelper: Bool {
    ProcessInfo.processInfo.environment["COMPUTER_USE_TEST_REPARENT_HELPER"] == "1"
        || Bundle.main.object(forInfoDictionaryKey: "FixtureReparent") as? Bool == true
}
private var fixtureLifetime: Double {
    Double(ProcessInfo.processInfo.environment["COMPUTER_USE_TEST_FIXTURE_SECONDS"] ?? "")
        ?? (Bundle.main.object(forInfoDictionaryKey: "FixtureLifetime") as? NSNumber)?.doubleValue ?? 60
}

// The helper deliberately exposes no accessibility content. Its real native
// window and button still exercise screenshot-based observation and clicking.
@MainActor final class AuxiliaryProbeApplication: NSApplication {
    var isHelper: Bool {
        CommandLine.arguments.contains("--helper")
            && ProcessInfo.processInfo.environment["COMPUTER_USE_TEST_HELPER_AX"] != "1"
    }
    override func accessibilityRole() -> NSAccessibility.Role? { isHelper ? nil : super.accessibilityRole() }
    override func accessibilityChildren() -> [Any]? { isHelper ? [] : super.accessibilityChildren() }
    override func accessibilityWindows() -> [Any]? {
        (delegate as? AuxiliaryProbeDelegate)?.moveOnReadIfRequested()
        return isHelper ? [] : super.accessibilityWindows()
    }
    override func accessibilityFocusedWindow() -> Any? { isHelper ? nil : super.accessibilityFocusedWindow() }
    override func accessibilityMainWindow() -> Any? { isHelper ? nil : super.accessibilityMainWindow() }
}

@MainActor final class AuxiliaryProbeDelegate: NSObject, NSApplicationDelegate {
    var window: NSWindow!
    var hiddenWindow: NSWindow?
    var helperStatus: NSTextField?
    var children = [Process]()
    let directory =
        CommandLine.arguments.count > 1
        ? URL(fileURLWithPath: CommandLine.arguments[1]) : Bundle.main.bundleURL.deletingLastPathComponent()
    var helper: Bool { CommandLine.arguments.contains("--helper") }

    func applicationDidFinishLaunching(_ notification: Notification) {
        let logName = helper ? (CommandLine.arguments.contains("--outside") ? "outside" : "helper") : "host"
        try! Data(String(ProcessInfo.processInfo.processIdentifier).utf8).write(
            to: directory.appendingPathComponent(logName + ".pid"))
        window = NSWindow(
            contentRect: NSRect(x: helper ? 400 : 40, y: 200, width: 340, height: 180), styleMask: [.titled, .closable],
            backing: .buffered, defer: false)
        window.isReleasedWhenClosed = false
        window.title =
            helper
            ? (ProcessInfo.processInfo.environment["COMPUTER_USE_TEST_UNTITLED_HELPER"] == "1"
                ? "" : "Auxiliary export") : "Main document"
        let button = NSButton(
            title: helper
                ? (CommandLine.arguments.contains("--outside") ? "UNRELATED OUTSIDE WINDOW" : "Export test result")
                : "Open auxiliary", target: self, action: helper ? #selector(exported) : #selector(openHelpers))
        button.frame = NSRect(x: 50, y: 50, width: 240, height: 44)
        window.contentView!.addSubview(button)
        if !helper && reparentHelper {
            let finish = NSButton(title: "Finish helper launch", target: self, action: #selector(finishLaunch))
            finish.frame = NSRect(x: 50, y: 105, width: 240, height: 32)
            window.contentView!.addSubview(finish)
        }
        if helper && reparentHelper {
            let mark = NSButton(title: "Mark helper", target: self, action: #selector(marked))
            mark.frame = NSRect(x: 50, y: 105, width: 240, height: 32)
            window.contentView!.addSubview(mark)
            let status = NSTextField(labelWithString: "Helper waiting")
            status.frame = NSRect(x: 20, y: 150, width: 300, height: 20)
            window.contentView!.addSubview(status)
            helperStatus = status
        }
        window.orderBack(nil)
        window.makeKey()
        window.makeMain()
        if !helper { try! Data().write(to: directory.appendingPathComponent("host-ready")) }
        if helper {
            let hidden = NSWindow(
                contentRect: NSRect(x: 400, y: 200, width: 240, height: 100), styleMask: [.titled], backing: .buffered,
                defer: false)
            hidden.title = "Hidden auxiliary"
            hidden.orderBack(nil)
            hidden.orderOut(nil)
            hiddenWindow = hidden
        }
        Timer.scheduledTimer(withTimeInterval: fixtureLifetime, repeats: false) { _ in
            MainActor.assumeIsolated { NSApp.terminate(nil) }
        }
    }

    @objc func openHelpers() {
        guard children.isEmpty else { return }
        let inside = Bundle.main.bundleURL.appendingPathComponent("Contents/Helpers/Child.app/Contents/MacOS/probe")
        let outside = directory.appendingPathComponent("Outside.app/Contents/MacOS/probe")
        for (url, extra) in [(inside, [String]()), (outside, ["--outside"])] {
            let child = Process()
            child.executableURL = url
            let relay = extra.isEmpty && reparentHelper
            child.arguments = [directory.path, "--helper"] + extra + (relay ? ["--launcher"] : [])
            try! child.run()
            children.append(child)
        }
    }

    // Re-layout can occur while an app handles AX requests. Move the real
    // window once after the SDK has already collected its native catalogue.
    func moveOnReadIfRequested() {
        let request = directory.appendingPathComponent("move-on-next-read")
        guard !helper, let window, FileManager.default.fileExists(atPath: request.path) else { return }
        try! FileManager.default.removeItem(at: request)
        let old = window.frame
        window.setFrameOrigin(NSPoint(x: old.minX + 45, y: old.minY + 25))
        let evidence = ["before": NSStringFromRect(old), "after": NSStringFromRect(window.frame)]
        try! JSONSerialization.data(withJSONObject: evidence).write(to: directory.appendingPathComponent("moved.json"))
    }

    @objc func marked() {
        helperStatus?.stringValue = "Helper marked"
        try! Data("same helper after reparenting".utf8).write(to: directory.appendingPathComponent("marked-result"))
        let evidence = ["pid": getpid(), "parent": getppid()]
        try! JSONSerialization.data(withJSONObject: evidence).write(
            to: directory.appendingPathComponent("marked-state.json"))
    }

    @objc func finishLaunch() { try! Data().write(to: directory.appendingPathComponent("release-launcher")) }

    @objc func exported() {
        let result = CommandLine.arguments.contains("--outside") ? "outside-result" : "result"
        try! Data("correct helper button".utf8).write(to: directory.appendingPathComponent(result))
        NSApp.terminate(nil)
    }

    func applicationWillTerminate(_ notification: Notification) {
        for child in children where child.isRunning { child.terminate() }
    }
}

// A real intermediate process exits only after the test observed its child.
// No AppKit state or event delivery is changed to simulate reparenting.
if CommandLine.arguments.contains("--launcher") {
    let directory = URL(fileURLWithPath: CommandLine.arguments[1])
    try! Data(String(getpid()).utf8).write(to: directory.appendingPathComponent("launcher.pid"))
    let helper = Process()
    helper.executableURL = Bundle.main.executableURL
    helper.arguments = [directory.path, "--helper"]
    try! helper.run()
    let deadline = Date().addingTimeInterval(fixtureLifetime)
    while !FileManager.default.fileExists(atPath: directory.appendingPathComponent("release-launcher").path)
        && Date() < deadline
    { Thread.sleep(forTimeInterval: 0.02) }
    try! Data("launcher exiting".utf8).write(to: directory.appendingPathComponent("launcher-exited"))
    exit(0)
}

let application = AuxiliaryProbeApplication.shared
application.setActivationPolicy(.regular)
let delegate = AuxiliaryProbeDelegate()
application.delegate = delegate
application.run()
