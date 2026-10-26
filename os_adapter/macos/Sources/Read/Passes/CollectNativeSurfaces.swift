import AppKit
import ApplicationServices

/// QUERY/RESOURCE: collect only the requested native facts using original services and registration timing.
@MainActor enum CollectNativeSurfacesPass {
    static func run(_ request: NativeReadRequest, application: String, access: NativeQueryAccess) throws -> [String:
        Any]
    {
        let op = request.operation, requested = request.requested
        guard !op.writable, try access.scopes.directoryTarget(requested) != nil else {
            throw DriverError("directory query requires a session read scope", code: .invalidRequest)
        }
        try access.sharing.checkAvailable()
        let result = try access.directory.collectSurfaces(application)
        try op.check()
        return result
    }
}
