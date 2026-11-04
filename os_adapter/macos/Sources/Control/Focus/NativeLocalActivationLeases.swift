// Copyright (c) Huawei Technologies Co., Ltd. 2026. All rights reserved.

import Darwin

/// Submission ownership only. A retained lease never proves that the process
/// consumed its local activation notification or became an input receiver.
struct NativeLocalActivationLeases {
    enum Cleanup: String {
        case deactivate
        case foreground
        case processChanged
        case processUnavailable
        case foregroundUnavailable
    }

    private var posted: [pid_t: NativeProcessIdentity] = [:]

    @discardableResult mutating func recordPosted(_ process: NativeProcessIdentity) -> NativeProcessIdentity? {
        posted.updateValue(process, forKey: process.pid)
    }

    func isCurrent(_ process: NativeProcessIdentity, current: NativeProcessIdentity?, foregroundPID: pid_t?) -> Bool {
        posted[process.pid] == process
            && Self.cleanup(process, current: current, foregroundPID: foregroundPID) == .deactivate
    }

    /// Remove ownership before any cleanup callback can reenter the coordinator.
    mutating func take(_ pid: pid_t, matching process: NativeProcessIdentity? = nil) -> NativeProcessIdentity? {
        guard let retained = posted[pid], process == nil || process == retained else { return nil }
        return posted.removeValue(forKey: pid)
    }

    /// A real activation notification supersedes synthetic ownership even if
    /// another app has become frontmost before its queued callback is handled.
    mutating func takeForWorkspaceActivation(_ process: NativeProcessIdentity?) -> NativeProcessIdentity? {
        guard let process else { return nil }
        return take(process.pid, matching: process)
    }

    mutating func takeAll() -> [NativeProcessIdentity] {
        let result = posted.values.sorted { $0.pid < $1.pid }
        posted.removeAll(keepingCapacity: true)
        return result
    }

    static func cleanup(_ process: NativeProcessIdentity, current: NativeProcessIdentity?, foregroundPID: pid_t?)
        -> Cleanup
    {
        guard let current else { return .processUnavailable }
        guard current == process else { return .processChanged }
        guard let foregroundPID else { return .foregroundUnavailable }
        return foregroundPID == process.pid ? .foreground : .deactivate
    }
}
