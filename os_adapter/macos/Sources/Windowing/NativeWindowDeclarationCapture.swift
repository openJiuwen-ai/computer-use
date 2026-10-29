// Copyright (c) Huawei Technologies Co., Ltd. 2026. All rights reserved.

import ApplicationServices

/// Passive raw results from actual WindowGeometry AXWindows getters. No AX
/// read, PID lookup, filtering or parsing runs inside the observer.
typealias NativeWindowsReadObserver = @MainActor (pid_t, AXUIElement, AXRead<CFTypeRef>) -> Void

@MainActor final class NativeWindowDeclarationCapture {
    private struct Root: Equatable {
        let pid: pid_t
        let element: AXUIElement
        static func == (a: Self, b: Self) -> Bool { a.pid == b.pid && CFEqual(a.element, b.element) }
    }
    private var attempt: NativeWindowDeclarationAttempt<Root, NativeProcessIdentity, AXRead<CFTypeRef>>

    init(root: AXUIElement, process: NativeProcessIdentity) {
        attempt = .init(root: Root(pid: process.pid, element: root), owner: process)
    }

    func record(pid: pid_t, root: AXUIElement, read: AXRead<CFTypeRef>) {
        attempt.record(root: Root(pid: pid, element: root), payload: read)
    }

    func take(currentProcess: NativeProcessIdentity?) -> [AXUIElement]? {
        guard let read = attempt.take(owner: currentProcess), case .value(let raw) = read else { return nil }
        return Self.completeElements(raw)
    }

    func close() { attempt.close() }

    /// Validate the entire raw array. Swift's conditional CF collection bridge
    /// alone does not establish every member's native type. Empty is complete.
    static func completeElements(_ raw: CFTypeRef) -> [AXUIElement]? {
        guard CFGetTypeID(raw) == CFArrayGetTypeID(), let members = raw as? [AnyObject] else { return nil }
        var elements = [AXUIElement]()
        for member in members {
            guard CFGetTypeID(member) == AXUIElementGetTypeID() else { return nil }
            elements.append(unsafeDowncast(member, to: AXUIElement.self))
        }
        return elements
    }
}
