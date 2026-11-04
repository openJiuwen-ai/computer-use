// Copyright (c) Huawei Technologies Co., Ltd. 2026. All rights reserved.

import ApplicationServices
import Foundation

@main enum NativePhysicalInputTests {
    @MainActor static func main() async throws {
        typealias Physical = NativePhysicalInput
        let held = Physical.State(modifiers: [.maskCommand], buttons: [.left])
        let released = Physical.State(modifiers: [], buttons: [])
        for delivery in [NativeClickDelivery.objectAction, .objectSelection, .windowDirected] {
            let dependency = Physical.dependency(for: delivery)
            precondition(dependency == nil)
            var samples = 0
            try Physical.validate(dependency, sample: { samples += 1; return held })
            try await Physical.wait(dependency, check: {}, sample: { samples += 1; return held })
            precondition(samples == 0, "addressed mechanisms cannot require an idle physical keyboard/mouse")
        }
        var idleChecks = 0
        try await Physical.wait(.globalHID, check: { idleChecks += 1 }, sample: { released }, pause: { _ in
            preconditionFailure("an already released physical device cannot add a fixed wait")
        })
        precondition(idleChecks == 0, "an idle physical device cannot trigger extra scope discovery")
        precondition(Physical.dependency(for: .systemPointer) == .globalHID)
        for dependency in [Physical.Dependency.globalHID, .menuTracking] {
            do {
                try Physical.validate(dependency, sample: { held })
                preconditionFailure("shared input conflict was ignored")
            } catch let error as DriverError {
                precondition(error.code == .inputUnavailable)
                precondition(error.description.contains("modifier.command"))
                precondition(error.description.contains("mouse.button.0"))
            }
            var time = 10.0, samples = 0, checks = 0
            try await Physical.wait(
                dependency, check: { checks += 1 },
                sample: { samples += 1; return time < 10.02 ? held : released },
                clock: { time }, pause: { time += $0 })
            precondition(samples >= 2 && checks == 2 && time < 10.08,
                         "a transient gesture needs only initial/final full checks, with no scope queries in its polling loop")
            time = 10
            do {
                try await Physical.wait(
                    dependency, check: {}, sample: { held }, clock: { time }, pause: { time += $0 })
                preconditionFailure("permanently held input caused an unbounded wait")
            } catch let error as DriverError {
                precondition(error.code == .inputUnavailable && time <= 10.081)
            }
        }
        // Our paired mouse up must not conflict with our own down; unrelated
        // buttons and modifiers remain real conflicts for global injection.
        try Physical.validate(.globalHID, allowing: .left,
                              sample: { .init(modifiers: [], buttons: [.left]) })
        do {
            try Physical.validate(.globalHID, allowing: .left, sample: { held })
            preconditionFailure("allowing our button suppressed a different held modifier")
        } catch is DriverError {}
        var pauses = 0
        do {
            try await Physical.wait(.globalHID, check: { throw CancellationError() },
                                    sample: { held }, pause: { _ in pauses += 1 })
            preconditionFailure("cancelled input waited")
        } catch is CancellationError {}
        precondition(pauses == 0)
    }
}
