// Copyright (c) Huawei Technologies Co., Ltd. 2026. All rights reserved.

import AppKit
import ApplicationServices

@MainActor enum ReadPipeline {
    static func collect(_ raw: Any?, operation: (Any?) throws -> NativeOperation, access: NativeQueryAccess) throws
        -> [String: Any]
    {
        let request = try ResolveNativeReadRequestPass.run(raw, operation: operation)
        let p = request.params, op = request.operation
        switch request.query {
        case .launch: return try CollectLaunchDeclarationPass.run(request, access: access)
        case .applications(let scope):
            return try CollectApplicationDirectoryPass.run(request, scope: scope, access: access)
        case .windowCatalogue(let appName):
            guard !op.writable, try access.scopes.directoryTarget(request.requested) != nil else {
                throw DriverError("window catalogue requires a session read scope", code: .invalidRequest)
            }
            try access.sharing.checkAvailable()
            let catalogue = try access.directory.windowCatalogue(appName: appName) {
                try access.desktop().windowCatalogue(
                    appName: appName, retainWindow: access.directory.retainDesktopWindow)
            }
            try op.check()
            return catalogue
        case .surfaceBatch(let applications, let profile):
            return try NativeSurfaceBatchPipeline.collect(request, applications: applications, profile: profile, access: access)
        case .capturedText(let ref, let offset, let length):
            return try ReadCapturedNativeTextPass.run(request, ref: ref, offset: offset, length: length, access: access)
        case .surfaces(let application, let profile):
            return try CollectNativeSurfacesPass.run(request, application: application, profile: profile, access: access)
        case .roots(let application, let applicationContent):
            return try CollectNativeRootsPass.run(
                request, application: application, applicationContent: applicationContent, access: access)
        case .relation(let property, let cursor, let limit):
            try ValidateNativeRelationProfilePass.run(access: access, property: property, cursor: cursor, limit: limit)
            return try NativeNodeQueryPipeline.collect(p["target"], op: op, access: access) { node in
                try access.objects.nativeRelation(
                    node, session: access.session, property: property, cursor: cursor, limit: limit)
            }
        case .nodes(
            let fields, let nodes, let depth, let childrenPage, let relationPage, let advertisedFields,
            let followRelations, let textContent, let actionDetail, let focusedElement, let shareAttributeCatalogues):
            try ValidateNativeNodeProfilePass.run(
                access: access, fields: fields, followRelations: followRelations, relationPage: relationPage)
            return try NativeNodeQueryPipeline.collect(
                p["target"], op: op, access: access, shareAttributeCatalogues: shareAttributeCatalogues
            ) { node in
                try access.objects.nativeFacts(
                    node, session: access.session, fields: fields, maxNodes: nodes, maxDepth: depth,
                    childrenPage: childrenPage, relationPage: relationPage, advertisedFields: advertisedFields,
                    followRelations: followRelations, textContent: textContent, actionDetail: actionDetail,
                    focusedElement: focusedElement)
            }
        case .inputContext(let within, let actuator, let receiver, let basis, let currentReceiver):
            return try NativeInputContextPipeline.collect(
                p: p, op: op, within: within, actuator: actuator, receiver: receiver, basis: basis,
                currentReceiver: currentReceiver, access: access)
        }
    }
}
