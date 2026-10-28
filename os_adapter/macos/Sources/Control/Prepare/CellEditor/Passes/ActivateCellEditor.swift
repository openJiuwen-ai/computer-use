import Foundation

/// PREPARE/contentPossible: the gesture enters editing at the original cell.
/// Its caller journals this independently from requested replacement dispatch.
@MainActor enum ActivateCellEditorPass {
    static func run(
        _ plan: NativeCellEditorPreparationPlan, check: @escaping @MainActor () throws -> Void,
        onActivation: @escaping @MainActor () -> Void,
        onCleanupUnknown: @escaping @MainActor () -> Void
    ) async throws {
        try await plan.pointer.deliver(
            plan.activation, check: check, onDispatch: onActivation, onPosted: {},
            onCleanupUnknown: onCleanupUnknown)
    }
}
