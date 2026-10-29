// Copyright (c) Huawei Technologies Co., Ltd. 2026. All rights reserved.

import AppKit

/// Display the agent's received frames without sending desktop input.
@MainActor final class PreviewController: NSObject {
    private final class Panel: NSPanel {
        override var canBecomeKey: Bool { false }
        override var canBecomeMain: Bool { false }
    }
    private let panel: Panel
    private let appLabel = NSTextField(labelWithString: "")
    private let imageView = NSImageView()
    private let placeholder = NSTextField(labelWithString: "暂无可共享的应用画面")
    private var imageHeight: NSLayoutConstraint!
    private(set) var isDismissed = false

    override init() {
        panel = Panel(
            contentRect: NSRect(x: 0, y: 0, width: 420, height: 300), styleMask: [.borderless, .nonactivatingPanel],
            backing: .buffered, defer: false)
        super.init()
        panel.title = "Computer Use Preview"
        panel.level = .floating
        panel.animationBehavior = .none
        panel.hidesOnDeactivate = false
        panel.becomesKeyOnlyIfNeeded = true
        panel.isReleasedWhenClosed = false
        panel.isMovableByWindowBackground = true
        panel.collectionBehavior = [.canJoinAllSpaces, .fullScreenAuxiliary]
        panel.isOpaque = false
        panel.backgroundColor = .clear
        panel.hasShadow = true
        panel.appearance = NSAppearance(named: .darkAqua)

        let content = NSVisualEffectView()
        content.material = .hudWindow
        content.state = .active
        content.blendingMode = .withinWindow
        content.wantsLayer = true
        content.layer?.cornerRadius = 12
        content.layer?.masksToBounds = true
        panel.contentView = content
        appLabel.font = .systemFont(ofSize: 13, weight: .medium)
        appLabel.lineBreakMode = .byTruncatingTail
        appLabel.setAccessibilityIdentifier("preview-app")
        appLabel.setContentCompressionResistancePriority(.defaultLow, for: .horizontal)
        placeholder.font = .systemFont(ofSize: 12)
        placeholder.textColor = .secondaryLabelColor
        imageView.imageScaling = .scaleProportionallyUpOrDown
        imageView.setContentCompressionResistancePriority(.defaultLow, for: .horizontal)
        imageView.setContentCompressionResistancePriority(.defaultLow, for: .vertical)
        imageView.setAccessibilityLabel("应用共享画面")
        let close = NSButton(
            image: NSImage(systemSymbolName: "xmark", accessibilityDescription: "关闭预览")!, target: self,
            action: #selector(dismiss))
        close.isBordered = false
        close.contentTintColor = .secondaryLabelColor
        close.toolTip = "关闭预览，后台操作继续"
        for view in [appLabel, imageView, placeholder, close] {
            view.translatesAutoresizingMaskIntoConstraints = false
            content.addSubview(view)
        }
        imageHeight = imageView.heightAnchor.constraint(equalToConstant: 250)
        NSLayoutConstraint.activate([
            content.widthAnchor.constraint(equalToConstant: 420),
            appLabel.topAnchor.constraint(equalTo: content.topAnchor, constant: 12),
            appLabel.leadingAnchor.constraint(equalTo: content.leadingAnchor, constant: 12),
            appLabel.trailingAnchor.constraint(equalTo: close.leadingAnchor, constant: -10),
            close.trailingAnchor.constraint(equalTo: content.trailingAnchor, constant: -10),
            close.centerYAnchor.constraint(equalTo: appLabel.centerYAnchor),
            close.widthAnchor.constraint(equalToConstant: 20), close.heightAnchor.constraint(equalToConstant: 20),
            imageView.topAnchor.constraint(equalTo: appLabel.bottomAnchor, constant: 10),
            imageView.leadingAnchor.constraint(equalTo: content.leadingAnchor, constant: 8),
            imageView.trailingAnchor.constraint(equalTo: content.trailingAnchor, constant: -8), imageHeight,
            imageView.bottomAnchor.constraint(equalTo: content.bottomAnchor, constant: -8),
            placeholder.centerXAnchor.constraint(equalTo: imageView.centerXAnchor),
            placeholder.centerYAnchor.constraint(equalTo: imageView.centerYAnchor)
        ])
        if let screen = NSScreen.screens.first {
            panel.setFrameOrigin(NSPoint(x: screen.visibleFrame.maxX - 440, y: screen.visibleFrame.minY + 20))
        }
    }

    func reset() {
        imageView.image = nil
        appLabel.stringValue = ""
        panel.orderOut(nil)
    }

    /// Each frame is self-contained, so slow viewers can skip older frames.
    func frame(name: String, png: String?) {
        guard !isDismissed else { return }
        appLabel.stringValue = name
        appLabel.toolTip = name
        imageView.image = png.flatMap { Data(base64Encoded: $0) }.flatMap { NSImage(data: $0) }
        placeholder.isHidden = imageView.image != nil
        if let image = imageView.image, image.size.width > 0 {
            imageHeight.constant = min(420, max(120, 404 * image.size.height / image.size.width))
        }
        panel.contentView?.layoutSubtreeIfNeeded()
        var frame = panel.frame
        frame.size = NSSize(width: 420, height: panel.contentView?.fittingSize.height ?? 300)
        if let screen = panel.screen {
            frame.origin.y = max(screen.visibleFrame.minY, min(frame.minY, screen.visibleFrame.maxY - frame.height))
        }
        panel.setFrame(frame, display: true)
        panel.orderFrontRegardless()
    }

    @objc private func dismiss() {
        isDismissed = true
        panel.orderOut(nil)
    }
}

/// Coalesce cross-thread display updates before scheduling work on AppKit.
/// This also bounds memory while an AX request temporarily occupies the main thread.
final class PreviewLatestValue<Value: Sendable>: @unchecked Sendable {
    private let lock = NSLock()
    private var pending: Value?
    private var scheduled = false
    private let receive: @MainActor @Sendable (Value) -> Void
    init(receive: @escaping @MainActor @Sendable (Value) -> Void) { self.receive = receive }
    func submit(_ value: Value) {
        lock.lock()
        pending = .some(value)
        guard !scheduled else {
            lock.unlock()
            return
        }
        scheduled = true
        lock.unlock()
        Task { @MainActor in if let value = self.take() { self.receive(value) } }
    }
    private func take() -> Value? {
        lock.lock()
        defer { lock.unlock() }
        let value = pending
        pending = nil
        scheduled = false
        return value
    }
}
