// Copyright (c) Huawei Technologies Co., Ltd. 2026. All rights reserved.

import AppKit
import ApplicationServices

/// QUERY/RESOURCE: resolve and retain before the original desktop lease; admission has not occurred on failure.
@MainActor enum ResolveNativeResourceTargetPass {
    static func run(_ begin: NativeBeginRequest, access: NativeResourceAccess) throws -> NativeResourceTarget {
        let p = begin.value, mode = begin.mode, intent = begin.intent.kind
        let installation = intent == "launch" ? try access.directory.installation(p["target"]) : nil
        let discovery = installation == nil ? try access.scopes.directoryTarget(p["target"]) : nil
        let visualTarget = (p["target"] as? [String: Any]).flatMap { $0["capture"] is String ? $0 : nil }
        let visual = visualTarget != nil
        let windowScope =
            installation == nil && discovery == nil && !visual && (p["target"] as? [String: Any])?["node"] is NSNull
        let initial: [String: Any]
        let releaseNode: () -> Void
        if let installation {
            try access.sharing.checkAvailable()
            initial = installation.wire
            releaseNode = {}
        } else if let discovery {
            guard mode == "read" else {
                throw DriverError("unbound discovery cannot authorize input", code: .invalidRequest)
            }
            initial = discovery
            releaseNode = {}
        } else if let visualTarget {
            let desktop = access.scopes.isDesktop(visualTarget)
            guard mode == "write",
                NativePointerIntent.Kind(rawValue: intent) != nil || desktop && ["insert", "key"].contains(intent)
            else {
                throw DriverError(
                    "image input supports pointer intents; desktop input also supports insertion and keys",
                    code: .invalidRequest)
            }
            try access.sharing.checkAvailable()
            initial = try access.scopes.inputBinding(p["target"]).wire
            releaseNode = {}
        } else if windowScope {
            try access.sharing.checkAvailable()
            initial =
                mode == "read"
                ? try access.scopes.readScope(p["target"]).wire
                : try access.scopes.inputScope(p["target"], canonicalize: true).wire
            releaseNode = {}
        } else {
            try access.sharing.checkAvailable()
            let resolved: (wire: [String: Any], node: String)
            if mode == "read" {
                resolved = try access.scopes.observationTarget(p["target"])
            } else {
                let input = try access.scopes.target(p["target"], canonicalize: true)
                resolved = (input.wire, input.node)
            }
            guard let retained = access.objects.retainNode(resolved.node) else {
                throw DriverError("operation target expired before retention", code: .staleTarget)
            }
            initial = resolved.wire
            releaseNode = retained
        }
        return NativeResourceTarget(
            initial: initial, releaseNode: releaseNode, installation: installation != nil, discovery: discovery != nil,
            visual: visual, windowScope: windowScope)

    }
}
