// Copyright (c) Huawei Technologies Co., Ltd. 2026. All rights reserved.

import Foundation

/// Concurrent preparation and pixels with one structured lifetime. A failed
/// branch cancels its sibling and waits for its actual completion; publication
/// remains outside this join, after both successes and original-scope checks.
@MainActor enum WindowCaptureJoin {
    private enum Result<Value: Sendable>: Sendable {
        case sharingReady
        case pixels(Value)
    }
    static func run<Value: Sendable>(
        check: @MainActor @Sendable @escaping () throws -> Void,
        include: @MainActor @Sendable @escaping () async throws -> Void,
        pixels: @MainActor @Sendable @escaping () async throws -> Value
    ) async throws -> Value {
        try Task.checkCancellation()
        try check()
        let image = try await withThrowingTaskGroup(of: Result<Value>.self) { group in
            group.addTask {
                try Task.checkCancellation()
                try await check()
                try await include()
                try Task.checkCancellation()
                try await check()
                return .sharingReady
            }
            group.addTask {
                try Task.checkCancellation()
                try await check()
                let image = try await pixels()
                try Task.checkCancellation()
                try await check()
                return .pixels(image)
            }
            var image: Value?
            while let result = try await group.next() {
                if case .pixels(let value) = result { image = value }
            }
            return image!
        }
        try Task.checkCancellation()
        try check()
        return image
    }
}
