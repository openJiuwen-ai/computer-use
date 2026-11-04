// Copyright (c) Huawei Technologies Co., Ltd. 2026. All rights reserved.

import Foundation

/// Async App observation stays outside fixed Window/node collection. Its
/// existing session read lease cannot prepare input or rebind an old target.
@MainActor enum NativeAppChangePipeline {
    static func collect(_ raw: Any?, operation: (Any?) throws -> NativeOperation,
                        access: NativeQueryAccess) async throws -> [String: Any] {
        let p = try object(raw, keys: ["operation", "target", "query"])
        let op = try operation(p["operation"])
        try op.check()
        guard !op.writable, let target = p["target"] as? [String: Any], op.permits(target),
            try access.scopes.directoryTarget(target) != nil else {
            throw DriverError("App change observation requires its session read scope", code: .invalidRequest)
        }
        let kind = try string((p["query"] as? [String: Any])?["kind"], label: "query.kind")
        let query = try object(p["query"], keys: kind == "appChanges"
            ? ["kind", "application"] : ["kind", "application", "after", "timeoutMs"])
        let application = try string(query["application"], label: "application")
        return try await op.observe {
            if kind == "appChanges" { return try access.directory.appChanges(application) }
            guard kind == "waitAppChange" else {
                throw DriverError("unsupported App change query", code: .invalidRequest)
            }
            let timeout = try number(query["timeoutMs"], label: "timeoutMs")
            guard timeout.rounded() == timeout, (1...400).contains(timeout) else {
                throw DriverError("App change wait must be an integer in 1...400ms", code: .invalidRequest)
            }
            return try await access.directory.waitAppChange(application,
                after: string(query["after"], label: "after"), timeoutMs: Int(timeout))
        }
    }
}
