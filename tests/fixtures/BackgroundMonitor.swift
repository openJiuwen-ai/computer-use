import AppKit
import CoreGraphics

// Passive acceptance diagnostics. Never activates, changes focus, or posts input.
let targetID = CommandLine.arguments[1]
let stopFile = CommandLine.arguments[2]
var samples = 0, previous = ""
func emit(_ data: [String: Any]) {
    var event = data
    event["timestampMs"] = Date().timeIntervalSince1970 * 1000
    let bytes = try! JSONSerialization.data(withJSONObject: event, options: [.sortedKeys])
    FileHandle.standardOutput.write(bytes + Data([10]))
}
func sample(_ kind: String) {
    samples += 1
    let front = NSWorkspace.shared.frontmostApplication
    let point = CGEvent(source: nil)?.location ?? .zero
    let target = NSWorkspace.shared.runningApplications.first { $0.bundleIdentifier == targetID }
    let windows =
        CGWindowListCopyWindowInfo([.optionOnScreenOnly, .excludeDesktopElements], kCGNullWindowID) as? [[String: Any]]
        ?? []
    // Include floating panels and tooltips: an app can cover the user's window
    // without becoming NSWorkspace's frontmost application.
    let order: [[String: Any]] = windows.map {
        [
            "id": ($0[kCGWindowNumber as String] as? NSNumber)?.intValue ?? 0,
            "pid": ($0[kCGWindowOwnerPID as String] as? NSNumber)?.intValue ?? 0,
            "layer": ($0[kCGWindowLayer as String] as? NSNumber)?.intValue ?? 0,
            "alpha": ($0[kCGWindowAlpha as String] as? NSNumber)?.doubleValue ?? 1,
            "bounds": $0[kCGWindowBounds as String] as? [String: Any] ?? [:]
        ]
    }
    func frame(_ window: [String: Any]) -> CGRect {
        guard let bounds = window["bounds"] as? [String: Any] else { return .null }
        return CGRect(dictionaryRepresentation: bounds as CFDictionary) ?? .null
    }
    let frontWindows = order.enumerated().filter { ($0.element["pid"] as? Int) == Int(front?.processIdentifier ?? 0) }
    let covering = order.enumerated().filter { index, window in
        guard target != nil, (window["pid"] as? Int) == Int(target!.processIdentifier),
            (window["alpha"] as? Double ?? 0) > 0
        else { return false }
        return frontWindows.contains { index < $0.offset && frame(window).intersects(frame($0.element)) }
    }.map { $0.element["id"] as! Int }
    let state: [String: Any] = [
        "frontmost": front?.bundleIdentifier ?? "", "frontPID": front?.processIdentifier ?? 0,
        "targetPID": target?.processIdentifier ?? 0, "cursor": ["x": point.x, "y": point.y],
        "flags": CGEventSource.flagsState(.combinedSessionState).rawValue,
        "clipboardChangeCount": NSPasteboard.general.changeCount, "windows": order,
        "targetCoveringFrontWindows": covering
    ]
    let signature = String(
        decoding: try! JSONSerialization.data(withJSONObject: state, options: [.sortedKeys]), as: UTF8.self)
    if kind != "poll" || signature != previous {
        var event = state
        event["kind"] = kind
        event["samples"] = samples
        emit(event)
        previous = signature
    }
}
let observer = NSWorkspace.shared.notificationCenter.addObserver(
    forName: NSWorkspace.didActivateApplicationNotification, object: nil, queue: .main
) { event in
    if let app = event.userInfo?[NSWorkspace.applicationUserInfoKey] as? NSRunningApplication {
        emit(["kind": "activation", "bundleID": app.bundleIdentifier ?? "", "pid": app.processIdentifier])
    }
    sample("after-activation")
}
sample("start")
while !FileManager.default.fileExists(atPath: stopFile) {
    RunLoop.main.run(until: Date().addingTimeInterval(0.05))
    sample("poll")
}
sample("finish")
NSWorkspace.shared.notificationCenter.removeObserver(observer)
