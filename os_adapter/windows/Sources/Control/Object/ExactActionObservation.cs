// Copyright (c) Huawei Technologies Co., Ltd. 2026. All rights reserved.

using System;

namespace ComputerUse.Windows
{
    internal static class NativeExactActionObservation
    {
        // QUERY: only the selected method's capability and required permission.
        // Role, other pattern getters and value observations are not authority
        // for this explicit method. Boundary validation follows all callbacks.
        internal static NativeObjectState Read(object native, string action, Func<bool?> enabled,
                                               Func<bool> available, Action boundary, Request request)
        {
            request.Check();
            var state = new NativeObjectState {
                Native = native,
                Enabled = NativeObjectActionPermission.RequiresEnabled("native_action", action, true)
                              ? enabled() : null
            };
            request.Check();
            state.ExactAction = new NativeExactActionEvidence(action, available());
            request.Check();
            boundary();
            request.Check();
            return state;
        }
    }
}
