// Copyright (c) Huawei Technologies Co., Ltd. 2026. All rights reserved.

namespace ComputerUse.Windows
{
    // QUERY-only guard for one typed dispatch. Preparation/resolution happened
    // before the sequence was frozen. Each invocation samples anew; no cache,
    // retries, input, activation or source changes occur in this pipeline.
    internal static class NativeTextDispatchPipeline
    {
        internal static NativeKeyboardState Run(INativeTypedTextDispatchSequence sequence, NativeInputContract contract,
                                                Request request)
        {
            var timing = NativeTextDispatchTiming.Start();
            bool completed = false;
            try
            {
                using (NativeTextDispatchTiming.Measure(timing, "nativeBefore"))
                    CheckPinnedTextQueuePass.Run(sequence, request);
                NativeKeyboardState state;
                using (NativeTextDispatchTiming.Measure(timing, "sample")) state =
                    ReadTextDispatchStatePass.Run(sequence, contract, request);
                CheckTextDispatchStatePass.Run(state, contract, request);
                using (NativeTextDispatchTiming.Measure(timing, "boundary"))
                    ValidateTextDispatchScopePass.Run(sequence, request);
                using (NativeTextDispatchTiming.Measure(timing, "uiaReceiver"))
                    ValidateTextDispatchReceiverPass.Run(sequence, request);
                using (NativeTextDispatchTiming.Measure(timing, "explicitWithin"))
                    ValidateTextDispatchExplicitScopePass.Run(sequence, request);
                using (NativeTextDispatchTiming.Measure(timing, "uiaFocus"))
                    ValidateTextDispatchFocusPass.Run(sequence, request);
                using (NativeTextDispatchTiming.Measure(timing, "nativeFinal"))
                    ValidateTextDispatchQueuePass.Run(sequence, state, request);
                completed = true;
                return state;
            }
            finally
            {
                NativeTextDispatchTiming.Finish(timing, completed);
            }
        }
    }
}
