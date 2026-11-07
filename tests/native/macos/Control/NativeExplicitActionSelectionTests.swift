// Copyright (c) Huawei Technologies Co., Ltd. 2026. All rights reserved.

import Foundation

@main enum NativeExplicitActionSelectionTests {
    typealias Resolution = NativeAction.ExplicitResolution

    enum ProbeError: Error {
        case cancelled, descriptionFailed
    }

    static func expect(
        _ expected: Resolution, request: String, names: [String]?,
        descriptions: [String: String] = [:], reads expectedReads: [String] = []
    ) throws {
        var reads = [String]()
        let actual = try NativeAction.resolveExplicit(request: request, names: names, description: { name in
            reads.append(name)
            return descriptions[name]
        })
        precondition(actual == expected, "unexpected explicit action resolution for \(request): \(actual)")
        precondition(reads == expectedReads, "description queries must be necessary, bounded and single-pass")
    }

    static func main() throws {
        try expect(.namesUnavailable, request: "AXPress", names: nil)
        try expect(.absent, request: "AXPress", names: [])

        // A raw AX name already identifies the command. Neither a missing nor
        // throwing localized description may block it or replace its identity.
        for raw in ["AXPress", "CustomAction", " CustomAction "] {
            try expect(.selected(.init(name: raw, description: nil)), request: raw,
                       names: ["OtherAction", raw, "LastAction"])
            let exact = try NativeAction.resolveExplicit(request: raw, names: [raw], description: { _ in
                preconditionFailure("exact native names must not query descriptions")
            })
            precondition(exact == .selected(.init(name: raw, description: nil)))
        }
        // AXPerformAction addresses the name, not its array occurrence. An
        // appended superclass declaration does not introduce another command.
        try expect(.selected(.init(name: "AXPress", description: nil)), request: "AXPress",
                   names: ["AXPress", "OtherAction", "AXPress"], reads: [])
        try expect(.selected(.init(name: "AXPick", description: nil)), request: "AXPick",
                   names: ["AXPick", "AXPick"], reads: [])
        let uniquePick = NativeAction.catalogue(["AXPick"]) { _ in nil }
        let repeatedPick = NativeAction.catalogue(["AXPick", "AXPick"]) { _ in nil }
        precondition(NativeAction.matches(uniquePick, name: "AXPick", description: nil))
        precondition(!NativeAction.matches(repeatedPick, name: "AXPick", description: nil))

        // The description budget must not truncate the native-name identity
        // check. Repeating that same name after the budget adds no ambiguity.
        let large = (0..<129).map { "NativeAction\($0)" }
        for index in [0, 64, 128] {
            try expect(.selected(.init(name: large[index], description: nil)),
                       request: large[index], names: large)
        }
        try expect(.selected(.init(name: large[0], description: nil)),
                   request: large[0], names: large + [large[0]], reads: [])
        try expect(.descriptionBudgetExceeded, request: "Localized action", names: large)

        // A localized alias is safe only after the bounded catalogue is known.
        // Finding one match early cannot hide a later duplicate or unknown item.
        let names = ["FirstAction", "OriginalAction", "LastAction"]
        let descriptions = ["FirstAction": "First", "OriginalAction": "执行操作", "LastAction": "Last"]
        try expect(.selected(.init(name: "OriginalAction", description: "执行操作")),
                   request: "执行操作", names: names, descriptions: descriptions, reads: names)
        try expect(.absent, request: "Missing", names: names, descriptions: descriptions, reads: names)
        try expect(.absent, request: "first", names: names, descriptions: descriptions, reads: names)
        try expect(.ambiguous, request: "Shared", names: names,
                   descriptions: ["FirstAction": "Shared", "OriginalAction": "Shared", "LastAction": "Last"],
                   reads: names)
        try expect(.selected(.init(name: "OriginalAction", description: "Shared")),
                   request: "Shared", names: ["OriginalAction", "OriginalAction"],
                   descriptions: ["OriginalAction": "Shared"], reads: ["OriginalAction"])
        try expect(.descriptionsUnavailable, request: "执行操作", names: names,
                   descriptions: ["FirstAction": "First", "OriginalAction": "执行操作"], reads: names)
        try expect(.descriptionsUnavailable, request: "Missing", names: names,
                   descriptions: ["OriginalAction": "Other", "LastAction": "Last"], reads: names)

        var uncertainReads = [String]()
        let uncertain = try NativeAction.resolveExplicit(request: "Shared", names: names, description: { name in
            uncertainReads.append(name)
            return name == "LastAction" ? nil : "Shared"
        })
        precondition(uncertain == .ambiguous || uncertain == .descriptionsUnavailable,
                     "known duplicate aliases must never select an action even with an unknown description")
        precondition(uncertainReads == names)

        let bounded = Array(large.prefix(128))
        precondition(NativeAction.boundedNames(["AXPress", "AXPress"]) == ["AXPress"],
                     "automatic write actions already address distinct names")
        precondition(NativeAction.boundedNames(bounded + bounded) == bounded,
                     "the observation budget counts distinct commands, not repeated declarations")
        precondition(NativeAction.boundedNames(large) == nil,
                     "the automatic action description budget remains bounded")
        let complete = Dictionary(uniqueKeysWithValues: bounded.map { ($0, "Description for \($0)") })
        try expect(.selected(.init(name: bounded[0], description: complete[bounded[0]])),
                   request: complete[bounded[0]]!, names: bounded, descriptions: complete, reads: bounded)
        try expect(.absent, request: "Missing", names: bounded, descriptions: complete, reads: bounded)

        // Resolution retains the native command, so the dispatch guard can
        // reject disappearance or alias transfer instead of retargeting it.
        let original = try NativeAction.resolveExplicit(request: "Localized", names: ["OriginalAction"],
                                                        description: { _ in "Localized" })
        let disappeared = try NativeAction.resolveExplicit(request: "Localized", names: [], description: { _ in
            preconditionFailure("an empty catalogue has no descriptions")
        })
        let transferred = try NativeAction.resolveExplicit(request: "Localized", names: ["ReplacementAction"],
                                                           description: { _ in "Localized" })
        precondition(original == .selected(.init(name: "OriginalAction", description: "Localized")))
        precondition(disappeared == .absent)
        precondition(transferred == .selected(.init(name: "ReplacementAction", description: "Localized")))
        precondition(original != transferred, "equal descriptions must not erase native-name identity")

        // Cancellation and provider errors leave immediately. They are not a
        // missing-description signal and must not restart the catalogue scan.
        var cancellationChecks = 0
        var cancellationReads = [String]()
        do {
            _ = try NativeAction.resolveExplicit(request: "Alias", names: names, description: { name in
                cancellationReads.append(name)
                return "Other"
            }, check: {
                cancellationChecks += 1
                throw ProbeError.cancelled
            })
            preconditionFailure("cancellation must propagate")
        } catch ProbeError.cancelled {}
        precondition(cancellationChecks == 1 && cancellationReads.isEmpty)

        var interruptedReads = [String]()
        var interruptions = 0
        do {
            _ = try NativeAction.resolveExplicit(request: "Alias", names: names, description: { name in
                interruptedReads.append(name)
                return "Other"
            }, check: {
                if !interruptedReads.isEmpty {
                    interruptions += 1
                    throw ProbeError.cancelled
                }
            })
            preconditionFailure("cancellation during description discovery must propagate")
        } catch ProbeError.cancelled {}
        precondition(interruptedReads == [names[0]] && interruptions == 1)

        var failedReads = [String]()
        do {
            _ = try NativeAction.resolveExplicit(request: "Alias", names: names, description: { name in
                failedReads.append(name)
                if name == names[1] { throw ProbeError.descriptionFailed }
                return "Other"
            })
            preconditionFailure("a throwing description query must propagate")
        } catch ProbeError.descriptionFailed {}
        precondition(failedReads == Array(names.prefix(2)), "a failed provider query must not be retried")

        print("Explicit native action selection regressions passed")
    }
}
