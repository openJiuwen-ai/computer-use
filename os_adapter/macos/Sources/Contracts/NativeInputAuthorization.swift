// Copyright (c) Huawei Technologies Co., Ltd. 2026. All rights reserved.

/// An immutable view of the current connection's explicit input authorization.
/// Foreground activation and addressed input do not require global authority.
/// The runtime installs this view; route preparation and dispatch only consume it.
enum NativeInputAuthorization {
    @TaskLocal static var globalInputAllowed = false
    @TaskLocal static var globalInputRequested = false

    static func requireGlobalInput(_ route: String) throws {
        guard globalInputRequested else {
            throw DriverError(
                "this macOS object operation cannot use \(route); use explicit Computer input for the current desktop recipient",
                code: .inputUnavailable, effect: FailureEffect.none)
        }
        guard globalInputAllowed else {
            throw DriverError(
                "the selected \(route) route requires global input authorization; no global input was sent",
                code: .permissionRequired, effect: FailureEffect.none,
                next: "obtain caller approval before creating a Computer with allow_global_input=True")
        }
    }
}
