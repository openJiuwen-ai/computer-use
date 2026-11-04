// Copyright (c) Huawei Technologies Co., Ltd. 2026. All rights reserved.

import Foundation

@main enum NativeWindowCaptureJoinTests {
    enum Failure: Error { case sharing, pixels, stopped }
    @MainActor final class Probe {
        var sharingStarted = false, pixelsStarted = false, sharingCancelled = false, pixelsCancelled = false
        var joined = false, stopped = false
        var sharingResume: CheckedContinuation<Void, Never>?
        var pixelsResume: CheckedContinuation<String, Never>?
    }
    @MainActor static func main() async throws {
        let success = Probe()
        let complete = Task { @MainActor in
            let value = try await WindowCaptureJoin.run(check: {}, include: {
                success.sharingStarted = true
                await withCheckedContinuation { success.sharingResume = $0 }
            }, pixels: {
                success.pixelsStarted = true
                return await withCheckedContinuation { success.pixelsResume = $0 }
            })
            success.joined = true
            return value
        }
        while !success.sharingStarted || !success.pixelsStarted { await Task.yield() }
        success.pixelsResume!.resume(returning: "original-window-pixels")
        await Task.yield()
        precondition(!success.joined, "pixels cannot be published before the system sharing indicator succeeds")
        success.sharingResume!.resume()
        let completed = try await complete.value
        precondition(completed == "original-window-pixels")

        for failure in [Failure.sharing, .pixels] {
            let probe = Probe()
            do {
                _ = try await WindowCaptureJoin.run(check: {}, include: {
                    probe.sharingStarted = true
                    while !probe.pixelsStarted { await Task.yield() }
                    if failure == .sharing { throw failure }
                    do { try await Task.sleep(for: .seconds(30)) }
                    catch { probe.sharingCancelled = true; throw error }
                }, pixels: {
                    probe.pixelsStarted = true
                    while !probe.sharingStarted { await Task.yield() }
                    if failure == .pixels { throw failure }
                    do { try await Task.sleep(for: .seconds(30)); return "must-not-publish" }
                    catch { probe.pixelsCancelled = true; throw error }
                })
                preconditionFailure("a failed joined acquisition must not publish")
            } catch is Failure {
                precondition(failure == .sharing ? probe.pixelsCancelled : probe.sharingCancelled,
                             "failure must cancel and join its in-flight sibling")
            }
        }
        let cancelled = Probe()
        let cancelledTask = Task { @MainActor in
            try await WindowCaptureJoin.run(check: {}, include: {
                cancelled.sharingStarted = true
                do { try await Task.sleep(for: .seconds(30)) }
                catch { cancelled.sharingCancelled = true; throw error }
            }, pixels: {
                cancelled.pixelsStarted = true
                do { try await Task.sleep(for: .seconds(30)); return "cancelled" }
                catch { cancelled.pixelsCancelled = true; throw error }
            })
        }
        while !cancelled.sharingStarted || !cancelled.pixelsStarted { await Task.yield() }
        cancelledTask.cancel()
        do { _ = try await cancelledTask.value; preconditionFailure("cancelled capture cannot publish") }
        catch is CancellationError {
            precondition(cancelled.sharingCancelled && cancelled.pixelsCancelled)
        }
        let completedAfterCancel = Probe()
        let lateTask = Task { @MainActor in
            try await WindowCaptureJoin.run(check: {}, include: {
                completedAfterCancel.sharingStarted = true
                await withCheckedContinuation { completedAfterCancel.sharingResume = $0 }
            }, pixels: {
                completedAfterCancel.pixelsStarted = true
                return await withCheckedContinuation { completedAfterCancel.pixelsResume = $0 }
            })
        }
        while !completedAfterCancel.sharingStarted || !completedAfterCancel.pixelsStarted { await Task.yield() }
        lateTask.cancel()
        completedAfterCancel.sharingResume!.resume()
        completedAfterCancel.pixelsResume!.resume(returning: "native completed after cancellation")
        do { _ = try await lateTask.value; preconditionFailure("late native success cannot override cancellation") }
        catch is CancellationError {}
        let stopped = Probe()
        do {
            _ = try await WindowCaptureJoin.run(check: {
                if stopped.stopped { throw Failure.stopped }
            }, include: {}, pixels: { stopped.stopped = true; return "completed-but-revoked" })
            preconditionFailure("a session stop before publication must be respected")
        } catch Failure.stopped {}
        print("PASS concurrent capture joins both results, fails promptly and respects session stop")
    }
}
