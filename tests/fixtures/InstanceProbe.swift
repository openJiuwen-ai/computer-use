import AppKit

// A registered application instance with no windows, menus, activation or input.
let application = NSApplication.shared
application.setActivationPolicy(.accessory)
Timer.scheduledTimer(withTimeInterval: 60, repeats: false) { _ in MainActor.assumeIsolated { NSApp.terminate(nil) } }
application.run()
