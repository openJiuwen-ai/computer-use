// Copyright (c) Huawei Technologies Co., Ltd. 2026. All rights reserved.

import AppKit
import ApplicationServices

struct NativeFocusSnapshot {
    let foreground: Bool
    let reportedActive: Bool?
    let focusedWindow: CGWindowID?
    let prepared: Bool
}

struct NativeFocusOutcome {
    enum WaitEnd: String { case notNeeded, activeObserved, providerUnknown, exhausted }
    let initial: NativeFocusSnapshot
    let final: NativeFocusSnapshot
    let waitEnd: WaitEnd
}

/// One selected window's local preparation; no route selection or requested input.
@MainActor struct NativeFocusRequest {
    let target: BackgroundTarget
    let window: WindowGeometry.WindowMapping
    let element: AXUIElement?
    let needsKeyWindow: Bool
    let check: @MainActor () throws -> Void
    let onPreparation: @MainActor (String) -> Void
    let onCleanupUnknown: @MainActor () -> Void
    var onNativePreparation: @MainActor (NativePreparationEffect) -> Void = { _ in }
}

struct NativeFocusPlan {
    let notify: Bool
    let selectWindow: Bool
}

/// Narrow native stage ports. The parent owns order and the unchanged Plan;
/// each implementation retains the same immutable original-window request.
@MainActor struct NativeFocusStages {
    let begin: @MainActor (NativeFocusRequest) throws -> Void
    let applicationContext: @MainActor (NativeFocusRequest) async throws -> Void
    let sample: @MainActor (NativeFocusRequest) throws -> NativeFocusSnapshot
    let notify: @MainActor (NativeFocusRequest) throws -> Void
    let selectWindow: @MainActor (NativeFocusRequest) throws -> Void
    let settle: @MainActor (NativeFocusRequest) async throws -> Void
    let confirm: @MainActor (NativeFocusRequest) throws -> Void
    var finishPreparation: @MainActor (NativeFocusRequest) async throws -> Bool = { _ in false }
}
