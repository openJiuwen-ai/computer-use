import AppKit
import ApplicationServices

struct NativeInputScope {
    let wire: [String: Any]
    let application: NSRunningApplication
    let window: AXUIElement?
    let menuRoot: AXUIElement?
    let blocked: [String]

    func requireWindow() throws -> AXUIElement {
        guard menuRoot == nil, let window else {
            throw DriverError(
                "this input unit requires a window surface, not an application menu", code: .inputUnavailable)
        }
        return window
    }
}
