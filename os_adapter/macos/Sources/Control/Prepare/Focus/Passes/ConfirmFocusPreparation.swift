// Copyright (c) Huawei Technologies Co., Ltd. 2026. All rights reserved.

import AppKit

/// QUERY/RESOURCE: retain transport evidence, without claiming keyboard focus.
@MainActor enum ConfirmFocusPreparationPass {
    static func run(_ request: NativeFocusRequest) throws {
        try request.check()
        try request.target.validate(input: true)
        try BackgroundEvents.requireSyntheticFocusRoute(window: request.window)
        try SyntheticFocusCoordinator.shared.remember(
            request.target, window: request.window, needsKeyWindow: request.needsKeyWindow)
    }
}
