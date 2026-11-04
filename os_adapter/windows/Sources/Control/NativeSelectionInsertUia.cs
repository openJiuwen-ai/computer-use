// Copyright (c) Huawei Technologies Co., Ltd. 2026. All rights reserved.

using System;
using System.Collections.Generic;
using System.Runtime.InteropServices;
using System.Windows.Automation;

namespace ComputerUse.Windows
{
    internal sealed partial class NativeUiaObjects : INativeSelectionInsertProvider
    {
        public NativeSelectionInsertOffer OfferSelectionInsert(Dictionary<string, object> target, string[] within,
                                                               Request request)
        {
            if (target["node"] == null)
                return null; // Element-only first slice.
            try
            {
                return new NativeUiaKeyboard(store).SelectionInsertOffer(target, within, request);
            }
            catch (COMException)
            {
                request.Check();
                return null;
            }
            catch (Failure error)
            {
                request.Check();
                // Optional capability absence cannot erase the existing
                // keyboard route. Identity/scope/modal/cancel/deadline propagate.
                if (error.Code != "input_unavailable" || request.Effect || request.Dispatched)
                    throw;
                return null;
            }
        }
    }
    internal sealed partial class NativeUiaKeyboard
    {
        internal NativeSelectionInsertOffer SelectionInsertOffer(Dictionary<string, object> binding, string[] within,
                                                                 Request request)
        {
            var state = Read(binding, within, request, true);
            request.Check();
            var target = (Target)state.Native;
            if (target.Node == null || target.Editor != null || state.Role != "ControlType.Edit" || !state.Ready ||
                state.TextWritable != true)
                return null;
            var sequence = new Sequence(this, target, request);
            var facts = ((INativeSelectionInsertSequence)sequence).Read(request);
            return facts.Eligible && facts.Ready && facts.Enabled == true && facts.Writable == true
                       ? new NativeSelectionInsertOffer(sequence, facts)
                       : null;
        }
        sealed partial class Sequence : INativeSelectionInsertSequence
        {
            NativeSelectionInsertMechanism selectionMechanism;
            NativeSelectionInsertMechanism SelectionMechanism(Request request)
            {
                var classes = CharacterClassEvidence(request);
                string uiaClass = NativeUiaBoundary.Property(target.Node == null ? element : target.Node.Element,
                                                             AutomationElement.ClassNameProperty, request) as string;
                long style = NativeSelectionInsertWin32.Style(focus);
                request.Check();
                return NativeSelectionInsertMechanism.Resolve(classes.NativeClass, classes.RealClass, classes.Framework,
                                                              classes.Unicode, style, uiaClass);
            }
            NativeSelectionInsertState INativeSelectionInsertSequence.Read(Request request)
            {
                return NativeAppInsertRecipientFailure.Query(target.Node == null, request,
                                                             () => ReadSelectionInsert(request));
            }
            NativeSelectionInsertState ReadSelectionInsert(Request request)
            {
                owner.ValidateTarget(target, request);
                NativeRoute(request);
                var sample =
                    target.Node == null
                        ? ReadPinnedInsertProperties(request)
                        : owner.ReadTextDispatchState(target, new NativeInputContract { Replacement = false }, request);
                if (sample == null)
                    return new NativeSelectionInsertState();
                var state = new NativeSelectionInsertState { Role = sample.Role, Enabled = sample.Enabled,
                                                             Writable = sample.TextWritable };
                if (target.Editor != null || role != ControlType.Edit || multiline ||
                    target.Node == null && pid != target.Window.Native.Pid)
                    return state;
                bool? password = NativeUiaBoundary.Property(target.Node == null ? element : target.Node.Element,
                                                            AutomationElement.IsPasswordProperty, request) as bool
                    ? ;
                var mechanism = SelectionMechanism(request);
                bool leaf = IsLeaf(request), standalone = StandaloneCharacterEdit(request);
                IntPtr raw = RawWindow(request);
                bool sameHwndElement = true;
                if (target.Node == null && mechanism != null && raw == focus && leaf && standalone)
                {
                    request.Check();
                    var native = UiaElement.FromHandle(focus);
                    request.Check();
                    sameHwndElement =
                        native != null && NativeReadStore.Runtime(native) == runtime &&
                        UiaClient.Call(() => UiaClient.Current.CompareElements(element.Native, native.Native)) != 0;
                    request.Check();
                }
                state.Eligible = password == false && mechanism != null && raw == focus && leaf && standalone &&
                                 sameHwndElement && (selectionMechanism == null || selectionMechanism.Same(mechanism));
                if (!state.Eligible)
                {
                    if (target.Node == null && selectionMechanism != null)
                        throw new Failure("input_unavailable",
                                          "the retained App selection editor lost its declared mechanism or identity");
                    return state;
                }
                if (selectionMechanism == null)
                    selectionMechanism = mechanism; // Identity specification only, never permissions.
                // Only positively confirmed native selection-edit identity adds
                // this requirement to App's legacy fallback. Virtual/unknown
                // mechanisms keep their old keyboard capability and same pin.
                if (target.Node == null &&
                    (sample.Role != "ControlType.Edit" || sample.Enabled != true || sample.TextWritable != true))
                    throw new Failure("input_unavailable",
                                      "the confirmed native App editor has no fresh enabled writable text declaration");
                state.Selection = NativeSelectionInsertWin32.Selection(focus, request);
                // Selection and provider queries may run application code.
                // Revalidate retained original target and receiver afterwards.
                owner.ValidateTarget(target, request);
                ValidateDispatchReceiver(request);
                owner.RequireExplicitWithin(target, request);
                ValidateDispatchFocus(request);
                NativeRoute(request);
                state.Ready = true;
                return state;
            }
            void INativeSelectionInsertSequence.ValidateFinal(NativeSelectionInsertState state, Request request)
            {
                NativeAppInsertRecipientFailure.Query(target.Node == null, request,
                                                      () =>
                                                      {
                                                          ValidateSelectionInsertFinal(state, request);
                                                          return true;
                                                      });
            }
            void ValidateSelectionInsertFinal(NativeSelectionInsertState state, Request request)
            {
                request.Check();
                owner.ValidateTarget(target, request);
                ValidateDispatchReceiver(request);
                var writable =
                    target.Node == null
                        ? ReadPinnedInsertProperties(request)
                        : owner.ReadTextDispatchState(target, new NativeInputContract { Replacement = false }, request);
                if (writable.Role != state.Role || writable.Enabled != true || writable.TextWritable != true)
                    throw new Failure(
                        "input_unavailable",
                        "the original selected edit no longer declares enabled writable text; no edit sent");
                // All retained-provider callbacks (including password and
                // class/framework evidence) precede the fresh focus sample.
                bool? password = NativeUiaBoundary.Property(target.Node == null ? element : target.Node.Element,
                                                            AutomationElement.IsPasswordProperty, request) as bool
                    ? ;
                var mechanism = SelectionMechanism(request);
                if (password != false || selectionMechanism == null || !selectionMechanism.Same(mechanism))
                    throw new Failure("input_unavailable",
                                      "the retained selection editor password or mechanism changed; no edit sent");
                owner.RequireExplicitWithin(target, request);
                ValidateDispatchFocus(request);
                // No retained-provider callback after this fresh focus query.
                // This is not an atomic UIA snapshot. Selection editing uses this object's
                // current selection, not an entry-time numeric range permit.
                // Recheck native readonly/enabled/class, lifetime/queue last.
                NativeInputAccess.RequireWindow(target.Window.Native.Handle);
                NativeInputAccess.RequireWindow(focus);
                var classes = NativeCharacterWin32.ClassEvidence(focus);
                long style = NativeSelectionInsertWin32.Style(focus);
                request.Check();
                if (selectionMechanism == null || classes.NativeClass != selectionMechanism.NativeClass ||
                    classes.RealClass != selectionMechanism.RealClass || !classes.Unicode ||
                    (style & (4 | 8 | 16 | 32 | 0x400 | 0x800 | 0x2000)) != 0 || !Native.IsWindowEnabled(focus) ||
                    !Native.IsWindowEnabled(target.Window.Native.Handle))
                    throw new Failure(
                        "input_unavailable",
                        "the original native selection editor became read-only, disabled or changed mechanism; no edit sent");
                NativeRoute(request);
                request.Check();
            }
            IDisposable INativeSelectionInsertSequence.CreateBuffer(string text)
            {
                return NativeSelectionInsertWin32.CreateBuffer(text);
            }
            NativeSelectionInsertSendResult INativeSelectionInsertSequence.Send(IDisposable buffer, int timeoutMs)
            {
                return NativeSelectionInsertWin32.Send(focus, buffer, timeoutMs);
            }
        }
    }
}
