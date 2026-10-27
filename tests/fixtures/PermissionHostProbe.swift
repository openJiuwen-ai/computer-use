import AppKit
import ApplicationServices

// A fresh LaunchServices host exercises real TCC responsibility inheritance.
// It requests no permissions, shows no window and never posts input.
let app = NSApplication.shared
app.setActivationPolicy(.prohibited)
let output = URL(fileURLWithPath: CommandLine.arguments[1])
let command = CommandLine.arguments[2]
let process = Process()
process.executableURL = URL(fileURLWithPath: command)
process.currentDirectoryURL = output.deletingLastPathComponent()
process.environment = ProcessInfo.processInfo.environment.filter {
    !$0.key.hasPrefix("COMPUTER_USE_") && !["PYTHONPATH", "VIRTUAL_ENV"].contains($0.key)
}
let stdout = Pipe(), stderr = Pipe()
process.standardOutput = stdout
process.standardError = stderr
var record: [String: Any] = [
    "hostBundle": Bundle.main.bundleIdentifier ?? "unknown", "hostAccessibility": AXIsProcessTrusted(),
    "hostScreenCapture": CGPreflightScreenCaptureAccess(), "hostInput": CGPreflightPostEventAccess(),
    "command": command, "permissionRequest": false
]
do {
    try process.run()
    let deadline = Date().addingTimeInterval(20)
    while process.isRunning && Date() < deadline { Thread.sleep(forTimeInterval: 0.05) }
    if process.isRunning {
        record["error"] = "Permission command exceeded the fixture deadline"
        process.terminate()
        let cleanup = Date().addingTimeInterval(2)
        while process.isRunning && Date() < cleanup { Thread.sleep(forTimeInterval: 0.05) }
        if process.isRunning { kill(process.processIdentifier, SIGKILL) }
    }
    process.waitUntilExit()
    record["exit"] = process.terminationStatus
    record["stdout"] = String(data: stdout.fileHandleForReading.readDataToEndOfFile(), encoding: .utf8) ?? ""
    record["stderr"] = String(data: stderr.fileHandleForReading.readDataToEndOfFile(), encoding: .utf8) ?? ""
} catch { record["error"] = String(describing: error) }
try JSONSerialization.data(withJSONObject: record, options: [.prettyPrinted, .sortedKeys]).write(to: output)
