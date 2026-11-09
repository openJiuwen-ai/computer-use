// Copyright (c) Huawei Technologies Co., Ltd. 2026. All rights reserved.

using System;
using System.Collections.Generic;
using System.Linq;

namespace ComputerUse.Windows
{
    internal sealed class NativePreparePipeline
    {
        const int ReceiverObservationMs = 400;
        readonly INativeKeyboardProvider provider;
        readonly INativeKeyboardTextProvider textProvider;
        readonly Func<NativeInputContract, Request, bool, NativeKeyboardState> guard;
        internal NativePreparePipeline(INativeKeyboardProvider provider, Func<NativeInputContract, Request, bool, NativeKeyboardState> guard)
        {
            this.provider = provider;
            this.textProvider = provider as INativeKeyboardTextProvider;
            this.guard = guard;
        }

        internal bool Run(NativeInputContract contract, bool foreground, Request r, List<string> preparation, string replacementText, NativeWriteOperation operation)
        {
            // NativePreparePipeline. Each selected stage retains the original
            // state queries and limits. A failed stage returns before dispatch;
            // no stage selects a fallback or restores the previous foreground.
            var state = InspectPreparedTargetPass.Run(contract, r, guard);
            InspectReplacementPass.Run(contract, state, replacementText, r, textProvider);
            if (NativeKeyboard.Ready(state, contract.Replacement))
                return true;

            var original = state.InteractionWindow;
            bool activationCompleted = false;
            double observeUntil = 0;
            try
            {
                state = PrepareForegroundPass.Run(contract, foreground, state, r, preparation, provider, guard,
                                                  ref activationCompleted);
                if (state == null)
                    return false;
                state = PrepareNativeReceiverPass.Run(contract, state, r, preparation, provider, guard);
                if (state == null)
                    return false;
                observeUntil = Math.Min(r.Deadline, Wire.Now + ReceiverObservationMs);
                state = ObservePreparationPass.Run(contract, state, r, guard, observeUntil);
                return NativeKeyboard.Ready(state, contract.Replacement) || PrepareEditorPass.Run(contract, state, operation, r, preparation, provider, guard);
            }
            catch (NativeOwnedModalBlock block)
            {
                if (!foreground || contract.Unit != "key_sequence" || contract.Text || contract.Replacement ||
                    contract.Target["node"] == null || original == null || !ReferenceEquals(original, block.Original) ||
                    operation.Dispatched || r.Dispatched || !(activationCompleted || block.AfterActivation))
                    throw;
                r.Check();
                // A modal can interrupt before receiver observation starts.
                // Use that allowance once, or its remaining part if begun.
                if (observeUntil == 0)
                    observeUntil = Math.Min(r.Deadline, Wire.Now + ReceiverObservationMs);
                if (Wire.Now >= observeUntil)
                    throw;
                var modal = block.Sample(r, false);
                if (modal == NativeOwnedModalState.Changed)
                    throw;
                return ObserveOriginalAfterModal(contract, r, block, observeUntil, modal);
            }
        }

        bool ObserveOriginalAfterModal(NativeInputContract contract, Request r, NativeOwnedModalBlock block,
                                       double until, NativeOwnedModalState modal)
        {
            // The original activation is already spent. Only the original
            // receiver may become ready, through passive observations in its
            // existing receiver observation. No action is sent to the dialog.
            while (true)
            {
                r.Check();
                if (Wire.Now >= until)
                    throw Unsettled(block, modal == NativeOwnedModalState.Cleared);
                if (modal == NativeOwnedModalState.Cleared)
                {
                    try
                    {
                        var state = guard(contract, r, true);
                        if (!ReferenceEquals(state.InteractionWindow, block.Original))
                            throw new Failure("stale_target", "the original keyboard interaction window changed while an owned modal was active");
                        if (Wire.Now < until && NativeKeyboard.Ready(state, false))
                            return true;
                    }
                    catch (Failure error)
                    {
                        if (error.Code != "input_unavailable")
                            throw;
                        // A new route after a modal interruption could prepare a
                        // different receiver. Keep this original refusal terminal.
                        throw Unsettled(block, true);
                    }
                }
                double remaining = until - Wire.Now;
                if (remaining <= 0)
                    throw Unsettled(block, modal == NativeOwnedModalState.Cleared);
                r.Wait((int)Math.Ceiling(Math.Min(20, remaining)));
                if (modal == NativeOwnedModalState.Pending && Wire.Now < until)
                {
                    modal = block.Sample(r, true);
                    if (modal == NativeOwnedModalState.Changed)
                        throw block;
                }
            }
        }

        static Failure Unsettled(NativeOwnedModalBlock block, bool cleared)
        {
            return cleared
                ? new Failure("blocked_by_modal",
                              "an owned modal interrupted preparation and the original keyboard receiver was not confirmed")
                : block;
        }
    }
}
