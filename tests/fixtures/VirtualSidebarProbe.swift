import AppKit
import SwiftUI

// Native SwiftUI virtualization, with no accessibility overrides or fake errors.
@MainActor final class SidebarModel: ObservableObject {
    @Published var selection: Int? = 0 { didSet { record() } }
    @Published var presses = 0 { didSet { record() } }
    let directory = Bundle.main.bundleURL.deletingLastPathComponent()
    func record() {
        let state: [String: Any] = [
            "selection": selection as Any? ?? NSNull(), "presses": presses, "guard": "Unrelated guard", "pid": getpid(),
            "foreground": NSWorkspace.shared.frontmostApplication?.processIdentifier ?? 0
        ]
        try! JSONSerialization.data(withJSONObject: state, options: [.sortedKeys]).write(
            to: directory.appendingPathComponent("state.json"), options: .atomic)
    }
}

struct SidebarContent: View {
    @ObservedObject var model: SidebarModel
    var body: some View {
        NavigationSplitView {
            List(0..<80, id: \.self, selection: $model.selection) { index in
                Label(String(format: "Record %03d", index), systemImage: "doc").tag(index)
            }.listStyle(.sidebar)
        } detail: {
            VStack {
                Text("Unrelated guard")
                Text("Selected record: \(model.selection ?? -1)")
                Button("Finish outside collection") { model.presses += 1 }.accessibilityIdentifier("finish")
            }
        }
    }
}

@MainActor final class SidebarDelegate: NSObject, NSApplicationDelegate {
    var window: NSWindow!
    let model = SidebarModel()
    func applicationDidFinishLaunching(_ note: Notification) {
        window = NSWindow(
            contentRect: NSRect(x: 110, y: 130, width: 650, height: 350), styleMask: [.titled, .closable],
            backing: .buffered, defer: false)
        window.title = "Virtual Sidebar Acceptance"
        window.isReleasedWhenClosed = false
        window.contentView = NSHostingView(rootView: SidebarContent(model: model))
        window.orderBack(nil)
        model.record()
        Timer.scheduledTimer(withTimeInterval: 180, repeats: false) { _ in
            MainActor.assumeIsolated { NSApp.terminate(nil) }
        }
    }
}
let app = NSApplication.shared
app.setActivationPolicy(.regular)
let delegate = SidebarDelegate()
app.delegate = delegate
app.run()
