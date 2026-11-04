// Copyright (c) Huawei Technologies Co., Ltd. 2026. All rights reserved.

import AppKit

/// One window image's pixel basis, resolved once for an ensuing pointer gesture.
/// The retained image is immutable; only this operation-local frame may differ.
@MainActor struct NativeCapturedPointerPlan {
    let mapping: WindowGeometry.WindowMapping

    static func resolve(
        original: WindowGeometry.WindowMapping, current: WindowGeometry.WindowMapping
    ) throws -> Self {
        guard current.id == original.id, current.pid == original.pid else {
            throw ObservationChanged("window disappeared or owner changed")
        }
        guard [current.frame.minX, current.frame.minY, current.frame.width, current.frame.height].allSatisfy(\.isFinite),
            current.frame.size == original.frame.size, current.frame.width > 0, current.frame.height > 0,
            original.width > 0, original.height > 0
        else {
            throw ObservationChanged("window disappeared or geometry changed")
        }
        return Self(mapping: .init(
            id: original.id, pid: original.pid, frame: current.frame, title: original.title,
            width: original.width, height: original.height))
    }

    func requireCurrent(_ current: WindowGeometry.WindowMapping, continuingDrag: Bool = false) throws {
        guard current.id == mapping.id, current.pid == mapping.pid else {
            throw ObservationChanged("pointer window disappeared or changed owner; input stopped")
        }
        guard [current.frame.minX, current.frame.minY, current.frame.width, current.frame.height].allSatisfy(\.isFinite),
            current.frame.width > 0, current.frame.height > 0
        else { throw ObservationChanged("pointer window geometry is unavailable; input stopped") }
        // The image fixes admission coordinates. Once this drag has posted its
        // own down, the same receiving window may move or resize. The original
        // screen path and image-to-screen mapping are never recomputed here.
        guard continuingDrag || current.frame == mapping.frame else {
            throw ObservationChanged("window geometry changed during the pointer sequence; input stopped")
        }
    }

    func point(_ pixel: CGPoint) throws -> CGPoint {
        guard pixel.x >= 0, pixel.y >= 0, pixel.x < Double(mapping.width), pixel.y < Double(mapping.height) else {
            throw DriverError("pointer coordinates are outside the retained window image", code: .invalidRequest)
        }
        return CGPoint(
            x: mapping.frame.minX + pixel.x * mapping.frame.width / Double(mapping.width),
            y: mapping.frame.minY + pixel.y * mapping.frame.height / Double(mapping.height))
    }
}
