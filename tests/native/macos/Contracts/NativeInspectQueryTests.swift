// Copyright (c) Huawei Technologies Co., Ltd. 2026. All rights reserved.

import Foundation

@main struct NativeInspectQueryTests {
    static func accepted(_ query: Any) -> Bool {
        do {
            _ = try NativeInspectQuery(query)
            return true
        } catch let error as DriverError {
            precondition(error.code == .invalidRequest, "malformed query must be an invalid request")
            return false
        } catch { preconditionFailure("unexpected decoder failure: \(error)") }
    }

    @MainActor static func exactApplicationLookup() throws {
        let files = FileManager.default
        let root = files.temporaryDirectory.appendingPathComponent(UUID().uuidString)
        defer { try? files.removeItem(at: root) }
        let app = root.appendingPathComponent("Fixture.app")
        let contents = app.appendingPathComponent("Contents")
        let binaries = contents.appendingPathComponent("MacOS")
        try files.createDirectory(at: binaries, withIntermediateDirectories: true)
        let metadata = [
            "CFBundleIdentifier": "local.computer-use.test.exact-path",
            "CFBundleExecutable": "Fixture",
            "CFBundleName": "Fixture",
            "CFBundlePackageType": "APPL",
        ]
        try PropertyListSerialization.data(fromPropertyList: metadata, format: .xml, options: 0)
            .write(to: contents.appendingPathComponent("Info.plist"))
        let executable = binaries.appendingPathComponent("Fixture")
        try Data("#!/bin/sh\nexit 0\n".utf8).write(to: executable)
        try files.setAttributes([.posixPermissions: 0o755], ofItemAtPath: executable.path)
        let alias = root.appendingPathComponent("Alias.app")
        try files.createSymbolicLink(at: alias, withDestinationURL: app)

        let directory = NativeDirectory(state: NativeDesktopState(), session: "test-session")
        let result = try directory.applications(scope: .path(alias.path))
        let rows = result["applications"] as! [[String: Any]]
        precondition(result["path"] as? String == alias.path && rows.count == 1)
        precondition(rows[0]["path"] as? String == app.resolvingSymlinksInPath().path)
        precondition(rows[0]["isRunning"] as? Bool == false)
        let installation = rows[0]["id"] as! String
        let pending = try directory.applications(scope: .installation(installation))
        precondition(pending["installation"] as? String == installation)
        precondition((pending["applications"] as! [[String: Any]]).isEmpty)

        // A newly written executable at the same path is a different installation.
        try Data("#!/bin/sh\nexit 1\n# replaced\n".utf8).write(to: executable, options: .atomic)
        do {
            _ = try directory.applications(scope: .installation(installation))
            preconditionFailure("retained installation matched replacement file identity")
        } catch let error as DriverError { precondition(error.code == .staleTarget) }
    }

    static func exactEndedApplications() {
        let instances = ApplicationInstances<Int>()
        let original = instances.id(for: 1)
        let peer = instances.id(for: 2)
        precondition(instances.ended.isEmpty)
        instances.retire { $0 == 1 }
        precondition(instances.ended == [original])
        precondition(instances.instance(original) == nil && instances.instance(peer) == 2)
        instances.retire { _ in false }
        precondition(instances.ended == [original], "catalogue rereads must retain exact termination evidence")
        precondition(instances.id(for: 3) != original)
    }

    @MainActor static func main() throws {
        if CommandLine.arguments.dropFirst().first == "--stdio" {
            // The real Python encoder supplies these frames during the native
            // test run. No desktop session or application objects are involved.
            while let line = readLine() {
                let query = try JSONSerialization.jsonObject(with: Data(line.utf8), options: [.fragmentsAllowed])
                print(accepted(query) ? "true" : "false")
            }
            return
        }
        let source = URL(fileURLWithPath: CommandLine.arguments[1])
        let cases = try JSONSerialization.jsonObject(with: Data(contentsOf: source)) as! [[String: Any]]
        for test in cases { precondition(accepted(test["query"]!) == test["valid"] as! Bool, test["name"] as! String) }
        try exactApplicationLookup()
        exactEndedApplications()
        let nodes: [String: Any] = ["kind": "nodes", "fields": ["AXRole"], "maxNodes": 1, "maxDepth": 0]
        for requested in ["none", "names", "full"] {
            var raw = nodes
            raw["actionDetail"] = requested
            guard case .nodes(_, _, _, _, _, _, _, _, let detail, _) = try NativeInspectQuery(raw) else {
                preconditionFailure("lost explicit node action profile")
            }
            precondition(detail.rawValue == requested)
        }
        guard case .nodes(_, _, _, _, _, _, _, _, let defaultDetail, _) = try NativeInspectQuery(nodes) else {
            preconditionFailure("lost ordinary observation profile")
        }
        precondition(defaultDetail == .full, "ordinary observations still collect complete action descriptions")
        for invalid: Any in [NSNull(), false, 0, "partial"] {
            var raw = nodes
            raw["actionDetail"] = invalid
            precondition(!accepted(raw))
        }
        let within = (0..<17).map { "node-\($0)" }
        for visual in [false, true] {
            var raw: [String: Any] = ["kind": "surfaceBatch", "applications": ["first", "second"]]
            if visual { raw["profile"] = "visual" }
            guard case .surfaceBatch(let applications, let profile) = try NativeInspectQuery(raw) else {
                preconditionFailure("lost application batch query")
            }
            precondition(applications == ["first", "second"])
            precondition(profile == (visual ? .visual : .accessibility))
        }
        precondition(!accepted(["kind": "surfaceBatch", "applications": []]))
        precondition(!accepted(["kind": "surfaceBatch", "applications": ["same", "same"]]))
        precondition(!accepted(["kind": "surfaceBatch", "applications": (0..<65).map { "app-\($0)" }]))
        precondition(!accepted(["kind": "surfaceBatch", "applications": ["first"], "target": "other"]))
        for profile: Any in [NSNull(), false, "accessibility", "contents"] {
            precondition(!accepted(["kind": "surfaceBatch", "applications": ["first"], "profile": profile]))
        }
        precondition(accepted(["kind":"surfaces", "application":"instance:test"]))
        if case .surfaces(let application, let profile) = try NativeInspectQuery([
            "kind":"surfaces", "application":"instance:test", "profile":"visual"
        ]) { precondition(application == "instance:test" && profile == .visual) }
        else { preconditionFailure("visual directory intent lost") }
        for profile: Any in [NSNull(), false, "accessibility", "contents"] {
            precondition(!accepted(["kind":"surfaces", "application":"instance:test", "profile":profile]))
        }
        precondition(!accepted(["kind":"surfaces", "application":"instance:test", "includeAccessibility":false]))
        precondition(!accepted(["kind":"surfaces", "application":"instance:test", "profile":"visual", "includeAccessibility":false]))
        precondition(!accepted(["kind": "inputContext", "within": within]))
        if case .inputContext(let values, let actuator, _, _, _) = try NativeInspectQuery([
            "kind": "inputContext", "within": ["root", "group"]
        ]) {
            precondition(values == ["root", "group"] && actuator == nil)
        } else {
            preconditionFailure("lost context query")
        }
        if case .inputContext(let values, let actuator, _, _, _) = try NativeInspectQuery([
            "kind": "inputContext", "within": [], "actuator": ["node": "editor", "relation": "containedEditor"]
        ]) {
            precondition(values.isEmpty && actuator?.node == "editor")
        } else {
            preconditionFailure("lost selected editor")
        }
        precondition(accepted(["kind": "inputContext", "within": [], "currentReceiver": true]))
        precondition(!accepted(["kind": "inputContext", "within": [], "currentReceiver": false]))
        precondition(
            !accepted([
                "kind": "inputContext", "within": [], "currentReceiver": true,
                "actuator": ["node": "editor", "relation": "containedEditor"]
            ]))
        let scrollBasis: [[String: Any]] = [
            ["observed": "facts", "node": "region", "fields": ["AXRole"], "actions": false, "relations": []]
        ]
        for hit in ["region", "passive-label"] {
            let raw: [String: Any] = ["node": hit, "region": "region", "relation": "scrollPointer"]
            let selected = try NativeInspectQuery.Actuator(raw)
            precondition(selected.node == hit && selected.region == "region" && selected.bar == nil)
            precondition(NSDictionary(dictionary: selected.wire).isEqual(to: raw))
            precondition(Set(selected.references) == Set([hit, "region"]))
            precondition(accepted(["kind": "inputContext", "within": [], "actuator": raw, "basis": scrollBasis]))
            precondition(!accepted(["kind": "inputContext", "within": [], "actuator": raw]))
            precondition(!accepted(["kind": "inputContext", "within": [], "actuator": raw, "basis": []]))
        }
        for selected in [
            ["node": "hit", "relation": "scrollPointer"],
            ["node": "hit", "region": "area", "relation": "scrollPointer", "bar": "bar"],
            ["node": "hit", "region": "area", "relation": "scrollPointer", "x": 10],
            ["node": "hit", "region": "", "relation": "scrollPointer"]
        ] as [[String: Any]] {
            precondition(!accepted(["kind": "inputContext", "within": [], "actuator": selected, "basis": scrollBasis]))
        }
        precondition(
            !accepted([
                "kind": "inputContext", "within": [],
                "actuator": ["node": "hit", "region": "area", "relation": "scrollPointer"],
                "receiver": ["relation": "containedFocus", "node": "focus"],
                "basis": [["observed": "facts", "node": "hit", "fields": ["AXRole"], "actions": false, "relations": []]]
            ]))
        if case .capturedText(let ref, let offset, let length) = try NativeInspectQuery([
            "kind": "capturedText", "textRef": "captured-field", "offset": 3, "length": 4096
        ]) {
            precondition(ref == "captured-field" && offset == 3 && length == 4096)
        } else {
            preconditionFailure("lost captured text range")
        }
        print("PASS \(cases.count) shared native inspection request contracts")
    }
}
