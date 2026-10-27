import Foundation
import CoreGraphics

@main struct NativeInputWindowHitTests {
    static func main() {
        var reads = 0
        func publicHit() -> NativeInputWindowHit.Accessibility {
            reads += 1
            return .window(17)
        }

        // Removing hit SPI must not remove an independently confirmed public
        // foreground destination or require any background transport capability.
        for privateResult in [NativeInputWindowHit.WindowServer.unavailable, .failed(-123)] {
            let result = NativeInputWindowHit.resolve(privateResult, publicHit: publicHit)
            precondition(result.windowID == 17 && result.source == .accessibility)
            precondition(result.windowServer == privateResult && result.accessibility == .window(17))
        }
        precondition(reads == 2)
        // Keep the fast production path free of new AX reads, including a known
        // empty physical hit. An AX object behind it must not override that result.
        for privateResult in [NativeInputWindowHit.WindowServer.window(29), .noWindow] {
            let result = NativeInputWindowHit.resolve(privateResult, publicHit: publicHit)
            precondition(result.windowServer == privateResult && result.accessibility == nil)
            precondition(result.windowID == (privateResult == .noWindow ? nil : 29))
        }
        precondition(reads == 2)
        // Communication failure, no object, ambiguous mapping and missing
        // owner remain different evidence; none is a confirmed input window.
        for publicResult in [
            NativeInputWindowHit.Accessibility.noElement, .failed(-25204), .unresolvedWindow, .ambiguousWindow
        ] {
            let result = NativeInputWindowHit.resolve(.unavailable) { publicResult }
            precondition(result.accessibility == publicResult && result.windowID == nil && result.source == nil)
        }

        let frame = CGRect(x: 10, y: 20, width: 100, height: 80), point = CGPoint(x: 50, y: 50)
        let target = NativeInputWindowHit.PublicWindow(id: 17, pid: 42, frame: frame)
        let overlay = NativeInputWindowHit.PublicWindow(id: 99, pid: 60, frame: frame)
        // Public rows are identity mapping only. Their z-order cannot override
        // the independently observed AX hit, even with an overlapping overlay.
        for rows in [[target, overlay], [overlay, target]] {
            precondition(
                NativeInputWindowHit.mapPublicWindow(pid: 42, frame: frame, at: point, windows: rows) == .window(17))
        }
        let samePIDOverlay = NativeInputWindowHit.PublicWindow(id: 98, pid: 42, frame: frame)
        precondition(
            NativeInputWindowHit.mapPublicWindow(pid: 42, frame: frame, at: point, windows: [target, samePIDOverlay])
                == .ambiguousWindow)
        precondition(
            NativeInputWindowHit.mapPublicWindow(pid: 42, frame: frame, at: point, windows: [overlay])
                == .unresolvedWindow)
        precondition(
            NativeInputWindowHit.mapPublicWindow(pid: 42, frame: frame, at: .zero, windows: [target])
                == .unresolvedWindow)
        precondition(NativeInputWindowHit.confirmsRetainedWindow(expected: target, current: target, at: point))
        for wrong in [
            nil, overlay, samePIDOverlay, NativeInputWindowHit.PublicWindow(id: 17, pid: 60, frame: frame),
            NativeInputWindowHit.PublicWindow(id: 17, pid: 42, frame: frame.offsetBy(dx: 5, dy: 0))
        ] { precondition(!NativeInputWindowHit.confirmsRetainedWindow(expected: target, current: wrong, at: point)) }
        precondition(!NativeInputWindowHit.confirmsRetainedWindow(expected: target, current: target, at: .zero))
        // Missing AX-window SPI permits a unique public mapping. A vanished
        // known native ID, changed owner or mismatched ID must not substitute
        // another public window, even if that fallback would otherwise work.
        var publicIdentityReads = 0
        func identity() -> NativeInputWindowHit.PublicWindow? {
            publicIdentityReads += 1
            return target
        }
        precondition(
            NativeInputWindowHit.identity(
                pid: 42, nativeID: nil, current: { _ in preconditionFailure("missing native ID queried") },
                publicIdentity: identity)?.id == 17)
        precondition(publicIdentityReads == 1)
        for wrong in [nil, overlay, samePIDOverlay, NativeInputWindowHit.PublicWindow(id: 17, pid: 60, frame: frame)] {
            precondition(
                NativeInputWindowHit.identity(pid: 42, nativeID: 17, current: { _ in wrong }, publicIdentity: identity)
                    == nil)
        }
        precondition(publicIdentityReads == 1)
        precondition(
            NativeInputWindowHit.identity(pid: 42, nativeID: 17, current: { _ in target }, publicIdentity: identity)?.id
                == 17)
        precondition(publicIdentityReads == 1)
        let malformed = CGRect(x: Double.nan, y: 20, width: 100, height: 80)
        precondition(
            NativeInputWindowHit.mapPublicWindow(pid: 42, frame: malformed, at: point, windows: [target])
                == .unresolvedWindow)

        print("NativeInputWindowHitTests passed")
    }
}
