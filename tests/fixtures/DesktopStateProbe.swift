import AppKit
import CoreGraphics

// Passive desktop-state sampling for shared-mode acceptance. No UI inspection,
// event interception or input; clipboard content is never read.
let includeForegroundIdentity = ProcessInfo.processInfo.environment["COMPUTER_USE_TEST_MONITOR_IDENTITY"] == "1"
let dateFormatter = ISO8601DateFormatter()
dateFormatter.formatOptions = [.withInternetDateTime, .withFractionalSeconds]
func sample() {
    guard let event = CGEvent(source: nil) else { return }
    let point = event.location
    // Use the same running-app snapshot for PID and identity. A later lookup by
    // PID could instead find no process, or a different process after PID reuse.
    let foreground = NSWorkspace.shared.frontmostApplication
    var value: [String: Any] = [
        "pid": foreground?.processIdentifier ?? 0, "x": point.x, "y": point.y,
        "flags": CGEventSource.flagsState(.combinedSessionState).rawValue, "clipboard": NSPasteboard.general.changeCount
    ]
    if includeForegroundIdentity {
        var identity: [String: String] = [:]
        if let name = foreground?.localizedName { identity["name"] = name }
        if let identifier = foreground?.bundleIdentifier { identity["bundleIdentifier"] = identifier }
        if let url = foreground?.executableURL { identity["executableURL"] = url.absoluteString }
        if let date = foreground?.launchDate { identity["launchDate"] = dateFormatter.string(from: date) }
        value["foregroundIdentity"] = identity.isEmpty ? NSNull() : identity
    }
    FileHandle.standardOutput.write(
        try! JSONSerialization.data(withJSONObject: value, options: [.sortedKeys]) + Data([10]))
}
sample()
let timer = Timer.scheduledTimer(withTimeInterval: 0.05, repeats: true) { _ in sample() }
let duration = Double(ProcessInfo.processInfo.environment["COMPUTER_USE_TEST_MONITOR_SECONDS"] ?? "") ?? 90
RunLoop.main.run(until: Date().addingTimeInterval(duration))
