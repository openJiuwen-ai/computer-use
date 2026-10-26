import AppKit
import ApplicationServices

/// RESOURCE: publish declarations to the original operation contract owner, then preserve its existing deadline check.
@MainActor enum RegisterNativeInputContractsPass {
    static func run(_ value: NativeSampledInput, access: any NativeContractAccess) throws {
        access.replacePrimitiveContracts(value.plan.operation.token, try value.declarations.registeredContracts())
        try value.plan.operation.check()
    }
}
