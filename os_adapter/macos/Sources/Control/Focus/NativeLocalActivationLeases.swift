// Copyright (c) Huawei Technologies Co., Ltd. 2026. All rights reserved.

import Darwin

/// Submission ownership only. A retained lease never proves that the process
/// consumed its local activation notification or became an input receiver.
/// Retiring a record does not authorize an opposite activation notification.
struct NativeLocalActivationLeases {
    private var posted: [pid_t: NativeProcessIdentity] = [:]

    @discardableResult mutating func recordPosted(_ process: NativeProcessIdentity) -> NativeProcessIdentity? {
        posted.updateValue(process, forKey: process.pid)
    }

    func isCurrent(_ process: NativeProcessIdentity, current: NativeProcessIdentity?, foregroundPID: pid_t?) -> Bool {
        posted[process.pid] == process && current == process && foregroundPID != nil && foregroundPID != process.pid
    }

    /// Retire only the original incarnation; this never sends input or rewinds UI.
    mutating func take(_ pid: pid_t, matching process: NativeProcessIdentity? = nil) -> NativeProcessIdentity? {
        guard let retained = posted[pid], process == nil || process == retained else { return nil }
        return posted.removeValue(forKey: pid)
    }

    /// A current real activation supersedes this target's synthetic ownership.
    /// The caller validates the notification lifetime and current foreground;
    /// an unrelated activation must leave other background receivers intact.
    mutating func takeForWorkspaceActivation(_ process: NativeProcessIdentity?) -> NativeProcessIdentity? {
        guard let process else { return nil }
        return take(process.pid, matching: process)
    }

    mutating func takeAll() -> [NativeProcessIdentity] {
        let result = posted.values.sorted { $0.pid < $1.pid }
        posted.removeAll(keepingCapacity: true)
        return result
    }
}
