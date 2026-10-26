import AppKit
import ApplicationServices

@MainActor struct NativeLaunchAccess {
    let sharing: WindowSharing
    let directory: NativeDirectory
    let contracts: any NativeContractLookup
    let discovery: ApplicationDiscovery
    let recordEffect: () -> Void
}
