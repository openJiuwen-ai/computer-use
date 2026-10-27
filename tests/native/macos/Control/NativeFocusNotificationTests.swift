import AppKit
import ApplicationServices
import Foundation

@main struct NativeFocusNotificationTests {
    @MainActor static func main() throws {
        let event = try SyntheticFocusEvents.keyFocus()
        guard let native = NSEvent(cgEvent: event) else { preconditionFailure("missing native event") }
        precondition(native.type.rawValue == 21 && native.subtype.rawValue == .min)
        precondition(native.windowNumber == 0 && native.modifierFlags.isEmpty)
        precondition(native.locationInWindow == .zero && native.data1 == 0 && native.data2 == 0)

        var order = [String](), posts = 0
        var effects = [(NativePreparationEffect.Kind, NativePreparationEffect.Phase, String, UInt32?)]()
        func record(_ effect: NativePreparationEffect) {
            effects.append((effect.kind, effect.phase, effect.detail, effect.window))
            switch effect.phase {
            case .willSend: order.append("willSend")
            case .returned: order.append("returned")
            }
        }
        try PrepareSyntheticApplicationPass.send(
            window: 7,
            make: {
                order.append("make")
                return event
            }, beforeSend: { order.append("before") },
            onPreparation: { detail in
                precondition(detail == "process key-focus notification")
                order.append("detail")
            }, onNativePreparation: record,
            post: { value in
                precondition(value === event)
                posts += 1
                order.append("post")
            }, afterSend: { order.append("after") })
        precondition(order == ["make", "before", "detail", "willSend", "post", "returned", "after"])
        precondition(posts == 1 && effects.count == 2)
        for (kind, _, detail, window) in effects {
            guard case .focusNotification = kind else { preconditionFailure("unexpected preparation kind") }
            precondition(window == 7 && detail == "process key-focus notification")
        }

        // A failed builder cannot perform preparation or enter any post.
        order = []
        posts = 0
        effects = []
        do {
            try PrepareSyntheticApplicationPass.send(
                window: 7,
                make: {
                    order.append("make")
                    throw DriverError("unavailable event", code: .inputUnavailable)
                }, beforeSend: { order.append("before") }, onPreparation: { _ in order.append("detail") },
                onNativePreparation: record, post: { _ in posts += 1 }, afterSend: { order.append("after") })
            preconditionFailure("failed builder admitted")
        } catch let error as DriverError { precondition(error.code == .inputUnavailable) }
        precondition(order == ["make"] && posts == 0 && effects.isEmpty)

        // Cancellation or a known boundary conflict before send is zero-post.
        for cancel in [true, false] {
            order = []
            posts = 0
            effects = []
            do {
                try PrepareSyntheticApplicationPass.send(
                    window: 7,
                    make: {
                        order.append("make")
                        return event
                    },
                    beforeSend: {
                        order.append("before")
                        if cancel { throw CancellationError() }
                        throw DriverError("original window changed", code: .staleTarget)
                    }, onPreparation: { _ in order.append("detail") }, onNativePreparation: record,
                    post: { _ in posts += 1 }, afterSend: { order.append("after") })
                preconditionFailure("invalid boundary admitted")
            } catch is CancellationError { precondition(cancel) } catch let error as DriverError {
                precondition(!cancel && error.code == .staleTarget)
            }
            precondition(order == ["make", "before"] && posts == 0 && effects.isEmpty)
        }

        // Failure after the native post retains its actual preparation facts;
        // it is not permission to send again or manufacture a second recipe.
        order = []
        posts = 0
        effects = []
        do {
            try PrepareSyntheticApplicationPass.send(
                window: 7,
                make: {
                    order.append("make")
                    return event
                }, beforeSend: { order.append("before") }, onPreparation: { _ in order.append("detail") },
                onNativePreparation: record,
                post: { _ in
                    posts += 1
                    order.append("post")
                },
                afterSend: {
                    order.append("after")
                    throw CancellationError()
                })
            preconditionFailure("post-send cancellation ignored")
        } catch is CancellationError {}
        precondition(posts == 1 && effects.count == 2)
        precondition(order == ["make", "before", "detail", "willSend", "post", "returned", "after"])

        // Existing Plan rules remain unchanged; false active is not a new
        // dispatch prohibition, and actual foreground requires no notification.
        for active in [Optional(false), Optional(true), nil] {
            let state = NativeFocusSnapshot(foreground: true, reportedActive: active, focusedWindow: 7, prepared: false)
            precondition(!PlanFocusPreparationPass.run(state, window: 7).notify)
        }
        let passive = NativeFocusSnapshot(foreground: false, reportedActive: false, focusedWindow: 7, prepared: true)
        precondition(PlanFocusPreparationPass.run(passive, window: 7).notify)
        print("PASS focus-only event, single-post progress, cancellation and existing plan contracts")
    }
}
