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
        case .applications: return try CollectApplicationDirectoryPass.run(request, access: access)
        case .capturedText(let ref, let offset, let length):
            return try ReadCapturedNativeTextPass.run(request, ref: ref, offset: offset, length: length, access: access)
        case .surfaces(let application):
            return try CollectNativeSurfacesPass.run(request, application: application, access: access)
        case .roots(let application):
            return try CollectNativeRootsPass.run(request, application: application, access: access)
        case .relation(let property, let cursor, let limit):
            try ValidateNativeRelationProfilePass.run(access: access, property: property, cursor: cursor, limit: limit)
            return try NativeNodeQueryPipeline.collect(p["target"], op: op, access: access) { node in
                try access.objects.nativeRelation(
                    node, session: access.session, property: property, cursor: cursor, limit: limit)
            }
        case .nodes(
            let fields, let nodes, let depth, let childrenPage, let relationPage, let advertisedFields,
            let followRelations):
            try ValidateNativeNodeProfilePass.run(
                access: access, fields: fields, followRelations: followRelations, relationPage: relationPage)
            return try NativeNodeQueryPipeline.collect(p["target"], op: op, access: access) { node in
                try access.objects.nativeFacts(
                    node, session: access.session, fields: fields, maxNodes: nodes, maxDepth: depth,
                    childrenPage: childrenPage, relationPage: relationPage, advertisedFields: advertisedFields,
                    followRelations: followRelations)
            }
        case .inputContext(let within, let actuator, let receiver, let basis, let currentReceiver):
            return try NativeInputContextPipeline.collect(
                p: p, op: op, within: within, actuator: actuator, receiver: receiver, basis: basis,
                currentReceiver: currentReceiver, access: access)
        }
    }
}
