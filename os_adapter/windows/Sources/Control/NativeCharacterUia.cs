// Copyright (c) Huawei Technologies Co., Ltd. 2026. All rights reserved.

using System;
using System.Windows.Automation;

namespace ComputerUse.Windows
{
    internal sealed partial class NativeUiaKeyboard
    {
        sealed partial class Sequence : INativeCharacterTextSequence, INativeCharacterRecipientQuery
        {
            bool characterSelected, directedCharacters;
            NativeCharacterClassFamily characterFamily;
            NativeKeyboardState CharacterWritable(Request r)
            {
                var facts = new NativeKeyboardState();
                var currentRole =
                    NativeUiaBoundary.Property(element, AutomationElement.ControlTypeProperty, r) as ControlType;
                facts.Role = currentRole == null ? null : currentRole.ProgrammaticName;
                facts.Enabled = NativeUiaBoundary.Property(element, AutomationElement.IsEnabledProperty, r) as bool ? ;
                ReadTextCapability(element, facts, r, false);
                if (facts.Role != "ControlType.Edit" || facts.Enabled != true || facts.TextWritable != true)
                    throw new Failure(
                        "input_unavailable",
                        "the actual native character recipient no longer declares an enabled writable Edit");
                return facts;
            }
            NativeCharacterClassEvidence CharacterClassEvidence(Request r)
            {
                var evidence = NativeCharacterWin32.ClassEvidence(focus);
                r.Check();
                evidence.Framework =
                    NativeUiaBoundary.Property(element, AutomationElement.FrameworkIdProperty, r) as string;
                return evidence;
            }
            public NativeTextDelivery SelectTextDelivery(Request r)
            {
                if (characterSelected)
                    return directedCharacters ? NativeTextDelivery.DirectedCharacters
                                              : NativeTextDelivery.KeyboardInput;
                string reason = "receiver is virtual, composite, rich, or not an Edit";
                try
                {
                    if (element != null && target.Editor == null && role == ControlType.Edit && !RichMultiline &&
                        SupportsDispatchSampling)
                    {
                        var resolution = NativeCharacterClasses.Read(() => CharacterClassEvidence(r), r);
                        if (resolution.Error is ElementNotAvailableException)
                            throw resolution.Error;
                        reason = resolution.Reason;
                        var family = resolution.Family;
                        if (family != null)
                        {
                            IntPtr raw = RawWindow(r);
                            reason = "exact HWND, raw leaf or standalone root ancestry not confirmed";
                            if (raw != IntPtr.Zero && raw == focus && IsLeaf(r) && StandaloneCharacterEdit(r))
                            {
                                NativeCharacterWin32.RequireWritable(focus, target.Window.Native.Handle, family);
                                var writable = CharacterWritable(r);
                                r.Check();
                                var native = UiaElement.FromHandle(focus);
                                r.Check();
                                if (native != null)
                                {
                                    string hwndRuntime = NativeReadStore.Runtime(native);
                                    r.Check();
                                    bool same = UiaClient.Call(() => UiaClient.Current.CompareElements(
                                                                   element.Native, native.Native)) != 0;
                                    r.Check();
                                    directedCharacters =
                                        NativeCharacterEligibility.Eligible(new NativeCharacterEvidence {
                                            Role = role.ProgrammaticName, Runtime = runtime, HwndRuntime = hwndRuntime,
                                            RawWindow = raw, NativeFocus = focus, Composite = false, SameElement = same,
                                            Leaf = true, Standalone = true, Unicode = true, KnownEditFamily = true,
                                            Enabled = writable.Enabled, Writable = writable.TextWritable,
                                            Focused = HasFocus(r)
                                        });
                                    reason = directedCharacters
                                                 ? resolution.Reason
                                                 : "receiver identity, focus or writable evidence is incomplete";
                                    if (directedCharacters)
                                        characterFamily = family;
                                }
                                else
                                    reason = "native HWND has no matching UIA element";
                            }
                        }
                    }
                }
                catch (ElementNotAvailableException)
                {
                    r.Check();
                    throw;
                }
                catch (Exception error)
                {
                    r.Check();
                    directedCharacters = false;
                    reason = "initial recipient evidence failed: " + error.GetType().Name + ": " + error.Message;
                }
                characterSelected = true;
                NativeTextDispatchTiming.CharacterStrategy(directedCharacters, reason,
                                                           characterFamily == null ? null : characterFamily.Kind);
                return directedCharacters ? NativeTextDelivery.DirectedCharacters : NativeTextDelivery.KeyboardInput;
            }
            public void ValidateClassFamily(Request r)
            {
                r.Check();
                NativeCharacterClasses.RequireCurrent(characterFamily, CharacterClassEvidence(r));
                r.Check();
            }
            bool StandaloneCharacterEdit(Request r)
            {
                r.Check();
                var root = UiaElement.FromHandle(target.Window.Native.Handle);
                r.Check();
                if (root == null)
                    return false;
                string rootRuntime = NativeReadStore.Runtime(root);
                r.Check();
                if (String.IsNullOrEmpty(rootRuntime))
                    return false;
                var current = element;
                return NativeCharacterAncestry.Clear(
                    () =>
                    {
                        current = UiaClient.Parent(current);
                        r.Check();
                        if (current == null)
                            return null;
                        object raw =
                            NativeUiaBoundary.Property(current, AutomationElement.NativeWindowHandleProperty, r);
                        if (!(raw is int))
                            return null;
                        IntPtr handle = new IntPtr((int)raw);
                        if (handle != IntPtr.Zero && !Native.Inside(target.Window.Native.Handle, handle))
                            return null;
                        string identity = NativeReadStore.Runtime(current);
                        r.Check();
                        var ancestorRole = NativeUiaBoundary.Property(current, AutomationElement.ControlTypeProperty, r)
                                               as ControlType;
                        bool rootMatch =
                            handle == target.Window.Native.Handle && identity == rootRuntime &&
                            UiaClient.Call(() => UiaClient.Current.CompareElements(current.Native, root.Native)) != 0;
                        r.Check();
                        return new NativeCharacterAncestor {
                            Identity = identity, Role = ancestorRole == null ? null : ancestorRole.ProgrammaticName,
                            Within = true, Root = rootMatch
                        };
                    },
                    r);
            }
            public IntPtr RawWindow(Request r)
            {
                object raw = NativeUiaBoundary.Property(element, AutomationElement.NativeWindowHandleProperty, r);
                if (!(raw is int))
                    throw new Failure("input_unavailable", "the original character recipient HWND is unknown");
                return new IntPtr((int)raw);
            }
            public bool IsLeaf(Request r)
            {
                r.Check();
                var child = UiaClient.FirstChild(element);
                r.Check();
                return child == null;
            }
            public bool? HasFocus(Request r)
            {
                return NativeUiaBoundary.Property(element, AutomationElement.HasKeyboardFocusProperty, r) as bool ? ;
            }
            public void RequireCharacterIdle(Request r)
            {
                owner.RequireIdle(new NativeKeyEvent[0], r);
            }
            public void ValidateCharacterContinuation(Request r)
            {
                // First unit already completed. Complete only this scalar on
                // the same original object; never follow its new focus.
                owner.ValidateTarget(target, r);
                ValidateClassFamily(r);
                if (NativeReadStore.Runtime(element) != runtime || RawWindow(r) != focus ||
                    !owner.store.Within(element, target.Window, r) || !IsLeaf(r))
                    throw new Failure("input_unavailable",
                                      "the original Unicode character recipient changed between surrogate units");
                CharacterWritable(r);
                owner.RequireExplicitWithin(target, r);
                NativeInputAccess.RequireWindow(focus);
                target.Window.Native.Validate();
                NativeCharacterWin32.RequireWritable(focus, target.Window.Native.Handle, characterFamily);
                if (!Native.IsWindow(focus) || Native.PID(focus) != pid || Native.Generation(focus) != generation ||
                    !Native.Inside(target.Window.Native.Handle, focus))
                    throw new Failure("input_unavailable",
                                      "the original character HWND lifetime changed between surrogate units");
                r.Check();
            }
            public NativeCharacterMessageResult SendCharacter(char unit, int timeoutMs)
            {
                return owner.characterMessages.Send(focus, unit, timeoutMs);
            }
        }
    }
}
