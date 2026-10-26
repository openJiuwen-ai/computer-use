import AppKit
import ApplicationServices

/// Borrowed query capabilities; all instances are original session services.
@MainActor struct NativeQueryAccess {
    let session: String
    let scopes: any NativeScopeAccess
    let units: any NativeInputUnitAccess
    let captures: NativeCapture
    let desktop: () -> DesktopCapture
    let objects: any NativeQueryObjectAccess
    let directory: NativeDirectory
    let sharing: WindowSharing
    let contracts: any NativeContractAccess
}
