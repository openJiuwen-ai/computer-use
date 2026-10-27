import Foundation
import ApplicationServices

@main struct NativeElementHitSelectionTests {
    enum Stop: Error { case cancelled }
    static func main() throws {
        typealias Evidence = NativeElementHitEvidence
        typealias Selection = NativeElementHitSelection
        let bounds = CGRect(x: 100, y: 200, width: 120, height: 80)
        let center = CGPoint(x: 160, y: 240), activation = CGPoint(x: 112, y: 216)
        let candidates = Selection.candidates(bounds: bounds, activationPoint: activation)
        precondition(candidates.count == 10 && candidates[0].point == activation && candidates[1].point == center)
        precondition(candidates.allSatisfy { bounds.contains($0.point) })
        for invalid in [CGPoint(x: CGFloat.infinity, y: 210), CGPoint(x: 99, y: 210)] {
            precondition(Selection.candidates(bounds: bounds, activationPoint: invalid).first?.source == .center)
        }
        precondition(Selection.candidates(bounds: bounds, activationPoint: center).first?.source == .center)
        precondition(Selection.candidates(bounds: .null, activationPoint: nil).isEmpty)
        let tiny = Selection.candidates(bounds: CGRect(x: 0, y: 0, width: 0.1, height: 0.1), activationPoint: nil)
        precondition(tiny.count <= 9 && tiny.allSatisfy { $0.point.x > 0 && $0.point.x < 0.1 })
        // Wheel-region geometry must not take a member's click activation point
        // or bypass the center hit that Python needs to classify for scrolling.
        let wheelCandidates = Selection.candidates(bounds: bounds, activationPoint: activation, expand: false)
        precondition(
            wheelCandidates.count == 1 && wheelCandidates[0].point == center && wheelCandidates[0].source == .center)

        // Failed application-level AX hit tests cannot be reported as empty
        // points or occlusion, nor retire the original retained element.
        for (status, relation) in [
            (AXError.noValue, Evidence.Relation.noElement), (.notImplemented, .unsupported),
            (.attributeUnsupported, .unsupported), (.cannotComplete, .queryFailed), (.invalidUIElement, .queryFailed),
            (.apiDisabled, .queryFailed), (.illegalArgument, .queryFailed)
        ] {
            let evidence = Evidence.result(status, relation: .target)
            precondition(evidence.status == status && evidence.relation == relation)
        }
        precondition(Evidence.result(.success, relation: nil).relation == .noElement)
        precondition(Evidence.result(.success, relation: .otherControl).relation == .otherControl)

        // Only an actual current parent path to the original window qualifies
        // a coarse hit. AXWindow shortcuts or cached ancestors are not inputs.
        func reaches(_ parents: [Int: Int], surfaces: Set<Int> = [3], budget: Bool = true) -> Bool {
            Selection.reachesWindow(
                1, window: 3, same: ==, parent: { parents[$0] }, isSurface: { surfaces.contains($0) },
                canContinue: { budget })
        }
        precondition(reaches([1: 2, 2: 3]))
        precondition(!reaches([1: 2]))
        precondition(!reaches([1: 2, 2: 1]))
        precondition(!reaches([1: 2, 2: 4, 4: 3], surfaces: [3, 4]))
        precondition(!reaches([1: 2, 2: 3], budget: false))

        func observation(_ relation: Evidence.Relation, foreground: Bool = true, status: AXError = .success)
            -> Selection.Observation<Int>
        {
            .init(
                evidence: .init(status: status, relation: relation), hit: relation == .target ? 7 : nil,
                foreground: foreground)
        }
        // A covered center does not reject the element if a bounded edge query
        // confirms the same retained target. These callbacks only observe.
        var queries = [Selection.Candidate]()
        let edge = Selection.select(candidates, exclusive: false, canContinue: { _ in true }) { candidate in
            queries.append(candidate)
            return observation(candidate.source == .edge ? .target : .otherControl)
        }!
        precondition(edge.basis == .exactHit && edge.candidate.source == .edge && queries.count == 3)
        // A precise later hit outranks a compatible background geometry path.
        let exact = Selection.select(candidates, exclusive: false, canContinue: { _ in true }) { candidate in
            observation(candidate.source == .edge ? .target : .otherWindow, foreground: false)
        }!
        precondition(exact.basis == .exactHit && exact.candidate.source == .edge)
        let activated = Selection.select(candidates, exclusive: false, canContinue: { _ in true }) { candidate in
            observation(candidate.source == .activationPoint ? .target : .otherControl)
        }!
        precondition(activated.candidate.source == .activationPoint && activated.basis == .exactHit)
        var wheelQueries = [CGPoint]()
        let initialWheel = Selection.select(wheelCandidates, exclusive: false, canContinue: { _ in true }) {
            candidate in
            wheelQueries.append(candidate.point)
            // A passive member under the region center is still exposed for
            // public qualification rather than silently choosing an empty edge.
            return Selection.Observation(
                evidence: Evidence.result(.success, relation: .otherControl), hit: 11, foreground: true)
        }!
        precondition(initialWheel.basis == .rejected && initialWheel.observation.hit == 11 && wheelQueries == [center])
        let qualifiedWheel = Selection.select(wheelCandidates, exclusive: false, canContinue: { _ in true }) { _ in
            observation(.target)
        }!
        precondition(qualifiedWheel.basis == .exactHit && qualifiedWheel.candidate.point == center)
        // A healthy provider can take 120ms. Optional candidate queries may time
        // out, but must not shorten the original center's response window.
        var timeoutPolicies = [Float?]()
        let slowProvider = Selection.select(candidates, exclusive: false, canContinue: { _ in true }) { candidate in
            timeoutPolicies.append(candidate.queryTimeout)
            let timedOut = candidate.queryTimeout.map { $0 < 0.12 } ?? false
            return observation(timedOut ? .queryFailed : .target, status: timedOut ? .cannotComplete : .success)
        }!
        precondition(slowProvider.basis == .exactHit && slowProvider.candidate.source == .center)
        precondition(timeoutPolicies.count == 2 && timeoutPolicies[0] == 0.05 && timeoutPolicies[1] == nil)
        let frozenCenter = Selection.select([slowProvider.candidate], exclusive: false, canContinue: { _ in true }) {
            candidate in
            precondition(candidate.queryTimeout == nil)
            return observation(.target)
        }!
        precondition(frozenCenter.basis == .exactHit && frozenCenter.candidate.point == center)
        precondition(wheelCandidates.allSatisfy { $0.queryTimeout == nil })

        // A confirmed coarse original window supports only the existing
        // center and physical foreground route. It is never an exact AX hit,
        // nor permission for an edge/activation point or background rectangle.
        for candidate in candidates {
            for foreground in [true, false] {
                for exclusive in [true, false] {
                    let coarse = Selection.select([candidate], exclusive: exclusive, canContinue: { _ in true }) { _ in
                        observation(.coarseAncestorWindow, foreground: foreground)
                    }!
                    let allowed = foreground && candidate.source == .center
                    precondition(coarse.basis == (allowed ? .coarseAncestorGeometry : .rejected))
                }
            }
        }
        let exactAfterCoarse = Selection.select(candidates, exclusive: true, canContinue: { _ in true }) { candidate in
            observation(candidate.source == .edge ? .target : .coarseAncestorWindow)
        }!
        precondition(exactAfterCoarse.basis == .exactHit && exactAfterCoarse.candidate.source == .edge)
        let lostCoarse = Selection.select([slowProvider.candidate], exclusive: true, canContinue: { _ in true }) { _ in
            observation(.otherControl)
        }!
        precondition(lostCoarse.basis == .rejected)

        // Known same-window mismatch/unknown ownership remain a boundary.
        // Missing AX evidence retains the old background geometry route, with
        // its uncertainty explicit; it never authorizes foreground HID.
        for relation in [
            Evidence.Relation.otherWindow, .noElement, .unsupported, .queryFailed, .otherControl, .unknownOwner
        ] {
            for exclusive in [true, false] {
                for foreground in [true, false] {
                    let result = Selection.select(candidates, exclusive: exclusive, canContinue: { _ in true }) { _ in
                        observation(relation, foreground: foreground)
                    }!
                    let permitted = !exclusive && !foreground && ![.otherControl, .unknownOwner].contains(relation)
                    precondition(result.basis == (permitted ? .backgroundGeometry : .rejected))
                    precondition(result.candidate.source == .center)
                }
            }
        }
        // An unverified activation/edge point never becomes a new fallback.
        for candidate in candidates.filter({ $0.source != .center }) {
            let result = Selection.select([candidate], exclusive: false, canContinue: { _ in true }) { _ in
                observation(.noElement, foreground: false)
            }!
            precondition(result.basis == .rejected)
        }
        // Slow global AX failures stop expansion after the baseline center.
        var failedQueries = 0
        _ = Selection.select(candidates, exclusive: false, canContinue: { _ in true }) { _ in
            failedQueries += 1
            return observation(.queryFailed, foreground: false, status: .cannotComplete)
        }
        precondition(failedQueries == 2)
        var budgetQueries = 0
        _ = Selection.select(candidates, exclusive: false, canContinue: { $0.source == .center }) { _ in
            budgetQueries += 1
            return observation(.otherControl)
        }
        precondition(budgetQueries == 1)
        do {
            _ = try Selection.select(candidates, exclusive: false, canContinue: { _ in throw Stop.cancelled }) { _ in
                observation(.target)
            }
            preconditionFailure("cancelled candidate lookup continued")
        } catch Stop.cancelled {}

        // A later pointer event receives only the frozen selected candidate.
        // Another point becoming clickable cannot turn a lost hit into replay.
        var frozenQueries = [CGPoint]()
        let frozen = Selection.select([edge.candidate], exclusive: false, canContinue: { _ in true }) { candidate in
            frozenQueries.append(candidate.point)
            return observation(.otherControl)
        }!
        precondition(frozen.basis == .rejected && frozenQueries == [edge.candidate.point])
        print("NativeElementHitSelectionTests passed")
    }
}
