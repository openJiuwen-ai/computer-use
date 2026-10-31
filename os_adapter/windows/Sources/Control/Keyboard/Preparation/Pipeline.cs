// Copyright (c) Huawei Technologies Co., Ltd. 2026. All rights reserved.

using System;
using System.Collections.Generic;
using System.Linq;

namespace ComputerUse.Windows
{
    internal sealed class NativePreparePipeline
    {
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
            state = PrepareForegroundPass.Run(contract, foreground, state, r, preparation, provider, guard);
            if (state == null)
                return false;
            state = PrepareNativeReceiverPass.Run(contract, state, r, preparation, provider, guard);
            if (state == null)
                return false;
            state = ObservePreparationPass.Run(contract, state, r, guard);
            return NativeKeyboard.Ready(state, contract.Replacement) || PrepareEditorPass.Run(contract, state, operation, r, preparation, provider, guard);
        }
    }
}
