import AppKit
import ApplicationServices

/// Control borrows fixed-scope resolution and original resource owners.
@MainActor struct NativeControlAccess {
    let scopes: any NativeScopeAccess
    let units: any NativeInputUnitAccess
    let captures: NativeCapture
    let desktop: () -> DesktopCapture
    let objects: any NativeControlObjectAccess
    let sharing: WindowSharing
    let contracts: any NativeContractLookup
    let recordEffect: () -> Void
}
