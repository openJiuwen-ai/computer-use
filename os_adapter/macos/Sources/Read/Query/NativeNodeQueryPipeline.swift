import AppKit
import ApplicationServices

@MainActor enum NativeNodeQueryPipeline {
    static func collect(
        _ raw: Any?, op: NativeOperation, access: NativeQueryAccess, collect: (String) throws -> [String: Any]
    ) throws -> [String: Any] {
        let collection = try NativeQueryPipeline.collect(
            resolveQueryScope: { try access.scopes.observationTarget(raw) },
            collectRequestedFacts: { try collect($0.node) },
            checkQueryBoundary: { _ in
                _ = try access.scopes.observationTarget(raw)
                try op.check()
            },
            registerObservedObjects: { binding, result in
                if op.writable {
                    try access.contracts.selectionBasis(op.token).retain(result)
                } else {
                    access.scopes.observePointer(binding.wire)
                }
            })
        return NativeObservationEncoding.encode(collection)
    }
}
