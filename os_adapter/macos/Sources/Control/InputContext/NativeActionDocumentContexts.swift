// Copyright (c) Huawei Technologies Co., Ltd. 2026. All rights reserved.

/// NativeOperation owns this evidence across capability refreshes. Current
/// primitive contracts borrow records; an absent action publishes no capability
/// but cannot erase an already observed competing document in the operation.
@MainActor final class NativeActionDocumentContexts {
    struct Scope: Equatable {
        let node: String
        let surface: String
        let within: [String]
        let actuator: NativeInspectQuery.Actuator?
        let receiver: NativeInspectQuery.Receiver?
    }
    private struct Entry {
        let scope: Scope
        let context: NativeActionDocumentContract
    }
    private var entries = [Entry]()

    func context(_ scope: Scope, observed: (any NativeActionDocumentInput)?) -> NativeActionDocumentContract? {
        if let entry = entries.first(where: { $0.scope == scope }) { return entry.context }
        guard let observed else { return nil }
        let context = NativeActionDocumentContract(observed)
        entries.append(.init(scope: scope, context: context))
        return context
    }

    /// Late availability is sampled only when the operation has no original
    /// document record. Existing records never rebind to a newly read window.
    func context(_ scope: Scope, sample: () throws -> (any NativeActionDocumentInput)?) rethrows
        -> NativeActionDocumentContract?
    {
        if let retained = context(scope, observed: nil) { return retained }
        return context(scope, observed: try sample())
    }

    func clear() { entries.removeAll() }
}
