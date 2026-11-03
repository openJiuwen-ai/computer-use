// Copyright (c) Huawei Technologies Co., Ltd. 2026. All rights reserved.

/// Display time only. No input timing, application state or desktop policy.
struct AgentPointerVisualState {
    private(set) var active = false
    private(set) var expired = false
    private var idleSince: Double?

    mutating func begin() {
        active = true
        expired = false
        idleSince = nil
    }
    mutating func finish(at time: Double) {
        active = false
        idleSince = time
    }
    mutating func observeNewWindow(at time: Double) {
        guard !expired, idleSince == nil else { return }
        idleSince = time
    }

    /// Observation and geometry updates must not extend the last operation's lifetime.
    mutating func expireIfIdle(at time: Double) -> Bool {
        guard !active, !expired, let idleSince, time - idleSince >= 30 else { return false }
        expired = true
        return true
    }

    func opacity(at time: Double) -> Float {
        guard !active, let idleSince, time - idleSince >= AgentPointerAssets.idleDelay else { return 1 }
        return Float(AgentPointerAssets.idleOpacity)
    }
}
