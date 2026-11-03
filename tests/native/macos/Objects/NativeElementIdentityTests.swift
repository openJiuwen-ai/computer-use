// Copyright (c) Huawei Technologies Co., Ltd. 2026. All rights reserved.

import AppKit
import ApplicationServices

/// Mechanism regressions use real retained AX references and the production
/// registry, with explicit provider facts. No application or desktop is opened.
@main enum NativeElementIdentityTests {
    static let rowFacts = [
        "AXRole": "AXRow", "AXIdentifier": "row-control", "AXTitle": "Before",
        "AXDescription": "Record A: seed", "AXRowIndexRange": "0:1"
    ]

    static func verifyRowPresentation() {
        let original = ElementIdentity(facts: .init(values: rowFacts))
        let changed = rowFacts.merging([
            "AXTitle": "After", "AXDescription": "Record A: seedabcd"
        ]) { _, next in next }
        precondition(original.check(.init(values: changed)) == .matched,
                     "editing a row's child may change the row presentation without changing its identity")
        precondition(original.refining(with: .init(facts: .init(values: changed))) != nil,
                     "ordinary observation must reconcile the same row after its presentation changes")
        precondition(original.check(.init(values: changed.filter {
            !["AXTitle", "AXDescription"].contains($0.key)
        }, absent: ["AXTitle", "AXDescription"])) == .matched,
                     "optional row presentation cannot become a mandatory identity query")

        let partial = ElementIdentity(facts: .init(values: ["AXTitle": "Later", "AXDescription": "Later description"]))
        precondition(partial.attributes.isEmpty,
                     "a partial row observation cannot freeze its presentation as identity")
        print("PASS row title/description are mutable presentation, including partial observations")
    }

    static func verifyStrongEvidence() {
        let strong = [
            "AXRole": "AXCell", "AXSubrole": "AXTableCell", "AXIdentifier": "original-control",
            "AXURL": "file:///original", "AXDocument": "file:///document",
            "AXRowIndexRange": "0:1", "AXColumnIndexRange": "1:1"
        ]
        let original = ElementIdentity(facts: .init(values: strong))
        for key in strong.keys {
            var changed = strong
            changed[key] = strong[key]! + "-changed"
            precondition(original.check(.init(values: changed)) == .changed,
                         "a known structural/identity contradiction must remain rejected: \(key)")
            precondition(original.refining(with: .init(facts: .init(values: changed))) == nil,
                         "re-observation cannot merge a known identity contradiction: \(key)")
            let unknown = strong.filter { $0.key != key }
            precondition(original.check(.init(values: unknown)) == .unavailable,
                         "a failed known-field query is uncertainty, not a match or destruction: \(key)")
            precondition(original.check(.init(values: unknown, absent: [key])) == .changed,
                         "explicit disappearance of a known identity constraint remains a contradiction: \(key)")
        }
        print("PASS role, identifier, URL/document and advertised position constraints remain enforced")
    }

    @MainActor static func verifyCommandPresentation() {
        for role in ["AXButton", "AXMenuItem", "AXMenuBarItem", "AXCheckBox", "AXRadioButton", "AXLink"] {
            let values = ["AXRole": role, "AXIdentifier": "control", "AXTitle": "Count 0", "AXDescription": "Off"]
            let original = ElementIdentity(facts: .init(values: values))
            for key in ["AXTitle", "AXDescription"] {
                let changed = values.merging([key: "Count 1 / On"]) { _, next in next }
                precondition(original.check(.init(values: changed)) == .matched,
                             "a counter/toggle's display can change without destroying its native control: \(role)/\(key)")
                precondition(original.refining(with: .init(facts: .init(values: changed))) != nil)
                precondition(original.check(.init(values: values.filter { $0.key != key }, absent: [key])) == .matched,
                             "optional presentation cannot make the original control permanently stale")
            }
        }
        precondition(!ElementIdentity.keys.contains("AXTitle") && !ElementIdentity.keys.contains("AXDescription"))
        precondition(NativeFactCollector.fields.contains("AXTitle") && NativeFactCollector.fields.contains("AXDescription"),
                     "raw presentation collection remains independent of native identity queries")
        print("PASS command/control labels can change while raw presentation remains observable")
    }

    static func verifyActionSelection() throws {
        precondition(NativeAction.matches([.init(name: "AXPress", description: "Current press alias")],
                                          name: "AXPress", description: "Earlier press alias"),
                     "a standard action is still identified by its declared native name")
        precondition(!NativeAction.matches([.init(name: "AXShowMenu", description: "Press")],
                                           name: "AXPress", description: "Press"),
                     "display-label freedom must not authorize a different native action")
        precondition(!NativeAction.matches([.init(name: "custom.action", description: "new alias")],
                                           name: "custom.action", description: "old alias"),
                     "selected custom action aliases retain their own route contract")
        let names = ["custom.action"]
        let matched = try NativeAction.resolveExplicit(request: "old alias", names: names, description: { _ in "old alias" })
        precondition(matched == .selected(.init(name: "custom.action", description: "old alias")))
        let changed = try NativeAction.resolveExplicit(request: "old alias", names: names, description: { _ in "new alias" })
        precondition(changed == .absent,
                     "an explicit localized request must still resolve against the current action catalogue")
        print("PASS action names and explicit/custom aliases keep their route-specific checks")
    }

    @MainActor final class Scene {
        let registry = ApplicationElements()
        let app = NSRunningApplication.current
        let window = AXUIElementCreateApplication(19100)
        let row = AXUIElementCreateApplication(19101)
        let cell = AXUIElementCreateApplication(19102)
        let field = AXUIElementCreateApplication(19103)
        let scope = "original-working-window"

        func key(_ element: AXUIElement, scope: String? = nil) -> ApplicationElements.Identity {
            .init(pid: app.processIdentifier, launched: app.launchDate, element: element, scope: scope ?? self.scope)
        }

        func target(
            _ element: AXUIElement, values: [String: String], parent: ApplicationElements.Target? = nil,
            provider: NativeProcessIdentity? = nil
        ) -> ApplicationElements.Target {
            .init(
                application: app, launched: app.launchDate, element: element, scope: scope,
                role: values["AXRole"] ?? "", identity: .init(facts: .init(values: values)),
                provider: provider, providerKnown: provider != nil,
                ancestors: parent.map { $0.ancestors + [$0.element] } ?? [],
                context: parent.map(ApplicationElements.childContext) ?? [],
                contextComplete: parent?.contextComplete ?? (values["AXRole"] == "AXWindow"))
        }

        func targets(
            rowValues: [String: String] = rowFacts, cellPosition: String = "0:1",
            fieldElement: AXUIElement? = nil, provider: NativeProcessIdentity? = nil
        ) -> (row: ApplicationElements.Target, field: ApplicationElements.Target) {
            let root = target(window, values: ["AXRole": "AXWindow", "AXDocument": "file:///original"])
            let rowTarget = target(row, values: rowValues, parent: root)
            let cellTarget = target(cell, values: [
                "AXRole": "AXCell", "AXRowIndexRange": cellPosition, "AXColumnIndexRange": "0:1"
            ], parent: rowTarget)
            return (rowTarget, target(fieldElement ?? field, values: [
                "AXRole": "AXTextField", "AXIdentifier": "field-control"
            ], parent: cellTarget, provider: provider))
        }

        func observe(_ value: ApplicationElements.Target) -> String {
            registry.observe(
                key(value.element), evidence: .init(
                    identity: value.identity, provider: value.provider, context: value.context,
                    providerKnown: value.providerKnown), value: value)
        }
    }

    @MainActor static func verifyRegistryContinuity() {
        let scene = Scene()
        let initial = scene.targets()
        let rowLife = scene.observe(initial.row)
        let life = scene.observe(initial.field)
        let alias = scene.registry.bind(life, text: "seed")!
        let rowAlias = scene.registry.bind(rowLife, text: "Before")!
        let editorState = scene.registry.target(alias)!.cellEditorState
        let release = scene.registry.retain(alias)!
        defer { release() }
        for value in ["seeda", "seedabcd", "seedabcdZ"] {
            let next = scene.targets(rowValues: rowFacts.merging([
                "AXTitle": value, "AXDescription": "Record A: " + value
            ]) { _, next in next })
            precondition(CFEqual(initial.row.element, next.row.element))
            precondition(CFEqual(initial.field.element, next.field.element))
            precondition(initial.field.context.map(\.native) == next.field.context.map(\.native),
                         "the real native parent/context identities must remain exactly the same")
            precondition(scene.observe(next.row) == rowLife,
                         "a read must not retire the original raw row for changing display text")
            precondition(scene.observe(next.field) == life,
                         "a read must preserve the original field lifetime under its edited row")
            let retained = scene.registry.target(alias)!
            precondition(CFEqual(retained.element, initial.field.element))
            precondition(retained.cellEditorState === editorState,
                         "reconcile must inherit the same sole cell-editor owner for later Tab/input")
            precondition(scene.registry.target(rowAlias) != nil)
            precondition(retained.context.count == initial.field.context.count,
                         "the fix must preserve original window/row/position context boundaries")
        }
        precondition(scene.registry.text(alias)?.value == "seed",
                     "live native reconciliation must not rewrite the immutable observed text")
        print("PASS real registry re-observation preserves raw row/field lifetimes and the original editor-state owner")
    }

    @MainActor static func verifyRegistryBoundaries() {
        for (name, rowChange, positionChange) in [
            ("identifier", ["AXIdentifier": "different-control"], "0:1"),
            ("cell position", [:], "3:1"),
            ("row role", ["AXRole": "AXGroup"], "0:1")
        ] {
            let scene = Scene()
            let original = scene.targets()
            let life = scene.observe(original.field)
            let alias = scene.registry.bind(life, text: "old")!
            let editorState = scene.registry.target(alias)!.cellEditorState
            let changed = scene.targets(
                rowValues: rowFacts.merging(rowChange) { _, next in next }, cellPosition: positionChange)
            precondition(CFEqual(original.field.element, changed.field.element))
            let nextLife = scene.observe(changed.field)
            precondition(nextLife != life && scene.registry.target(alias) == nil,
                         "same raw field does not excuse a known ancestor contradiction: \(name)")
            precondition(scene.registry.target(nextLife)!.cellEditorState !== editorState,
                         "a new logical/structural binding must not inherit the previous editor proof")
        }

        let scene = Scene()
        let original = scene.targets()
        let life = scene.observe(original.field)
        let alias = scene.registry.bind(life, text: "old")!
        let replacement = scene.targets(fieldElement: AXUIElementCreateApplication(19104))
        precondition(!CFEqual(original.field.element, replacement.field.element))
        precondition(scene.key(original.field.element) != scene.key(replacement.field.element))
        let newLife = scene.observe(replacement.field)
        precondition(newLife != life)
        precondition(CFEqual(scene.registry.target(alias)!.element, original.field.element),
                     "a new object with identical labels/ID must never replace an old alias's raw object")
        scene.registry.invalidate { $0.scope == scene.scope && CFEqual($0.element, original.field.element) }
        precondition(scene.registry.target(alias) == nil && scene.registry.target(newLife) != nil)

        let providerScene = Scene()
        let firstProvider = NativeProcessIdentity(
            pid: providerScene.app.processIdentifier, parent: 0, seconds: 1, microseconds: 0,
            executable: "/opaque-provider")
        let nextProvider = NativeProcessIdentity(
            pid: providerScene.app.processIdentifier, parent: 0, seconds: 2, microseconds: 0,
            executable: "/opaque-provider")
        let providerLife = providerScene.observe(providerScene.targets(provider: firstProvider).field)
        let providerAlias = providerScene.registry.bind(providerLife, text: "provider")!
        precondition(providerScene.observe(providerScene.targets(provider: nextProvider).field) != providerLife)
        precondition(providerScene.registry.target(providerAlias) == nil,
                     "a known provider incarnation change remains a new lifetime")
        precondition(scene.key(scene.field, scope: "other-window") != scene.key(scene.field),
                     "the same raw object cannot erase the original working scope")
        print("PASS original identifier/position/object/provider/scope boundaries remain enforced")
    }

    @MainActor static func verifyLocalContextEvolution() {
        let scene = Scene()
        func root(_ version: String) -> ApplicationElements.Target {
            scene.target(scene.window, values: [
                "AXRole": "AXWindow", "AXDocument": "file:///original",
                "AXIdentifier": version, "AXSubrole": version
            ])
        }
        func wrapped(_ object: AXUIElement, _ version: String) -> ApplicationElements.Target {
            let wrapper = scene.target(object, values: [
                "AXRole": "AXGroup", "AXIdentifier": "layout-" + version, "AXURL": "file:///metadata-" + version
            ], parent: root(version))
            return scene.target(scene.field, values: ["AXRole": "AXButton", "AXIdentifier": "stable-button"], parent: wrapper)
        }
        let first = wrapped(scene.row, "first")
        let life = scene.observe(first)
        let alias = scene.registry.bind(life, text: "immutable observation")!
        precondition(first.context.count == 1 && first.context[0].identity.attributes == [
            "AXRole": "AXWindow", "AXDocument": "file:///original"
        ], "layout metadata and window presentation do not become child identity constraints")
        for candidate in [wrapped(scene.row, "changed"), wrapped(scene.cell, "replacement")] {
            precondition(scene.observe(candidate) == life)
            precondition(CFEqual(scene.registry.target(alias)!.element, scene.field))
            precondition(scene.registry.target(alias)!.ancestors.contains { CFEqual($0, candidate.ancestors.last!) })
        }
        var partial = wrapped(scene.cell, "partial")
        partial.context = []
        partial.ancestors = []
        partial.contextComplete = false
        precondition(scene.observe(partial) == life, "partial context omission does not prove a different owner")
        precondition(scene.registry.target(alias)!.context.count == 1)
        precondition(!scene.registry.target(alias)!.ancestors.isEmpty)

        var refreshed = wrapped(scene.cell, "refreshed-window-proxy")
        let newWindow = AXUIElementCreateApplication(19120)
        refreshed.context = [.init(native: scene.key(newWindow), identity: first.context[0].identity)]
        precondition(scene.observe(refreshed) == life,
                     "same leaf/scope may refresh its AXWindow proxy; physical lifetime is checked independently")
        precondition(scene.registry.target(alias)!.observedInteractionSurface == nil,
                     "a descendant does not force an old AXWindow proxy over its fixed input scope")
        precondition(scene.registry.text(alias)?.value == "immutable observation")

        var changedDocument = refreshed
        changedDocument.context = [.init(native: scene.key(newWindow), identity: .init(facts: .init(values: [
            "AXRole": "AXWindow", "AXDocument": "file:///different-document"
        ])))]
        precondition(scene.observe(changedDocument) != life && scene.registry.target(alias) == nil,
                     "refreshing a window proxy must not erase a real document contradiction")
        print("PASS live leaf survives layout wrapper/metadata changes, partial reads and window proxy refresh")
    }

    @MainActor static func verifyIdentifiedRowMovement() {
        let scene = Scene()
        let first = scene.targets().row
        let life = scene.observe(first)
        let alias = scene.registry.bind(life, text: "row")!
        let moved = scene.targets(rowValues: rowFacts.merging(["AXRowIndexRange": "12:1"]) { _, next in next }).row
        precondition(scene.observe(moved) == life && scene.registry.target(alias) != nil,
                     "identified row remains the same native object after its ordinal moves")
        let known = ElementIdentity(facts: .init(values: rowFacts))
        let partial = ElementIdentity(facts: .init(values: ["AXRole": "AXRow", "AXRowIndexRange": "12:1"]))
        precondition(known.refining(with: partial)?.attributes["AXRowIndexRange"] == nil,
                     "a partial later observation must not reintroduce ordinal identity to an identified row")
        precondition(partial.refining(with: known)?.attributes["AXRowIndexRange"] == nil,
                     "learning the row's identifier refines away its former ordinal fallback")
        let unkeyed = ElementIdentity(facts: .init(values: ["AXRole": "AXRow", "AXRowIndexRange": "0:1"]))
        precondition(unkeyed.check(.init(values: ["AXRole": "AXRow", "AXRowIndexRange": "12:1"])) == .changed,
                     "unidentified virtual row position still distinguishes a reused native receiver")
        let cell = ElementIdentity(facts: .init(values: [
            "AXRole": "AXCell", "AXIdentifier": "cell", "AXRowIndexRange": "0:1"
        ]))
        precondition(cell.check(.init(values: [
            "AXRole": "AXCell", "AXIdentifier": "cell", "AXRowIndexRange": "12:1"
        ])) == .changed, "positional cells retain their explicit position contract")
        print("PASS identified row ordinal is mutable; unkeyed virtual rows and positional cells remain bounded")
    }

    static func verifyIdentityFailureEvidence() {
        let identity = ElementIdentity(facts: .init(values: ["AXRole": "AXGroup", "AXDocument": "file:///original"]))
        precondition(NativeElementInspection.identityCheck(identity, evidence: .invalid, isTarget: true).state == .changed)
        precondition(NativeElementInspection.identityCheck(identity, evidence: .invalid, isTarget: false).state == .unavailable,
                     "an invalid ancestor is not evidence that the retained child was destroyed")
        let unknown = NativeElementInspection.identityCheck(identity, evidence: .unavailable(.cannotComplete), isTarget: true)
        precondition(unknown.state == .unavailable && unknown.detail.contains(String(AXError.cannotComplete.rawValue)))
        let changed = NativeElementInspection.identityCheck(identity, evidence: .value(.init(values: [
            "AXRole": "AXGroup", "AXDocument": "file:///other"
        ])), isTarget: false)
        precondition(changed.state == .changed && changed.detail == "fields=AXDocument")
        precondition(!changed.detail.contains("file:///"), "diagnostics name the fact without logging application content")
        print("PASS identity errors distinguish leaf death, incomplete ancestor evidence and concrete changed fields")
    }

    @MainActor static func verifyDocumentOwnershipAcrossRoles() {
        let scene = Scene()
        let root = scene.target(scene.window, values: ["AXRole": "AXWindow"])
        let oldDocument = scene.target(scene.row, values: [
            "AXRole": "AXGroup", "AXDocument": "file:///old-document"
        ], parent: root)
        let original = scene.target(scene.field, values: ["AXRole": "AXButton", "AXIdentifier": "button"], parent: oldDocument)
        let life = scene.observe(original)
        let alias = scene.registry.bind(life, text: "original")!
        let nested = scene.target(scene.cell, values: [
            "AXRole": "AXGroup", "AXDocument": "file:///nested-document"
        ], parent: oldDocument)
        let extended = scene.target(scene.field, values: ["AXRole": "AXButton", "AXIdentifier": "button"], parent: nested)
        var partial = extended
        partial.context = [ApplicationElements.childContext(nested).last!]
        partial.ancestors = [nested.element]
        partial.contextComplete = false
        precondition(scene.observe(partial) == life,
                     "a new same-role owner in partial evidence can be nested, not a replacement")
        precondition(scene.observe(extended) == life,
                     "adding a nested boundary while the original owner remains is not a context-count contradiction")
        let newDocument = scene.target(AXUIElementCreateApplication(19130), values: [
            "AXRole": "AXWebArea", "AXDocument": "file:///new-document", "AXURL": "file:///new-document"
        ], parent: root)
        let replaced = scene.target(scene.field, values: ["AXRole": "AXButton", "AXIdentifier": "button"], parent: newDocument)
        precondition(scene.observe(replaced) != life && scene.registry.target(alias) == nil,
                     "same leaf in a complete path under a different-role document cannot retain its old ownership")
        print("PASS complete paths distinguish new nested evidence from actual different-role document replacement")
    }

    @MainActor static func verifyCommandRegistryContinuity() {
        for role in ["AXButton", "AXMenuItem", "AXMenuBarItem", "AXCheckBox", "AXRadioButton", "AXLink"] {
            let scene = Scene()
            let parent = scene.targets().row
            let original = scene.target(scene.field, values: [
                "AXRole": role, "AXIdentifier": "control", "AXTitle": "Count 0", "AXDescription": "Off"
            ], parent: parent)
            let life = scene.observe(original)
            let alias = scene.registry.bind(life, text: "Count 0")!
            let owner = scene.registry.target(alias)!.cellEditorState
            for count in [1, 2] {
                let next = scene.target(AXUIElementCreateApplication(19103), values: [
                    "AXRole": role, "AXIdentifier": "control", "AXTitle": "Count \(count)",
                    "AXDescription": count == 1 ? "On" : "Off"
                ], parent: parent)
                precondition(CFEqual(original.element, next.element))
                precondition(original.context.map(\.native) == next.context.map(\.native))
                precondition(scene.observe(next) == life,
                             "read/reconcile must preserve the same raw control despite its mutable text: \(role)")
                let retained = scene.registry.target(alias)!
                precondition(CFEqual(retained.element, original.element))
                precondition(retained.cellEditorState === owner)
            }
            precondition(scene.registry.text(alias)?.value == "Count 0")
            let changedID = scene.target(scene.field, values: [
                "AXRole": role, "AXIdentifier": "different-control", "AXTitle": "Count 2", "AXDescription": "Off"
            ], parent: parent)
            precondition(scene.observe(changedID) != life && scene.registry.target(alias) == nil,
                         "same raw object and display text cannot hide a known Identifier change: \(role)")
        }
        print("PASS command read/reconcile preserves raw handles; known Identifier changes still retire them")
    }

    @MainActor static func main() throws {
        verifyRowPresentation()
        verifyStrongEvidence()
        verifyCommandPresentation()
        try verifyActionSelection()
        verifyRegistryContinuity()
        verifyRegistryBoundaries()
        verifyCommandRegistryContinuity()
        verifyLocalContextEvolution()
        verifyIdentifiedRowMovement()
        verifyIdentityFailureEvidence()
        verifyDocumentOwnershipAcrossRoles()
    }
}
