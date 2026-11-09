// Copyright (c) Huawei Technologies Co., Ltd. 2026. All rights reserved.

import AppKit
import ApplicationServices
import Darwin

/// WindowServer discovery is independent of an application's AX implementation.
/// Include the main process and confirmed in-bundle descendants. A title or
/// process name alone never establishes ownership of another process's window.
@MainActor enum NativeWindows {
    private static let ownership = NativeProcessOwnership()
    struct Window {
        let application: NSRunningApplication
        let process: NativeProcessIdentity
        let mapping: WindowGeometry.WindowMapping
        let onScreen: Bool
        var level: Int? = nil
        var menu: AXUIElement? = nil
        var menuParent: CGWindowID? = nil
        // AX ancestry can prove a logical document host without a physical parent.
        var menuHost: CGWindowID? = nil
        var parentID: CGWindowID? = nil
    }

    static func belongs(_ process: NativeProcessIdentity, to app: NSRunningApplication) -> Bool {
        guard !app.isTerminated, let bundle = app.bundleURL,
            let owner = NativeProcessIdentity.read(app.processIdentifier)
        else { return false }
        return ownership.belongs(process, to: owner, bundle: bundle)
    }

    static func list(_ app: NSRunningApplication) -> [Window] { listIfAvailable(app) ?? [] }

    /// Group a helper window only through the existing in-bundle process
    /// ancestry proof. This describes application membership, never document
    /// parentage or modality. No installed/running application scan is needed.
    static func discoveryOwner(_ window: Window) -> NSRunningApplication {
        var owner = window.application, current = window.process, seen = Set<pid_t>()
        for _ in 0..<16 {
            guard seen.insert(current.pid).inserted, current.parent > 1,
                let parent = NativeProcessIdentity.read(current.parent), current.wasBorn(afterOrWith: parent)
            else { break }
            if let application = NSRunningApplication(processIdentifier: parent.pid), belongs(window.process, to: application) {
                owner = application
            }
            current = parent
        }
        return owner
    }

    /// Absence proofs must not turn a WindowServer query failure into an empty desktop.
    static func listIfAvailable(_ app: NSRunningApplication) -> [Window]? {
        ownership.prune()
        // discoverable already requires kCGWindowIsOnscreen. Ask WindowServer
        // for that set directly instead of materializing every hidden buffer
        // before each input boundary check. AX discovery retains hidden roots.
        guard
            let catalog = CGWindowListCopyWindowInfo([.optionOnScreenOnly, .excludeDesktopElements], kCGNullWindowID)
                as? [[String: Any]]
        else { return nil }
        var windows = catalogue(catalog, owner: app)
        let parents = NativeWindowParents.read(windows.map { $0.mapping.id })
        for index in windows.indices { windows[index].parentID = parents?[windows[index].mapping.id] }
        return NativeMenuSurfaces.classify(windows)
    }

    /// Reuse an already captured WindowServer catalogue. Desktop discovery
    /// does not initialize AX providers, scan application catalogues or infer
    /// document ownership from window geometry. Process identity is sampled
    /// once per owner and retained with the native window reference.
    static func catalogue(_ catalog: [[String: Any]], owner app: NSRunningApplication? = nil) -> [Window] {
        var owners = [pid_t: NativeProcessIdentity](), rejected = Set<pid_t>()
        return catalog.compactMap { value in
            guard let pid = (value[kCGWindowOwnerPID as String] as? NSNumber)?.int32Value, !rejected.contains(pid),
                let id = (value[kCGWindowNumber as String] as? NSNumber)?.uint32Value,
                let bounds = value[kCGWindowBounds as String] as? [String: Any],
                let frame = CGRect(dictionaryRepresentation: bounds as CFDictionary)
            else { return nil }
            let title = value[kCGWindowName as String] as? String ?? ""
            let onScreen = (value[kCGWindowIsOnscreen as String] as? NSNumber)?.boolValue == true
            guard
                WindowSelection.discoverable(
                    .init(id: id, frame: frame, title: title, visible: onScreen),
                    alpha: (value[kCGWindowAlpha as String] as? NSNumber)?.doubleValue)
            else { return nil }
            let process: NativeProcessIdentity
            if let known = owners[pid] {
                process = known
            } else {
                guard let candidate = NativeProcessIdentity.read(pid),
                    app.map({ pid == $0.processIdentifier || belongs(candidate, to: $0) }) ?? true
                else {
                    rejected.insert(pid)
                    return nil
                }
                process = candidate
                owners[pid] = candidate
            }
            guard let application = NSRunningApplication(processIdentifier: pid), !application.isTerminated else {
                return nil
            }
            return Window(
                application: application, process: process,
                mapping: WindowGeometry.WindowMapping(
                    id: id, pid: pid, frame: frame, title: title, width: Int(frame.width.rounded(.up)),
                    height: Int(frame.height.rounded(.up))), onScreen: onScreen,
                level: (value[kCGWindowLayer as String] as? NSNumber)?.intValue)
        }
    }

}
