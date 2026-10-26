import AppKit
import ApplicationServices

@MainActor enum NativeInputContextPipeline {
    static func collect(
        p: [String: Any], op: NativeOperation, within: [String], actuator: NativeInspectQuery.Actuator?,
        receiver: NativeInspectQuery.Receiver?, basis: [NativeInspectQuery.Dependency], currentReceiver: Bool = false,
        access: NativeQueryAccess
    ) throws -> [String: Any] {
        let query = try ResolveNativeInputQueryScopePass.run(
            p: p, op: op, within: within, actuator: actuator, receiver: receiver, basis: basis, access: access)
        let plan = NativeInputContextPlan(
            query: query, operation: op, within: within, actuator: actuator, receiver: receiver, basis: basis)
        if currentReceiver { return try ObserveNativeCurrentReceiverPass.run(plan, access: access) }
        let declarations = NativePrimitiveDeclarations(
            previous: access.contracts.primitiveContracts(op.token), op: op, query: query, within: within,
            actuator: actuator, receiver: receiver, basis: basis)
        let sampled: NativeSampledInput
        if op.clickContract != nil {
            sampled = try SampleNativeClickOffersPass.run(plan, declarations: declarations, access: access)
        } else {
            sampled = try SampleNativeInputRoutesPass.run(plan, declarations: declarations, access: access)
        }
        try RegisterNativeInputContractsPass.run(sampled, access: access.contracts)
        return EncodeNativeInputContextPass.run(sampled)
    }
}
