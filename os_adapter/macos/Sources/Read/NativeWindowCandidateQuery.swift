// Copyright (c) Huawei Technologies Co., Ltd. 2026. All rights reserved.

/// QUERY ordering for one synchronous window collector. Input validation may
/// consume a later declaration read performed by its input-window helper;
/// ordinary observation retains declaration -> hints -> input-window order.
enum NativeWindowQueryPurpose: Equatable {
    case observation
    case inputValidation
}

enum NativeWindowCandidateQuery {
    struct Sources<Element> {
        let declared: [Element]
        let hints: [Element]
        let inputWindow: Element?

        var candidates: [Element] { declared + hints + (inputWindow.map { [$0] } ?? []) }
    }

    static func collect<Element>(
        purpose: NativeWindowQueryPurpose, declared: () -> [Element], hints: () -> [Element],
        inputWindow: () -> Element?, latestDeclared: () -> [Element]?
    ) -> Sources<Element> {
        switch purpose {
        case .observation:
            let roots = declared()
            let offered = hints()
            return Sources(declared: roots, hints: offered, inputWindow: inputWindow())
        case .inputValidation:
            let offered = hints()
            let input = inputWindow()
            return Sources(declared: latestDeclared() ?? declared(), hints: offered, inputWindow: input)
        }
    }
}

/// Last actual attempt, including unusable payloads and unrelated roots. An
/// instance belongs only to one synchronous helper call, never a request/event.
struct NativeWindowDeclarationAttempt<Root: Equatable, Owner: Equatable, Payload> {
    private let root: Root
    private let owner: Owner
    private var latest: (root: Root, payload: Payload)?
    private var open = true

    init(root: Root, owner: Owner) {
        self.root = root
        self.owner = owner
    }

    mutating func record(root: Root, payload: Payload) {
        guard open else { return }
        latest = (root, payload)
    }

    mutating func take(owner current: Owner?) -> Payload? {
        defer { close() }
        guard open, current == owner, let latest, latest.root == root else { return nil }
        return latest.payload
    }

    mutating func close() {
        open = false
        latest = nil
    }
}
