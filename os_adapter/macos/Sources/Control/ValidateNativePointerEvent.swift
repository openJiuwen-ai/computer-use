// Copyright (c) Huawei Technologies Co., Ltd. 2026. All rights reserved.

import Foundation

/// One non-release event's query/check boundary. It owns no reusable facts or
/// input: the sequence immediately marks/posts after this synchronous pass.
@MainActor enum ValidateNativePointerEventPass {
    static func run(
        kind: NativePointerIntent.Kind, continuingDrag: Bool,
        guardOperation: () throws -> Void, checkTarget: () throws -> Void,
        queryRecipient: () throws -> Void, queryReadiness: () throws -> Bool,
        checkPhysicalInput: () throws -> Void, unavailable: () -> Error
    ) throws {
        // Click queries neither prepare nor dispatch. Their final original
        // target check remains after those queries; do not repeat it before.
        if kind == .click { try guardOperation() }
        else { try checkTarget() }
        try queryRecipient()
        if kind != .click { try checkPhysicalInput() }
        // A held captured drag keeps its original receiver and screen path.
        // Its first scope check still applies; z-order is not a new down hit.
        if continuingDrag { return }
        let ready = try queryReadiness()
        try checkTarget()
        guard ready else { throw unavailable() }
        // A real conflict may arrive while AX queries are in flight. Paired
        // release is independent cleanup and never enters this admission.
        if kind == .click { try checkPhysicalInput() }
    }
}
