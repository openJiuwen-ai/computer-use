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
        precondition(leases.takeAll().isEmpty, "no actual activation post grants no submission ownership")

        leases.recordPosted(a)
        precondition(leases.isCurrent(a, current: a, foregroundPID: 200))
        precondition(leases.isCurrent(a, current: a, foregroundPID: 200), "querying does not consume a lease")
        precondition(!leases.isCurrent(a, current: replacement, foregroundPID: 200))
        precondition(!leases.isCurrent(a, current: nil, foregroundPID: 200))
        precondition(!leases.isCurrent(a, current: a, foregroundPID: nil))
        precondition(!leases.isCurrent(a, current: a, foregroundPID: a.pid))
        leases.recordPosted(a)
        precondition(leases.takeAll() == [a], "repeated posting retains one process lease")
        precondition(leases.takeAll().isEmpty, "repeated stop cannot retire ownership twice")

        leases.recordPosted(a)
        leases.recordPosted(b)
        precondition(leases.take(a.pid, matching: a) == a, "provider deactivation drops only its own lease")
        precondition(leases.takeAll() == [b])
        leases.recordPosted(a)
        precondition(leases.recordPosted(replacement) == a)
        precondition(leases.take(a.pid, matching: a) == nil, "an old observer cannot remove a new incarnation")
        precondition(leases.take(a.pid, matching: replacement) == replacement)

        // Switching between other applications must leave this background
        // receiver usable across the open -> read -> select tool sequence.
        leases.recordPosted(a)
        leases.recordPosted(b)
        precondition(leases.takeForWorkspaceActivation(process(200)) == nil)
        precondition(leases.isCurrent(a, current: a, foregroundPID: 200))
        precondition(leases.isCurrent(b, current: b, foregroundPID: 200))
        precondition(leases.takeForWorkspaceActivation(process(201)) == nil)
        precondition(leases.isCurrent(a, current: a, foregroundPID: 201))
        precondition(leases.isCurrent(b, current: b, foregroundPID: 201))

        // A's real activation supersedes A's local submission only. It does not
        // authorize a deactivation of B or clear B's independent preparation.
        precondition(leases.takeForWorkspaceActivation(a) == a)
        precondition(!leases.isCurrent(a, current: a, foregroundPID: 201))
        precondition(leases.isCurrent(b, current: b, foregroundPID: a.pid))
        precondition(leases.takeAll() == [b])
        leases.recordPosted(replacement)
        precondition(leases.takeForWorkspaceActivation(a) == nil, "old activation payload cannot claim a new incarnation")
        precondition(leases.takeForWorkspaceActivation(nil) == nil, "missing payload identity grants no ownership")
        precondition(leases.takeAll() == [replacement])

        // Teardown only drains local tracking. It cannot manufacture an inverse
        // event that would close the menu/editor the request just opened.
        leases.recordPosted(a)
        leases.recordPosted(b)
        let retired = leases.takeAll()
        precondition(retired == [a, b])
        precondition(leases.take(a.pid) == nil)
        precondition(leases.take(b.pid) == nil)
        precondition(leases.takeAll().isEmpty)
        print("PASS local activation ownership, independent background receivers and effect-preserving retirement")
    }
}
