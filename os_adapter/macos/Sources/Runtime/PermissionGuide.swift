// Copyright (c) Huawei Technologies Co., Ltd. 2026. All rights reserved.

import AppKit

struct PermissionGuideOrder {
    private var pending = true

    mutating func refresh() { pending = true }

    mutating func needsPlacement(guideIndex: Int?, settingsIndex: Int) -> Bool {
        guard pending else { return false }
        pending = false
        return guideIndex.map { $0 > settingsIndex } ?? true
    }
}

@MainActor final class PermissionGuide: NSObject, NSApplicationDelegate, NSWindowDelegate {
    private static let shownKey = "didShowDesktopPermissionGuide"
    nonisolated private static let settingsBundle = "com.apple.systempreferences"
    private let startingAt: DesktopPermission
    private let automatic: Bool
    private let bundle = Bundle.main.bundleURL
    private var panel: NSPanel?
    private var tracking: Timer?
    private var activationObserver: NSObjectProtocol?
    private var settingsWindow: CGWindowID?
    private var ordering = PermissionGuideOrder()
    private var navigation: UUID?
    private var waitForSettingsUntil: TimeInterval = 0
    private var shown = false
    private var dragging = false
    private lazy var sections = NSSegmentedControl(
        labels: [guideText("Accessibility", "辅助功能"), guideText("Screen Recording", "屏幕录制")],
        trackingMode: .selectOne, target: self, action: #selector(openSettings))

    private init(startingAt: DesktopPermission, automatic: Bool) {
        self.startingAt = startingAt
        self.automatic = automatic
    }

    static func open(startingAt permission: DesktopPermission) {
        let bundle = Bundle.main.bundleURL
        guard bundle.pathExtension == "app", !UserDefaults.standard.bool(forKey: shownKey) else { return }
        let configuration = NSWorkspace.OpenConfiguration()
        configuration.createsNewApplicationInstance = true
        configuration.activates = false
        configuration.addsToRecentItems = false
        configuration.arguments = ["--permission-guide", permission.rawValue, "--automatic"]
        // A Swift driver can outlive the SDK request but exit with the session.
        // Launch the guide separately so the pane stays available after EOF.
        NSWorkspace.shared.openApplication(at: bundle, configuration: configuration) { _, error in
            if let error { diagnose("permission guide: \(error)") }
        }
    }

    static func run(arguments: [String]) {
        guard Bundle.main.bundleURL.pathExtension == "app" else { return }
        let startingAt = arguments.dropFirst().first.flatMap(DesktopPermission.init(rawValue:))
        guard let permission = startingAt ?? DesktopPermission.allCases.first(where: { !$0.granted }) else { return }
        let guide = PermissionGuide(startingAt: permission, automatic: arguments.contains("--automatic"))
        let application = NSApplication.shared
        application.delegate = guide
        withExtendedLifetime(guide) { application.run() }
    }

    func applicationDidFinishLaunching(_ notification: Notification) {
        if automatic && UserDefaults.standard.bool(forKey: Self.shownKey) {
            NSApp.terminate(nil)
            return
        }
        NSApp.setActivationPolicy(.accessory)
        panel = makePanel()
        activationObserver = NSWorkspace.shared.notificationCenter.addObserver(
            forName: NSWorkspace.didActivateApplicationNotification, object: nil, queue: .main
        ) { [weak self] notification in
            guard let app = notification.userInfo?[NSWorkspace.applicationUserInfoKey] as? NSRunningApplication,
                app.bundleIdentifier == Self.settingsBundle else { return }
            MainActor.assumeIsolated { self?.ordering.refresh() }
        }
        openSettings()
        let timer = Timer(timeInterval: 0.25, repeats: true) { [weak self] _ in
            MainActor.assumeIsolated { self?.followSettings() }
        }
        tracking = timer
        RunLoop.main.add(timer, forMode: .common)
    }

    func windowWillClose(_ notification: Notification) {
        tracking?.invalidate()
        if let activationObserver { NSWorkspace.shared.notificationCenter.removeObserver(activationObserver) }
        panel = nil
        NSApp.terminate(nil)
    }

    private func makePanel() -> NSPanel {
        sections.selectedSegment = startingAt == .screenCapture ? 1 : 0
        sections.segmentStyle = .rounded
        sections.controlSize = .small
        let instruction = NSTextField(labelWithString: guideText(
            "Turn on above. Drag in if missing.", "在上方开启，缺少时拖入"))
        instruction.font = .systemFont(ofSize: 12, weight: .medium)
        let arrow = NSImageView()
        arrow.image = NSImage(systemSymbolName: "arrow.up", accessibilityDescription: nil)
        arrow.contentTintColor = .controlAccentColor
        arrow.symbolConfiguration = NSImage.SymbolConfiguration(pointSize: 15, weight: .semibold)
        arrow.setAccessibilityElement(false)
        let directions = NSStackView(views: [arrow, instruction])
        directions.alignment = .centerY
        directions.spacing = 6

        let appRow = PermissionAppRow(bundle: bundle) { [weak self] dragging in
            self?.dragging = dragging
            if dragging { self?.panel?.orderOut(nil) }
            else {
                self?.ordering.refresh()
                self?.followSettings()
            }
        }
        let copyTitle = guideText("Copy App Path", "复制应用路径")
        let copyPath = NSButton(
            image: NSImage(systemSymbolName: "doc.on.doc", accessibilityDescription: copyTitle) ?? NSImage(),
            target: self, action: #selector(copyAppPath(_:)))
        copyPath.isBordered = false
        copyPath.toolTip = guideText(
            "Copy the app path and close this guide. Click + in System Settings, press ⇧⌘G, and paste the path.",
            "复制应用路径后关闭引导。点系统设置中的「＋」，按 ⇧⌘G 并粘贴路径。")
        copyPath.setAccessibilityLabel(copyTitle)
        copyPath.setAccessibilityHelp(copyPath.toolTip)
        let closeTitle = guideText("Close Permission Guide", "关闭授权引导")
        let close = NSButton(
            image: NSImage(systemSymbolName: "xmark.circle", accessibilityDescription: closeTitle) ?? NSImage(),
            target: self, action: #selector(closeGuide))
        close.isBordered = false
        close.toolTip = closeTitle
        close.setAccessibilityLabel(closeTitle)
        let sectionsDivider = NSBox()
        let actionsDivider = NSBox()
        for divider in [sectionsDivider, actionsDivider] {
            divider.boxType = .separator
            divider.widthAnchor.constraint(equalToConstant: 1).isActive = true
            divider.heightAnchor.constraint(equalToConstant: 22).isActive = true
        }
        let layout = NSStackView(views: [
            appRow, directions, sectionsDivider, sections, actionsDivider, copyPath, close,
        ])
        layout.alignment = .centerY
        layout.spacing = 10
        layout.setCustomSpacing(6, after: copyPath)
        layout.translatesAutoresizingMaskIntoConstraints = false
        NSLayoutConstraint.activate([
            appRow.widthAnchor.constraint(equalToConstant: 156),
            appRow.heightAnchor.constraint(equalToConstant: 30),
            copyPath.widthAnchor.constraint(equalToConstant: 24),
            copyPath.heightAnchor.constraint(equalToConstant: 24),
            close.widthAnchor.constraint(equalToConstant: 24),
            close.heightAnchor.constraint(equalToConstant: 24),
        ])

        let width = ceil(layout.fittingSize.width) + 20
        let panel = PermissionGuidePanel(contentRect: NSRect(x: 0, y: 0, width: width, height: 46),
                            styleMask: [.borderless, .nonactivatingPanel], backing: .buffered, defer: false)
        panel.title = "Jiuwen CUA"
        panel.delegate = self
        panel.isOpaque = false
        panel.backgroundColor = .clear
        panel.hasShadow = true
        panel.level = .normal
        panel.animationBehavior = .none
        panel.hidesOnDeactivate = false
        panel.becomesKeyOnlyIfNeeded = true
        let content = NSVisualEffectView()
        content.material = .popover
        content.blendingMode = .behindWindow
        content.state = .active
        content.wantsLayer = true
        content.layer?.cornerRadius = 10
        content.layer?.masksToBounds = true
        panel.contentView = content
        content.addSubview(layout)
        NSLayoutConstraint.activate([
            layout.leadingAnchor.constraint(equalTo: content.leadingAnchor, constant: 10),
            layout.trailingAnchor.constraint(equalTo: content.trailingAnchor, constant: -10),
            layout.centerYAnchor.constraint(equalTo: content.centerYAnchor),
        ])
        return panel
    }

    @objc private func openSettings() {
        let section = sections.selectedSegment == 1 ? "Privacy_ScreenCapture" : "Privacy_Accessibility"
        let url = URL(string: "x-apple.systempreferences:com.apple.preference.security?\(section)")!
        let configuration = NSWorkspace.OpenConfiguration()
        configuration.activates = settingsWindow == nil
        let request = UUID()
        navigation = request
        waitForSettingsUntil = ProcessInfo.processInfo.systemUptime + 2
        NSWorkspace.shared.open(url, configuration: configuration) { [weak self] _, error in
            DispatchQueue.main.async {
                guard let self, self.navigation == request else { return }
                if let error { diagnose("permission settings: \(error)") }
                self.ordering.refresh()
                self.followSettings()
            }
        }
    }

    private func followSettings() {
        guard let panel, !dragging, let primary = NSScreen.screens.first else { return }
        if DesktopPermission.allCases.allSatisfy(\.granted) {
            panel.close()
            return
        }
        let settings = NSRunningApplication.runningApplications(withBundleIdentifier: Self.settingsBundle).first
        let catalogue = CGWindowListCopyWindowInfo(.optionOnScreenOnly, kCGNullWindowID) as? [[String: Any]]
        let windows = catalogue?.filter { ($0[kCGWindowLayer as String] as? NSNumber)?.intValue == 0 }
        let attachedWindow = settingsWindow
        let matches = windows?.enumerated().compactMap { index, row -> (Int, CGWindowID, CGRect)? in
            guard let settings, (row[kCGWindowOwnerPID as String] as? NSNumber)?.int32Value == settings.processIdentifier,
                let number = (row[kCGWindowNumber as String] as? NSNumber)?.uint32Value,
                let bounds = row[kCGWindowBounds as String] as? [String: Any],
                let frame = CGRect(dictionaryRepresentation: bounds as CFDictionary), frame.width > 200, frame.height > 200
            else { return nil }
            return (index, number, CGRect(x: frame.minX, y: primary.frame.maxY - frame.maxY,
                                          width: frame.width, height: frame.height))
        }
        let waitingForSettings = ProcessInfo.processInfo.systemUptime < waitForSettingsUntil
        let existing = attachedWindow.flatMap { number in matches?.first { $0.1 == number } }
        let match = existing ?? (attachedWindow == nil || !waitingForSettings ? matches?.first : nil)
        if let (index, number, frame) = match {
            if attachedWindow != number { ordering.refresh() }
            settingsWindow = number
            let screen = NSScreen.screens.first { $0.frame.contains(CGPoint(x: frame.midX, y: frame.midY)) } ?? primary
            let below = frame.minY - panel.frame.height - 8
            let y = below >= screen.visibleFrame.minY + 12 ? below : frame.minY + 8
            position(panel, at: CGPoint(x: frame.midX, y: y), within: screen.visibleFrame)
            // When there is no space below Settings, keep the guide above its
            // content while Settings is active without repeatedly ordering it.
            let overlapsSettings = panel.frame.intersects(frame)
            let settingsIsActive = NSWorkspace.shared.frontmostApplication?.bundleIdentifier == Self.settingsBundle
            let level: NSWindow.Level = overlapsSettings && settingsIsActive ? .floating : .normal
            if panel.level != level {
                panel.level = level
                ordering.refresh()
            }
            let panelIndex = windows?.firstIndex {
                ($0[kCGWindowNumber as String] as? NSNumber)?.intValue == panel.windowNumber
            }
            if ordering.needsPlacement(guideIndex: panelIndex, settingsIndex: index) {
                if level == .normal { panel.order(.above, relativeTo: Int(number)) }
                else if !panel.isVisible { panel.orderFrontRegardless() }
            }
        } else if settingsWindow != nil, windows != nil, !waitingForSettings {
            panel.close()
            return
        } else if !waitingForSettings, !panel.isVisible {
            position(panel, at: CGPoint(x: primary.visibleFrame.midX, y: primary.visibleFrame.minY + 24),
                     within: primary.visibleFrame)
            panel.orderFrontRegardless()
        }
        if panel.isVisible, !shown {
            shown = true
            if automatic { UserDefaults.standard.set(true, forKey: Self.shownKey) }
        }
    }

    private func position(_ panel: NSPanel, at anchor: CGPoint, within visible: CGRect) {
        let x = max(visible.minX + 12, min(anchor.x - panel.frame.width / 2, visible.maxX - panel.frame.width - 12))
        let y = max(visible.minY + 12, min(anchor.y, visible.maxY - panel.frame.height - 12))
        let origin = NSPoint(x: x, y: y)
        if panel.frame.origin != origin { panel.setFrameOrigin(origin) }
    }

    @objc private func closeGuide() { panel?.close() }

    @objc private func copyAppPath(_ sender: NSButton) {
        NSPasteboard.general.clearContents()
        if NSPasteboard.general.setString(bundle.path, forType: .string) {
            let copied = guideText("Path Copied", "已复制路径")
            sender.image = NSImage(systemSymbolName: "checkmark", accessibilityDescription: copied)
            sender.setAccessibilityLabel(copied)
        }
    }
}

private final class PermissionGuidePanel: NSPanel {
    override var canBecomeKey: Bool { true }
    override func cancelOperation(_ sender: Any?) { close() }
}

private final class PermissionAppRow: NSView, NSDraggingSource {
    private let bundle: URL
    private let icon: NSImage
    private let dragChanged: (Bool) -> Void

    init(bundle: URL, dragChanged: @escaping (Bool) -> Void) {
        self.bundle = bundle
        self.icon = NSWorkspace.shared.icon(forFile: bundle.path)
        self.dragChanged = dragChanged
        super.init(frame: .zero)
        let iconView = NSImageView(image: icon)
        let label = NSTextField(labelWithString: bundle.lastPathComponent)
        label.font = .systemFont(ofSize: 12, weight: .medium)
        label.lineBreakMode = .byTruncatingMiddle
        label.setContentHuggingPriority(.defaultLow, for: .horizontal)
        let grip = NSImageView()
        grip.image = NSImage(systemSymbolName: "circle.grid.2x2.fill", accessibilityDescription: nil)
        grip.contentTintColor = .tertiaryLabelColor
        grip.symbolConfiguration = NSImage.SymbolConfiguration(pointSize: 10, weight: .medium)
        grip.setAccessibilityElement(false)
        let row = NSStackView(views: [iconView, label, grip])
        row.alignment = .centerY
        row.spacing = 7

        let background = NSBox()
        background.boxType = .custom
        background.titlePosition = .noTitle
        background.borderWidth = 0
        background.fillColor = .controlBackgroundColor
        background.cornerRadius = 6
        background.contentViewMargins = NSSize(width: 6, height: 4)
        background.contentView = row
        background.autoresizingMask = [.width, .height]
        addSubview(background)
        NSLayoutConstraint.activate([
            iconView.widthAnchor.constraint(equalToConstant: 22), iconView.heightAnchor.constraint(equalToConstant: 22),
        ])
        setAccessibilityLabel(bundle.lastPathComponent)
        setAccessibilityHelp(guideText("Drag this app into System Settings.", "将此应用拖入系统设置列表。"))
    }

    required init?(coder: NSCoder) { nil }

    override var mouseDownCanMoveWindow: Bool { false }
    override func acceptsFirstMouse(for event: NSEvent?) -> Bool { true }
    override func hitTest(_ point: NSPoint) -> NSView? { super.hitTest(point).map { _ in self } }
    override func mouseDown(with event: NSEvent) {}

    override func resetCursorRects() {
        addCursorRect(bounds, cursor: .openHand)
    }

    override func mouseDragged(with event: NSEvent) {
        let point = convert(event.locationInWindow, from: nil)
        let item = NSDraggingItem(pasteboardWriter: bundle as NSURL)
        item.setDraggingFrame(NSRect(x: point.x - 16, y: point.y - 16, width: 32, height: 32), contents: icon)
        beginDraggingSession(with: [item], event: event, source: self)
    }

    func draggingSession(_ session: NSDraggingSession, sourceOperationMaskFor context: NSDraggingContext) -> NSDragOperation {
        .copy
    }

    func draggingSession(_ session: NSDraggingSession, willBeginAt screenPoint: NSPoint) { dragChanged(true) }
    func draggingSession(_ session: NSDraggingSession, endedAt screenPoint: NSPoint, operation: NSDragOperation) {
        dragChanged(false)
    }
}

private func guideText(_ english: String, _ chinese: String) -> String {
    Locale.preferredLanguages.first?.hasPrefix("zh") == true ? chinese : english
}
