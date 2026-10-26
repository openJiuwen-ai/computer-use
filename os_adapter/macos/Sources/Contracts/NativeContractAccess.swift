import AppKit
import ApplicationServices

/// Lookup cannot replace the operation's primitive declarations.
@MainActor protocol NativeContractLookup: AnyObject {
    func primitiveContracts(_ token: String) -> [NativePrimitiveContract]
    func selectionBasis(_ token: String) -> NativeSelectionBasis
}

/// Only the input-context query publisher needs the writable contract view.
@MainActor protocol NativeContractAccess: NativeContractLookup {
    func replacePrimitiveContracts(_ token: String, _ values: [NativePrimitiveContract])
}
