// Copyright (c) Huawei Technologies Co., Ltd. 2026. All rights reserved.

/// The original Target owns one established proof. A failed current query
/// grants no borrow and cannot erase history or adopt the current candidate.
struct NativeCellEditorLifetime<Proof> {
    private var established: Proof?
    mutating func retain(_ proof: Proof) { established = proof }
    func resolve<Result>(_ query: (Proof) throws -> Result?) rethrows -> Result? {
        guard let established else { return nil }
        return try query(established)
    }
}
