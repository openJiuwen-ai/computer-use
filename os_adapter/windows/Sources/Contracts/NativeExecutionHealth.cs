// Copyright (c) Huawei Technologies Co., Ltd. 2026. All rights reserved.

using System;

namespace ComputerUse.Windows
{
    // Private native-worker health; never part of the SDK request/result schema.
    // An unsafe process cannot become healthy through a new request or lease.
    internal interface INativeWorkerHealthStatus
    {
        string WorkerRetirementReason { get; }
    }
    // Stop succeeds only after this owner's process exit was actually observed.
    // A false/unknown result must never authorize rebinding or another worker.
    internal interface INativeWorkerProcess : IDisposable
    {
        void Send(object value);
        string Poll(int milliseconds);
        bool Stop(int grace = 0);
        bool ExitConfirmed { get; }
    }
}
