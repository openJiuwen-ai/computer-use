import AppKit
import ApplicationServices

/// Native geometry for one retained window. Preparing a window never clicks
/// its activation point: that point may belong to an unrelated content control.
@MainActor struct NativeWindowUnit {
    let application: NSRunningApplication
    let window: AXUIElement

    /// Exact native window geometry, independent of PNGs or AX tree coverage.
    func mapping() throws -> WindowGeometry.WindowMapping {
        try BackgroundTarget(application).validate(input: true)
        let current = NativeInputWindowHit.identity(
            pid: application.processIdentifier, nativeID: BackgroundEvents.nativeWindowID(window),
            current: { WindowServerInfo.window($0).flatMap(NativeInputWindowQuery.row) },
            publicIdentity: {
                NativeInputWindowQuery.publicIdentity(of: window, pid: application.processIdentifier).window
            })
        guard let current else {
            throw DriverError("the selected window has no confirmed native input route", code: .inputUnavailable)
        }
        return .init(
            id: current.id, pid: application.processIdentifier, frame: current.frame, title: nil,
            width: max(1, Int(current.frame.width.rounded(.up))), height: max(1, Int(current.frame.height.rounded(.up)))
        )
    }

}
