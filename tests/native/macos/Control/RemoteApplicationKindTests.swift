@main struct RemoteApplicationKindTests {
    @MainActor static func main() {
        var count = 0
        func check(_ value: Bool) {
            precondition(value)
            count += 1
        }
        for policy in [0, 1] {
            check(
                RemoteApplicationKind.classify(
                    available: true, finished: true, terminated: false, activationPolicy: policy)
                    == .ordinaryApplication)
        }
        check(
            RemoteApplicationKind.classify(available: true, finished: true, terminated: false, activationPolicy: 2)
                == .prohibitedApplication)
        for policy: Int? in [nil, -1, 3, 0, 1, 2] {
            check(
                RemoteApplicationKind.classify(
                    available: false, finished: true, terminated: false, activationPolicy: policy) == .unknown)
            check(
                RemoteApplicationKind.classify(
                    available: true, finished: false, terminated: false, activationPolicy: policy) == .unknown)
            check(
                RemoteApplicationKind.classify(
                    available: true, finished: true, terminated: true, activationPolicy: policy) == .unknown)
        }
        check(
            RemoteApplicationKind.classify(available: true, finished: true, terminated: false, activationPolicy: nil)
                == .unknown)
        check(
            RemoteApplicationKind.classify(available: true, finished: true, terminated: false, activationPolicy: 3)
                == .unknown)
        print("PASS: \(count) production classifier pure checks; no AppKit runtime sampling")
    }
}
