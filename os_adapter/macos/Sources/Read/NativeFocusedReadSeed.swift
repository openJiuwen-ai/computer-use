// Copyright (c) Huawei Technologies Co., Ltd. 2026. All rights reserved.

import ApplicationServices

/// Prove an application-reported focus belongs to the observed root by its real Parent chain.
@MainActor enum NativeFocusedReadSeed {
    static func path(
        from focus: AXUIElement, to root: AXUIElement, maximumDepth: Int,
        parent: (AXUIElement) throws -> AXUIElement?, canContinue: () throws -> Bool
    ) throws -> [AXUIElement]? {
        var path = [AXUIElement]()
        var current = focus
        while try canContinue() {
            if CFEqual(current, root) { return path }
            guard path.count < maximumDepth, !path.contains(where: { CFEqual($0, current) }) else { return nil }
            path.append(current)
            guard let next = try parent(current) else { return nil }
            current = next
        }
        return nil
    }
}
