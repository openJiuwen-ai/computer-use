import AppKit
import Foundation
import Darwin

@main struct ForegroundActivationTests {
    @MainActor static func main() async throws {
        let arguments = CommandLine.arguments
        if let status = ForegroundActivation.runIfRequested(arguments) { exit(status) }
        if arguments.dropFirst().first == "--activation-fixture" {
            let kind = arguments[2]
            if kind == "hold" {
                try Data(String(getpid()).utf8).write(to: URL(fileURLWithPath: arguments[3]))
                signal(SIGTERM, SIG_IGN)
                try await Task.sleep(for: .seconds(10))
                return
            }
            if kind == "lost" { return }
            if kind == "oversize" {
                try FileHandle.standardOutput.write(contentsOf: Data(repeating: 65, count: 5000))
                return
            }
            if kind == "rejected" {
                let receipt = ForegroundActivation.Receipt(
                    version: 1, invoked: false, accepted: nil, reason: "process identity changed",
                    applicationBefore: false, applicationAfter: false, targetUnchanged: false)
                try FileHandle.standardOutput.write(contentsOf: JSONEncoder().encode(receipt) + Data([10]))
                return
            }
            let receipt = ForegroundActivation.Receipt(
                version: 1, invoked: true, accepted: kind == "true", reason: nil, applicationBefore: false,
                applicationAfter: false, targetUnchanged: true)
            try FileHandle.standardOutput.write(contentsOf: JSONEncoder().encode(receipt) + Data([10]))
            return
        }

        // Importing AppKit is not an NSApplication initialization. These tests
        // never activate another process or start an application event loop.
        precondition(NSApp == nil)
        let identity = NativeProcessIdentity.read(getpid())!
        let request = ForegroundActivation.Request(identity: identity, deadline: 100)
        let text = String(decoding: try JSONEncoder().encode(request), as: UTF8.self)
        let decoded = try ForegroundActivation.Request.decode(text)
        precondition(decoded.identity == identity)
        for invalid in ["{}", "[]", "null", String(repeating: "a", count: 16_385)] {
            do {
                _ = try ForegroundActivation.Request.decode(invalid)
                preconditionFailure("invalid payload admitted")
            } catch {}
        }
        var activations = 0, resolves = 0
        func resolve(_ pid: pid_t) -> (() -> Bool)? {
            precondition(pid == identity.pid)
            resolves += 1
            return {
                activations += 1
                return false
            }
        }
        let returned = ForegroundActivation.perform(
            request, now: { 1 }, identity: { _ in identity }, applicationInitialized: { false }, resolve: resolve)
        precondition(returned.invoked && returned.accepted == false && activations == 1 && resolves == 1)
        let expired = ForegroundActivation.perform(
            request, now: { 100 }, identity: { _ in identity }, applicationInitialized: { false }, resolve: resolve)
        precondition(!expired.invoked && activations == 1 && resolves == 1)
        let initialized = ForegroundActivation.perform(
            request, now: { 1 }, identity: { _ in identity }, applicationInitialized: { true }, resolve: resolve)
        precondition(!initialized.invoked && activations == 1 && resolves == 1)
        let changed = NativeProcessIdentity(
            pid: identity.pid, parent: identity.parent, seconds: identity.seconds + 1,
            microseconds: identity.microseconds, executable: identity.executable)
        var reads = 0
        let replaced = ForegroundActivation.perform(
            request, now: { 1 },
            identity: { _ in
                reads += 1
                return reads == 1 ? identity : changed
            }, applicationInitialized: { false }, resolve: resolve)
        precondition(!replaced.invoked && activations == 1 && resolves == 2 && reads == 2)

        let executable = URL(fileURLWithPath: arguments[0]).standardizedFileURL
        func currentRequest(_ duration: TimeInterval = 2) -> ForegroundActivation.Request {
            .init(identity: identity, deadline: ProcessInfo.processInfo.systemUptime + duration)
        }
        for accepted in ["true", "false"] {
            let receipt = try await ForegroundActivation.run(
                request: currentRequest(), executable: executable, arguments: ["--activation-fixture", accepted],
                check: {})
            precondition(receipt.invoked && receipt.accepted == (accepted == "true"))
        }
        for outcome in ["lost", "oversize"] {
            do {
                _ = try await ForegroundActivation.run(
                    request: currentRequest(), executable: executable, arguments: ["--activation-fixture", outcome],
                    check: {})
                preconditionFailure("missing receipt admitted")
            } catch let error as DriverError {
                precondition(error.code == .executionUnknown && error.effect == .possible)
            }
        }
        do {
            _ = try await ForegroundActivation.run(
                request: currentRequest(), executable: executable, arguments: ["--activation-fixture", "rejected"],
                check: {})
            preconditionFailure("known uninvoked child admitted")
        } catch let error as DriverError { precondition(error.code == .staleTarget && error.effect != .possible) }
        do {
            _ = try await ForegroundActivation.run(
                request: currentRequest(), executable: URL(fileURLWithPath: "/no-such-activation-helper"),
                arguments: [], check: {})
            preconditionFailure("spawn failure admitted")
        } catch let error as DriverError { precondition(error.code == .inputUnavailable && error.effect != .possible) }

        let directory = FileManager.default.temporaryDirectory.appendingPathComponent(
            "foreground-activation-" + UUID().uuidString)
        try FileManager.default.createDirectory(at: directory, withIntermediateDirectories: false)
        defer { try? FileManager.default.removeItem(at: directory) }
        for cancel in [true, false] {
            let marker = directory.appendingPathComponent(cancel ? "cancel" : "deadline")
            var cleanupUnknown = false
            let task = Task { @MainActor in
                try await ForegroundActivation.run(
                    request: currentRequest(cancel ? 2 : 0.25), executable: executable,
                    arguments: ["--activation-fixture", "hold", marker.path], check: { try Task.checkCancellation() },
                    onCleanupUnknown: { cleanupUnknown = true })
            }
            for _ in 0..<100 {
                if FileManager.default.fileExists(atPath: marker.path) { break }
                try await Task.sleep(for: .milliseconds(10))
            }
            let child = pid_t(try String(contentsOf: marker, encoding: .utf8))!
            if cancel { task.cancel() }
            switch await task.result {
            case .success: preconditionFailure("cancelled/expired helper returned")
            case .failure(let error):
                precondition(cancel ? error is CancellationError : error is RequestDeadlineExceeded)
            }
            precondition(!cleanupUnknown && kill(child, 0) == -1 && errno == ESRCH)
        }
        var checks = 0
        do {
            _ = try await ForegroundActivation.run(
                request: currentRequest(), executable: executable, arguments: ["--activation-fixture", "true"],
                check: {
                    checks += 1
                    throw CancellationError()
                })
            preconditionFailure("pre-spawn cancellation ignored")
        } catch is CancellationError { precondition(checks == 1) }
        precondition(NSApp == nil)
        print("ForegroundActivationTests passed (no desktop activation/input)")
    }
}
