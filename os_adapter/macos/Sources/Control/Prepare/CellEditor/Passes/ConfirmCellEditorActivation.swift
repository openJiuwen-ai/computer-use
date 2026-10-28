import Foundation

/// QUERY/RESOURCE: publish only a complete live cell-to-editor ownership proof.
@MainActor enum ConfirmCellEditorActivationPass {
    static func run(_ plan: NativeCellEditorPreparationPlan, owner: NativeCellEditorState) throws -> Bool {
        guard try plan.handoff.prove(
            NativeReceiverSnapshot.read(application: plan.application, window: plan.window)) != nil
        else { return false }
        owner.retain(plan.handoff)
        return true
    }
}
