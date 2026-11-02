// Copyright (c) Huawei Technologies Co., Ltd. 2026. All rights reserved.

import Darwin

@main enum NativeLocalActivationLeaseTests {
    static func process(_ pid: pid_t, born: UInt64 = 1) -> NativeProcessIdentity {
        .init(pid: pid, parent: 1, seconds: born, microseconds: 0, executable: "/test/\(pid)")
    }

    static func main() {
        let a = process(101), b = process(102), replacement = process(101, born: 2)
        var leases = NativeLocalActivationLeases()
        precondition(!leases.isCurrent(a, current: a, foregroundPID: 200))
        precondition(leases.takeAll().isEmpty, "no actual activation post grants no cleanup ownership")

        leases.recordPosted(a)
        precondition(leases.isCurrent(a, current: a, foregroundPID: 200))
        precondition(leases.isCurrent(a, current: a, foregroundPID: 200), "querying does not consume a lease")
        precondition(!leases.isCurrent(a, current: replacement, foregroundPID: 200))
        precondition(!leases.isCurrent(a, current: nil, foregroundPID: 200))
        precondition(!leases.isCurrent(a, current: a, foregroundPID: nil))
        precondition(!leases.isCurrent(a, current: a, foregroundPID: a.pid))
        leases.recordPosted(a)
        precondition(leases.takeAll() == [a], "repeated posting retains one process lease")
        precondition(leases.takeAll().isEmpty, "repeated stop cannot replay deactivation")

        for (current, foreground, expected): (NativeProcessIdentity?, pid_t?, NativeLocalActivationLeases.Cleanup) in [
            (a, 200, .deactivate), (a, a.pid, .foreground), (replacement, 200, .processChanged),
            (nil, 200, .processUnavailable), (a, nil, .foregroundUnavailable)
        ] {
            precondition(NativeLocalActivationLeases.cleanup(a, current: current, foregroundPID: foreground) == expected)
        }

        leases.recordPosted(a)
        leases.recordPosted(b)
        precondition(leases.take(a.pid, matching: a) == a, "provider deactivation drops only its own lease")
        precondition(leases.takeAll() == [b])
        leases.recordPosted(a)
        precondition(leases.recordPosted(replacement) == a)
        precondition(leases.take(a.pid, matching: a) == nil, "an old observer cannot remove a new incarnation")
        precondition(leases.take(a.pid, matching: replacement) == replacement)

        // A's real activation notification was queued; B is frontmost by the
        // time it is handled. Its payload, not that later snapshot, ends A's
        // synthetic ownership, so neither process receives an inactive event.
        leases.recordPosted(a)
        leases.recordPosted(b)
        precondition(leases.takeForWorkspaceActivation(a) == a)
        let delayedActivationCleanup = leases.takeAll().filter {
            NativeLocalActivationLeases.cleanup($0, current: $0, foregroundPID: b.pid) == .deactivate
        }
        precondition(delayedActivationCleanup.isEmpty, "a queued real activation must not be synthetically revoked")
        leases.recordPosted(replacement)
        precondition(leases.takeForWorkspaceActivation(a) == nil, "old activation payload cannot claim a new incarnation")
        precondition(leases.takeForWorkspaceActivation(nil) == nil, "missing payload identity grants no ownership")
        precondition(leases.takeAll() == [replacement])

        // The coordinator drains before posting. A synchronous provider callback
        // or another stop during cleanup therefore cannot acquire a second send.
        leases.recordPosted(a)
        leases.recordPosted(b)
        var sent: [pid_t] = []
        for retained in leases.takeAll() {
            if NativeLocalActivationLeases.cleanup(retained, current: retained, foregroundPID: b.pid) == .deactivate {
                precondition(leases.take(retained.pid) == nil)
                precondition(leases.takeAll().isEmpty)
                sent.append(retained.pid)
            }
        }
        precondition(sent == [a.pid], "workspace activation must not deactivate the actual foreground process")
        precondition(leases.takeAll().isEmpty)
        print("PASS local activation lease ownership and guarded cleanup")
    }
}
