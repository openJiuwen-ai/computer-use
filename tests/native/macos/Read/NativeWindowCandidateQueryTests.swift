@main enum NativeWindowCandidateQueryTests {
    struct Owner: Equatable {
        var pid: Int
        var incarnation: Int
    }
    struct Root: Equatable {
        var pid: Int
        var object: Int
    }
    typealias Capture = NativeWindowDeclarationAttempt<Root, Owner, [String]?>
    static let owner = Owner(pid: 42, incarnation: 7)
    static let root = Root(pid: 42, object: 1)

    final class Sample {
        var trace = [String]()
        var capture = Capture(root: root, owner: owner)
        var current: Owner? = owner
        var fallback = ["fresh-root"]
        var helperReads = [(Root, [String]?)]()

        func collect(_ purpose: NativeWindowQueryPurpose) -> NativeWindowCandidateQuery.Sources<String> {
            NativeWindowCandidateQuery.collect(
                purpose: purpose,
                declared: {
                    trace.append("reader.AXWindows")
                    return fallback
                },
                hints: {
                    trace += ["reader.focused", "reader.main"]
                    return ["focused", "main"]
                },
                inputWindow: {
                    trace.append("inputWindow.begin")
                    for (index, read) in helperReads.enumerated() {
                        trace.append("helper.AXWindows.\(index)")
                        capture.record(root: read.0, payload: read.1)
                    }
                    trace.append("inputWindow.end")
                    return "input"
                },
                latestDeclared: {
                    trace.append("capture.take")
                    return capture.take(owner: current) ?? nil
                })
        }
    }

    static func main() {
        observationOrder()
        successfulLateDeclaration()
        lastAttemptReplacesPrevious()
        unavailableDoesNotMeanEmpty()
        identityMismatchFallsBack()
        isolatedCollectors()
        closedScopeCannotEscape()
        print("NativeWindowCandidateQueryTests passed")
    }

    static func observationOrder() {
        let sample = Sample()
        sample.helperReads = [(root, ["late"])]
        let result = sample.collect(.observation)
        precondition(result.candidates == ["fresh-root", "focused", "main", "input"])
        precondition(
            sample.trace == [
                "reader.AXWindows", "reader.focused", "reader.main", "inputWindow.begin", "helper.AXWindows.0",
                "inputWindow.end"
            ])
        precondition(!sample.trace.contains("capture.take"))
    }

    static func successfulLateDeclaration() {
        let sample = Sample()
        sample.helperReads = [(root, ["document", "late-nonWindow-text-root"])]
        let result = sample.collect(.inputValidation)
        precondition(result.candidates == ["document", "late-nonWindow-text-root", "focused", "main", "input"])
        precondition(
            sample.trace == [
                "reader.focused", "reader.main", "inputWindow.begin", "helper.AXWindows.0", "inputWindow.end",
                "capture.take"
            ])
        // A valid empty catalogue is a complete fact. Hints/input remain separate.
        let empty = Sample()
        empty.helperReads = [(root, [])]
        precondition(empty.collect(.inputValidation).candidates == ["focused", "main", "input"])
        precondition(!empty.trace.contains("reader.AXWindows"))
    }

    static func lastAttemptReplacesPrevious() {
        let changed = Sample()
        changed.helperReads = [(root, ["old"]), (root, ["late-new-root"])]
        precondition(changed.collect(.inputValidation).declared == ["late-new-root"])
        precondition(changed.trace.filter { $0.hasPrefix("helper.AXWindows") }.count == 2)
        let failed = Sample()
        failed.helperReads = [(root, ["old"]), (root, nil)]
        precondition(failed.collect(.inputValidation).declared == ["fresh-root"])
        precondition(failed.trace.suffix(2) == ["capture.take", "reader.AXWindows"])
    }

    static func unavailableDoesNotMeanEmpty() {
        let absent = Sample()
        precondition(absent.collect(.inputValidation).declared == ["fresh-root"])
        precondition(absent.trace.last == "reader.AXWindows")
        let malformed = Sample()
        malformed.helperReads = [(root, nil)]
        precondition(malformed.collect(.inputValidation).declared == ["fresh-root"])
        // The helper's failure does not retry the helper or authorize any input.
        precondition(malformed.trace.filter { $0 == "inputWindow.begin" }.count == 1)
    }

    static func identityMismatchFallsBack() {
        for wrong in [Root(pid: 99, object: 1), Root(pid: 42, object: 2)] {
            let sample = Sample()
            sample.helperReads = [(root, ["old"]), (wrong, ["other"])]
            precondition(sample.collect(.inputValidation).declared == ["fresh-root"])
        }
        for current in [Owner(pid: 99, incarnation: 7), Owner(pid: 42, incarnation: 8), nil] {
            let sample = Sample()
            sample.current = current
            sample.helperReads = [(root, ["old"])]
            precondition(sample.collect(.inputValidation).declared == ["fresh-root"])
        }
    }

    static func isolatedCollectors() {
        let outer = Sample(), nested = Sample()
        outer.capture.record(root: root, payload: ["outer"])
        nested.helperReads = [(root, ["nested"])]
        precondition(nested.collect(.inputValidation).declared == ["nested"])
        precondition(outer.capture.take(owner: owner)! == ["outer"])
        let next = Sample()
        precondition(next.collect(.inputValidation).declared == ["fresh-root"])
    }

    static func closedScopeCannotEscape() {
        enum Stop: Error { case cancellation }
        var capture = Capture(root: root, owner: owner)
        func cancelled() throws {
            defer { capture.close() }
            capture.record(root: root, payload: ["before-cancel"])
            throw Stop.cancellation
        }
        do {
            try cancelled()
            preconditionFailure()
        } catch {}
        capture.record(root: root, payload: ["after-close"])
        precondition(capture.take(owner: owner) == nil)
        var consumed = Capture(root: root, owner: owner)
        consumed.record(root: root, payload: ["once"])
        precondition(consumed.take(owner: owner)! == ["once"])
        precondition(consumed.take(owner: owner) == nil)
    }

}
