// Copyright (c) Huawei Technologies Co., Ltd. 2026. All rights reserved.

import AppKit

/// Sampled AppKit route classification, not proof that a key was consumed.
/// Unknown/unlaunched processes never establish the ordinary-application route.
enum RemoteApplicationKind: Equatable {
    case ordinaryApplication
    case prohibitedApplication
    case unknown

    static func classify(available: Bool, finished: Bool, terminated: Bool, activationPolicy: Int?) -> Self {
        guard available, finished, !terminated else { return .unknown }
        switch activationPolicy {
        case 0, 1: return .ordinaryApplication
        case 2: return .prohibitedApplication
        default: return .unknown
        }
    }

    @MainActor static func read(_ pid: pid_t) -> Self {
        guard let app = NSRunningApplication(processIdentifier: pid) else { return .unknown }
        // AppKit caches dynamic properties until a common-mode main-runloop
        // turn. Do not spin that loop or treat a fresh instance as a cache bypass.
        return classify(
            available: true, finished: app.isFinishedLaunching, terminated: app.isTerminated,
            activationPolicy: app.activationPolicy.rawValue)
    }
}
