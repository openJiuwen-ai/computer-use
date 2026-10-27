import ApplicationServices

@main enum NativeWindowDeclarationCaptureTests {
    @MainActor static func main() {
        let first = AXUIElementCreateApplication(42), second = AXUIElementCreateApplication(43)
        let valid = [first, second] as CFArray
        precondition(NativeWindowDeclarationCapture.completeElements(valid)?.count == 2)
        precondition(NativeWindowDeclarationCapture.completeElements([] as CFArray)?.isEmpty == true)
        precondition(NativeWindowDeclarationCapture.completeElements("wrong" as CFString) == nil)
        let mixed = [first, "wrong" as CFString] as CFArray
        precondition(NativeWindowDeclarationCapture.completeElements(mixed) == nil)
        let process = NativeProcessIdentity(pid: 42, parent: 1, seconds: 7, microseconds: 8, executable: "/fixture")
        let changed = NativeProcessIdentity(pid: 42, parent: 1, seconds: 9, microseconds: 8, executable: "/fixture")
        let appRoot = AXUIElementCreateApplication(42)
        let complete = NativeWindowDeclarationCapture(root: appRoot, process: process)
        // Independent application handles for the same exact PID compare by
        // CF identity rather than their allocation address.
        complete.record(pid: 42, root: AXUIElementCreateApplication(42), read: .value(valid))
        let accepted = complete.take(currentProcess: process)
        precondition(accepted?.count == 2 && CFEqual(accepted![0], first) && CFEqual(accepted![1], second))
        precondition(complete.take(currentProcess: process) == nil)
        let empty = NativeWindowDeclarationCapture(root: appRoot, process: process)
        empty.record(pid: 42, root: appRoot, read: .value([] as CFArray))
        precondition(empty.take(currentProcess: process)?.isEmpty == true)
        let capture = NativeWindowDeclarationCapture(root: appRoot, process: process)
        capture.record(pid: 42, root: appRoot, read: .value(valid))
        capture.record(pid: 42, root: appRoot, read: .unavailable(.cannotComplete))
        precondition(capture.take(currentProcess: process) == nil)
        let malformed = NativeWindowDeclarationCapture(root: appRoot, process: process)
        malformed.record(pid: 42, root: appRoot, read: .value(valid))
        malformed.record(pid: 42, root: appRoot, read: .value(mixed))
        precondition(malformed.take(currentProcess: process) == nil)
        for read: AXRead<CFTypeRef> in [.unsupported, .invalid, .unavailable(.noValue)] {
            let failed = NativeWindowDeclarationCapture(root: appRoot, process: process)
            failed.record(pid: 42, root: appRoot, read: .value(valid))
            failed.record(pid: 42, root: appRoot, read: read)
            precondition(failed.take(currentProcess: process) == nil)
        }
        let reusedPID = NativeWindowDeclarationCapture(root: appRoot, process: process)
        reusedPID.record(pid: 42, root: appRoot, read: .value(valid))
        precondition(reusedPID.take(currentProcess: changed) == nil)
        let wrongRoot = NativeWindowDeclarationCapture(root: appRoot, process: process)
        wrongRoot.record(pid: 42, root: appRoot, read: .value(valid))
        wrongRoot.record(pid: 43, root: second, read: .value(valid))
        precondition(wrongRoot.take(currentProcess: process) == nil)
        let unrelated = NativeWindowDeclarationCapture(root: appRoot, process: process)
        unrelated.record(pid: 42, root: AXUIElementCreateSystemWide(), read: .value(valid))
        precondition(unrelated.take(currentProcess: process) == nil)
        let unknownOwner = NativeWindowDeclarationCapture(root: appRoot, process: process)
        unknownOwner.record(pid: 42, root: appRoot, read: .value(valid))
        precondition(unknownOwner.take(currentProcess: nil) == nil)
        print("NativeWindowDeclarationCaptureTests passed; no AX query or input")
    }
}
