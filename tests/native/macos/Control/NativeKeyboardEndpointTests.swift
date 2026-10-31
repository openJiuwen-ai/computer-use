// Copyright (c) Huawei Technologies Co., Ltd. 2026. All rights reserved.

import AppKit

@main enum NativeKeyboardEndpointTests {
    static func main() throws {
        typealias Endpoint = NativeKeyboardEndpoint
        let pids: [Int: pid_t] = [1: 10, 2: 20, 3: 30, 4: 40]
        let parents = [1: 2, 2: 3, 3: 4]
        let kinds: [pid_t: RemoteApplicationKind] = [
            10: .unknown, 20: .prohibitedApplication, 30: .ordinaryApplication,
        ]
        var reads = [Int]()
        let service = try Endpoint.find(
            focus: 1, same: ==,
            pid: {
                reads.append($0)
                return pids[$0]
            },
            kind: { kinds[$0] ?? .unknown }, parent: { parents[$0] })
        precondition(service?.element == 2 && service?.pid == 20 && service?.kind == .prohibitedApplication)
        precondition(reads == [1, 2], "stop at the nearest event-capable endpoint")
        let ordinary = try Endpoint.find(
            focus: 3, same: ==, pid: { pids[$0] }, kind: { kinds[$0] ?? .unknown }, parent: { parents[$0] })
        precondition(ordinary?.element == 3 && ordinary?.kind == .ordinaryApplication)
        for (limit, links) in [(1, parents), (32, [1: 2, 2: 1])] {
            let unavailable = try Endpoint.find(
                focus: 1, limit: limit, same: ==, pid: { pids[$0] }, kind: { _ in .unknown }, parent: { links[$0] })
            precondition(unavailable == nil, "bounded traversal cannot manufacture an endpoint")
        }
        let missing = try Endpoint.find(
            focus: 1, same: ==, pid: { $0 == 1 ? nil : pids[$0] }, kind: { kinds[$0] ?? .unknown },
            parent: { parents[$0] })
        precondition(missing?.pid == 20, "a non-event proxy does not erase its parent endpoint")
        enum Stop: Error { case cancelled }
        do {
            _ = try Endpoint.find(
                focus: 1, same: ==, pid: { pids[$0] }, kind: { kinds[$0] ?? .unknown }, parent: { parents[$0] },
                check: { throw Stop.cancelled })
            preconditionFailure("cancellation must stop traversal")
        } catch Stop.cancelled {}

        precondition(
            RemoteApplicationKind.classify(
                available: true, finished: true, terminated: false,
                activationPolicy: 2) == .prohibitedApplication)
        for policy in [0, 1] {
            precondition(
                RemoteApplicationKind.classify(
                    available: true, finished: true, terminated: false,
                    activationPolicy: policy) == .ordinaryApplication)
        }
        precondition(
            RemoteApplicationKind.classify(
                available: false, finished: true, terminated: false,
                activationPolicy: 2) == .unknown)
        precondition(
            RemoteApplicationKind.classify(
                available: true, finished: false, terminated: false,
                activationPolicy: 2) == .unknown)
        precondition(
            RemoteApplicationKind.classify(
                available: true, finished: true, terminated: true,
                activationPolicy: 2) == .unknown)

        let unavailable = try NativeReadiness.probeRoute(
            check: {},
            probe: {
                throw DriverError("endpoint query unavailable", code: .inputUnavailable)
            })
        precondition(!unavailable)
        do {
            _ = try NativeReadiness.probeRoute(check: {}, probe: { throw Stop.cancelled })
            preconditionFailure("cancellation is not an unsupported route")
        } catch Stop.cancelled {}
        do {
            _ = try NativeReadiness.probeRoute(
                check: {},
                probe: {
                    throw DriverError("original target changed", code: .staleTarget)
                })
            preconditionFailure("identity failures must not authorize another route")
        } catch let error as DriverError { precondition(error.code == .staleTarget) }
    }
}
