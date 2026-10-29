// Copyright (c) Huawei Technologies Co., Ltd. 2026. All rights reserved.

import AppKit
import ApplicationServices

/// Protocol v6 endpoint. Operation lifetime is independent of request lifetime;
/// native units never select alternative input routes on behalf of Python.
@MainActor final class NativeExecution: NativeContractAccess {
    let session: String
    let state: NativeDesktopState
    let cancelActive: () -> Void
    var current: NativeOperation?
    var pointerActivity: AgentPointer.Activity?
    var retained = [String: NativeOperation]()
    var order = [String]()
    var contracts = [String: [NativePrimitiveContract]]()
    var selections = [String: NativeSelectionBasis]()
    lazy var directory = NativeDirectory(state: state, session: session)
    lazy var captures = NativeCapture(directory: directory, sharing: state.sharing)
    var hasUnfinishedOperation: Bool { current.map { $0.state != .closed || $0.cleanup != "complete" } ?? false }

    init(state: NativeDesktopState, session: String, cancelActive: @escaping () -> Void) {
        self.state = state
        self.session = session
        self.cancelActive = cancelActive
    }

    func cancel() { current?.stop("cancelled") }
    func shutdown() {
        current?.stop("connection_closed")
        state.pointer.stop()
        SyntheticFocusCoordinator.shared.stop()
    }

    lazy var scopes = NativeScopeResolver(
        session: session, applications: state.applications, appSelections: state.appSelections, sharing: state.sharing,
        desktop: { [state] in state.desktop }, directory: directory, captures: captures, pointer: state.pointer)

    func primitiveContracts(_ token: String) -> [NativePrimitiveContract] { contracts[token] ?? [] }
    func replacePrimitiveContracts(_ token: String, _ values: [NativePrimitiveContract]) { contracts[token] = values }
    func selectionBasis(_ token: String) -> NativeSelectionBasis { selections[token]! }

    var queryAccess: NativeQueryAccess {
        NativeQueryAccess(
            session: session, scopes: scopes, units: scopes, captures: captures, desktop: { [state] in state.desktop },
            objects: state.applications, directory: directory, sharing: state.sharing, contracts: self)
    }
    var controlAccess: NativeControlAccess {
        NativeControlAccess(
            scopes: scopes, units: scopes, captures: captures, desktop: { [state] in state.desktop },
            objects: state.applications, sharing: state.sharing, contracts: self, recordEffect: state.recordEffect)
    }
    var launchAccess: NativeLaunchAccess {
        NativeLaunchAccess(
            sharing: state.sharing, directory: directory, contracts: self, discovery: state.discovery,
            recordEffect: state.recordEffect)
    }
    var resourceAccess: NativeResourceAccess {
        NativeResourceAccess(
            directory: directory, scopes: scopes, sharing: state.sharing, objects: state.applications,
            hasUnfinishedOperation: { [weak self] in self?.hasUnfinishedOperation ?? false },
            register: registerNativeResourceScope, cancelActive: cancelActive)
    }

    func operation(_ value: Any?) throws -> NativeOperation {
        let token = try string(value, label: "operation")
        guard let operation = retained[token] else { throw DriverError("native operation expired", code: .staleTarget) }
        return operation
    }

    func registerNativeResourceScope(
        _ op: NativeOperation, selection: NativeSelectionBasis, mode: String, intent: String
    ) {
        let activity = mode == "write" && intent != "launch" ? state.pointer.begin() : nil
        pointerActivity = activity
        op.onClose = { success in activity?.finish(success: success) }
        current = op
        retained[op.token] = op
        order.append(op.token)
        contracts[op.token] = []
        selections[op.token] = selection
        while order.count > 8 {
            let token = order.removeFirst()
            retained.removeValue(forKey: token)
            contracts.removeValue(forKey: token)
            selections.removeValue(forKey: token)
        }
    }

    func perform(_ method: String, _ raw: Any?, timeoutMs: Int) async throws -> [String: Any] {
        let token = (raw as? [String: Any])?["operation"] as? String
        let activity = token == current?.token ? pointerActivity : nil
        return try await AgentPointer.$current.withValue(activity) {
            do {
                return try await DriverPipeline.perform(
                    method, raw, timeoutMs: timeoutMs, operation: self.operation, resource: self.resourceAccess,
                    query: self.queryAccess, control: self.controlAccess, launch: self.launchAccess)
            } catch {
                activity?.recordOutcome(success: false)
                throw error
            }
        }
    }
}
