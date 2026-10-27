# First content click and pointer coordinates

`PointerLocationProbe.swift` is a disposable native AppKit application. Its two
standard document windows each start with the first 55 characters selected.
The `NSTextView` uses normal AppKit first-mouse handling; it does not override
`acceptsFirstMouse` to make an unsafe route appear reliable.

The fixture writes `state.json` and `events.jsonl` into its first argument,
including the real key window, first responder, actual editor selection, the
application's received mouse events and the content view's received mouse-down.
An application mouse-down without `editor-before` is not a successful content
click. `AXFocusedWindow`, window `AXFocused` and the global foreground process
are separate facts; use the fixture's real state as the effect oracle.

Build a fresh disposable `.app` using `swiftc -swift-version 6`, a generated
`Info.plist` with `CFBundleExecutable` matching the binary, and ad-hoc `codesign`.
Launch the binary directly with a new output directory. Stop it after each case;
a second click on an already warmed window cannot validate the first click.
The standard `computerHost()` SDK can then select `Selection target 0` from
`computer.app(bundle_path).read()` and observe that exact App before using its
public coordinate click. No project API or fixture setup should click the
target content before the tested request.

Optional fixture flags preserve the original default behavior:

- `--offset`: target 0 has native top-left origin `(1279, 30)` on the main
  display. A `(118, 244)` window-pixel click tests a nonzero capture transform
  without clicking window chrome.
- `--front-other`: target 1 is the application's real key window and the app is
  globally foreground; target 0 remains a different, non-key document.
- `--dialog`: target 0 declares the official AX dialog subrole while retaining
  normal AppKit key-window and first-mouse behavior.
- `--non-key-panel`: target 0 is a real nonactivating NSPanel whose
  `canBecomeKey` and `canBecomeMain` are both false. Combine with `--front-other`
  to keep the owning app foreground while a different document remains key.
- `--foreground`: target 0 starts key and foreground, for the warm-route control.
- `--global-location`: the editor polls the actual global pointer, retaining the
  existing toolkit compatibility experiment. Omit it for the first-mouse case.

Run each cold case with a new fixture and SDK process:

1. Shared desktop, foreground assistance disabled, fixture in background:
   one requested click or drag must be refused before any desired mouse-down;
   both editor texts and selections must be unchanged.
2. Shared desktop, foreground assistance enabled, fixture in background:
   one public click at `(118, 244)` must prepare target 0 and produce exactly
   one `editor-before` with `clickCount=1`, a collapsed caret, and unchanged
   editor text. Only after that observation, insert a unique marker once and
   verify insertion at that caret; the originally selected prefix and the
   unrelated document must remain unchanged.
3. Repeat case 2 with `--front-other`. Same foreground PID is insufficient:
   target 0 must receive the single content mouse-down rather than spending it
   only on becoming key. Also repeat with `--dialog --front-other`.
4. With `--foreground` and no foreground assistance, the existing exact visual
   route must still work. Use `--non-key-panel --front-other` and, separately,
   `BackgroundPointerDragProbe.swift` for
   a nonactivating panel that cannot become key; document key requirements must
   not be imposed on that panel.

Retain the SDK command journal, before/after state, fixture events and driver
hash. Verify requested `pointer_sequence` occurs once, no input was replayed,
and cleanup still releases a held button if a mouse-down closes a window or a
drag is cancelled. A successful dispatch receipt alone is not acceptance.
