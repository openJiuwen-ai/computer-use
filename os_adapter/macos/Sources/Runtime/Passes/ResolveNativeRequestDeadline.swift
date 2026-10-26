import Foundation

/// PURE: intersect the existing operation and request deadlines without restarting a budget.
@MainActor enum ResolveNativeRequestDeadlinePass {
    static func run(_ operation: NativeOperation, enclosing: TimeInterval?) -> TimeInterval {
        min(operation.deadline, enclosing ?? .infinity)
    }
}
