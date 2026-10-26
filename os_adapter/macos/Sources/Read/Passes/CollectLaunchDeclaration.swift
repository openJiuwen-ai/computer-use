import AppKit
import ApplicationServices

/// QUERY/RESOURCE: collect only the requested native facts using original services and registration timing.
@MainActor enum CollectLaunchDeclarationPass {
    static func run(_ request: NativeReadRequest, access: NativeQueryAccess) throws -> [String: Any] {
        let p = request.params, op = request.operation
        guard op.writable, op.intent == "launch" else {
            throw DriverError("launch inspection requires an installation write operation", code: .invalidRequest)
        }
        let item = try access.directory.installation(p["target"])
        let unit = NativeInputUnit.launch(item.identity)
        let contract =
            access.contracts.primitiveContracts(op.token).first(where: { $0.unit == unit })
            ?? NativePrimitiveContract(
                id: UUID().uuidString, node: nil, within: [], actuator: nil, receiver: nil, basis: [], unit: unit)
        access.contracts.replacePrimitiveContracts(op.token, [contract])
        return [
            "kind": "launch", "binding": item.wire, "observed": UUID().uuidString, "targetLabel": item.name,
            "primitives": [
                unit.declaration(
                    id: contract.id, supported: true, readiness: NativeReadiness(),
                    exclusive: BackgroundTarget.isExclusive)
            ]
        ]
    }
}
