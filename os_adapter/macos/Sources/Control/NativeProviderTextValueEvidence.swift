// Copyright (c) Huawei Technologies Co., Ltd. 2026. All rights reserved.

import Foundation
import CoreGraphics

/// PURE: pinned Chromium Cocoa positive evidence for one addressed whole-value
/// replacement. Missing evidence only removes this new offer, never an old
/// property or keyboard offer. No app name, DOM id, URL or current text enters
/// the returned equality contract.
enum NativeProviderTextValueEvidence {
    enum OptionalAttribute<Value: Equatable>: Equatable { case value(Value), absent, unknown }
    enum Bounds: Equatable {
        case rectangle(CGRect)
        case unavailable(Int32)
        case malformed
    }
    struct ScopeFacts {
        var presenterPID: Int32
        var elementPublicPID: Int32?
        var elementActualPID: Int32?
        var windowPublicPID: Int32?
        var webAreaPublicPID: Int32?
        var webAreaActualPID: Int32?
        var originalWindowID: UInt32?
        var elementWindowID: UInt32?
        var webAreaWindowID: UInt32?
        var reachedOriginalWindow: Bool
        var exactlyOneWebArea: Bool
        var elementProviderNodeID: String?
        var webAreaProviderNodeID: String?
    }
    struct Facts {
        var role: String?
        var subrole: OptionalAttribute<String>
        var valueWritable: Bool?
        var enabled: Bool?
        var editable: OptionalAttribute<Bool>
        var valueUTF16Length: Int?
        var rangeLocation: Int
        var rangeLength: Int
        var bounds: Bounds
        var scope: ScopeFacts
    }
    struct Proof: Equatable {
        let presenterPID: Int32
        let windowID: UInt32
        let elementProviderNodeID: Int32
        let webAreaProviderNodeID: Int32
        fileprivate init(presenterPID: Int32, windowID: UInt32, element: Int32, webArea: Int32) {
            self.presenterPID = presenterPID
            self.windowID = windowID
            self.elementProviderNodeID = element
            self.webAreaProviderNodeID = webArea
        }
    }
    static func nodeID(_ value: String?) -> Int32? {
        guard let value, !value.isEmpty, value.utf8.count <= 10, value.utf8.allSatisfy({ (48...57).contains($0) }),
            let number = Int32(value), number > 0
        else { return nil }
        return number
    }
    static func prove(_ facts: Facts) -> Proof? {
        guard facts.role == "AXTextField", facts.valueWritable == true, facts.enabled == true else { return nil }
        switch facts.subrole {
        case .absent, .value(""): break
        default: return nil  // Includes search/secure and uninterpreted subroles.
        }
        switch facts.editable {
        case .absent, .value(true): break
        default: return nil
        }
        guard let length = facts.valueUTF16Length, length > 0, facts.rangeLocation == 0,
            (1...2).contains(facts.rangeLength), facts.rangeLength <= length, case .rectangle(let rect) = facts.bounds,
            !rect.isNull, !rect.isInfinite,
            [rect.origin.x, rect.origin.y, rect.size.width, rect.size.height].allSatisfy({ $0.isFinite }),
            rect.size.width > 0, rect.size.height > 0
        else { return nil }
        // Runtime negatives returned success + a finite non-null 0x0 rect.
        // Neither API success, non-null nor a parameter-name declaration alone
        // is this positive. Native-empty shares that unknown result for now.
        let scope = facts.scope, pid = scope.presenterPID
        guard pid > 0, scope.elementPublicPID == pid, scope.elementActualPID == pid, scope.windowPublicPID == pid,
            scope.webAreaPublicPID == pid, scope.webAreaActualPID == pid, let window = scope.originalWindowID,
            window != 0, scope.elementWindowID == window, scope.webAreaWindowID == window, scope.reachedOriginalWindow,
            scope.exactlyOneWebArea, let element = nodeID(scope.elementProviderNodeID),
            let webArea = nodeID(scope.webAreaProviderNodeID), element != webArea
        else { return nil }
        return Proof(presenterPID: pid, windowID: window, element: element, webArea: webArea)
    }
}
