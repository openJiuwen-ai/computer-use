// Copyright (c) Huawei Technologies Co., Ltd. 2026. All rights reserved.

/// Availability of independent native mechanisms, not evidence that an
/// application will consume an event. No route borrows another route's SPI.
struct NativePointerCapabilities: Equatable {
    let windowDirectedTransport: Bool
    let syntheticFocusNotifications: Bool
    let windowServerHit: Bool
    let foregroundWindowPreparation: Bool
}
