// Copyright (c) Huawei Technologies Co., Ltd. 2026. All rights reserved.

import AppKit
import ApplicationServices

struct NativeInputQueryScope {
    let binding: (wire: [String: Any], blocked: [String], node: String?)
    let desktop: Bool
    let selection: NativeSelectionBasis
    let scope: String
}

@MainActor struct NativeInputContextPlan {
    let query: NativeInputQueryScope
    let operation: NativeOperation
    let within: [String]
    let actuator: NativeInspectQuery.Actuator?
    let receiver: NativeInspectQuery.Receiver?
    let basis: [NativeInspectQuery.Dependency]
}

@MainActor struct NativeSampledInput {
    let plan: NativeInputContextPlan
    let declarations: NativePrimitiveDeclarations
    let enabled: Bool?
    let foreground: Bool
    let input: [String: Any]
    let nativeRole: String?
    let actionsComplete: Bool
    let applicationMenu: Bool
}
