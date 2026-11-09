// Copyright (c) Huawei Technologies Co., Ltd. 2026. All rights reserved.

import AppKit

/// Adapter for the selected, already-visible original popup point. It shares
/// the ordinary element pointer/event pipeline; it has no preparation or retry.
@MainActor protocol NativePopupPointerInput {
    func deliver(
        _ request: NativePointerIntent, check: @escaping @MainActor () throws -> Void, onDispatch: () throws -> Void,
        onPosted: () -> Void, onCleanupUnknown: @escaping @MainActor () -> Void) async throws
}

@MainActor struct NativePopupPointerUnit: NativePopupPointerInput {
    let element: NativeElementPointerUnit
    let qualification: NativePopupPointerQualification

    func deliver(
        _ request: NativePointerIntent, check: @escaping @MainActor () throws -> Void, onDispatch: () throws -> Void,
        onPosted: () -> Void, onCleanupUnknown: @escaping @MainActor () -> Void
    ) async throws {
        guard request.kind == .click, request.button == "left", request.count == 1 else {
            throw DriverError("popup point qualification belongs to one primary click", code: .invalidRequest)
        }
        var fixed = qualification.context.kind == .submenu
            ? element.requiring(.contentReceiver, exactElementHit: true) : element
        fixed.frozenGeometry = qualification.geometry
        try await fixed.deliver(
            request, check: check, guardOperation: check, onDispatch: onDispatch, onPosted: onPosted,
            onCleanupUnknown: onCleanupUnknown,
            delivery: .windowDirected)
    }
}
