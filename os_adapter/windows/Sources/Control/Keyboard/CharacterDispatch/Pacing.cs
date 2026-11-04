// Copyright (c) Huawei Technologies Co., Ltd. 2026. All rights reserved.

namespace ComputerUse.Windows
{
    // PURE, call-local accounting. A completed synchronous character call does
    // not confirm queued application work; this only bounds continuous dispatch
    // before offering its normal message loop a scheduling opportunity.
    internal sealed class NativeCharacterPacing
    {
        internal const int MaximumScalars = 8, MaximumContinuousMs = 64;
        int completedScalars;
        double resumedAt;
        internal NativeCharacterPacing(double startedAt)
        {
            resumedAt = startedAt;
        }
        internal void CompletedScalar()
        {
            completedScalars++;
        }
        internal bool ShouldYield(double now)
        {
            return completedScalars >= MaximumScalars || now - resumedAt >= MaximumContinuousMs;
        }
        internal void Resumed(double now)
        {
            completedScalars = 0;
            resumedAt = now;
        }
    }
    internal sealed class NativeRequestTextScheduler : INativeTextScheduler
    {
        public double Now
        {
            get {
                return Wire.Now;
            }
        }
        public void Wait(Request request, int milliseconds)
        {
            request.Wait(milliseconds);
        }
    }
}
