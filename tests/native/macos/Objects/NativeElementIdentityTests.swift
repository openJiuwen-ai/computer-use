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

    @MainActor static func verifyCurrentBoundaryOrder(complete: Bool) {
        let scene = Scene()
        let root = scene.target(scene.window, values: ["AXRole": "AXWindow"])
        let popover = scene.target(scene.row, values: ["AXRole": "AXPopover"], parent: root)
        func button(_ parent: ApplicationElements.Target) -> ApplicationElements.Target {
            scene.target(scene.field, values: ["AXRole": "AXButton", "AXIdentifier": "button"], parent: parent)
        }
        let life = scene.observe(button(popover))
        let alias = scene.registry.bind(life, text: "original button")!
        let editorState = scene.registry.target(alias)!.cellEditorState
        let outerSheet = scene.target(scene.cell, values: ["AXRole": "AXSheet"], parent: root)
        let nestedPopover = scene.target(scene.row, values: ["AXRole": "AXPopover"], parent: outerSheet)
        var extended = button(nestedPopover)
        if !complete {
            extended.context.removeFirst()
            extended.ancestors.removeFirst()
            extended.contextComplete = false
        }
        precondition(scene.observe(extended) == life,
                     "adding an outer owner while the exact popover and leaf survive does not replace the target")
        let retained = scene.registry.target(alias)!
        precondition(retained.context.map { $0.identity.attributes["AXRole"]! } == ["AXWindow", "AXSheet", "AXPopover"],
                     "complete and partial observations preserve proven local order instead of appending new owners")
        precondition(CFEqual(retained.observedInteractionSurface!, popover.element),
                     "a newly observed outer sheet must not steal the surviving inner popover's input surface")
        precondition(retained.cellEditorState === editorState)
        if !complete {
            precondition(retained.ancestors.contains { CFEqual($0, root.element) },
                         "equal-size partial paths can add a sheet yet omit the still-required host window")
            precondition(!retained.contextComplete,
                         "retaining the omitted window does not manufacture a current complete path")
        }

        let innerSheet = scene.target(AXUIElementCreateApplication(19140), values: ["AXRole": "AXSheet"], parent: nestedPopover)
        precondition(scene.observe(button(innerSheet)) == life)
        precondition(CFEqual(scene.registry.target(alias)!.observedInteractionSurface!, innerSheet.element),
                     "an actual new innermost surface, unlike an outer insertion, becomes the current surface")

        var partial = button(nestedPopover)
        partial.context = [ApplicationElements.childContext(nestedPopover).last!]
        partial.ancestors = [nestedPopover.element]
        partial.contextComplete = false
        precondition(scene.observe(partial) == life)
        precondition(CFEqual(scene.registry.target(alias)!.observedInteractionSurface!, innerSheet.element),
                     "a partial relationship read cannot prove the omitted inner owner disappeared")
        precondition(scene.registry.text(alias)?.value == "original button")

        var reordered = button(innerSheet)
        reordered.context = [
            ApplicationElements.childContext(innerSheet).last!,
            ApplicationElements.childContext(nestedPopover).last!
        ]
        reordered.ancestors = [innerSheet.element, nestedPopover.element]
        reordered.contextComplete = false
        precondition(scene.observe(reordered) == life)
        let current = scene.registry.target(alias)!
        precondition(current.context.map(\.native) == [
            root, outerSheet, innerSheet, nestedPopover
        ].map { ApplicationElements.childContext($0).last!.native },
        "a partial path's explicitly observed owners can reorder while omitted window/sheet evidence survives")
        precondition(CFEqual(current.observedInteractionSurface!, nestedPopover.element),
                     "reordering two known boundaries must update the actual nearest input surface")

        let movedSheet = scene.target(innerSheet.element, values: ["AXRole": "AXSheet"], parent: outerSheet)
        let movedPopover = scene.target(popover.element, values: ["AXRole": "AXPopover"], parent: movedSheet)
        let completeCurrent = button(movedPopover)
        precondition(scene.observe(completeCurrent) == life)
        let currentAncestors = scene.registry.target(alias)!.ancestors
        precondition(currentAncestors.count == completeCurrent.ancestors.count
                     && zip(currentAncestors, completeCurrent.ancestors).allSatisfy { CFEqual($0, $1) },
                     "a later complete path must replace retained historical ancestry with its actual current path")
        precondition(scene.registry.target(alias)!.contextComplete)

        let replacement = scene.target(AXUIElementCreateApplication(19141), values: ["AXRole": "AXPopover"], parent: outerSheet)
        let nextLife = scene.observe(button(replacement))
        precondition(nextLife != life && scene.registry.target(alias) == nil,
                     "a complete path proving the original popover disappeared still retires the old binding")
        precondition(scene.registry.target(nextLife)!.cellEditorState !== editorState)
        print("PASS boundary order selects the real innermost surface (complete=\(complete)); partial reads retain owners")
    }

    @MainActor static func verifyUncertainReobservation() {
        let scene = Scene()
        var original = scene.targets().field
        original.providerKnown = true
        let life = scene.observe(original)
        let alias = scene.registry.bind(life, text: "original text")!
        let editorState = scene.registry.target(alias)!.cellEditorState
        var partial = scene.target(scene.field, values: ["AXRole": "AXTextField"])
        partial.contextComplete = false
        precondition(scene.observe(partial) == life,
                     "temporarily unreadable identifier/provider/parent facts are not contradictory evidence")
        let retained = scene.registry.target(alias)!
        precondition(retained.identity == original.identity && retained.context == original.context)
        precondition(retained.providerKnown && retained.provider == nil)
        precondition(retained.cellEditorState === editorState)
        precondition(!retained.contextComplete,
                     "retaining old ancestors must not turn a partial read into a complete current path")
        precondition(scene.observe(original) == life,
                     "the same reference remains usable when the original facts can be read again")

        var missingIdentifier = partial
        missingIdentifier.identity = .init(facts: .init(values: ["AXRole": "AXTextField"], absent: ["AXIdentifier"]))
        precondition(scene.observe(missingIdentifier) != life && scene.registry.target(alias) == nil,
                     "confirmed disappearance of an earlier known leaf identifier differs from a failed query")
        precondition(scene.registry.text(alias)?.value == "original text",
                     "retiring the live object does not rewrite its captured historical text")
        print("PASS uncertain re-observation preserves lifetime and provenance; confirmed leaf conflict still retires")
    }

    @MainActor static func verifyProviderFailureLifetime(reading: Bool) {
        func validate(_ scene: Scene, _ life: String) throws {
            let inspection = NativeElementInspection(registry: scene.registry)
            if reading {
                _ = try NativeFactCollector(registry: scene.registry, inspection: inspection).readTarget(life)
            } else {
                try inspection.validateTargetIdentity(scene.registry.target(life)!)
            }
        }
        for known in [false, true] {
            let scene = Scene()
            var target = scene.target(scene.field, values: ["AXRole": "AXButton"])
            target.providerKnown = known
            let life = scene.observe(target)
            let alias = scene.registry.bind(life, text: "provider snapshot")!
            let unrelated = scene.observe(scene.target(scene.cell, values: ["AXRole": "AXButton"]))
            do {
                try validate(scene, life)
                preconditionFailure("a foreign native PID without its process incarnation cannot authorize input")
            } catch let error as DriverError {
                precondition(error.code == (known ? .staleTarget : .observationIncomplete))
            } catch {
                preconditionFailure("unexpected provider error: \(error)")
            }
            precondition((scene.registry.target(life) == nil) == known,
                         "confirmed provider movement retires; unknown provider evidence retains the original lifetime")
            precondition((scene.registry.target(alias) == nil) == known)
            precondition(scene.registry.target(unrelated) != nil,
                         "a leaf provider failure cannot retire unrelated objects in the same scope")
            precondition(scene.registry.text(alias)?.value == "provider snapshot")
            if known {
                precondition(scene.observe(target) != life && scene.registry.target(life) == nil,
                             "later observations must not revive a provider-conflicted token")
            } else {
                precondition(scene.observe(target) == life,
                             "an uncertain provider query must not force the caller to acquire another token")
            }
        }

        let scene = Scene()
        var target = scene.target(AXUIElementCreateApplication(scene.app.processIdentifier), values: ["AXRole": "AXApplication"])
        target.provider = NativeProcessIdentity(
            pid: scene.app.processIdentifier, parent: 0, seconds: 0, microseconds: 0, executable: "/old-provider")
        target.providerKnown = true
        let life = scene.observe(target)
        do {
            try validate(scene, life)
            preconditionFailure("a changed provider incarnation must be rejected before attribute or input reads")
        } catch let error as DriverError {
            precondition(error.code == .staleTarget && error.description.contains("lifetime changed"))
        } catch {
            preconditionFailure("unexpected provider incarnation error: \(error)")
        }
        precondition(scene.registry.target(life) == nil,
                     "process incarnation conflict must retire the original native action reference")

        let invalidScene = Scene()
        let invalid = invalidScene.observe(invalidScene.target(
            AXUIElementCreateApplication(-1), values: ["AXRole": "AXButton"]))
        do {
            try validate(invalidScene, invalid)
            preconditionFailure("AX invalidUIElement is positive leaf end-of-lifetime evidence")
        } catch let error as DriverError {
            precondition(error.code == .staleTarget)
        } catch {
            preconditionFailure("unexpected invalid native object error: \(error)")
        }
        precondition(invalidScene.registry.target(invalid) == nil,
                     "both reading and writing must retire a native object confirmed invalid")
        print("PASS provider validation (reading=\(reading)) retains unknown evidence and retires confirmed conflicts")
    }

    // Only metadata is substituted in the mismatch tests; each expected
    // failure must occur before any process lookup or AX query on this token.
    final class LaunchMetadata: NSRunningApplication, @unchecked Sendable {
        var value: Date?
        override var launchDate: Date? { value }
    }

    @MainActor static func verifyLaunchMetadataLifetime() throws {
        let initial = Date(timeIntervalSince1970: 10), later = Date(timeIntervalSince1970: 20)
        precondition(NativeFactCollector.launchDateCheck(expected: nil, current: nil) == .matched)
        precondition(NativeFactCollector.launchDateCheck(expected: nil, current: initial) == .matched,
                     "newly available launch metadata cannot block the original retained application")
        precondition(NativeFactCollector.launchDateCheck(expected: initial, current: initial) == .matched)
        precondition(NativeFactCollector.launchDateCheck(expected: initial, current: nil) == .unavailable)
        precondition(NativeFactCollector.launchDateCheck(expected: initial, current: later) == .changed)

        for current in [nil, later] {
            let app = LaunchMetadata()
            app.value = current
            let registry = ApplicationElements(), element = AXUIElementCreateApplication(getpid())
            let identity = ElementIdentity(facts: .init(values: ["AXRole": "AXApplication"]))
            let target = ApplicationElements.Target(
                application: app, launched: initial, element: element, scope: "launch-metadata",
                role: "AXApplication", identity: identity)
            let life = registry.observe(
                .init(pid: getpid(), launched: initial, element: element, scope: target.scope),
                evidence: .init(identity: identity, provider: nil, context: []), value: target)
            let alias = registry.bind(life, text: "original metadata")!
            let collector = NativeFactCollector(registry: registry, inspection: .init(registry: registry))
            do {
                _ = try collector.readTarget(life)
                preconditionFailure("known launch metadata must be checked before reading the native object")
            } catch let error as DriverError {
                precondition(error.code == (current == nil ? .observationIncomplete : .staleTarget))
            }
            precondition((registry.target(life) == nil) == (current != nil),
                         "only a known launch-date contradiction permanently retires the original reference")
            precondition(registry.text(alias)?.value == "original metadata")
        }

        let scene = Scene()
        let element = AXUIElementCreateApplication(scene.app.processIdentifier)
        let identity = ElementIdentity(facts: .init(values: ["AXRole": "AXApplication"]))
        let target = ApplicationElements.Target(
            application: scene.app, launched: nil, element: element, scope: scene.scope,
            role: "AXApplication", identity: identity, providerKnown: true)
        let life = scene.observe(target)
        let collector = NativeFactCollector(registry: scene.registry, inspection: .init(registry: scene.registry))
        let current = try collector.readTarget(life)
        precondition(CFEqual(current.element, element) && scene.registry.target(life) != nil,
                     "unknown historical launch metadata still permits the real application and native provider checks")
        print("PASS launch metadata enrichment remains usable; missing known facts retain and contradictions retire")
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
        // An unregistered CLI has NSRunningApplication.current PID -1. Create
        // only the windowless AppKit process, never activate or open a surface.
        NSApplication.shared.setActivationPolicy(.prohibited)
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
        verifyCurrentBoundaryOrder(complete: true)
        verifyCurrentBoundaryOrder(complete: false)
        verifyUncertainReobservation()
        verifyProviderFailureLifetime(reading: false)
        verifyProviderFailureLifetime(reading: true)
        try verifyLaunchMetadataLifetime()
    }
}
