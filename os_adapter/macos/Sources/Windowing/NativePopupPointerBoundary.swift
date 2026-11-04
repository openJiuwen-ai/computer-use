// Copyright (c) Huawei Technologies Co., Ltd. 2026. All rights reserved.

import Foundation
import ApplicationServices

/// Production final boundary. Context constrains the original document; exact
/// current physical facts independently authorize only the selected point.
@MainActor enum NativePopupPointerBoundary {
    static func validate<Node, Process>(
        _ proof: NativePopupPointerProof<Node, Process>, original: NativePopupOwnerStamp<Node, Process>?, leaf: Node,
        ancestors: [Node], popup: Node, currentOwner: Node?, facts: NativePopupFacts<Node, Process>,
        sample: () throws -> NativePopupPointerSample<Node>, same: (Node, Node) -> Bool,
        ownerBoundary: (Node, Node) throws -> [String]
    ) throws -> [String] {
        guard let original, original.matches(proof.context, same: same), same(leaf, proof.element), let currentOwner,
            try NativePopupAttachment.confirms(
                original, leaf: leaf, ancestors: ancestors, popup: popup, currentOwner: currentOwner, facts: facts,
                same: same)
        else { throw DriverError("the original popup choice context changed", code: .staleTarget) }
        let blocked = try ownerBoundary(original.popup, original.owner)
        guard blocked.isEmpty else { return blocked }
        guard try proof.accepts(sample(), same: same) else {
            throw DriverError(
                "the original popup choice point is no longer physically confirmed", code: .inputUnavailable)
        }
        return []
    }
}
